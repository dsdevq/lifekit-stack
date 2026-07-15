"""Unit tests for the devclaw MCP client.

Hermetic: we never make a real HTTP call. We inject a stub ``httpx.AsyncClient``
via the constructor's ``http_client=`` kwarg (designed for exactly this).

The stub is MCP-session-aware (2026-07-05): devclaw's FastMCP runs stateful, so
the client now performs an ``initialize`` handshake (capturing the
``Mcp-Session-Id`` header) and sends ``notifications/initialized`` before any
``tools/call``. The stub answers ``initialize`` with a valid result + a session
header and applies the caller-configured response/exception to the ``tools/call``
leg — so the failure-path assertions target the real tool call, not the
handshake.
"""

from __future__ import annotations

import json

import httpx
import pytest

from ops_agent.mcp_client import DevclawMCPClient, MCPClientError

_TEST_SESSION = "test-session-123"


class _StubClient:
    """Stand-in for ``httpx.AsyncClient`` — captures requests + replays canned
    responses, with a realistic MCP handshake.

    ``initialize`` always succeeds (returns a valid result and, unless
    ``session_id=None``, an ``Mcp-Session-Id`` header). ``notifications/
    initialized`` returns 202. The configured ``response``/``exc`` — or a queue
    of ``tool_responses`` for multi-step scenarios like the self-heal retry —
    applies to ``tools/call`` only."""

    def __init__(
        self,
        response: httpx.Response | None = None,
        exc: Exception | None = None,
        *,
        session_id: str | None = _TEST_SESSION,
        tool_responses: list[httpx.Response] | None = None,
    ):
        self._response = response
        self._exc = exc
        self._session_id = session_id
        self._tool_responses = list(tool_responses) if tool_responses is not None else None
        self.posts: list[dict] = []
        self.closed = False

    async def post(self, url, *, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        method = (json or {}).get("method")
        if method == "initialize":
            init_headers = {"mcp-session-id": self._session_id} if self._session_id else {}
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": (json or {}).get("id"),
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "devclaw", "version": "1"},
                    },
                },
                headers=init_headers,
            )
        if method == "notifications/initialized":
            return httpx.Response(202, text="")
        # tools/call (and anything else)
        if self._exc is not None:
            raise self._exc
        if self._tool_responses is not None:
            return self._tool_responses.pop(0)
        return self._response

    async def aclose(self) -> None:
        self.closed = True

    @property
    def tool_posts(self) -> list[dict]:
        return [p for p in self.posts if (p["json"] or {}).get("method") == "tools/call"]

    @property
    def init_posts(self) -> list[dict]:
        return [p for p in self.posts if (p["json"] or {}).get("method") == "initialize"]


def _ok_response(verdict: dict) -> httpx.Response:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": json.dumps(verdict)}],
            "isError": False,
        },
    }
    return httpx.Response(200, json=body)


def _http_error(status: int, text: str = "boom") -> httpx.Response:
    return httpx.Response(status, text=text)


# ---- happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_goal_returns_parsed_verdict() -> None:
    verdict = {
        "goal_id": "g1",
        "verdict": "on_track",
        "rationale": "fine",
        "corrections": "",
        "question": "",
    }
    stub = _StubClient(response=_ok_response(verdict))
    client = DevclawMCPClient(url="http://x/mcp", http_client=stub)

    out = await client.evaluate_goal("g1")
    assert out == verdict
    # Used the expected JSON-RPC envelope on the tool leg (post[0] is now the
    # initialize handshake).
    tp = stub.tool_posts[0]
    assert tp["json"]["method"] == "tools/call"
    assert tp["json"]["params"]["name"] == "evaluate_goal"
    assert tp["json"]["params"]["arguments"] == {"goal_id": "g1"}


@pytest.mark.asyncio
async def test_request_id_increments_per_call() -> None:
    verdict = {"goal_id": "g", "verdict": "ok"}
    stub = _StubClient(response=_ok_response(verdict))
    client = DevclawMCPClient(http_client=stub)

    await client.evaluate_goal("g")
    await client.evaluate_goal("g")
    tp = stub.tool_posts
    assert len(tp) == 2
    assert tp[0]["json"]["id"] < tp[1]["json"]["id"]  # monotonic across calls


@pytest.mark.asyncio
async def test_bearer_token_when_configured() -> None:
    verdict = {"goal_id": "g", "verdict": "ok"}
    stub = _StubClient(response=_ok_response(verdict))
    client = DevclawMCPClient(http_client=stub, token="secret")

    await client.evaluate_goal("g")
    # Every leg carries the bearer token (initialize AND tools/call).
    assert stub.posts[0]["headers"]["Authorization"] == "Bearer secret"
    assert stub.tool_posts[0]["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_no_bearer_header_when_token_unset() -> None:
    verdict = {"goal_id": "g", "verdict": "ok"}
    stub = _StubClient(response=_ok_response(verdict))
    client = DevclawMCPClient(http_client=stub, token=None)

    await client.evaluate_goal("g")
    assert "Authorization" not in stub.posts[0]["headers"]


@pytest.mark.asyncio
async def test_uses_env_url_default(monkeypatch) -> None:
    monkeypatch.setenv("OPS_AGENT_DEVCLAW_MCP_URL", "http://other/mcp")
    verdict = {"goal_id": "g", "verdict": "ok"}
    stub = _StubClient(response=_ok_response(verdict))
    client = DevclawMCPClient(http_client=stub)

    await client.evaluate_goal("g")
    assert stub.posts[0]["url"] == "http://other/mcp"


# ---- MCP session handshake ------------------------------------------------


@pytest.mark.asyncio
async def test_initializes_session_before_first_tool_call() -> None:
    stub = _StubClient(response=_ok_response({"verdict": "ok"}))
    client = DevclawMCPClient(http_client=stub)

    await client.evaluate_goal("g")

    methods = [(p["json"] or {}).get("method") for p in stub.posts]
    assert methods[0] == "initialize"
    assert "notifications/initialized" in methods
    assert methods[-1] == "tools/call"
    # The session id captured from initialize is attached to the tool call.
    assert stub.tool_posts[0]["headers"].get("Mcp-Session-Id") == _TEST_SESSION
    assert stub.tool_posts[0]["headers"].get("MCP-Protocol-Version") == "2025-06-18"


@pytest.mark.asyncio
async def test_session_reused_across_calls() -> None:
    stub = _StubClient(response=_ok_response({"verdict": "ok"}))
    client = DevclawMCPClient(http_client=stub)

    await client.evaluate_goal("g")
    await client.evaluate_goal("g")

    # Handshake happens once; the session is reused for the second call.
    assert len(stub.init_posts) == 1
    assert len(stub.tool_posts) == 2


@pytest.mark.asyncio
async def test_reinitializes_and_retries_once_on_session_error() -> None:
    """A stale/missing session (HTTP 400 'Missing session ID') triggers exactly
    one re-initialize + retry, and the retried call succeeds."""
    err = httpx.Response(400, text="Bad Request: Missing session ID")
    ok = _ok_response({"goal_id": "g", "verdict": "ok"})
    stub = _StubClient(tool_responses=[err, ok])
    client = DevclawMCPClient(http_client=stub)

    out = await client.evaluate_goal("g")
    assert out == {"goal_id": "g", "verdict": "ok"}
    assert len(stub.init_posts) == 2  # initial handshake + one re-init
    assert len(stub.tool_posts) == 2  # failed call + retry


@pytest.mark.asyncio
async def test_stateless_server_without_session_header_still_works() -> None:
    """A server that returns no Mcp-Session-Id (stateless mode) is fine — the
    client simply sends no session id and the tool call still goes through."""
    stub = _StubClient(response=_ok_response({"verdict": "ok"}), session_id=None)
    client = DevclawMCPClient(http_client=stub)

    out = await client.evaluate_goal("g")
    assert out == {"verdict": "ok"}
    assert "Mcp-Session-Id" not in stub.tool_posts[0]["headers"]


# ---- input validation -----------------------------------------------------


@pytest.mark.asyncio
async def test_empty_goal_id_raises_protocol_error() -> None:
    stub = _StubClient(response=_ok_response({"verdict": "ok"}))
    client = DevclawMCPClient(http_client=stub)

    with pytest.raises(MCPClientError) as exc_info:
        await client.evaluate_goal("")
    assert exc_info.value.reason == "protocol"
    # No HTTP call at all — not even the handshake.
    assert stub.posts == []


# ---- failure paths --------------------------------------------------------


@pytest.mark.asyncio
async def test_transport_error_becomes_typed() -> None:
    stub = _StubClient(exc=httpx.ConnectError("conn refused"))
    client = DevclawMCPClient(http_client=stub)

    with pytest.raises(MCPClientError) as exc_info:
        await client.evaluate_goal("g")
    assert exc_info.value.reason == "transport"


@pytest.mark.asyncio
async def test_http_error_status_becomes_typed() -> None:
    stub = _StubClient(response=_http_error(500, "oh no"))
    client = DevclawMCPClient(http_client=stub)

    with pytest.raises(MCPClientError) as exc_info:
        await client.evaluate_goal("g")
    assert exc_info.value.reason == "http_status"
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_http_401_becomes_typed() -> None:
    stub = _StubClient(response=_http_error(401, "auth"))
    client = DevclawMCPClient(http_client=stub)

    with pytest.raises(MCPClientError) as exc_info:
        await client.evaluate_goal("g")
    assert exc_info.value.reason == "http_status"
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_malformed_jsonrpc_becomes_protocol_error() -> None:
    stub = _StubClient(response=httpx.Response(200, text="not json at all"))
    client = DevclawMCPClient(http_client=stub)

    with pytest.raises(MCPClientError) as exc_info:
        await client.evaluate_goal("g")
    assert exc_info.value.reason == "protocol"


@pytest.mark.asyncio
async def test_tool_error_becomes_typed() -> None:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "unknown goal_id: g"},
    }
    stub = _StubClient(response=httpx.Response(200, json=body))
    client = DevclawMCPClient(http_client=stub)

    with pytest.raises(MCPClientError) as exc_info:
        await client.evaluate_goal("g")
    assert exc_info.value.reason == "tool_error"
    assert "unknown goal_id" in exc_info.value.message


@pytest.mark.asyncio
async def test_is_error_envelope_becomes_typed() -> None:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "isError": True,
            "content": [{"type": "text", "text": "tool blew up"}],
        },
    }
    stub = _StubClient(response=httpx.Response(200, json=body))
    client = DevclawMCPClient(http_client=stub)

    with pytest.raises(MCPClientError) as exc_info:
        await client.evaluate_goal("g")
    assert exc_info.value.reason == "tool_error"
    assert "tool blew up" in exc_info.value.message


@pytest.mark.asyncio
async def test_missing_content_becomes_typed() -> None:
    body = {"jsonrpc": "2.0", "id": 1, "result": {"isError": False}}
    stub = _StubClient(response=httpx.Response(200, json=body))
    client = DevclawMCPClient(http_client=stub)

    with pytest.raises(MCPClientError) as exc_info:
        await client.evaluate_goal("g")
    assert exc_info.value.reason == "unknown_response"


@pytest.mark.asyncio
async def test_tool_returns_non_json_text() -> None:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": "not-json-at-all"}],
            "isError": False,
        },
    }
    stub = _StubClient(response=httpx.Response(200, json=body))
    client = DevclawMCPClient(http_client=stub)

    with pytest.raises(MCPClientError) as exc_info:
        await client.evaluate_goal("g")
    assert exc_info.value.reason == "protocol"


# ---- SSE fallback parser --------------------------------------------------


@pytest.mark.asyncio
async def test_sse_body_is_parsed_when_returned() -> None:
    verdict = {"goal_id": "g", "verdict": "ok"}
    body = (
        "data: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(verdict)}],
                    "isError": False,
                },
            }
        )
        + "\n\n"
    )
    stub = _StubClient(response=httpx.Response(200, text=body))
    client = DevclawMCPClient(http_client=stub)

    out = await client.evaluate_goal("g")
    assert out == verdict


# ---- lifecycle ------------------------------------------------------------


@pytest.mark.asyncio
async def test_owned_client_closed_on_aclose() -> None:
    # Don't inject — let the client own the inner httpx.AsyncClient.
    client = DevclawMCPClient(url="http://x")
    await client.aclose()
    # Idempotency check: second close shouldn't crash.
    await client.aclose()


@pytest.mark.asyncio
async def test_injected_client_not_closed_on_aclose() -> None:
    stub = _StubClient()
    client = DevclawMCPClient(http_client=stub)
    await client.aclose()
    assert stub.closed is False


@pytest.mark.asyncio
async def test_async_context_manager_closes_owned() -> None:
    async with DevclawMCPClient(url="http://x") as client:
        assert client is not None


# ---- structural narrowness contract --------------------------------------


def test_client_exposes_only_authorized_tools() -> None:
    """Load-bearing boundary: client surface MUST stay narrow to the PR's authority.

    ops-PR2 authorized ``evaluate_goal``; ops-PR3 added ``steer_goal``; ops-PR4
    adds ``fix_bug`` (L3 self-heal against devclaw defects — gated by the
    ``l3_enabled`` config flag + ``devclaw_repo_path`` allowlist at the
    daemon layer). Any FURTHER tool (``cancel_goal``, phase transitions,
    workspace manipulation) stays explicitly forbidden — if a future PR
    adds another MCP method to this class without updating this allowlist
    the test fails, forcing a deliberate review of the authority escalation.
    """
    public_async_methods = {
        name
        for name in dir(DevclawMCPClient)
        if not name.startswith("_") and callable(getattr(DevclawMCPClient, name))
    }
    # Allowed surface: the three tools through ops-PR4 + lifecycle helpers.
    assert "evaluate_goal" in public_async_methods
    assert "steer_goal" in public_async_methods
    assert "fix_bug" in public_async_methods
    # Explicitly assert the tools that remain DEFERRED beyond ops-PR4 are absent.
    forbidden = {"cancel_goal", "implement_feature", "answer_unknowns"}
    assert public_async_methods.isdisjoint(
        forbidden
    ), f"DevclawMCPClient leaked deferred tools: {public_async_methods & forbidden}"

"""Unit tests for the devclaw MCP client.

Hermetic: we never make a real HTTP call. We inject a stub ``httpx.AsyncClient``
via the constructor's ``http_client=`` kwarg (designed for exactly this).
"""

from __future__ import annotations

import json

import httpx
import pytest

from ops_agent.mcp_client import DevclawMCPClient, MCPClientError


class _StubClient:
    """Stand-in for ``httpx.AsyncClient`` — captures requests + replays canned responses."""

    def __init__(self, response: httpx.Response | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.posts: list[dict] = []
        self.closed = False

    async def post(self, url, *, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        if self._exc is not None:
            raise self._exc
        return self._response

    async def aclose(self) -> None:
        self.closed = True


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
    # Used the expected JSON-RPC envelope.
    assert stub.posts[0]["json"]["method"] == "tools/call"
    assert stub.posts[0]["json"]["params"]["name"] == "evaluate_goal"
    assert stub.posts[0]["json"]["params"]["arguments"] == {"goal_id": "g1"}


@pytest.mark.asyncio
async def test_request_id_increments_per_call() -> None:
    verdict = {"goal_id": "g", "verdict": "ok"}
    stub = _StubClient(response=_ok_response(verdict))
    client = DevclawMCPClient(http_client=stub)

    await client.evaluate_goal("g")
    await client.evaluate_goal("g")
    assert stub.posts[0]["json"]["id"] == 1
    assert stub.posts[1]["json"]["id"] == 2


@pytest.mark.asyncio
async def test_bearer_token_when_configured() -> None:
    verdict = {"goal_id": "g", "verdict": "ok"}
    stub = _StubClient(response=_ok_response(verdict))
    client = DevclawMCPClient(http_client=stub, token="secret")

    await client.evaluate_goal("g")
    assert stub.posts[0]["headers"]["Authorization"] == "Bearer secret"


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


# ---- input validation -----------------------------------------------------


@pytest.mark.asyncio
async def test_empty_goal_id_raises_protocol_error() -> None:
    stub = _StubClient(response=_ok_response({"verdict": "ok"}))
    client = DevclawMCPClient(http_client=stub)

    with pytest.raises(MCPClientError) as exc_info:
        await client.evaluate_goal("")
    assert exc_info.value.reason == "protocol"
    # No HTTP call should have been made.
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


def test_client_exposes_only_evaluate_goal() -> None:
    """Load-bearing boundary: ops-PR2 client must NOT expose any other write surface.

    If a future PR adds an MCP method to this class, this test will fail —
    forcing a deliberate review of the authority escalation.
    """
    public_async_methods = {
        name
        for name in dir(DevclawMCPClient)
        if not name.startswith("_") and callable(getattr(DevclawMCPClient, name))
    }
    # Allowed surface: the one tool + lifecycle helpers.
    assert "evaluate_goal" in public_async_methods
    # Explicitly assert the tools that are DEFERRED to ops-PR3+ are absent.
    forbidden = {"steer_goal", "cancel_goal", "fix_bug", "implement_feature", "answer_unknowns"}
    assert public_async_methods.isdisjoint(
        forbidden
    ), f"DevclawMCPClient leaked deferred tools: {public_async_methods & forbidden}"

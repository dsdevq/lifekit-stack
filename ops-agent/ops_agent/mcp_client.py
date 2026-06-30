"""Devclaw MCP client — the SINGLE structural seam through which the
ops-agent acts on devclaw.

Boundary contract (load-bearing — see ``~/memory/projects/devclaw/plan.md``):

  - ops-PR2 exposed exactly ONE tool: ``evaluate_goal``. ops-PR3 escalates
    to TWO tools by adding ``steer_goal`` (L2 — inbox injection). Beyond
    that — ``cancel_goal``, phase transitions, etc. — remains deferred.
  - The class :class:`DevclawMCPClient` MUST stay structurally narrow:
    adding a method here is a deliberate authority escalation. Reviewers
    should treat any PR that grows this class with the same care as a PR
    that grows devclaw's MCP surface itself.
  - All failures are typed (:class:`MCPClientError`). The daemon never
    crashes on an MCP failure — the playbook records and falls back.

Transport: streamable-http JSON-RPC 2.0 against devclaw-mcp's ``/mcp``
endpoint (see ``compose/devclaw-mcp/Dockerfile`` and devclaw's
``server/lifecycle.py``). We use ``httpx`` directly rather than the official
``mcp`` Python SDK because:

  1. We need exactly one tool call — pulling in the full MCP SDK + its
     transport abstractions is more surface than we use.
  2. Devclaw's transport (FastMCP streamable-http) accepts a plain
     ``POST /mcp`` with a JSON-RPC body and returns either JSON or SSE.
     For a single ``tools/call`` we can ask for ``application/json`` via
     the Accept header and get a one-shot response.
  3. A thin client is easier to audit for the boundary discipline above.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

_log = logging.getLogger("ops_agent.mcp")


@dataclass(frozen=True)
class MCPClientError(Exception):
    """Typed MCP-transport failure.

    ``reason`` is one of:
      - ``transport``       — httpx couldn't reach the server
      - ``http_status``     — server returned a non-2xx
      - ``protocol``        — response wasn't valid JSON-RPC 2.0
      - ``tool_error``      — JSON-RPC error from the tool itself (ToolError)
      - ``unknown_response`` — response shape we don't recognize
    """

    reason: str
    message: str
    status_code: int | None = None
    raw_body_tail: str = ""

    def __str__(self) -> str:  # pragma: no cover — debug-only
        return f"MCPClientError({self.reason}): {self.message}"


def _default_url() -> str:
    # Compose service DNS — see compose/docker-compose.yml.
    return os.environ.get("OPS_AGENT_DEVCLAW_MCP_URL", "http://devclaw-mcp:8000/mcp")


def _default_token() -> str | None:
    raw = os.environ.get("OPS_AGENT_DEVCLAW_MCP_TOKEN", "").strip()
    return raw or None


def _default_timeout_s() -> float:
    raw = os.environ.get("OPS_AGENT_DEVCLAW_MCP_TIMEOUT_S", "30").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 30.0


class DevclawMCPClient:
    """Narrow MCP client — ONLY exposes the tools ops-PR2 is authorized to call.

    Use as an async context manager so the underlying httpx client is closed
    cleanly, or share one instance across the daemon lifecycle and close at
    shutdown.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        token: str | None = None,
        timeout_s: float | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url or _default_url()
        self._token = token if token is not None else _default_token()
        self._timeout_s = timeout_s if timeout_s is not None else _default_timeout_s()
        # Allow tests to inject a stub; production keeps lifecycle here.
        self._owned_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=self._timeout_s)
        # Monotonic request-id counter — the JSON-RPC id only needs to be
        # unique per outstanding call.
        self._next_id = 1

    async def __aenter__(self) -> DevclawMCPClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        # Streamable-http MCP accepts either JSON or SSE for responses. We ask
        # for JSON so a single tool call returns one-shot, no SSE plumbing.
        h = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def evaluate_goal(self, goal_id: str) -> dict[str, Any]:
        """L1 action — force a direction evaluation NOW on ``goal_id``.

        Returns the verdict dict shape devclaw's :class:`GoalService.evaluate_goal`
        produces::

            {
              "goal_id": "...",
              "verdict": "on_track" | "off_track" | "blocked" | ...,
              "rationale": "...",
              "corrections": "..."  (may be empty / None),
              "question": "..."     (may be empty / None),
            }

        Raises :class:`MCPClientError` on transport / protocol / tool error.
        """
        if not goal_id:
            raise MCPClientError(
                reason="protocol",
                message="evaluate_goal requires non-empty goal_id",
            )
        return await self._call_tool("evaluate_goal", {"goal_id": goal_id})

    async def steer_goal(self, goal_id: str, message: str) -> dict[str, Any]:
        """L2 action — append a one-line steering correction to a goal's inbox.

        Maps onto devclaw's ``steer_goal(goal_id, message)`` MCP tool
        (see ``devclaw/server/tools.py``). Devclaw records the message
        as steering, unblocks the goal if blocked, and pokes the heartbeat
        so the next-action planner picks the correction up immediately.

        Returns whatever devclaw's tool returns (typically a small dict
        confirming the goal_id + the recorded steering). Raises
        :class:`MCPClientError` on transport / protocol / tool error —
        non-empty ``goal_id`` and ``message`` are required (devclaw's tool
        also enforces both, but we shortcut the round-trip here).
        """
        if not goal_id:
            raise MCPClientError(
                reason="protocol",
                message="steer_goal requires non-empty goal_id",
            )
        if not message:
            raise MCPClientError(
                reason="protocol",
                message="steer_goal requires non-empty message",
            )
        return await self._call_tool(
            "steer_goal",
            {"goal_id": goal_id, "message": message},
        )

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """JSON-RPC ``tools/call`` wrapper.

        Devclaw's FastMCP exposes the standard MCP method names. Response
        shape is the MCP envelope::

            {"jsonrpc": "2.0", "id": N, "result": {
                "content": [{"type": "text", "text": "<json string>"}],
                "isError": false
            }}

        Devclaw's tool returns a JSON-stringified dict in the text content,
        so we json.loads the inner text to give the caller a real dict.
        """
        req_id = self._next_id
        self._next_id += 1
        body = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }

        try:
            resp = await self._client.post(self._url, json=body, headers=self._headers())
        except httpx.RequestError as exc:
            _log.warning("mcp transport error url=%s err=%s", self._url, exc)
            raise MCPClientError(
                reason="transport",
                message=f"Could not reach devclaw MCP at {self._url}: {exc}",
            ) from exc

        if resp.status_code >= 400:
            _log.warning(
                "mcp http status url=%s status=%s body=%s",
                self._url,
                resp.status_code,
                resp.text[:200],
            )
            raise MCPClientError(
                reason="http_status",
                message=f"devclaw MCP returned HTTP {resp.status_code}",
                status_code=resp.status_code,
                raw_body_tail=resp.text[-500:],
            )

        # Streamable-http MAY return SSE for some calls. For tools/call with a
        # JSON Accept, FastMCP returns plain JSON — parse defensively.
        body_text = resp.text
        parsed = _try_parse_jsonrpc(body_text)
        if parsed is None:
            raise MCPClientError(
                reason="protocol",
                message="devclaw MCP response was not valid JSON-RPC",
                raw_body_tail=body_text[-500:],
            )

        if "error" in parsed:
            err = parsed["error"]
            return _raise_tool_error(err)

        result = parsed.get("result")
        if not isinstance(result, dict):
            raise MCPClientError(
                reason="unknown_response",
                message="JSON-RPC result missing or not an object",
                raw_body_tail=body_text[-500:],
            )

        # Standard MCP tool-result envelope. Devclaw's tool functions return a
        # JSON-formatted string in the text content; unwrap it.
        if result.get("isError"):
            content = result.get("content") or []
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            return _raise_tool_error({"message": "; ".join(text_parts) or "tool reported isError"})

        content = result.get("content")
        if not isinstance(content, list) or not content:
            raise MCPClientError(
                reason="unknown_response",
                message="tool result had no content",
                raw_body_tail=body_text[-500:],
            )

        first = content[0]
        if not isinstance(first, dict) or first.get("type") != "text":
            raise MCPClientError(
                reason="unknown_response",
                message="tool result content[0] not a text part",
                raw_body_tail=body_text[-500:],
            )

        text = first.get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise MCPClientError(
                reason="protocol",
                message=f"tool returned non-JSON text payload: {exc}",
                raw_body_tail=text[-500:],
            ) from exc


def _try_parse_jsonrpc(body_text: str) -> dict[str, Any] | None:
    """Parse a JSON-RPC response body — JSON or SSE-wrapped.

    Streamable-http with Accept: application/json normally returns plain
    JSON. If for some reason the server picks SSE, the body is a series of
    ``data: <json>`` lines; pick the last one that parses.
    """
    body_text = body_text.strip()
    if not body_text:
        return None
    try:
        parsed = json.loads(body_text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    # SSE fallback — pull the last ``data:`` line.
    last_obj: dict[str, Any] | None = None
    for line in body_text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                last_obj = obj
        except json.JSONDecodeError:
            continue
    return last_obj


def _raise_tool_error(err: dict[str, Any]) -> dict[str, Any]:
    raise MCPClientError(
        reason="tool_error",
        message=str(err.get("message", "tool error")),
        raw_body_tail=json.dumps(err)[-500:],
    )

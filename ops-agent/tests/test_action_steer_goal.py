"""Unit tests for the steer_goal L2 action.

Hermetic: stub MCP client mirrors test_action_evaluate_goal.py's pattern.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ops_agent.actions import ActionOutcome, outcome_to_dict, perform_steer_goal
from ops_agent.mcp_client import MCPClientError


@dataclass
class _StubMCP:
    """Stand-in for ``DevclawMCPClient`` — drives the action's paths."""

    result: dict | None = None
    error: MCPClientError | None = None
    raise_other: Exception | None = None
    calls: list = None  # populated by __post_init__

    def __post_init__(self) -> None:
        self.calls = []

    async def steer_goal(self, goal_id: str, message: str) -> dict:
        self.calls.append((goal_id, message))
        if self.raise_other is not None:
            raise self.raise_other
        if self.error is not None:
            raise self.error
        return self.result or {}


# ---- happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_perform_steer_goal_ok_returns_result() -> None:
    result = {"goal_id": "g1", "steering_recorded": True}
    mcp = _StubMCP(result=result)

    outcome = await perform_steer_goal("g1", "re-check direction", mcp)

    assert outcome.action == "steer_goal"
    assert outcome.status == "ok"
    assert outcome.detail["goal_id"] == "g1"
    assert outcome.detail["message"] == "re-check direction"
    assert outcome.detail["result"] == result
    assert outcome.error_reason is None
    assert outcome.error_message is None
    assert mcp.calls == [("g1", "re-check direction")]


# ---- failure paths --------------------------------------------------------


@pytest.mark.asyncio
async def test_perform_steer_goal_transport_failure_returns_failed_outcome() -> None:
    mcp = _StubMCP(error=MCPClientError(reason="transport", message="conn refused"))

    outcome = await perform_steer_goal("g1", "msg", mcp)

    assert outcome.status == "failed"
    assert outcome.error_reason == "transport"
    assert "conn refused" in outcome.error_message
    assert outcome.detail["goal_id"] == "g1"
    assert outcome.detail["message"] == "msg"


@pytest.mark.asyncio
async def test_perform_steer_goal_tool_error_returns_failed_outcome() -> None:
    mcp = _StubMCP(error=MCPClientError(reason="tool_error", message="unknown goal_id"))

    outcome = await perform_steer_goal("missing", "msg", mcp)

    assert outcome.status == "failed"
    assert outcome.error_reason == "tool_error"


@pytest.mark.asyncio
async def test_perform_steer_goal_protocol_error_returns_failed_outcome() -> None:
    mcp = _StubMCP(error=MCPClientError(reason="protocol", message="malformed"))

    outcome = await perform_steer_goal("g", "msg", mcp)

    assert outcome.status == "failed"
    assert outcome.error_reason == "protocol"


@pytest.mark.asyncio
async def test_perform_steer_goal_unexpected_exception_is_caught() -> None:
    mcp = _StubMCP(raise_other=RuntimeError("boom"))

    outcome = await perform_steer_goal("g", "msg", mcp)

    assert outcome.status == "failed"
    assert outcome.error_reason == "unexpected"
    assert "RuntimeError" in outcome.error_message


# ---- outcome serialization -----------------------------------------------


def test_outcome_to_dict_round_trip() -> None:
    outcome = ActionOutcome(
        action="steer_goal",
        status="ok",
        detail={"goal_id": "g", "message": "do X", "result": {"ok": True}},
    )
    d = outcome_to_dict(outcome)
    assert d["action"] == "steer_goal"
    assert d["status"] == "ok"
    assert d["detail"]["message"] == "do X"

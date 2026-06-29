"""Unit tests for the evaluate_goal L1 action.

Hermetic: we pass a stub MCP client (typed as :class:`DevclawMCPClient` only
nominally — duck-typing is fine; the action calls one method).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ops_agent.actions import ActionOutcome, outcome_to_dict, perform_evaluate_goal
from ops_agent.mcp_client import MCPClientError


@dataclass
class _StubMCP:
    """Stand-in for ``DevclawMCPClient`` — drives the action's two paths."""

    verdict: dict | None = None
    error: MCPClientError | None = None
    raise_other: Exception | None = None
    calls: list = None  # populated by __post_init__

    def __post_init__(self) -> None:
        self.calls = []

    async def evaluate_goal(self, goal_id: str) -> dict:
        self.calls.append(goal_id)
        if self.raise_other is not None:
            raise self.raise_other
        if self.error is not None:
            raise self.error
        return self.verdict or {}


# ---- happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_perform_evaluate_goal_ok_returns_verdict() -> None:
    verdict = {
        "goal_id": "g1",
        "verdict": "off_track",
        "rationale": "needs steering",
        "corrections": "switch to X",
        "question": "",
    }
    mcp = _StubMCP(verdict=verdict)

    outcome = await perform_evaluate_goal("g1", mcp)

    assert outcome.action == "evaluate_goal"
    assert outcome.status == "ok"
    assert outcome.detail["goal_id"] == "g1"
    assert outcome.detail["verdict"] == verdict
    assert outcome.error_reason is None
    assert outcome.error_message is None
    assert mcp.calls == ["g1"]


# ---- failure paths --------------------------------------------------------


@pytest.mark.asyncio
async def test_perform_evaluate_goal_transport_failure_returns_failed_outcome() -> None:
    mcp = _StubMCP(error=MCPClientError(reason="transport", message="conn refused"))

    outcome = await perform_evaluate_goal("g1", mcp)

    assert outcome.status == "failed"
    assert outcome.error_reason == "transport"
    assert "conn refused" in outcome.error_message
    assert outcome.detail["goal_id"] == "g1"


@pytest.mark.asyncio
async def test_perform_evaluate_goal_tool_error_returns_failed_outcome() -> None:
    mcp = _StubMCP(error=MCPClientError(reason="tool_error", message="unknown goal_id"))

    outcome = await perform_evaluate_goal("missing", mcp)

    assert outcome.status == "failed"
    assert outcome.error_reason == "tool_error"


@pytest.mark.asyncio
async def test_perform_evaluate_goal_protocol_error_returns_failed_outcome() -> None:
    mcp = _StubMCP(error=MCPClientError(reason="protocol", message="malformed"))

    outcome = await perform_evaluate_goal("g", mcp)

    assert outcome.status == "failed"
    assert outcome.error_reason == "protocol"


@pytest.mark.asyncio
async def test_perform_evaluate_goal_unexpected_exception_is_caught() -> None:
    # Defensive: any non-MCPClientError exception must also be caught.
    mcp = _StubMCP(raise_other=RuntimeError("boom"))

    outcome = await perform_evaluate_goal("g", mcp)

    assert outcome.status == "failed"
    assert outcome.error_reason == "unexpected"
    assert "RuntimeError" in outcome.error_message


# ---- outcome serialization -----------------------------------------------


def test_outcome_to_dict_round_trip() -> None:
    outcome = ActionOutcome(
        action="evaluate_goal",
        status="ok",
        detail={"goal_id": "g", "verdict": {"verdict": "on_track"}},
    )
    d = outcome_to_dict(outcome)
    assert d["action"] == "evaluate_goal"
    assert d["status"] == "ok"
    assert d["detail"]["goal_id"] == "g"


def test_outcome_failed_serialization_includes_error_fields() -> None:
    outcome = ActionOutcome(
        action="evaluate_goal",
        status="failed",
        detail={"goal_id": "g"},
        error_reason="transport",
        error_message="conn",
    )
    d = outcome_to_dict(outcome)
    assert d["error_reason"] == "transport"
    assert d["error_message"] == "conn"

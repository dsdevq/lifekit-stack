"""Unit tests for the L3 fix_bug action (ops-PR4).

The action wraps a single MCP call. Two guards live at this layer:

  1. Empty ``devclaw_repo_path`` → typed-failed. Prevents firing the tool
     at nothing when the config is misconfigured but L3 is on.
  2. Empty ``description`` → typed-failed. Defence in depth against the
     playbook parser being bypassed.

Both branches must NEVER raise — the daemon's polling loop must survive
misconfiguration and outages.
"""

from __future__ import annotations

from typing import Any

import pytest

from ops_agent.actions import ActionOutcome, perform_fix_bug
from ops_agent.mcp_client import MCPClientError


class _FakeMCP:
    """Minimal fake — records the fix_bug call args + returns a canned response."""

    def __init__(self, response: dict[str, Any] | None = None, raise_err: MCPClientError | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or {"created_goal_id": "devclaw-fix-abc123"}
        self._raise_err = raise_err

    async def fix_bug(self, *, workspace_dir: str, description: str, title: str | None = None) -> dict[str, Any]:
        self.calls.append({"workspace_dir": workspace_dir, "description": description, "title": title})
        if self._raise_err is not None:
            raise self._raise_err
        return self._response


@pytest.mark.asyncio
async def test_fires_and_records_response_on_happy_path():
    mcp = _FakeMCP({"created_goal_id": "devclaw-fix-x"})
    out = await perform_fix_bug(
        devclaw_repo_path="/srv/devclaw",
        description="Symptom: eval truncation on stalled goals.",
        title="fix(evaluator): retry on truncated output",
        triggering_goal_id="closeloop-mission-v2",
        mcp=mcp,
    )
    assert out.status == "ok"
    assert out.detail["response"] == {"created_goal_id": "devclaw-fix-x"}
    assert out.detail["triggering_goal_id"] == "closeloop-mission-v2"
    assert mcp.calls[0]["workspace_dir"] == "/srv/devclaw"
    assert mcp.calls[0]["title"] == "fix(evaluator): retry on truncated output"


@pytest.mark.asyncio
async def test_empty_devclaw_repo_path_short_circuits_before_mcp_call():
    mcp = _FakeMCP()
    out = await perform_fix_bug(
        devclaw_repo_path="",
        description="anything",
        title="",
        triggering_goal_id="g",
        mcp=mcp,
    )
    assert out.status == "failed"
    assert out.error_reason == "no_devclaw_repo_path"
    assert mcp.calls == []  # never called


@pytest.mark.asyncio
async def test_whitespace_devclaw_repo_path_short_circuits():
    mcp = _FakeMCP()
    out = await perform_fix_bug(
        devclaw_repo_path="   ",
        description="anything",
        title="",
        triggering_goal_id="g",
        mcp=mcp,
    )
    assert out.status == "failed"
    assert out.error_reason == "no_devclaw_repo_path"
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_empty_description_short_circuits_before_mcp_call():
    mcp = _FakeMCP()
    out = await perform_fix_bug(
        devclaw_repo_path="/srv/devclaw",
        description="",
        title="fix: x",
        triggering_goal_id="g",
        mcp=mcp,
    )
    assert out.status == "failed"
    assert out.error_reason == "empty_description"
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_mcp_error_becomes_typed_failure_not_raise():
    err = MCPClientError(reason="transport", message="devclaw-mcp unreachable")
    mcp = _FakeMCP(raise_err=err)
    out = await perform_fix_bug(
        devclaw_repo_path="/srv/devclaw",
        description="Symptom: eval truncation.",
        title="",
        triggering_goal_id="g",
        mcp=mcp,
    )
    assert out.status == "failed"
    assert out.error_reason == "transport"
    assert "unreachable" in (out.error_message or "")


@pytest.mark.asyncio
async def test_action_never_passes_empty_title_to_mcp():
    mcp = _FakeMCP()
    await perform_fix_bug(
        devclaw_repo_path="/srv/devclaw",
        description="a description",
        title="",
        triggering_goal_id="g",
        mcp=mcp,
    )
    # Empty title is passed through as None so devclaw's tool can derive one.
    assert mcp.calls[0]["title"] is None

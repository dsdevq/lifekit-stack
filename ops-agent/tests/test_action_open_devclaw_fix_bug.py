"""Unit tests for the L3 open_devclaw_fix_bug action.

Two safety gates ride on top of the MCP call: (1) classification confidence
and (2) OPS_AGENT_L3_ENABLED.  These tests cover each gate + the happy path
+ the failure path.  Hermetic — stub MCP client, no real network / env
outside the ones we monkeypatch."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from ops_agent.actions import (
    ActionOutcome,
    outcome_to_dict,
    perform_open_devclaw_fix_bug,
)
from ops_agent.classifiers import DefectClassification
from ops_agent.classifiers.devclaw_defect import L3_CONFIDENCE_THRESHOLD
from ops_agent.mcp_client import MCPClientError


@dataclass
class _StubMCP:
    """Stand-in for DevclawMCPClient — duck-typing is fine, the action
    calls exactly one method."""

    response: dict | None = None
    error: MCPClientError | None = None
    calls: list = None

    def __post_init__(self) -> None:
        self.calls = []

    async def fix_bug(
        self, workspace_dir: str, description: str, *,
        open_pr: bool = True, verify_cmd=None, notify_url=None,
    ) -> dict:
        self.calls.append({
            "workspace_dir": workspace_dir,
            "description": description,
            "open_pr": open_pr,
            "verify_cmd": verify_cmd,
            "notify_url": notify_url,
        })
        if self.error is not None:
            raise self.error
        return self.response or {"task_id": "tid-stub", "status": "pending"}


def _classification(*, is_defect: bool = True, confidence: float = 0.9,
                    signature: str = "eval_truncation",
                    description_draft: str = "fix the evaluator truncation") -> DefectClassification:
    return DefectClassification(
        is_defect=is_defect,
        signature=signature,
        confidence=confidence,
        description_draft=description_draft,
        rationale="test",
        evidence=["review was cut off"],
    )


# ---- gate 1: classification confidence ---------------------------------


async def test_not_a_defect_short_circuits_before_mcp():
    mcp = _StubMCP()
    outcome = await perform_open_devclaw_fix_bug(
        _classification(is_defect=False, confidence=0.0, signature=""),
        mcp,
    )
    assert outcome.status == "skipped"
    assert outcome.detail["reason"] == "not_a_defect"
    assert mcp.calls == []


async def test_low_confidence_short_circuits_before_mcp():
    mcp = _StubMCP()
    outcome = await perform_open_devclaw_fix_bug(
        _classification(confidence=0.30), mcp,
    )
    assert outcome.status == "skipped"
    assert outcome.detail["reason"] == "below_confidence_floor"
    assert outcome.detail["confidence_floor"] == L3_CONFIDENCE_THRESHOLD
    assert mcp.calls == []


async def test_confidence_at_the_floor_is_allowed_through():
    """Classifier confidence exactly at the floor should not short-circuit
    — the floor is a lower-bound, inclusive."""
    mcp = _StubMCP()
    outcome = await perform_open_devclaw_fix_bug(
        _classification(confidence=L3_CONFIDENCE_THRESHOLD),
        mcp,
        confidence_floor=L3_CONFIDENCE_THRESHOLD,
    )
    # L3 flag is default OFF → dry_run, not skipped
    assert outcome.status == "dry_run"


# ---- gate 2: OPS_AGENT_L3_ENABLED --------------------------------------


async def test_l3_flag_off_records_dry_run_without_calling_mcp(monkeypatch):
    monkeypatch.delenv("OPS_AGENT_L3_ENABLED", raising=False)
    mcp = _StubMCP()
    outcome = await perform_open_devclaw_fix_bug(_classification(), mcp)
    assert outcome.status == "dry_run"
    assert outcome.detail["reason"] == "l3_disabled_would_have_filed"
    assert "description_draft" in outcome.detail
    assert "OPS_AGENT_L3_ENABLED" in outcome.detail["hint"]
    assert mcp.calls == []


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "Yes"])
async def test_l3_flag_enabling_values_reach_mcp(monkeypatch, truthy: str):
    monkeypatch.setenv("OPS_AGENT_L3_ENABLED", truthy)
    mcp = _StubMCP()
    outcome = await perform_open_devclaw_fix_bug(_classification(), mcp)
    assert outcome.status == "ok"
    assert len(mcp.calls) == 1
    assert mcp.calls[0]["open_pr"] is True


@pytest.mark.parametrize("falsy", ["0", "false", "no", "", "gibberish"])
async def test_l3_flag_non_enabling_values_stay_in_dry_run(monkeypatch, falsy: str):
    monkeypatch.setenv("OPS_AGENT_L3_ENABLED", falsy)
    mcp = _StubMCP()
    outcome = await perform_open_devclaw_fix_bug(_classification(), mcp)
    assert outcome.status == "dry_run"
    assert mcp.calls == []


# ---- happy path + failure --------------------------------------------


async def test_enabled_high_confidence_calls_mcp_with_description(monkeypatch):
    monkeypatch.setenv("OPS_AGENT_L3_ENABLED", "1")
    mcp = _StubMCP(response={"task_id": "tid-42", "status": "pending"})
    outcome = await perform_open_devclaw_fix_bug(
        _classification(description_draft="fix the evaluator truncation bug"),
        mcp,
    )
    assert outcome.status == "ok"
    assert outcome.detail["mcp_response"] == {"task_id": "tid-42", "status": "pending"}
    assert mcp.calls[0]["description"] == "fix the evaluator truncation bug"


async def test_workspace_override_via_env(monkeypatch):
    monkeypatch.setenv("OPS_AGENT_L3_ENABLED", "1")
    monkeypatch.setenv("OPS_AGENT_DEVCLAW_WORKSPACE_DIR", "/repos/dc-alt")
    mcp = _StubMCP()
    outcome = await perform_open_devclaw_fix_bug(_classification(), mcp)
    assert outcome.status == "ok"
    assert mcp.calls[0]["workspace_dir"] == "/repos/dc-alt"


async def test_workspace_override_via_kwarg(monkeypatch):
    monkeypatch.setenv("OPS_AGENT_L3_ENABLED", "1")
    mcp = _StubMCP()
    outcome = await perform_open_devclaw_fix_bug(
        _classification(), mcp, workspace_dir="/repos/one-off",
    )
    assert mcp.calls[0]["workspace_dir"] == "/repos/one-off"


async def test_mcp_error_becomes_failed_outcome(monkeypatch):
    monkeypatch.setenv("OPS_AGENT_L3_ENABLED", "1")
    mcp = _StubMCP(error=MCPClientError(reason="transport", message="ECONNREFUSED"))
    outcome = await perform_open_devclaw_fix_bug(_classification(), mcp)
    assert outcome.status == "failed"
    assert outcome.error_reason == "transport"
    assert outcome.error_message == "ECONNREFUSED"


async def test_outcome_serializes_via_helper(monkeypatch):
    """The outcome must round-trip through the incident-log serializer used
    by every other action — sanity check that no dataclass surprises hide
    inside the detail dict."""
    monkeypatch.delenv("OPS_AGENT_L3_ENABLED", raising=False)
    mcp = _StubMCP()
    outcome = await perform_open_devclaw_fix_bug(_classification(), mcp)
    serialized = outcome_to_dict(outcome)
    assert serialized["action"] == "open_devclaw_fix_bug"
    assert serialized["status"] == "dry_run"
    assert "classification" in serialized["detail"]

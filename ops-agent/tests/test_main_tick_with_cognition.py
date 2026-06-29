"""Integration tests for ``tick()`` once the cognition + action layer is wired.

Stubs ``call_claude`` and the MCP client end-to-end so an O1 detection runs
through the playbook, the action, and the on-disk persistence — all hermetic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from ops_agent.cognition import CognitionCall, CognitionError
from ops_agent.config import OpsConfig
from ops_agent.detectors import NoProgressDetector
from ops_agent.incident import IncidentStore
from ops_agent.main import tick
from ops_agent.mcp_client import MCPClientError

from .conftest import write_goal


def _cfg(goals_dir: Path, incidents_dir: Path) -> OpsConfig:
    return OpsConfig(
        goals_dir=goals_dir,
        incidents_dir=incidents_dir,
        poll_interval_s=1.0,
        dedup_window_s=86400.0,
    )


@dataclass
class _StubMCP:
    """Stand-in for ``DevclawMCPClient`` used by the action layer."""

    verdict: dict | None = None
    error: MCPClientError | None = None
    calls: list = None

    def __post_init__(self) -> None:
        self.calls = []

    async def evaluate_goal(self, goal_id: str) -> dict:
        self.calls.append(goal_id)
        if self.error is not None:
            raise self.error
        return self.verdict or {"goal_id": goal_id, "verdict": "on_track"}


def _stub_call_claude_returning(stdout: str):
    async def _fake(prompt, *, role="ops-agent", model=None, timeout_s=None):
        return CognitionCall(stdout=stdout, model="stub", latency_ms=1, argv_head="stub")

    return _fake


def _stub_call_claude_raising(err: CognitionError):
    async def _fake(prompt, *, role="ops-agent", model=None, timeout_s=None):
        raise err

    return _fake


def _incident_folder(incidents_dir: Path) -> Path:
    folders = [p for p in incidents_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert len(folders) == 1, f"expected 1 incident folder, got {folders}"
    return folders[0]


# ---- evaluate_goal action wired through ---------------------------------


@pytest.mark.asyncio
async def test_tick_evaluate_action_writes_decision_and_outcome(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning(
            '{"action": "evaluate_goal", "reasoning": "fresh eval may unblock"}'
        ),
    )
    write_goal(goals_dir, "stuck", no_progress_notified=True, objective="ship X")

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP(verdict={"goal_id": "stuck", "verdict": "off_track", "rationale": "needs steer"})

    written = await tick(_cfg(goals_dir, incidents_dir), store, NoProgressDetector(), mcp)

    assert written == 1
    folder = _incident_folder(incidents_dir)

    # decision.json captures Claude's choice + the raw response.
    decision = json.loads((folder / "decision.json").read_text())
    assert decision["action"] == "evaluate_goal"
    assert "fresh eval" in decision["reasoning"]

    # action.json captures the MCP outcome.
    action = json.loads((folder / "action.json").read_text())
    assert action["action"] == "evaluate_goal"
    assert action["status"] == "ok"
    assert action["detail"]["verdict"]["verdict"] == "off_track"

    # outcome.md is the human-readable summary.
    outcome_md = (folder / "outcome.md").read_text()
    assert "Decision" in outcome_md
    assert "evaluate_goal" in outcome_md
    assert "off_track" in outcome_md

    # The MCP client was invoked exactly once.
    assert mcp.calls == ["stuck"]


@pytest.mark.asyncio
async def test_tick_noop_decision_skips_mcp_call(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning('{"action": "noop", "reasoning": "recently evaluated"}'),
    )
    write_goal(goals_dir, "g", no_progress_notified=True)

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()
    written = await tick(_cfg(goals_dir, incidents_dir), store, NoProgressDetector(), mcp)

    assert written == 1
    folder = _incident_folder(incidents_dir)
    decision = json.loads((folder / "decision.json").read_text())
    assert decision["action"] == "noop"
    # No action.json written when decision is noop.
    assert not (folder / "action.json").exists()
    # MCP was NOT called.
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_tick_handles_mcp_failure_gracefully(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning('{"action": "evaluate_goal", "reasoning": "try it"}'),
    )
    write_goal(goals_dir, "g", no_progress_notified=True)

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP(error=MCPClientError(reason="transport", message="conn refused"))

    written = await tick(_cfg(goals_dir, incidents_dir), store, NoProgressDetector(), mcp)

    assert written == 1
    folder = _incident_folder(incidents_dir)
    action = json.loads((folder / "action.json").read_text())
    assert action["status"] == "failed"
    assert action["error_reason"] == "transport"
    outcome_md = (folder / "outcome.md").read_text()
    assert "failed" in outcome_md.lower()


@pytest.mark.asyncio
async def test_tick_handles_cognition_failure_gracefully(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_raising(CognitionError(reason="timeout", message="60s")),
    )
    write_goal(goals_dir, "g", no_progress_notified=True)

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()

    written = await tick(_cfg(goals_dir, incidents_dir), store, NoProgressDetector(), mcp)

    assert written == 1  # incident WAS still detected + recorded
    folder = _incident_folder(incidents_dir)
    decision = json.loads((folder / "decision.json").read_text())
    # Cognition failure → noop fallback. MCP NEVER invoked.
    assert decision["action"] == "noop"
    assert "cognition_failed" in decision["reasoning"]
    assert "timeout" in decision["reasoning"]
    assert mcp.calls == []
    outcome_md = (folder / "outcome.md").read_text()
    assert "Cognition failed" in outcome_md


# ---- defensive: malformed cognition response ----------------------------


@pytest.mark.asyncio
async def test_tick_falls_back_to_noop_on_malformed_response(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning("definitely not json"),
    )
    write_goal(goals_dir, "g", no_progress_notified=True)
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()

    await tick(_cfg(goals_dir, incidents_dir), store, NoProgressDetector(), mcp)

    folder = _incident_folder(incidents_dir)
    decision = json.loads((folder / "decision.json").read_text())
    assert decision["action"] == "noop"
    assert "parse_failed" in decision["reasoning"]
    assert mcp.calls == []


# ---- idempotency -------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_does_not_redecide_existing_decision(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)

    # Pre-stage an incident folder with a decision.json — simulates a daemon
    # restart mid-incident (or a manual edit). The next tick must NOT
    # re-call cognition or the MCP for this incident.
    write_goal(goals_dir, "g", no_progress_notified=True)
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()

    call_count = {"n": 0}

    async def _counting_call(prompt, *, role="ops-agent", model=None, timeout_s=None):
        call_count["n"] += 1
        return CognitionCall(
            stdout='{"action": "noop", "reasoning": "test"}',
            model="stub",
            latency_ms=1,
            argv_head="stub",
        )

    monkeypatch.setattr("ops_agent.main.call_claude", _counting_call)

    # First tick — incident appears + decision made.
    await tick(_cfg(goals_dir, incidents_dir), store, NoProgressDetector(), mcp)
    assert call_count["n"] == 1
    folder = _incident_folder(incidents_dir)
    assert (folder / "decision.json").exists()

    # Pretend dedup window expired (rewrite the marker mtime) and tick again.
    # The new detection should NOT re-trigger cognition because decision.json
    # already exists for that incident folder.
    # NOTE: in practice the dedup window prevents re-firing, but we also want
    # belt+suspenders idempotency at the cognition level. Easier to assert by
    # invoking the lower-level helper directly:
    from ops_agent.incident import Incident
    from ops_agent.main import _process_incident

    incident = Incident(
        trigger="O1",
        goal_id="g",
        detected_at=fixed_now,
        payload={"objective": "x", "dedup_key": "k"},
    )
    await _process_incident(incident, folder, incidents_dir / "log.md", mcp)
    # Still 1 — the second pass saw decision.json and skipped cognition.
    assert call_count["n"] == 1


# ---- no MCP client (degraded run) ----------------------------------------


@pytest.mark.asyncio
async def test_tick_with_no_mcp_records_failed_action(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning('{"action": "evaluate_goal", "reasoning": "kick it"}'),
    )
    write_goal(goals_dir, "g", no_progress_notified=True)
    store = IncidentStore(incidents_dir, dedup_window_s=86400)

    await tick(_cfg(goals_dir, incidents_dir), store, NoProgressDetector(), None)

    folder = _incident_folder(incidents_dir)
    action = json.loads((folder / "action.json").read_text())
    assert action["status"] == "failed"
    assert action["error_reason"] == "no_mcp_client"


# ---- prompt persistence ------------------------------------------------


@pytest.mark.asyncio
async def test_tick_persists_prompt_to_incident_folder(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning('{"action": "noop", "reasoning": "x"}'),
    )
    write_goal(goals_dir, "g", no_progress_notified=True, objective="shipit")
    store = IncidentStore(incidents_dir, dedup_window_s=86400)

    await tick(_cfg(goals_dir, incidents_dir), store, NoProgressDetector(), _StubMCP())

    folder = _incident_folder(incidents_dir)
    prompt = (folder / "prompt.md").read_text()
    assert "shipit" in prompt
    assert "evaluate_goal" in prompt  # menu present


# ---- log line ---------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_appends_post_decision_log_line(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning('{"action": "evaluate_goal", "reasoning": "k"}'),
    )
    write_goal(goals_dir, "g", no_progress_notified=True)
    store = IncidentStore(incidents_dir, dedup_window_s=86400)

    await tick(_cfg(goals_dir, incidents_dir), store, NoProgressDetector(), _StubMCP())

    log = (incidents_dir / "log.md").read_text()
    # The IncidentStore writes the detection line; our wiring appends an
    # action line. Both should be in there.
    assert log.count("O1") >= 2 or "action" in log
    assert "evaluate_goal" in log

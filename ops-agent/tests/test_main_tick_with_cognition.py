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
from ops_agent.detectors import (
    NoProgressDetector,
    NoSteeringDetector,
    VerifyingStallDetector,
)
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
    """Stand-in for ``DevclawMCPClient`` used by the action layer.

    Records calls to both L1 (``evaluate_goal``) and L2 (``steer_goal``)
    surfaces so a single stub can serve both kinds of integration test.
    """

    verdict: dict | None = None
    steer_result: dict | None = None
    error: MCPClientError | None = None
    steer_error: MCPClientError | None = None
    calls: list = None
    steer_calls: list = None

    def __post_init__(self) -> None:
        self.calls = []
        self.steer_calls = []

    async def evaluate_goal(self, goal_id: str) -> dict:
        self.calls.append(goal_id)
        if self.error is not None:
            raise self.error
        return self.verdict or {"goal_id": goal_id, "verdict": "on_track"}

    async def steer_goal(self, goal_id: str, message: str) -> dict:
        self.steer_calls.append((goal_id, message))
        if self.steer_error is not None:
            raise self.steer_error
        return self.steer_result or {"goal_id": goal_id, "steering_recorded": True}


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

    written = await tick(_cfg(goals_dir, incidents_dir), store, [NoProgressDetector()], mcp)

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
    written = await tick(_cfg(goals_dir, incidents_dir), store, [NoProgressDetector()], mcp)

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

    written = await tick(_cfg(goals_dir, incidents_dir), store, [NoProgressDetector()], mcp)

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

    written = await tick(_cfg(goals_dir, incidents_dir), store, [NoProgressDetector()], mcp)

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

    await tick(_cfg(goals_dir, incidents_dir), store, [NoProgressDetector()], mcp)

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
    await tick(_cfg(goals_dir, incidents_dir), store, [NoProgressDetector()], mcp)
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
    await _process_incident(
        incident, folder, incidents_dir / "log.md", mcp, _cfg(goals_dir, incidents_dir)
    )
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

    await tick(_cfg(goals_dir, incidents_dir), store, [NoProgressDetector()], None)

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

    await tick(_cfg(goals_dir, incidents_dir), store, [NoProgressDetector()], _StubMCP())

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

    await tick(_cfg(goals_dir, incidents_dir), store, [NoProgressDetector()], _StubMCP())

    log = (incidents_dir / "log.md").read_text()
    # The IncidentStore writes the detection line; our wiring appends an
    # action line. Both should be in there.
    assert log.count("O1") >= 2 or "action" in log
    assert "evaluate_goal" in log


# ---- O2 + steer_goal action wired through -------------------------------


def _write_drifting_goal(goals_dir: Path, goal_id: str, *, hours_silent: int = 30) -> None:
    """Create an executing goal whose inbox.md was last touched ``hours_silent`` ago.

    Kept inline in this test file (not in conftest) because the base
    write_goal helper there doesn't carry an inbox.md — adding that
    capability for one integration test would muddy the O1 fixture surface.
    """
    import os

    goal_dir = goals_dir / goal_id
    goal_dir.mkdir(parents=True, exist_ok=True)
    (goal_dir / "goal.yaml").write_text(f"objective: drifting goal {goal_id}\ncadence: 1d\n")
    (goal_dir / "STATUS.md").write_text("---\nphase: executing\n---\n\n# status\n")
    inbox = goal_dir / "inbox.md"
    inbox.write_text("# inbox\n")
    # We can't reach fixed_now from here; the caller patches it. Use a real
    # past timestamp instead — the threshold is hours-based so any deeply-
    # historical mtime trips it.
    past = datetime.now().timestamp() - hours_silent * 3600
    os.utime(inbox, (past, past))


@pytest.mark.asyncio
async def test_tick_o2_steer_action_writes_decision_and_outcome(
    goals_dir: Path, incidents_dir: Path, monkeypatch
) -> None:
    """End-to-end O2 → drifting-goal-steer playbook → L2 MCP call."""
    from datetime import UTC

    # Pin _utcnow well after the inbox mtime so the threshold trips.
    now = datetime.now(UTC)
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning(
            '{"action": "steer_goal", "message": "summarize progress and blockers", '
            '"reasoning": "silent too long"}'
        ),
    )
    _write_drifting_goal(goals_dir, "drifty", hours_silent=30)

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()

    written = await tick(_cfg(goals_dir, incidents_dir), store, [NoSteeringDetector()], mcp)

    assert written == 1
    folder = _incident_folder(incidents_dir)

    decision = json.loads((folder / "decision.json").read_text())
    assert decision["action"] == "steer_goal"
    assert decision["message"] == "summarize progress and blockers"
    assert "silent too long" in decision["reasoning"]

    action = json.loads((folder / "action.json").read_text())
    assert action["action"] == "steer_goal"
    assert action["status"] == "ok"
    assert action["detail"]["message"] == "summarize progress and blockers"

    outcome_md = (folder / "outcome.md").read_text()
    assert "steer_goal" in outcome_md
    assert "summarize progress" in outcome_md

    # The L1 surface was NOT touched.
    assert mcp.calls == []
    # The L2 surface was hit exactly once with the right payload.
    assert mcp.steer_calls == [("drifty", "summarize progress and blockers")]


@pytest.mark.asyncio
async def test_tick_o2_noop_decision_skips_mcp_call(
    goals_dir: Path, incidents_dir: Path, monkeypatch
) -> None:
    from datetime import UTC

    now = datetime.now(UTC)
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning('{"action": "noop", "reasoning": "context too thin"}'),
    )
    _write_drifting_goal(goals_dir, "g", hours_silent=30)

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()
    written = await tick(_cfg(goals_dir, incidents_dir), store, [NoSteeringDetector()], mcp)

    assert written == 1
    folder = _incident_folder(incidents_dir)
    decision = json.loads((folder / "decision.json").read_text())
    assert decision["action"] == "noop"
    assert not (folder / "action.json").exists()
    assert mcp.calls == [] and mcp.steer_calls == []


@pytest.mark.asyncio
async def test_tick_o2_handles_mcp_failure_gracefully(
    goals_dir: Path, incidents_dir: Path, monkeypatch
) -> None:
    from datetime import UTC

    now = datetime.now(UTC)
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning(
            '{"action": "steer_goal", "message": "do X", "reasoning": "r"}'
        ),
    )
    _write_drifting_goal(goals_dir, "g", hours_silent=30)

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP(steer_error=MCPClientError(reason="transport", message="conn refused"))

    written = await tick(_cfg(goals_dir, incidents_dir), store, [NoSteeringDetector()], mcp)

    assert written == 1
    folder = _incident_folder(incidents_dir)
    action = json.loads((folder / "action.json").read_text())
    assert action["status"] == "failed"
    assert action["error_reason"] == "transport"


@pytest.mark.asyncio
async def test_tick_o2_with_no_mcp_records_failed_steer(
    goals_dir: Path, incidents_dir: Path, monkeypatch
) -> None:
    from datetime import UTC

    now = datetime.now(UTC)
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning(
            '{"action": "steer_goal", "message": "kick it", "reasoning": "r"}'
        ),
    )
    _write_drifting_goal(goals_dir, "g", hours_silent=30)
    store = IncidentStore(incidents_dir, dedup_window_s=86400)

    await tick(_cfg(goals_dir, incidents_dir), store, [NoSteeringDetector()], None)

    folder = _incident_folder(incidents_dir)
    action = json.loads((folder / "action.json").read_text())
    assert action["action"] == "steer_goal"
    assert action["status"] == "failed"
    assert action["error_reason"] == "no_mcp_client"


@pytest.mark.asyncio
async def test_tick_runs_both_detectors_in_one_pass(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    """The detectors list arg drives a multi-trigger tick.

    Stages an O1-firing goal AND an O2-firing goal, asserts both incidents
    land in the same scan with their respective folders / decisions /
    actions.
    """

    # Cognition stub returns an action-matched response per trigger by
    # peeking at which playbook prompt it received.
    async def _routing_call(prompt, *, role="ops-agent", model=None, timeout_s=None):
        if "no-progress watchdog" in prompt:
            return CognitionCall(
                stdout='{"action": "evaluate_goal", "reasoning": "kick it"}',
                model="stub",
                latency_ms=1,
                argv_head="stub",
            )
        if "no-steering watchdog" in prompt:
            return CognitionCall(
                stdout='{"action": "steer_goal", "message": "re-check", "reasoning": "drifted"}',
                model="stub",
                latency_ms=1,
                argv_head="stub",
            )
        return CognitionCall(
            stdout='{"action": "noop", "reasoning": "x"}',
            model="stub",
            latency_ms=1,
            argv_head="stub",
        )

    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr("ops_agent.main.call_claude", _routing_call)

    # O1 fixture (uses existing write_goal helper).
    write_goal(goals_dir, "stuck", no_progress_notified=True, phase="in_flight")
    # O2 fixture (drifting, executing, silent inbox).
    import os
    from datetime import timedelta

    o2 = goals_dir / "drifty"
    o2.mkdir()
    (o2 / "goal.yaml").write_text("objective: drift\ncadence: 1d\n")
    (o2 / "STATUS.md").write_text("---\nphase: executing\n---\n\n# s\n")
    inbox = o2 / "inbox.md"
    inbox.write_text("# inbox\n")
    past = (fixed_now - timedelta(hours=30)).timestamp()
    os.utime(inbox, (past, past))

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()

    written = await tick(
        _cfg(goals_dir, incidents_dir),
        store,
        [NoProgressDetector(), NoSteeringDetector()],
        mcp,
    )

    assert written == 2
    # Each MCP surface saw exactly the expected single call.
    assert mcp.calls == ["stuck"]
    assert mcp.steer_calls == [("drifty", "re-check")]


# ---- O3 + docker_restart action wired through ----------------------------


def _write_verifying_stall_goal(
    goals_dir: Path,
    goal_id: str,
    *,
    phase: str,
    last_progress_at: datetime,
) -> None:
    """Create a goal in a stall phase with a stale last_progress_at.

    Mirrors the writer in test_verifying_stall_detector.py; kept inline so
    the base conftest helper stays focused on O1's fixture shape.
    """
    goal_dir = goals_dir / goal_id
    goal_dir.mkdir(parents=True, exist_ok=True)
    (goal_dir / "goal.yaml").write_text(f"objective: stalled goal {goal_id}\ncadence: 1d\n")
    (goal_dir / "STATUS.md").write_text(
        f"---\nphase: {phase}\nlast_progress_at: '{last_progress_at.isoformat()}'\n---\n\n"
        "# status\n"
    )


@pytest.mark.asyncio
async def test_tick_o3_docker_restart_action_writes_decision_and_outcome(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    """End-to-end O3 → verifying-stall-unblock playbook → docker_restart action.

    Stubs ``asyncio.create_subprocess_exec`` with a fake that returns a
    zero exit — same pattern as the action's own unit tests.
    """
    from datetime import timedelta

    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning(
            '{"action": "docker_restart", "service_name": "compose-devclaw-mcp-1", '
            '"reasoning": "devclaw-mcp appears frozen"}'
        ),
    )

    # Stub the subprocess layer so no real docker call is made.
    import asyncio as _asyncio

    spawn_calls: list[tuple] = []

    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"compose-devclaw-mcp-1\n", b"")

    async def _fake_create(*args, **kwargs):
        spawn_calls.append(args)
        return _FakeProc()

    monkeypatch.setattr(_asyncio, "create_subprocess_exec", _fake_create)

    _write_verifying_stall_goal(
        goals_dir,
        "stalled",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=6),
    )

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()

    written = await tick(_cfg(goals_dir, incidents_dir), store, [VerifyingStallDetector()], mcp)

    assert written == 1
    folder = _incident_folder(incidents_dir)

    decision = json.loads((folder / "decision.json").read_text())
    assert decision["action"] == "docker_restart"
    assert decision["service_name"] == "compose-devclaw-mcp-1"
    assert "frozen" in decision["reasoning"]

    action = json.loads((folder / "action.json").read_text())
    assert action["action"] == "docker_restart"
    assert action["status"] == "ok"
    assert action["detail"]["service_name"] == "compose-devclaw-mcp-1"

    outcome_md = (folder / "outcome.md").read_text()
    assert "docker_restart" in outcome_md
    assert "compose-devclaw-mcp-1" in outcome_md

    # MCP surfaces were NOT touched — docker_restart bypasses MCP entirely.
    assert mcp.calls == []
    assert mcp.steer_calls == []
    # subprocess was invoked with exactly the safe argv shape.
    assert spawn_calls == [("docker", "restart", "compose-devclaw-mcp-1")]


@pytest.mark.asyncio
async def test_tick_o3_evaluate_goal_action_routes_through_mcp(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    """O3 incident + playbook choosing evaluate_goal → L1 MCP call (no docker)."""
    from datetime import timedelta

    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning(
            '{"action": "evaluate_goal", "reasoning": "direction probably wrong"}'
        ),
    )
    _write_verifying_stall_goal(
        goals_dir,
        "stalled",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=6),
    )

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()

    written = await tick(_cfg(goals_dir, incidents_dir), store, [VerifyingStallDetector()], mcp)
    assert written == 1

    folder = _incident_folder(incidents_dir)
    action = json.loads((folder / "action.json").read_text())
    assert action["action"] == "evaluate_goal"
    assert action["status"] == "ok"
    # L1 surface hit, docker NOT invoked.
    assert mcp.calls == ["stalled"]


@pytest.mark.asyncio
async def test_tick_o3_noop_decision_skips_all_side_effects(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    from datetime import timedelta

    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_call_claude_returning('{"action": "noop", "reasoning": "looks fine"}'),
    )
    _write_verifying_stall_goal(
        goals_dir,
        "stalled",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=6),
    )

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()
    written = await tick(_cfg(goals_dir, incidents_dir), store, [VerifyingStallDetector()], mcp)

    assert written == 1
    folder = _incident_folder(incidents_dir)
    assert not (folder / "action.json").exists()
    assert mcp.calls == [] and mcp.steer_calls == []


@pytest.mark.asyncio
async def test_tick_o3_prompt_includes_allowlist(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    """The cfg's docker_restart_allowlist is threaded into the O3 prompt."""
    from datetime import timedelta

    captured: dict[str, str] = {}

    async def _capture_call(prompt, *, role="ops-agent", model=None, timeout_s=None):
        captured["prompt"] = prompt
        return CognitionCall(
            stdout='{"action": "noop", "reasoning": "x"}',
            model="stub",
            latency_ms=1,
            argv_head="stub",
        )

    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr("ops_agent.main.call_claude", _capture_call)
    _write_verifying_stall_goal(
        goals_dir,
        "stalled",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=6),
    )

    cfg = OpsConfig(
        goals_dir=goals_dir,
        incidents_dir=incidents_dir,
        poll_interval_s=1.0,
        dedup_window_s=86400.0,
        docker_restart_allowlist=("compose-devclaw-mcp-1", "compose-notify-relay-1"),
    )
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    await tick(cfg, store, [VerifyingStallDetector()], _StubMCP())

    assert "compose-devclaw-mcp-1" in captured["prompt"]
    assert "compose-notify-relay-1" in captured["prompt"]


@pytest.mark.asyncio
async def test_tick_runs_all_three_detectors_in_one_pass(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    """The detectors list arg drives a three-trigger tick.

    Stages an O1-firing goal, an O2-firing goal, AND an O3-firing goal in
    the same goals dir, asserts all three incidents land in one tick
    with the right action dispatched per trigger.
    """
    import os
    from datetime import timedelta

    async def _routing_call(prompt, *, role="ops-agent", model=None, timeout_s=None):
        if "no-progress watchdog" in prompt:
            return CognitionCall(
                stdout='{"action": "evaluate_goal", "reasoning": "kick it"}',
                model="stub",
                latency_ms=1,
                argv_head="stub",
            )
        if "no-steering watchdog" in prompt:
            return CognitionCall(
                stdout='{"action": "steer_goal", "message": "re-check", "reasoning": "drifted"}',
                model="stub",
                latency_ms=1,
                argv_head="stub",
            )
        if "verifying-stall watchdog" in prompt:
            return CognitionCall(
                stdout=(
                    '{"action": "docker_restart", "service_name": "compose-devclaw-mcp-1", '
                    '"reasoning": "frozen"}'
                ),
                model="stub",
                latency_ms=1,
                argv_head="stub",
            )
        return CognitionCall(
            stdout='{"action": "noop", "reasoning": "x"}',
            model="stub",
            latency_ms=1,
            argv_head="stub",
        )

    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    monkeypatch.setattr("ops_agent.main.call_claude", _routing_call)

    spawn_calls: list[tuple] = []

    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"ok\n", b"")

    async def _fake_create(*args, **kwargs):
        spawn_calls.append(args)
        return _FakeProc()

    import asyncio as _asyncio

    monkeypatch.setattr(_asyncio, "create_subprocess_exec", _fake_create)

    # O1 fixture (uses existing write_goal helper) — phase explicitly not in
    # the O3 stall set, so it lights up O1 alone.
    write_goal(goals_dir, "stuck", no_progress_notified=True, phase="executing")
    # O2 fixture (executing, drifting inbox).
    o2 = goals_dir / "drifty"
    o2.mkdir()
    (o2 / "goal.yaml").write_text("objective: drift\ncadence: 1d\n")
    (o2 / "STATUS.md").write_text("---\nphase: executing\n---\n\n# s\n")
    inbox = o2 / "inbox.md"
    inbox.write_text("# inbox\n")
    past = (fixed_now - timedelta(hours=30)).timestamp()
    os.utime(inbox, (past, past))
    # O3 fixture (verifying-stall).
    _write_verifying_stall_goal(
        goals_dir,
        "stalled",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=6),
    )

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()

    written = await tick(
        _cfg(goals_dir, incidents_dir),
        store,
        [NoProgressDetector(), NoSteeringDetector(), VerifyingStallDetector()],
        mcp,
    )

    assert written == 3
    # L1 MCP called for the O1 incident, L2 MCP called for the O2 incident.
    assert mcp.calls == ["stuck"]
    assert mcp.steer_calls == [("drifty", "re-check")]
    # Docker subprocess called for the O3 incident.
    assert spawn_calls == [("docker", "restart", "compose-devclaw-mcp-1")]

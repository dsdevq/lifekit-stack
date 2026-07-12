"""Integration tests for the O5 blocked-question-answerer through ``tick()``.

Stubs both cognition entrypoints (one-shot ``call_claude`` and agentic
``call_claude_agentic``) and the MCP client so an O5 detection runs through
the playbook, the steer/escalate dispatch, and the on-disk persistence — all
hermetic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ops_agent.cognition import CognitionCall
from ops_agent.config import OpsConfig
from ops_agent.detectors import BlockedNeedsAnswerDetector, NoProgressDetector
from ops_agent.incident import IncidentStore
from ops_agent.main import tick


@dataclass
class _StubMCP:
    steer_calls: list = None
    calls: list = None

    def __post_init__(self) -> None:
        self.steer_calls = []
        self.calls = []

    async def evaluate_goal(self, goal_id: str) -> dict:
        self.calls.append(goal_id)
        return {"goal_id": goal_id, "verdict": "on_track"}

    async def steer_goal(self, goal_id: str, message: str) -> dict:
        self.steer_calls.append((goal_id, message))
        return {"goal_id": goal_id, "steered": True}


def _stub_returning(stdout: str):
    async def _fake(prompt, *a, **k):
        return CognitionCall(stdout=stdout, model="stub", latency_ms=1, argv_head="stub")

    return _fake


def _write_blocked_goal(
    goals_dir: Path,
    goal_id: str,
    *,
    blocked_on: str = "is package-lock.json out of sync, or is node mismatched?",
    workspace_dir: str = "/var/lib/devclaw/workspaces/finance-sentry",
    no_progress_notified: bool = False,
) -> None:
    gd = goals_dir / goal_id
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "goal.yaml").write_text(
        f"objective: ship {goal_id}\ncadence: 1d\nworkspace_dir: {workspace_dir}\n"
    )
    (gd / "STATUS.md").write_text(
        "---\n"
        "phase: blocked\n"
        "lifecycle: executing\n"
        f"blocked_on: {blocked_on!r}\n"
        "last_eval_verdict: needs_human\n"
        "last_eval_note: 'cannot decide without the lockfile'\n"
        f"no_progress_notified: {str(no_progress_notified).lower()}\n"
        "---\n\n# status\n"
    )


def _cfg(goals_dir: Path, incidents_dir: Path, **kw) -> OpsConfig:
    return OpsConfig(
        goals_dir=goals_dir,
        incidents_dir=incidents_dir,
        poll_interval_s=1.0,
        dedup_window_s=86400.0,
        **kw,
    )


@pytest.fixture
def goals_dir(tmp_path: Path) -> Path:
    d = tmp_path / "goals"
    d.mkdir()
    return d


@pytest.fixture
def incidents_dir(tmp_path: Path) -> Path:
    d = tmp_path / "incidents"
    d.mkdir()
    return d


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 7, 12, 2, 0, 0, tzinfo=UTC)


def _incident_folder(incidents_dir: Path) -> Path:
    folders = [p for p in incidents_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert len(folders) == 1, f"expected 1 incident folder, got {folders}"
    return folders[0]


# ---- answer path (agentic evidence mode ON) ----------------------------


@pytest.mark.asyncio
async def test_o5_agentic_answer_steers_and_unblocks(
    goals_dir: Path, incidents_dir: Path, now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: now)

    # The agentic call is the one that should fire (answer mode on + repo
    # resolvable). Fail loudly if the one-shot path is used instead.
    async def _one_shot_should_not_run(prompt, *a, **k):  # pragma: no cover
        raise AssertionError("one-shot call_claude used where agentic was expected")

    agentic_calls: list[str] = []

    async def _agentic(prompt, *, cwd, role="ops-agent-answer", model=None, timeout_s=None):
        agentic_calls.append(cwd)
        return CognitionCall(
            stdout=(
                '{"action": "steer_goal", '
                '"answer": "Chase the lockfile: package-lock.json is out of sync on main. '
                'Regenerate with npm install and commit; node 18 matches in both.", '
                '"reasoning": "git diff: lock pins react 18.2 but package.json wants 18.3"}'
            ),
            model="stub",
            latency_ms=5,
            argv_head="stub-agentic",
        )

    monkeypatch.setattr("ops_agent.main.call_claude", _one_shot_should_not_run)
    monkeypatch.setattr("ops_agent.main.call_claude_agentic", _agentic)

    # workspaces mount with the goal's checkout present, so the repo resolves.
    workspaces = goals_dir.parent / "workspaces"
    (workspaces / "finance-sentry").mkdir(parents=True)

    _write_blocked_goal(goals_dir, "finance-sentry")

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()
    cfg = _cfg(goals_dir, incidents_dir, answer_enabled=True, workspaces_dir=workspaces)

    written = await tick(cfg, store, [BlockedNeedsAnswerDetector()], mcp)

    assert written == 1
    # Agentic pass ran, scoped to the goal's checkout.
    assert agentic_calls == [str(workspaces / "finance-sentry")]

    folder = _incident_folder(incidents_dir)
    decision = json.loads((folder / "decision.json").read_text())
    assert decision["action"] == "steer_goal"
    assert "out of sync" in decision["answer"]

    action = json.loads((folder / "action.json").read_text())
    assert action["action"] == "steer_goal"
    assert action["status"] == "ok"

    # The answer was injected via steer_goal (which devclaw uses to unblock).
    assert len(mcp.steer_calls) == 1
    steered_goal, steered_msg = mcp.steer_calls[0]
    assert steered_goal == "finance-sentry"
    assert "package-lock.json" in steered_msg

    outcome_md = (folder / "outcome.md").read_text()
    assert "Answer (injected as steering)" in outcome_md


# ---- escalate path (answer mode OFF → one-shot, can only escalate) ------


@pytest.mark.asyncio
async def test_o5_disabled_runs_one_shot_and_escalates(
    goals_dir: Path, incidents_dir: Path, now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_returning(
            '{"action": "escalate", "escalation_reason": "product-priority call", '
            '"reasoning": "needs owner judgment"}'
        ),
    )

    async def _agentic_should_not_run(*a, **k):  # pragma: no cover
        raise AssertionError("agentic path used while answer mode is OFF")

    monkeypatch.setattr("ops_agent.main.call_claude_agentic", _agentic_should_not_run)

    _write_blocked_goal(goals_dir, "g")

    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()
    # answer_enabled defaults False → one-shot mode.
    written = await tick(_cfg(goals_dir, incidents_dir), store, [BlockedNeedsAnswerDetector()], mcp)

    assert written == 1
    folder = _incident_folder(incidents_dir)
    decision = json.loads((folder / "decision.json").read_text())
    assert decision["action"] == "escalate"
    assert "product-priority" in decision["escalation_reason"]
    # Escalate takes NO MCP action — the goal stays blocked for the human.
    assert mcp.steer_calls == []
    assert not (folder / "action.json").exists()
    outcome_md = (folder / "outcome.md").read_text()
    assert "Escalated to the human" in outcome_md


@pytest.mark.asyncio
async def test_o5_answer_enabled_but_repo_missing_falls_back_to_one_shot(
    goals_dir: Path, incidents_dir: Path, now: datetime, monkeypatch
) -> None:
    """answer_enabled=True but the goal's checkout isn't under the mount →
    no-evidence one-shot mode (the agentic path must NOT run)."""
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_returning('{"action": "escalate", "reasoning": "cannot verify without the repo"}'),
    )

    async def _agentic_should_not_run(*a, **k):  # pragma: no cover
        raise AssertionError("agentic path used but the repo does not resolve")

    monkeypatch.setattr("ops_agent.main.call_claude_agentic", _agentic_should_not_run)

    workspaces = goals_dir.parent / "workspaces"
    workspaces.mkdir()  # exists, but the goal's checkout subdir does NOT

    _write_blocked_goal(goals_dir, "g")
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()
    cfg = _cfg(goals_dir, incidents_dir, answer_enabled=True, workspaces_dir=workspaces)

    written = await tick(cfg, store, [BlockedNeedsAnswerDetector()], mcp)
    assert written == 1
    decision = json.loads((_incident_folder(incidents_dir) / "decision.json").read_text())
    assert decision["action"] == "escalate"


# ---- noop fallback never steers ----------------------------------------


@pytest.mark.asyncio
async def test_o5_malformed_response_noops_and_never_steers(
    goals_dir: Path, incidents_dir: Path, now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: now)
    monkeypatch.setattr("ops_agent.main.call_claude", _stub_returning("not json"))
    _write_blocked_goal(goals_dir, "g")
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()
    await tick(_cfg(goals_dir, incidents_dir), store, [BlockedNeedsAnswerDetector()], mcp)
    decision = json.loads((_incident_folder(incidents_dir) / "decision.json").read_text())
    assert decision["action"] == "noop"
    assert mcp.steer_calls == []


# ---- O1 precedence guard: a blocked goal is O5's, not O1's --------------


@pytest.mark.asyncio
async def test_o1_skips_blocked_goal_so_only_o5_fires(
    goals_dir: Path, incidents_dir: Path, now: datetime, monkeypatch
) -> None:
    """A blocked goal that ALSO has no_progress_notified must NOT light up O1
    (which would uselessly re-evaluate) — only O5 fires."""
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: now)
    monkeypatch.setattr(
        "ops_agent.main.call_claude",
        _stub_returning('{"action": "escalate", "reasoning": "human call"}'),
    )

    async def _agentic_should_not_run(*a, **k):  # pragma: no cover
        raise AssertionError("agentic path unexpected")

    monkeypatch.setattr("ops_agent.main.call_claude_agentic", _agentic_should_not_run)

    _write_blocked_goal(goals_dir, "g", no_progress_notified=True)
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    mcp = _StubMCP()

    written = await tick(
        _cfg(goals_dir, incidents_dir),
        store,
        [NoProgressDetector(), BlockedNeedsAnswerDetector()],
        mcp,
    )

    # Exactly ONE incident (O5). O1 skipped the blocked goal.
    assert written == 1
    folders = [p for p in incidents_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert len(folders) == 1
    assert "-O5-" in folders[0].name
    # evaluate_goal (O1's action) was never called.
    assert mcp.calls == []

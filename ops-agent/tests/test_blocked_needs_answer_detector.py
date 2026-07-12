"""Unit tests for the O5 blocked-needs-answer detector."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ops_agent.detectors import BlockedNeedsAnswerDetector
from ops_agent.detectors.blocked_needs_answer import read_snapshot


def _write_blocked_goal(
    goals_dir: Path,
    goal_id: str,
    *,
    objective: str = "ship the finance-sentry UI library",
    phase: str = "blocked",
    blocked_on: str | None = "is package-lock.json out of sync OR is node mismatched?",
    lifecycle: str = "executing",
    last_eval_verdict: str | None = "needs_human",
    last_eval_note: str = "cannot decide without inspecting the lockfile",
    workspace_dir: str | None = "/var/lib/devclaw/workspaces/finance-sentry",
) -> Path:
    goal_dir = goals_dir / goal_id
    goal_dir.mkdir(parents=True, exist_ok=True)
    gy = [f"objective: {objective}", "cadence: 1d"]
    if workspace_dir is not None:
        gy.append(f"workspace_dir: {workspace_dir}")
    (goal_dir / "goal.yaml").write_text("\n".join(gy) + "\n")
    fm = ["---", f"phase: {phase}", f"lifecycle: {lifecycle}"]
    if blocked_on is not None:
        fm.append(f"blocked_on: {blocked_on!r}")
    if last_eval_verdict is not None:
        fm.append(f"last_eval_verdict: {last_eval_verdict}")
    fm.append(f"last_eval_note: {last_eval_note!r}")
    fm.extend(["---", "", f"# {goal_id} — status", ""])
    (goal_dir / "STATUS.md").write_text("\n".join(fm) + "\n")
    return goal_dir


@pytest.fixture
def goals_dir(tmp_path: Path) -> Path:
    d = tmp_path / "goals"
    d.mkdir()
    return d


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 7, 12, 2, 0, 0, tzinfo=UTC)


# ---- firing ------------------------------------------------------------


def test_fires_on_blocked_goal_with_question(goals_dir: Path, now: datetime) -> None:
    _write_blocked_goal(goals_dir, "finance-sentry")
    incidents = BlockedNeedsAnswerDetector().scan(goals_dir, now=now)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.trigger == "O5"
    assert inc.goal_id == "finance-sentry"
    assert "package-lock.json" in inc.payload["blocked_on"]
    assert inc.payload["last_eval_verdict"] == "needs_human"
    assert inc.payload["workspace_dir"].endswith("finance-sentry")
    assert inc.payload["objective"].startswith("ship the finance-sentry")


def test_payload_carries_dedup_key_on_question(goals_dir: Path, now: datetime) -> None:
    _write_blocked_goal(goals_dir, "g")
    inc = BlockedNeedsAnswerDetector().scan(goals_dir, now=now)[0]
    assert inc.payload["dedup_key"].startswith("blocked_on=")


def test_different_question_changes_fingerprint(goals_dir: Path, now: datetime) -> None:
    _write_blocked_goal(goals_dir, "g", blocked_on="question one")
    a = BlockedNeedsAnswerDetector().scan(goals_dir, now=now)[0]
    _write_blocked_goal(goals_dir, "g", blocked_on="a totally different question two")
    b = BlockedNeedsAnswerDetector().scan(goals_dir, now=now)[0]
    assert a.fingerprint() != b.fingerprint()


# ---- non-firing --------------------------------------------------------


def test_skips_non_blocked_phase(goals_dir: Path, now: datetime) -> None:
    _write_blocked_goal(goals_dir, "g", phase="executing")
    assert BlockedNeedsAnswerDetector().scan(goals_dir, now=now) == []


def test_skips_blocked_goal_with_empty_blocked_on(goals_dir: Path, now: datetime) -> None:
    _write_blocked_goal(goals_dir, "g", blocked_on="")
    assert BlockedNeedsAnswerDetector().scan(goals_dir, now=now) == []


def test_skips_blocked_goal_with_no_blocked_on_field(goals_dir: Path, now: datetime) -> None:
    _write_blocked_goal(goals_dir, "g", blocked_on=None)
    assert BlockedNeedsAnswerDetector().scan(goals_dir, now=now) == []


def test_skips_goal_missing_status_md(goals_dir: Path, now: datetime) -> None:
    gd = goals_dir / "g"
    gd.mkdir()
    (gd / "goal.yaml").write_text("objective: x\n")
    assert BlockedNeedsAnswerDetector().scan(goals_dir, now=now) == []


def test_skips_goal_missing_goal_yaml(goals_dir: Path, now: datetime) -> None:
    gd = goals_dir / "g"
    gd.mkdir()
    (gd / "STATUS.md").write_text("---\nphase: blocked\nblocked_on: q\n---\n")
    assert BlockedNeedsAnswerDetector().scan(goals_dir, now=now) == []


def test_skips_hidden_dirs(goals_dir: Path, now: datetime) -> None:
    (goals_dir / ".seen").mkdir()
    assert BlockedNeedsAnswerDetector().scan(goals_dir, now=now) == []


def test_missing_goals_dir_returns_empty(tmp_path: Path, now: datetime) -> None:
    assert BlockedNeedsAnswerDetector().scan(tmp_path / "nope", now=now) == []


# ---- read_snapshot -----------------------------------------------------


def test_read_snapshot_parses_all_fields(goals_dir: Path) -> None:
    gd = _write_blocked_goal(goals_dir, "g", workspace_dir=None)
    snap = read_snapshot(gd)
    assert snap is not None
    assert snap.phase == "blocked"
    assert snap.lifecycle == "executing"
    assert "package-lock.json" in snap.blocked_on
    assert snap.workspace_dir == ""  # omitted → empty, not None


def test_read_snapshot_handles_malformed_goal_yaml(goals_dir: Path) -> None:
    gd = goals_dir / "g"
    gd.mkdir()
    (gd / "goal.yaml").write_text("objective: [oops\n")  # malformed YAML
    (gd / "STATUS.md").write_text("---\nphase: blocked\nblocked_on: q\n---\n")
    snap = read_snapshot(gd)
    assert snap is not None
    assert snap.objective == ""  # degraded, not crashed
    assert snap.blocked_on == "q"

"""O3 detector unit tests.

Coverage axes (mirroring test_no_progress_detector.py and
test_no_steering_detector.py where applicable):

  - empty goals dir → no incidents
  - non-stall-phase goal (executing/idle/done) → never fires
  - verifying phase, recent last_progress_at → no incidents
  - verifying phase, stale last_progress_at → one incident
  - in_flight phase, stale last_progress_at → one incident
  - missing last_progress_at → skipped (nothing to compare)
  - threshold is configurable via OPS_AGENT_VERIFYING_STALL_HOURS
  - dedup fingerprint shifts when last_progress_at advances
  - malformed goal → skipped, not raised
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ops_agent.detectors import VerifyingStallDetector


def _write_goal(
    goals_dir: Path,
    goal_id: str,
    *,
    objective: str = "test goal",
    phase: str = "verifying",
    last_progress_at: datetime | None = None,
) -> Path:
    """Local fixture writer — distinct from conftest.write_goal because the
    O3 detector keys on a parsed-datetime ``last_progress_at`` and ignores
    ``no_progress_notified`` entirely.
    """
    goal_dir = goals_dir / goal_id
    goal_dir.mkdir(parents=True, exist_ok=True)
    (goal_dir / "goal.yaml").write_text(f"objective: {objective}\ncadence: 1d\n")
    fm_lines = ["---", f"phase: {phase}"]
    if last_progress_at is not None:
        fm_lines.append(f"last_progress_at: '{last_progress_at.isoformat()}'")
    fm_lines.extend(["---", "", f"# {goal_id} — status", ""])
    (goal_dir / "STATUS.md").write_text("\n".join(fm_lines) + "\n")
    return goal_dir


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """The threshold reads from env at scan-time. Clear it so tests start clean."""
    monkeypatch.delenv("OPS_AGENT_VERIFYING_STALL_HOURS", raising=False)


# ---- empty / non-firing baselines --------------------------------------


def test_empty_goals_dir_returns_no_incidents(goals_dir: Path, fixed_now: datetime) -> None:
    assert VerifyingStallDetector().scan(goals_dir, now=fixed_now) == []


@pytest.mark.parametrize("phase", ["executing", "idle", "paused", "done", "blocked", "drafting"])
def test_non_stall_phases_never_fire(goals_dir: Path, fixed_now: datetime, phase: str) -> None:
    """Anything outside the {verifying, in_flight} allowlist is silent — even
    if last_progress_at is ancient. This keeps O3 from poaching O1's domain
    (executing goals route through the no-progress watchdog)."""
    _write_goal(
        goals_dir,
        f"g-{phase}",
        phase=phase,
        last_progress_at=fixed_now - timedelta(days=30),
    )
    assert VerifyingStallDetector().scan(goals_dir, now=fixed_now) == []


def test_recent_progress_in_verifying_phase_does_not_fire(
    goals_dir: Path, fixed_now: datetime
) -> None:
    # 1h < 4h default threshold.
    _write_goal(
        goals_dir,
        "g",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=1),
    )
    assert VerifyingStallDetector().scan(goals_dir, now=fixed_now) == []


# ---- firing paths -------------------------------------------------------


def test_stale_verifying_goal_fires_one_incident(goals_dir: Path, fixed_now: datetime) -> None:
    _write_goal(
        goals_dir,
        "stalled",
        objective="ship the done-gate",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=6),
    )
    incidents = VerifyingStallDetector().scan(goals_dir, now=fixed_now)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.trigger == "O3"
    assert inc.goal_id == "stalled"
    assert inc.detected_at == fixed_now
    assert inc.payload["objective"] == "ship the done-gate"
    assert inc.payload["phase"] == "verifying"
    assert inc.payload["age_hours"] >= 4.0
    assert inc.payload["threshold_hours"] == 4.0
    assert "dedup_key" in inc.payload
    assert "phase=verifying" in inc.payload["dedup_key"]
    assert "last_progress_at=" in inc.payload["dedup_key"]


def test_stale_in_flight_goal_fires_one_incident(goals_dir: Path, fixed_now: datetime) -> None:
    _write_goal(
        goals_dir,
        "in-flight-stalled",
        phase="in_flight",
        last_progress_at=fixed_now - timedelta(hours=5),
    )
    incidents = VerifyingStallDetector().scan(goals_dir, now=fixed_now)
    assert len(incidents) == 1
    assert incidents[0].payload["phase"] == "in_flight"


# ---- missing signal -----------------------------------------------------


def test_missing_last_progress_at_skips_goal(goals_dir: Path, fixed_now: datetime) -> None:
    _write_goal(goals_dir, "g", phase="verifying", last_progress_at=None)
    assert VerifyingStallDetector().scan(goals_dir, now=fixed_now) == []


# ---- malformed input ----------------------------------------------------


def test_malformed_goal_is_skipped(goals_dir: Path, fixed_now: datetime) -> None:
    bad = goals_dir / "half-baked"
    bad.mkdir()
    (bad / "goal.yaml").write_text("objective: nope\n")  # no STATUS.md
    (goals_dir / ".scratch").mkdir()
    _write_goal(
        goals_dir,
        "real",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=6),
    )
    incidents = VerifyingStallDetector().scan(goals_dir, now=fixed_now)
    assert [i.goal_id for i in incidents] == ["real"]


# ---- threshold configurability -----------------------------------------


def test_threshold_env_override_lowers_bar(
    goals_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    """A 1-hour threshold trips on goals the default 4h wouldn't."""
    monkeypatch.setenv("OPS_AGENT_VERIFYING_STALL_HOURS", "1")
    _write_goal(
        goals_dir,
        "g",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=2),
    )
    incidents = VerifyingStallDetector().scan(goals_dir, now=fixed_now)
    assert len(incidents) == 1
    assert incidents[0].payload["threshold_hours"] == 1.0


def test_threshold_env_override_raises_bar(
    goals_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    """A 100-hour threshold silences a goal the default 4h would fire on."""
    monkeypatch.setenv("OPS_AGENT_VERIFYING_STALL_HOURS", "100")
    _write_goal(
        goals_dir,
        "g",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=10),
    )
    assert VerifyingStallDetector().scan(goals_dir, now=fixed_now) == []


def test_threshold_invalid_env_falls_back_to_default(
    goals_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setenv("OPS_AGENT_VERIFYING_STALL_HOURS", "not-a-number")
    _write_goal(
        goals_dir,
        "g",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=6),
    )
    incidents = VerifyingStallDetector().scan(goals_dir, now=fixed_now)
    assert len(incidents) == 1
    assert incidents[0].payload["threshold_hours"] == 4.0


# ---- dedup fingerprint --------------------------------------------------


def test_dedup_key_changes_when_progress_advances(goals_dir: Path, fixed_now: datetime) -> None:
    """When fresh progress lands, last_progress_at advances → new fingerprint."""
    _write_goal(
        goals_dir,
        "g",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=6),
    )
    first = VerifyingStallDetector().scan(goals_dir, now=fixed_now)[0]

    # Re-write the goal with a still-stale-but-newer progress timestamp.
    _write_goal(
        goals_dir,
        "g",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=5),
    )
    second = VerifyingStallDetector().scan(goals_dir, now=fixed_now)[0]
    assert first.fingerprint() != second.fingerprint()


def test_dedup_key_changes_when_phase_transitions_within_stall_set(
    goals_dir: Path, fixed_now: datetime
) -> None:
    """A transition verifying → in_flight (both stall phases) re-fires.

    Verifying-stall → re-dispatched → in_flight-still-stalled is a real
    sequence: progress timestamp may stay the same but the phase moves.
    The dedup key folds both fields so it shifts on either change.
    """
    last_progress = fixed_now - timedelta(hours=6)
    _write_goal(goals_dir, "g", phase="verifying", last_progress_at=last_progress)
    first = VerifyingStallDetector().scan(goals_dir, now=fixed_now)[0]

    _write_goal(goals_dir, "g", phase="in_flight", last_progress_at=last_progress)
    second = VerifyingStallDetector().scan(goals_dir, now=fixed_now)[0]
    assert first.fingerprint() != second.fingerprint()


# ---- multiple goals -----------------------------------------------------


def test_mixed_goals_fires_only_stall_phase_ones(goals_dir: Path, fixed_now: datetime) -> None:
    _write_goal(
        goals_dir,
        "a-executing",
        phase="executing",
        last_progress_at=fixed_now - timedelta(hours=48),
    )
    _write_goal(
        goals_dir,
        "b-verifying-fresh",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=1),
    )
    _write_goal(
        goals_dir,
        "c-verifying-stale",
        phase="verifying",
        last_progress_at=fixed_now - timedelta(hours=6),
    )
    _write_goal(
        goals_dir,
        "d-in-flight-stale",
        phase="in_flight",
        last_progress_at=fixed_now - timedelta(hours=8),
    )
    _write_goal(
        goals_dir,
        "e-done",
        phase="done",
        last_progress_at=fixed_now - timedelta(hours=72),
    )
    fired = sorted(i.goal_id for i in VerifyingStallDetector().scan(goals_dir, now=fixed_now))
    assert fired == ["c-verifying-stale", "d-in-flight-stale"]

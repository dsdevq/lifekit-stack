"""O1 detector unit tests.

Coverage axes:
  - empty goals dir → no incidents
  - flag unset on every goal → no incidents
  - flag set on one goal → one incident with the right payload shape
  - malformed goal (missing STATUS.md / goal.yaml) → skipped, not raised
  - multiple goals, mixed → exactly the flagged ones fire
  - dedup_key reflects ``last_progress_at`` so re-stick events fingerprint differently
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ops_agent.detectors import NoProgressDetector

from .conftest import write_goal


def test_empty_goals_dir_returns_no_incidents(goals_dir: Path, fixed_now: datetime) -> None:
    incidents = NoProgressDetector().scan(goals_dir, now=fixed_now)
    assert incidents == []


def test_unset_flag_does_not_fire(goals_dir: Path, fixed_now: datetime) -> None:
    write_goal(goals_dir, "g1", no_progress_notified=False)
    write_goal(goals_dir, "g2", no_progress_notified=False)
    assert NoProgressDetector().scan(goals_dir, now=fixed_now) == []


def test_set_flag_fires_one_incident(goals_dir: Path, fixed_now: datetime) -> None:
    write_goal(
        goals_dir,
        "stuck-goal",
        objective="ship a thing",
        phase="in_flight",
        no_progress_notified=True,
        last_progress_at="2026-06-29T06:00:00+00:00",
        last_tick_at="2026-06-29T11:55:00+00:00",
    )
    incidents = NoProgressDetector().scan(goals_dir, now=fixed_now)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.trigger == "O1"
    assert inc.goal_id == "stuck-goal"
    assert inc.detected_at == fixed_now
    assert inc.payload["objective"] == "ship a thing"
    assert inc.payload["phase"] == "in_flight"
    assert inc.payload["last_progress_at"] == "2026-06-29T06:00:00+00:00"
    assert "dedup_key" in inc.payload


def test_malformed_goal_is_skipped(goals_dir: Path, fixed_now: datetime) -> None:
    # Goal with goal.yaml but no STATUS.md (mid-creation race).
    bad = goals_dir / "half-baked"
    bad.mkdir()
    (bad / "goal.yaml").write_text("objective: nope\n")
    # And a stray dotfile dir that should be ignored entirely.
    (goals_dir / ".scratch").mkdir()
    # And a real stuck goal alongside it.
    write_goal(goals_dir, "real", no_progress_notified=True)
    incidents = NoProgressDetector().scan(goals_dir, now=fixed_now)
    assert [i.goal_id for i in incidents] == ["real"]


def test_mixed_goals_fires_only_flagged(goals_dir: Path, fixed_now: datetime) -> None:
    write_goal(goals_dir, "a", no_progress_notified=False)
    write_goal(goals_dir, "b", no_progress_notified=True)
    write_goal(goals_dir, "c", no_progress_notified=True)
    write_goal(goals_dir, "d", no_progress_notified=False)
    fired = sorted(i.goal_id for i in NoProgressDetector().scan(goals_dir, now=fixed_now))
    assert fired == ["b", "c"]


def test_dedup_key_changes_when_last_progress_advances(
    goals_dir: Path, fixed_now: datetime
) -> None:
    write_goal(
        goals_dir,
        "g",
        no_progress_notified=True,
        last_progress_at="2026-06-29T06:00:00+00:00",
    )
    first = NoProgressDetector().scan(goals_dir, now=fixed_now)[0]

    # Simulate: goal made progress (last_progress_at advanced) and then went
    # stuck again. The fingerprint must differ so dedup doesn't suppress.
    write_goal(
        goals_dir,
        "g",
        no_progress_notified=True,
        last_progress_at="2026-06-29T10:00:00+00:00",
    )
    second = NoProgressDetector().scan(goals_dir, now=fixed_now)[0]

    assert first.fingerprint() != second.fingerprint()

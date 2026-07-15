"""O2 detector unit tests.

Coverage axes (mirroring test_no_progress_detector.py where applicable):
  - empty goals dir → no incidents
  - no executing goals → no incidents
  - inbox.md mtime within threshold → no incidents
  - inbox.md mtime past threshold + phase==executing → one incident
  - non-executing phases (idle/paused/done) → never fire even if old
  - missing inbox.md, created_at present → falls back to created_at
  - missing inbox.md AND no created_at → goal is skipped
  - threshold is configurable via OPS_AGENT_NO_STEERING_HOURS
  - dedup_key reflects the inbox mtime so a new steering reset re-fires
  - malformed goal (missing STATUS.md / goal.yaml) → skipped, not raised
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ops_agent.detectors import NoSteeringDetector


def _write_goal(
    goals_dir: Path,
    goal_id: str,
    *,
    objective: str = "test goal",
    phase: str = "executing",
    inbox_mtime: datetime | None = None,
    create_inbox: bool = True,
    created_at: str | None = None,
) -> Path:
    """Local fixture writer — distinct from conftest.write_goal because the
    O2 detector keys on the optional inbox.md + frontmatter ``created_at``
    fields that the base write_goal helper doesn't carry.
    """
    goal_dir = goals_dir / goal_id
    goal_dir.mkdir(parents=True, exist_ok=True)
    (goal_dir / "goal.yaml").write_text(f"objective: {objective}\ncadence: 1d\n")
    fm_lines = ["---", f"phase: {phase}"]
    if created_at is not None:
        fm_lines.append(f"created_at: '{created_at}'")
    fm_lines.extend(["---", "", f"# {goal_id} — status", ""])
    (goal_dir / "STATUS.md").write_text("\n".join(fm_lines) + "\n")
    if create_inbox:
        inbox = goal_dir / "inbox.md"
        inbox.write_text("# inbox\n")
        if inbox_mtime is not None:
            ts = inbox_mtime.timestamp()
            os.utime(inbox, (ts, ts))
    return goal_dir


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """The threshold reads from env at scan-time. Clear it so tests start clean."""
    monkeypatch.delenv("OPS_AGENT_NO_STEERING_HOURS", raising=False)


# ---- happy/empty paths ---------------------------------------------------


def test_empty_goals_dir_returns_no_incidents(goals_dir: Path, fixed_now: datetime) -> None:
    assert NoSteeringDetector().scan(goals_dir, now=fixed_now) == []


def test_recent_steering_does_not_fire(goals_dir: Path, fixed_now: datetime) -> None:
    # Inbox touched 1h ago, well under the 24h default.
    _write_goal(goals_dir, "g", inbox_mtime=fixed_now - timedelta(hours=1))
    assert NoSteeringDetector().scan(goals_dir, now=fixed_now) == []


def test_old_steering_fires_one_incident(goals_dir: Path, fixed_now: datetime) -> None:
    _write_goal(
        goals_dir,
        "drifty",
        objective="ship the new flow",
        phase="executing",
        inbox_mtime=fixed_now - timedelta(hours=30),
    )
    incidents = NoSteeringDetector().scan(goals_dir, now=fixed_now)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.trigger == "O2"
    assert inc.goal_id == "drifty"
    assert inc.detected_at == fixed_now
    assert inc.payload["objective"] == "ship the new flow"
    assert inc.payload["phase"] == "executing"
    assert inc.payload["last_steering_source"] == "inbox_mtime"
    assert inc.payload["age_hours"] >= 24.0
    assert inc.payload["threshold_hours"] == 24.0
    assert "dedup_key" in inc.payload
    assert inc.payload["dedup_key"].startswith("inbox_mtime=")


# ---- phase gating --------------------------------------------------------


@pytest.mark.parametrize("phase", ["idle", "paused", "done", "blocked", "drafting"])
def test_non_executing_phases_never_fire(goals_dir: Path, fixed_now: datetime, phase: str) -> None:
    """Idle / paused / done / blocked goals don't need steering — even if
    their inbox is months old, O2 must stay quiet."""
    _write_goal(
        goals_dir,
        f"g-{phase}",
        phase=phase,
        inbox_mtime=fixed_now - timedelta(days=30),
    )
    assert NoSteeringDetector().scan(goals_dir, now=fixed_now) == []


# ---- fallback to created_at when inbox is missing ----------------------


def test_missing_inbox_falls_back_to_created_at(goals_dir: Path, fixed_now: datetime) -> None:
    _write_goal(
        goals_dir,
        "no-inbox-yet",
        phase="executing",
        create_inbox=False,
        created_at=(fixed_now - timedelta(hours=48)).isoformat(),
    )
    incidents = NoSteeringDetector().scan(goals_dir, now=fixed_now)
    assert len(incidents) == 1
    assert incidents[0].payload["last_steering_source"] == "created_at"


def test_missing_inbox_and_no_created_at_skips_goal(goals_dir: Path, fixed_now: datetime) -> None:
    _write_goal(
        goals_dir,
        "no-signal",
        phase="executing",
        create_inbox=False,
        created_at=None,
    )
    assert NoSteeringDetector().scan(goals_dir, now=fixed_now) == []


# ---- malformed input -----------------------------------------------------


def test_malformed_goal_is_skipped(goals_dir: Path, fixed_now: datetime) -> None:
    bad = goals_dir / "half-baked"
    bad.mkdir()
    (bad / "goal.yaml").write_text("objective: nope\n")  # no STATUS.md
    (goals_dir / ".scratch").mkdir()
    _write_goal(
        goals_dir,
        "real",
        phase="executing",
        inbox_mtime=fixed_now - timedelta(hours=30),
    )
    incidents = NoSteeringDetector().scan(goals_dir, now=fixed_now)
    assert [i.goal_id for i in incidents] == ["real"]


# ---- threshold configurability -----------------------------------------


def test_threshold_env_override_lowers_bar(
    goals_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    """A 2-hour threshold trips on goals the default 24h wouldn't."""
    monkeypatch.setenv("OPS_AGENT_NO_STEERING_HOURS", "2")
    _write_goal(goals_dir, "g", inbox_mtime=fixed_now - timedelta(hours=3))
    incidents = NoSteeringDetector().scan(goals_dir, now=fixed_now)
    assert len(incidents) == 1
    assert incidents[0].payload["threshold_hours"] == 2.0


def test_threshold_env_override_raises_bar(
    goals_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    """A 100-hour threshold silences a goal the default 24h would fire on."""
    monkeypatch.setenv("OPS_AGENT_NO_STEERING_HOURS", "100")
    _write_goal(goals_dir, "g", inbox_mtime=fixed_now - timedelta(hours=30))
    assert NoSteeringDetector().scan(goals_dir, now=fixed_now) == []


def test_threshold_invalid_env_falls_back_to_default(
    goals_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setenv("OPS_AGENT_NO_STEERING_HOURS", "not-a-number")
    _write_goal(goals_dir, "g", inbox_mtime=fixed_now - timedelta(hours=30))
    incidents = NoSteeringDetector().scan(goals_dir, now=fixed_now)
    assert len(incidents) == 1  # default 24h kicked in
    assert incidents[0].payload["threshold_hours"] == 24.0


# ---- dedup fingerprint ---------------------------------------------------


def test_dedup_key_changes_when_inbox_mtime_advances(goals_dir: Path, fixed_now: datetime) -> None:
    """When fresh steering arrives, inbox mtime advances → new fingerprint."""
    g = _write_goal(goals_dir, "g", inbox_mtime=fixed_now - timedelta(hours=30))
    first = NoSteeringDetector().scan(goals_dir, now=fixed_now)[0]

    # Simulate new steering: bump inbox mtime to a still-old-but-different
    # time. (The watchdog re-fires only after the threshold trips again,
    # but the fingerprint must shift on ANY mtime change so the dedup
    # window from the prior incident doesn't suppress.)
    new_mtime = (fixed_now - timedelta(hours=26)).timestamp()
    os.utime(g / "inbox.md", (new_mtime, new_mtime))
    second = NoSteeringDetector().scan(goals_dir, now=fixed_now)[0]
    assert first.fingerprint() != second.fingerprint()


# ---- multiple goals ------------------------------------------------------


def test_mixed_goals_fires_only_drifting_executing_ones(
    goals_dir: Path, fixed_now: datetime
) -> None:
    _write_goal(goals_dir, "a", phase="executing", inbox_mtime=fixed_now - timedelta(hours=1))
    _write_goal(goals_dir, "b", phase="executing", inbox_mtime=fixed_now - timedelta(hours=30))
    _write_goal(goals_dir, "c", phase="idle", inbox_mtime=fixed_now - timedelta(hours=72))
    _write_goal(goals_dir, "d", phase="executing", inbox_mtime=fixed_now - timedelta(hours=48))
    fired = sorted(i.goal_id for i in NoSteeringDetector().scan(goals_dir, now=fixed_now))
    assert fired == ["b", "d"]

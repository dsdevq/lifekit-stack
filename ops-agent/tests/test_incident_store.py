"""IncidentStore — persistence + dedup behaviour.

The store is the boundary between "we detected something" and "the world
knows about it." It owns:
  - the per-incident folder + trigger.json + outcome.md
  - the .seen marker (dedup, mtime-based)
  - log.md append
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from ops_agent.incident import Incident, IncidentStore


def _mk_incident(now: datetime, goal_id: str = "g1", dedup_key: str = "k") -> Incident:
    return Incident(
        trigger="O1",
        goal_id=goal_id,
        detected_at=now,
        payload={"objective": "do the thing", "phase": "in_flight", "dedup_key": dedup_key},
    )


def test_write_creates_folder_with_trigger_and_outcome(
    incidents_dir: Path, fixed_now: datetime
) -> None:
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    inc = _mk_incident(fixed_now)
    folder = store.write(inc)
    assert folder.is_dir()
    assert folder.parent == incidents_dir
    trigger = json.loads((folder / "trigger.json").read_text())
    assert trigger["trigger"] == "O1"
    assert trigger["goal_id"] == "g1"
    assert trigger["payload"]["objective"] == "do the thing"
    outcome = (folder / "outcome.md").read_text()
    assert "L0" in outcome
    assert "ops-PR1" in outcome


def test_write_appends_log_line(incidents_dir: Path, fixed_now: datetime) -> None:
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    store.write(_mk_incident(fixed_now, goal_id="alpha"))
    store.write(_mk_incident(fixed_now + timedelta(minutes=1), goal_id="beta"))
    log = (incidents_dir / "log.md").read_text()
    assert "ops-agent — incident log" in log
    assert "O1 — alpha" in log
    assert "O1 — beta" in log


def test_dedup_marker_suppresses_recent_repeat(incidents_dir: Path, fixed_now: datetime) -> None:
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    inc = _mk_incident(fixed_now)
    store.write(inc)
    # Same fingerprint, slightly later — still inside the window.
    later = _mk_incident(fixed_now + timedelta(hours=1))
    assert store.is_deduped(later, now=fixed_now + timedelta(hours=1)) is True


def test_dedup_marker_expires_after_window(incidents_dir: Path, fixed_now: datetime) -> None:
    store = IncidentStore(incidents_dir, dedup_window_s=3600)  # 1h window
    inc = _mk_incident(fixed_now)
    store.write(inc)
    # 2h later — outside the window.
    future = fixed_now + timedelta(hours=2)
    repeat = _mk_incident(future)
    assert store.is_deduped(repeat, now=future) is False


def test_different_dedup_key_is_not_deduped(incidents_dir: Path, fixed_now: datetime) -> None:
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    store.write(_mk_incident(fixed_now, dedup_key="state-a"))
    repeat = _mk_incident(fixed_now + timedelta(minutes=5), dedup_key="state-b")
    assert store.is_deduped(repeat, now=fixed_now + timedelta(minutes=5)) is False


def test_long_objective_is_truncated_in_log(incidents_dir: Path, fixed_now: datetime) -> None:
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    long_obj = "x" * 200
    inc = Incident(
        trigger="O1",
        goal_id="g",
        detected_at=fixed_now,
        payload={"objective": long_obj, "dedup_key": "k"},
    )
    store.write(inc)
    log = (incidents_dir / "log.md").read_text()
    # One line per entry; verify the truncation marker is present.
    assert "..." in log
    # No line should exceed a reasonable width.
    for line in log.splitlines():
        assert len(line) < 200

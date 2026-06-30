"""End-to-end-ish test of one ``tick()`` against a real-on-disk goals dir.

This is the "ops-PR1 vertical slice" coverage: drop a stuck-goal STATUS.md
on disk, call tick, observe an incident folder appear and the log line land.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ops_agent.config import OpsConfig
from ops_agent.detectors import NoProgressDetector
from ops_agent.incident import IncidentStore
from ops_agent.main import tick

from .conftest import write_goal


def _cfg(goals_dir: Path, incidents_dir: Path) -> OpsConfig:
    return OpsConfig(
        goals_dir=goals_dir,
        incidents_dir=incidents_dir,
        poll_interval_s=1.0,
        dedup_window_s=86400.0,
    )


@pytest.mark.asyncio
async def test_tick_writes_incident_for_stuck_goal(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    write_goal(goals_dir, "stuck", no_progress_notified=True, objective="ship X")
    store = IncidentStore(incidents_dir, dedup_window_s=86400)

    written = await tick(_cfg(goals_dir, incidents_dir), store, [NoProgressDetector()], None)

    assert written == 1
    folders = [p for p in incidents_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert len(folders) == 1
    assert folders[0].name.endswith("-O1-stuck")
    assert (incidents_dir / "log.md").exists()


@pytest.mark.asyncio
async def test_tick_is_idempotent_within_window(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    write_goal(goals_dir, "stuck", no_progress_notified=True)
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    cfg = _cfg(goals_dir, incidents_dir)
    det = NoProgressDetector()

    first = await tick(cfg, store, [det], None)
    second = await tick(cfg, store, [det], None)

    assert first == 1
    assert second == 0  # dedup'd
    folders = [p for p in incidents_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert len(folders) == 1


@pytest.mark.asyncio
async def test_tick_re_fires_after_dedup_window(
    goals_dir: Path, incidents_dir: Path, fixed_now: datetime, monkeypatch
) -> None:
    write_goal(goals_dir, "stuck", no_progress_notified=True)
    store = IncidentStore(incidents_dir, dedup_window_s=3600)
    cfg = _cfg(goals_dir, incidents_dir)
    det = NoProgressDetector()

    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    assert await tick(cfg, store, [det], None) == 1

    later = fixed_now + timedelta(hours=2)
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: later)
    assert await tick(cfg, store, [det], None) == 1


@pytest.mark.asyncio
async def test_tick_handles_no_goals_dir(
    incidents_dir: Path, fixed_now: datetime, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("ops_agent.main._utcnow", lambda: fixed_now)
    missing = tmp_path / "does-not-exist"
    store = IncidentStore(incidents_dir, dedup_window_s=86400)
    written = await tick(_cfg(missing, incidents_dir), store, [NoProgressDetector()], None)
    assert written == 0

"""main.tick wiring for O5 + the detector-hygiene rules.

Pins the four load-bearing behaviors of this tranche:

  - an O5 incident routes to the MECHANICAL notify — the cognition layer is
    never consulted for it (zero-LLM by decision, Resolved O1/O2; asserted
    the same way devclaw pins its zero-token guard: a cognition stub that
    counts calls must stay at 0);
  - the O5 notify degrades safely: no notify_url → folder-only; a dead
    relay → logged, loop survives;
  - O3 incidents are SUPPRESSED while devclaw reports dispatch deliberately
    held (held ≠ stalled — the 2026-08-12 run-window false positive), and
    suppression happens BEFORE dedup so the marker isn't burned;
  - O1 skips goals in terminal phases (the daily zombie incidents on two
    long-cancelled goals).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ops_agent import main as main_mod
from ops_agent.config import OpsConfig
from ops_agent.detectors import DaemonLivenessDetector, NoProgressDetector
from ops_agent.health_probe import HealthSnapshot
from ops_agent.incident import Incident, IncidentStore

from .conftest import write_goal

NOW = datetime(2026, 8, 12, 22, 0, 0, tzinfo=UTC)


def _cfg(goals_dir: Path, incidents_dir: Path, **kw) -> OpsConfig:
    return OpsConfig(
        goals_dir=goals_dir,
        incidents_dir=incidents_dir,
        poll_interval_s=60.0,
        dedup_window_s=24 * 3600.0,
        **kw,
    )


class _O3Emitter:
    """Minimal stand-in that emits one O3 incident per scan."""

    trigger = "O3"

    def scan(self, goals_dir: Path, *, now: datetime) -> list[Incident]:
        return [
            Incident(
                trigger="O3",
                goal_id="held-goal",
                detected_at=now,
                payload={"dedup_key": "phase=in_flight|last_progress_at=x"},
            )
        ]


def _count_calls(monkeypatch):
    calls = {"n": 0}

    async def _counting_call_claude(prompt, **kw):
        calls["n"] += 1
        raise AssertionError("cognition must not be consulted")

    monkeypatch.setattr("ops_agent.main.call_claude", _counting_call_claude)
    return calls


@pytest.mark.asyncio
async def test_o5_incident_notifies_mechanically_with_zero_cognition(
    goals_dir, incidents_dir, monkeypatch
):
    calls = _count_calls(monkeypatch)
    posts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main_mod, "_post_text", lambda url, text, **kw: posts.append((url, text))
    )
    monkeypatch.setattr(
        main_mod,
        "probe_health",
        lambda url, timeout_s: HealthSnapshot(reachable=False, error="refused"),
    )
    monkeypatch.setattr(main_mod, "_utcnow", lambda: NOW)

    cfg = _cfg(
        goals_dir,
        incidents_dir,
        health_url="http://devclaw-mcp:8000/health",
        notify_url="http://notify-relay:8090/text",
    )
    store = IncidentStore(incidents_dir, dedup_window_s=cfg.dedup_window_s)
    written = await main_mod.tick(cfg, store, [DaemonLivenessDetector()], mcp=None)

    assert written == 1
    assert calls["n"] == 0  # the zero-LLM pin
    assert len(posts) == 1
    assert "daemon-down" in posts[0][1]
    (folder,) = [p for p in incidents_dir.iterdir() if p.is_dir() and p.name != ".seen"]
    assert "ping-only" in (folder / "outcome.md").read_text()


@pytest.mark.asyncio
async def test_o5_without_notify_url_is_folder_only_and_survives(
    goals_dir, incidents_dir, monkeypatch
):
    _count_calls(monkeypatch)
    monkeypatch.setattr(
        main_mod,
        "probe_health",
        lambda url, timeout_s: HealthSnapshot(reachable=False, error="refused"),
    )
    monkeypatch.setattr(main_mod, "_utcnow", lambda: NOW)
    cfg = _cfg(goals_dir, incidents_dir, health_url="http://x/health")  # no notify_url
    store = IncidentStore(incidents_dir, dedup_window_s=cfg.dedup_window_s)
    written = await main_mod.tick(cfg, store, [DaemonLivenessDetector()], mcp=None)
    assert written == 1
    (folder,) = [p for p in incidents_dir.iterdir() if p.is_dir() and p.name != ".seen"]
    assert "delivered: False" in (folder / "outcome.md").read_text()


@pytest.mark.asyncio
async def test_o3_suppressed_while_dispatch_held_and_fires_after(
    goals_dir, incidents_dir, monkeypatch
):
    held = HealthSnapshot(
        reachable=True,
        ok=True,
        dispatch_open=False,
        dispatch_hold_reason="outside run window 22:00-05:00",
    )
    monkeypatch.setattr(main_mod, "probe_health", lambda url, timeout_s: held)
    monkeypatch.setattr(main_mod, "_utcnow", lambda: NOW)
    cfg = _cfg(goals_dir, incidents_dir, health_url="http://x/health")
    store = IncidentStore(incidents_dir, dedup_window_s=cfg.dedup_window_s)

    written = await main_mod.tick(cfg, store, [_O3Emitter()], mcp=None)
    assert written == 0  # suppressed, and no dedup marker burned

    open_now = HealthSnapshot(reachable=True, ok=True, dispatch_open=True)
    monkeypatch.setattr(main_mod, "probe_health", lambda url, timeout_s: open_now)
    written = await main_mod.tick(cfg, store, [_O3Emitter()], mcp=None)
    assert written == 1  # same incident shape fires once the hold lifts


@pytest.mark.asyncio
async def test_o3_not_suppressed_when_health_lacks_dispatch_field(
    goals_dir, incidents_dir, monkeypatch
):
    """A pre-#494 devclaw (no dispatch_open field) must not silently disable
    O3 — unknown is not 'held'."""
    unknown = HealthSnapshot(reachable=True, ok=True, dispatch_open=None)
    monkeypatch.setattr(main_mod, "probe_health", lambda url, timeout_s: unknown)
    monkeypatch.setattr(main_mod, "_utcnow", lambda: NOW)
    cfg = _cfg(goals_dir, incidents_dir, health_url="http://x/health")
    store = IncidentStore(incidents_dir, dedup_window_s=cfg.dedup_window_s)
    assert await main_mod.tick(cfg, store, [_O3Emitter()], mcp=None) == 1


def test_o1_skips_terminal_phase_goals_the_zombie_fix(goals_dir):
    """2026-08-12 live evidence: two goals cancelled weeks earlier re-fired O1
    daily. A terminal phase wins over a leftover no_progress_notified flag."""
    for phase in ("cancelled", "done", "achieved"):
        write_goal(
            goals_dir,
            f"zombie-{phase}",
            phase=phase,
            no_progress_notified=True,
            last_progress_at="2026-07-20T06:00:00+00:00",
        )
    write_goal(
        goals_dir,
        "genuinely-stuck",
        phase="in_flight",
        no_progress_notified=True,
        last_progress_at="2026-08-12T06:00:00+00:00",
    )
    incidents = NoProgressDetector().scan(goals_dir, now=NOW)
    assert [i.goal_id for i in incidents] == ["genuinely-stuck"]

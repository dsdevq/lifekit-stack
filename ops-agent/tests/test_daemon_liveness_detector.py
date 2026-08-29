"""O5 daemon-liveness detector unit tests (observability-maxout P1).

Coverage axes:
  - probing disabled (None snapshot) → no incidents, no crash
  - unreachable / non-200 / ok:false → one ``daemon-down`` incident
  - fresh heartbeat → no incidents
  - stale ``last_tick_at`` past factor×tick → ``heartbeat-stale``
  - fresh boot (null tick, recent ``started_at``) → grace, no incident
  - old boot with no tick ever → stale (measured from started_at)
  - cycle-report dead-man OFF by default; armed → fires only past threshold
  - re-notify: dedup_key embeds the time bucket so a persisting condition
    re-fires next bucket instead of being silenced for the whole 24h window
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ops_agent.detectors import DaemonLivenessDetector
from ops_agent.health_probe import HealthSnapshot

NOW = datetime(2026, 8, 12, 22, 0, 0, tzinfo=UTC)


def _det(**kw) -> DaemonLivenessDetector:
    return DaemonLivenessDetector(**kw)


def _healthy(**overrides) -> HealthSnapshot:
    base = dict(
        reachable=True,
        ok=True,
        status_code=200,
        started_at=NOW - timedelta(hours=2),
        last_tick_at=NOW - timedelta(minutes=10),
        tick_seconds=900.0,
        last_cycle_report_at=NOW - timedelta(hours=12),
        dispatch_open=True,
    )
    base.update(overrides)
    return HealthSnapshot(**base)


def test_probing_disabled_none_snapshot_is_silent():
    assert _det().scan_health(None, now=NOW) == []


def test_unreachable_daemon_fires_daemon_down():
    snap = HealthSnapshot(reachable=False, error="ConnectError: refused")
    (inc,) = _det().scan_health(snap, now=NOW)
    assert inc.trigger == "O5"
    assert inc.payload["condition"] == "daemon-down"
    assert "refused" in inc.payload["detail"]


def test_non_200_and_ok_false_fire_daemon_down():
    (inc1,) = _det().scan_health(
        HealthSnapshot(reachable=True, ok=False, status_code=500, error="http 500"),
        now=NOW,
    )
    assert inc1.payload["condition"] == "daemon-down"
    (inc2,) = _det().scan_health(_healthy(ok=False), now=NOW)
    assert inc2.payload["condition"] == "daemon-down"


def test_fresh_heartbeat_is_silent():
    assert _det().scan_health(_healthy(), now=NOW) == []


def test_stale_last_tick_fires_heartbeat_stale():
    snap = _healthy(last_tick_at=NOW - timedelta(hours=2))  # 3×900s = 45min max
    (inc,) = _det().scan_health(snap, now=NOW)
    assert inc.payload["condition"] == "heartbeat-stale"
    assert inc.payload["basis"] == "last_tick_at"


def test_fresh_boot_null_tick_gets_grace_from_started_at():
    snap = _healthy(last_tick_at=None, started_at=NOW - timedelta(minutes=5))
    assert _det().scan_health(snap, now=NOW) == []


def test_old_boot_that_never_ticked_is_stale():
    snap = _healthy(last_tick_at=None, started_at=NOW - timedelta(hours=3))
    (inc,) = _det().scan_health(snap, now=NOW)
    assert inc.payload["condition"] == "heartbeat-stale"
    assert "no tick yet" in inc.payload["basis"]


def test_cycle_report_dead_man_off_by_default():
    snap = _healthy(last_cycle_report_at=NOW - timedelta(days=10))
    assert _det().scan_health(snap, now=NOW) == []


def test_cycle_report_dead_man_fires_when_armed_and_overdue():
    det = _det(cycle_report_max_age_h=26.0)
    assert det.scan_health(_healthy(), now=NOW) == []  # 12h old: fine
    snap = _healthy(last_cycle_report_at=NOW - timedelta(hours=30))
    (inc,) = det.scan_health(snap, now=NOW)
    assert inc.payload["condition"] == "cycle-report-overdue"


def test_cycle_report_null_is_unknown_not_overdue():
    det = _det(cycle_report_max_age_h=26.0)
    assert det.scan_health(_healthy(last_cycle_report_at=None), now=NOW) == []


def test_renotify_bucket_rotates_the_dedup_key():
    det = _det(renotify_s=4 * 3600.0)
    down = HealthSnapshot(reachable=False, error="down")
    (a,) = det.scan_health(down, now=NOW)
    (b,) = det.scan_health(down, now=NOW + timedelta(minutes=5))
    (c,) = det.scan_health(down, now=NOW + timedelta(hours=4, minutes=1))
    assert a.payload["dedup_key"] == b.payload["dedup_key"]  # same bucket → deduped
    assert a.payload["dedup_key"] != c.payload["dedup_key"]  # next bucket → re-ping

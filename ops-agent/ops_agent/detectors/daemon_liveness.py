"""O5 — daemon-liveness dead-man detector (observability-maxout P1).

Unlike O1–O4, O5 does not read goal folders: it judges the devclaw DAEMON
itself from a :class:`~ops_agent.health_probe.HealthSnapshot` the tick took.
This is the one condition only a sibling process can see — every other
detector consumes signals devclaw writes, so a dead devclaw reads as calm.

Three conditions, each its own incident kind:

- ``daemon-down``       — /health unreachable, non-200, or ``ok`` false.
- ``heartbeat-stale``   — the process serves HTTP but ``last_tick_at`` is
  older than ``stale_factor × tick_seconds``. A fresh boot (``last_tick_at``
  null) gets the same window measured from ``started_at`` — grace, not a
  blind spot.
- ``cycle-report-overdue`` — ``last_cycle_report_at`` older than
  ``cycle_report_max_age_h``. Opt-in (0 disables): a schedule-less install
  never emits reports and must not false-alarm. A null timestamp is treated
  as unknown (fresh db), not overdue.

Zero-LLM and ping-only by decision (observability-maxout Resolved O1/O2):
O5 incidents route to a mechanical owner notify in ``main.tick`` — never to
a playbook, never to docker_restart (that is a named follow-up gated on O5
having correct detections on record).

Re-notify: the dedup fingerprint embeds a coarse time bucket
(``renotify_s``), so a persisting condition re-pings once per bucket instead
of once per 24h dedup window — a dead daemon at 23:00 must not stay silent
until tomorrow night.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..health_probe import HealthSnapshot
from ..incident import Incident

_DEFAULT_TICK_S = 900.0


class DaemonLivenessDetector:
    trigger = "O5"

    def __init__(
        self,
        *,
        stale_factor: float = 3.0,
        renotify_s: float = 4 * 3600.0,
        cycle_report_max_age_h: float = 0.0,
    ) -> None:
        self._stale_factor = stale_factor
        self._renotify_s = max(renotify_s, 60.0)
        self._cycle_report_max_age_h = cycle_report_max_age_h

    def _incident(
        self, condition: str, detail: str, *, now: datetime, extra: dict[str, Any] | None = None
    ) -> Incident:
        bucket = int(now.timestamp() // self._renotify_s)
        payload: dict[str, Any] = {
            "condition": condition,
            "detail": detail,
            "dedup_key": f"{condition}|bucket={bucket}",
            **(extra or {}),
        }
        return Incident(
            trigger=self.trigger, goal_id="devclaw-daemon", detected_at=now, payload=payload
        )

    def scan_health(self, health: HealthSnapshot | None, *, now: datetime) -> list[Incident]:
        """Judge one snapshot. ``None`` (probing disabled/unconfigured) → []."""
        if health is None:
            return []

        if not health.reachable or not health.ok:
            reason = health.error or (
                f"http {health.status_code}" if health.status_code else "unreachable"
            )
            return [
                self._incident(
                    "daemon-down",
                    f"devclaw /health is not answering ok ({reason})",
                    now=now,
                    extra={"probe_error": health.error, "status_code": health.status_code},
                )
            ]

        incidents: list[Incident] = []

        interval = health.tick_seconds or _DEFAULT_TICK_S
        window_s = self._stale_factor * interval
        ref = health.last_tick_at or health.started_at
        if ref is not None:
            age_s = (now - ref).total_seconds()
            if age_s > window_s:
                basis = "last_tick_at" if health.last_tick_at else "started_at (no tick yet)"
                incidents.append(
                    self._incident(
                        "heartbeat-stale",
                        (
                            f"devclaw serves HTTP but the goal loop hasn't completed a pass "
                            f"in {age_s / 3600.0:.1f}h ({basis}; threshold "
                            f"{window_s / 3600.0:.1f}h = {self._stale_factor:g}×tick)"
                        ),
                        now=now,
                        extra={"age_s": age_s, "threshold_s": window_s, "basis": basis},
                    )
                )

        if self._cycle_report_max_age_h > 0 and health.last_cycle_report_at is not None:
            age_h = (now - health.last_cycle_report_at).total_seconds() / 3600.0
            if age_h > self._cycle_report_max_age_h:
                incidents.append(
                    self._incident(
                        "cycle-report-overdue",
                        (
                            f"no cycle report for {age_h:.1f}h "
                            f"(threshold {self._cycle_report_max_age_h:g}h) — the nightly "
                            f"window either never ran or its report edge is broken"
                        ),
                        now=now,
                        extra={"age_h": age_h, "threshold_h": self._cycle_report_max_age_h},
                    )
                )

        return incidents

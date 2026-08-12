"""Probe devclaw's token-free ``/health`` surface.

The one read that makes ops-agent able to see what NO goal-folder read can:
whether the devclaw daemon itself is alive and its heartbeat loop is actually
ticking. Devclaw's ``/health`` (feat/health-freshness, devclaw #494) carries
``last_tick_at`` (stamped only on a completed tick pass), ``started_at``,
``tick_seconds``, ``last_cycle_report_at``, and ``dispatch_open`` — everything
O5 and the O3 held≠stalled suppression need, over the only route that needs
no token.

Defensive by construction: any transport error, non-200, or unparseable body
degrades to a typed snapshot (``reachable=False`` / ``ok=False``) — the probe
NEVER raises into the polling loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx


@dataclass(frozen=True)
class HealthSnapshot:
    """What one probe of ``/health`` saw, parsed and typed."""

    reachable: bool
    ok: bool = False
    status_code: int | None = None
    started_at: datetime | None = None
    last_tick_at: datetime | None = None
    tick_seconds: float | None = None
    last_cycle_report_at: datetime | None = None
    #: None = the field is absent (a devclaw older than #494) — callers must
    #: treat that as unknown, not as "open" or "held".
    dispatch_open: bool | None = None
    dispatch_hold_reason: str | None = None
    git_sha: str | None = None
    error: str | None = None


def _parse_iso(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _parse_float(raw: Any) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def probe_health(url: str, *, timeout_s: float = 5.0) -> HealthSnapshot:
    """One GET against devclaw's ``/health``; never raises."""
    try:
        resp = httpx.get(url, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 — a dead daemon IS the signal
        return HealthSnapshot(reachable=False, error=f"{type(exc).__name__}: {exc}")
    if resp.status_code != 200:
        return HealthSnapshot(
            reachable=True,
            ok=False,
            status_code=resp.status_code,
            error=f"http {resp.status_code}",
        )
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — garbage body = unhealthy, typed
        return HealthSnapshot(
            reachable=True, ok=False, status_code=200, error="unparseable body"
        )
    if not isinstance(body, dict):
        return HealthSnapshot(
            reachable=True, ok=False, status_code=200, error="non-object body"
        )
    dispatch_open = body.get("dispatch_open")
    return HealthSnapshot(
        reachable=True,
        ok=bool(body.get("ok", False)),
        status_code=200,
        started_at=_parse_iso(body.get("started_at")),
        last_tick_at=_parse_iso(body.get("last_tick_at")),
        tick_seconds=_parse_float(body.get("tick_seconds")),
        last_cycle_report_at=_parse_iso(body.get("last_cycle_report_at")),
        dispatch_open=dispatch_open if isinstance(dispatch_open, bool) else None,
        dispatch_hold_reason=body.get("dispatch_hold_reason") or None,
        git_sha=body.get("git_sha") or None,
        error=None,
    )

"""Resident daemon entrypoint.

One process, one asyncio loop. Wakes every ``poll_interval_s``, asks each
detector for incidents, persists what's not deduped, and sleeps. No HTTP,
no MCP, no Claude — those arrive in ops-PR2+.

Exit shape: SIGINT/SIGTERM cancels the loop; the process exits 0. The
systemd unit + compose ``restart: on-failure:5`` policy handles real
failures.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from datetime import UTC, datetime

from .config import OpsConfig, load_config
from .detectors import NoProgressDetector
from .incident import IncidentStore

_log = logging.getLogger("ops_agent")


def _utcnow() -> datetime:
    # Kept as a module-level shim so tests can monkeypatch a deterministic clock
    # without dragging asyncio into the picture.
    return datetime.now(UTC)


async def tick(cfg: OpsConfig, store: IncidentStore, detector: NoProgressDetector) -> int:
    """One scan pass. Returns the number of incidents written.

    Broken out from ``run_loop`` so it's directly callable from tests without
    standing up the asyncio scheduler.
    """
    now = _utcnow()
    written = 0
    for incident in detector.scan(cfg.goals_dir, now=now):
        if store.is_deduped(incident, now=now):
            continue
        folder = store.write(incident)
        _log.info(
            "incident written trigger=%s goal=%s folder=%s",
            incident.trigger,
            incident.goal_id,
            folder,
        )
        written += 1
    return written


async def run_loop(cfg: OpsConfig, stop: asyncio.Event) -> None:
    store = IncidentStore(cfg.incidents_dir, dedup_window_s=cfg.dedup_window_s)
    detector = NoProgressDetector()
    _log.info(
        "ops-agent starting goals_dir=%s incidents_dir=%s poll=%.1fs",
        cfg.goals_dir,
        cfg.incidents_dir,
        cfg.poll_interval_s,
    )
    while not stop.is_set():
        try:
            await tick(cfg, store, detector)
        except Exception:
            # Catch-and-log so a transient FS hiccup doesn't kill the daemon.
            # Real defects surface in the log; the on-failure:5 policy bounds
            # systemic problems.
            _log.exception("tick failed; sleeping and retrying")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=cfg.poll_interval_s)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    def _request_stop() -> None:
        _log.info("shutdown signal received")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows / restricted envs — fall back to the default KeyboardInterrupt path.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)


def run() -> None:
    """Console-script entrypoint."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = load_config()
    cfg.incidents_dir.mkdir(parents=True, exist_ok=True)

    async def _main() -> None:
        stop = asyncio.Event()
        _install_signal_handlers(asyncio.get_running_loop(), stop)
        await run_loop(cfg, stop)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    run()

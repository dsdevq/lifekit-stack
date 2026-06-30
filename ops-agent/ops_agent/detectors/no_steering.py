"""O2 — no-steering watchdog detector.

Companion to O1 (:mod:`.no_progress`). Where O1 catches goals that have
gone *stuck* (devclaw flips ``no_progress_notified=True`` in STATUS.md),
O2 catches goals that are still *executing* but haven't received any
steering signal — from Denys or from ops-eval — in a long time. The
hypothesis: a goal that's been grinding for >24h with no human or
ops-eval correction may have drifted into the weeds with nobody noticing.

Signal source
-------------
STATUS.md frontmatter doesn't expose a ``last_steering_at`` field, so we
use ``inbox.md``'s filesystem mtime as a proxy:

  - ``inbox.md`` is append-only inside devclaw's goal layer (every
    Denys correction and every ops-eval correction lands as a new entry).
  - Its mtime therefore advances *whenever* a steering signal arrives.
  - The mtime is monotonically nondecreasing relative to steering events,
    which is the property the watchdog needs.

If ``inbox.md`` is missing we treat the goal as "never received steering"
and fall back to STATUS.md's ``created_at`` if available; if neither
exists we skip the goal (nothing useful to compare against).

Firing conditions
-----------------
The detector fires on a goal iff:

  - phase == ``executing`` (idle / paused / done goals don't need
    steering — see design doc).
  - (now - last_steering_at) exceeds the configurable threshold
    (default 24h via ``OPS_AGENT_NO_STEERING_HOURS``).

Dedup is keyed on the ISO-formatted inbox mtime. When new steering
arrives the mtime moves, the dedup key changes, and the next polling
cycle is free to re-fire (mirrors O1's ``last_progress_at`` strategy).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ..incident import Incident

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# Default no-steering threshold — 24h. Overridable via env so a fast-feedback
# config (e.g. a debugging session) doesn't require code changes.
_DEFAULT_THRESHOLD_HOURS = 24.0


def _threshold_seconds() -> float:
    """Read the configured threshold from env, with a 24h default.

    Kept module-level (not class state) so tests can monkeypatch
    ``OPS_AGENT_NO_STEERING_HOURS`` cleanly without poking detector
    internals. Negative or unparseable values fall back to the default
    rather than raising — the daemon must keep running on a misconfigured
    env var.
    """
    raw = os.environ.get("OPS_AGENT_NO_STEERING_HOURS")
    if raw is None or raw.strip() == "":
        return _DEFAULT_THRESHOLD_HOURS * 3600.0
    try:
        hours = float(raw)
    except ValueError:
        return _DEFAULT_THRESHOLD_HOURS * 3600.0
    if hours <= 0:
        return _DEFAULT_THRESHOLD_HOURS * 3600.0
    return hours * 3600.0


@dataclass(frozen=True)
class SteeringSnapshot:
    """Slice of a goal relevant to O2.

    Kept narrow on purpose — see the GoalSnapshot docstring in
    ``no_progress.py`` for the boundary-discipline reasoning.

    ``last_steering_at`` is a UTC ``datetime`` so the detector can do
    arithmetic without re-parsing strings.
    """

    goal_id: str
    objective: str
    phase: str
    last_steering_at: datetime | None
    last_steering_source: str  # "inbox_mtime" | "created_at" | "none"


def _read_frontmatter(text: str) -> dict[str, Any]:
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    parsed = yaml.safe_load(m.group(1))
    return parsed if isinstance(parsed, dict) else {}


def _read_goal_objective(goal_dir: Path) -> str:
    goal_yaml = goal_dir / "goal.yaml"
    if not goal_yaml.is_file():
        return ""
    try:
        raw = yaml.safe_load(goal_yaml.read_text()) or {}
    except yaml.YAMLError:
        return ""
    return str(raw.get("objective", "")).strip()


def _parse_iso_to_utc(value: Any) -> datetime | None:
    """Best-effort parse of a frontmatter timestamp into a UTC datetime.

    Devclaw writes ISO-8601 with offset; defensive against missing offset
    by assuming UTC. Returns ``None`` on anything that doesn't parse.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def read_snapshot(goal_dir: Path) -> SteeringSnapshot | None:
    """Load just the bits we need from one goal folder for O2.

    Returns ``None`` for malformed goals (no goal.yaml, no STATUS.md, or
    no usable steering timestamp at all) — same skip-not-crash discipline
    as :func:`no_progress.read_snapshot`.
    """
    if not (goal_dir / "goal.yaml").is_file():
        return None
    status_path = goal_dir / "STATUS.md"
    if not status_path.is_file():
        return None
    fm = _read_frontmatter(status_path.read_text())
    phase = str(fm.get("phase", "idle"))

    inbox = goal_dir / "inbox.md"
    last_steering_at: datetime | None = None
    source = "none"
    if inbox.is_file():
        try:
            mtime = inbox.stat().st_mtime
        except OSError:
            mtime = None
        if mtime is not None:
            last_steering_at = datetime.fromtimestamp(mtime, tz=UTC)
            source = "inbox_mtime"
    if last_steering_at is None:
        created = _parse_iso_to_utc(fm.get("created_at"))
        if created is not None:
            last_steering_at = created
            source = "created_at"

    if last_steering_at is None:
        return None

    return SteeringSnapshot(
        goal_id=goal_dir.name,
        objective=_read_goal_objective(goal_dir),
        phase=phase,
        last_steering_at=last_steering_at,
        last_steering_source=source,
    )


def _iter_goal_dirs(goals_dir: Path):
    """Yield real goal subdirs, mirroring :func:`no_progress.iter_goal_dirs`.

    Kept private (single-underscore prefix) — the public detector method
    is the surface other modules talk to.
    """
    if not goals_dir.exists():
        return []
    return (p for p in sorted(goals_dir.iterdir()) if p.is_dir() and not p.name.startswith("."))


class NoSteeringDetector:
    """Stateless O2 detector — all state lives in the IncidentStore.

    Threshold is read from the environment on every scan so a live config
    change takes effect on the next polling cycle without a daemon restart.
    """

    trigger = "O2"

    def scan(self, goals_dir: Path, *, now: datetime) -> list[Incident]:
        """Return one incident per executing goal whose last steering is too old.

        Only goals in ``phase == "executing"`` are considered — idle /
        paused / done goals don't need steering by definition.
        """
        threshold_s = _threshold_seconds()
        incidents: list[Incident] = []
        for goal_dir in _iter_goal_dirs(goals_dir):
            snap = read_snapshot(goal_dir)
            if snap is None:
                continue
            if snap.phase != "executing":
                continue
            assert snap.last_steering_at is not None  # guaranteed by read_snapshot
            age_s = (now - snap.last_steering_at).total_seconds()
            if age_s < threshold_s:
                continue
            inbox_iso = snap.last_steering_at.isoformat(timespec="seconds")
            payload: dict[str, Any] = {
                "objective": snap.objective,
                "phase": snap.phase,
                "last_steering_at": inbox_iso,
                "last_steering_source": snap.last_steering_source,
                "threshold_hours": threshold_s / 3600.0,
                "age_hours": age_s / 3600.0,
                # dedup_key: pin to the inbox mtime so a new steering signal
                # (mtime advances) cleanly resets the dedup window. Matches
                # O1's "fingerprint the underlying-state field" pattern.
                "dedup_key": f"inbox_mtime={inbox_iso}",
            }
            incidents.append(
                Incident(
                    trigger=self.trigger,
                    goal_id=snap.goal_id,
                    detected_at=now,
                    payload=payload,
                )
            )
        return incidents

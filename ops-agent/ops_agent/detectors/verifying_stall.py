"""O3 — verifying-stall watchdog detector.

Third member of the detector family (after O1 :mod:`.no_progress` and O2
:mod:`.no_steering`). O3 fires on the *specific* failure mode that
motivated ops-PR4: the closeloop incident, where a devclaw goal sat in
``phase=verifying`` for 10+ hours because the done-gate evaluator was
truncating output and the goal never re-dispatched.

Distinct from O1
----------------
O1 watches devclaw's own ``no_progress_notified`` flag — it tells the ops
loop "devclaw thinks this is stuck." O3 layers a *different* policy on top:
even when devclaw hasn't flagged the goal, if it's been sitting in a
*transient* phase (``verifying`` or ``in_flight``) past the threshold,
something downstream is wedged (the done-gate, the goal-firmer, or
devclaw-mcp itself). The remediation menu the playbook surfaces is
correspondingly different — see :mod:`..playbooks.verifying_stall_unblock`.

Signal source
-------------
STATUS.md frontmatter's ``last_progress_at`` — same field O1 uses, but
gated on phase rather than the ``no_progress_notified`` flag. Goals
missing the field are skipped (nothing useful to compare against).

Firing conditions
-----------------
The detector fires on a goal iff:

  - ``phase`` is in the module-level :data:`_STALL_PHASES` allowlist
    (``verifying`` or ``in_flight``). Adding a phase here is the surface
    update if devclaw later renames or splits a transient phase.
  - (now - last_progress_at) exceeds the configurable threshold
    (default 4h via ``OPS_AGENT_VERIFYING_STALL_HOURS``).

Dedup is keyed on (phase, last_progress_at). When the goal moves out of
the stall phase OR makes progress, the fingerprint shifts and the dedup
window from the prior incident no longer suppresses.
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

# Phases the watchdog treats as "should be transient — staying here past
# the threshold is a stall." Kept module-level so a phase rename in devclaw
# becomes a one-line surface update here.
_STALL_PHASES: frozenset[str] = frozenset({"verifying", "in_flight"})

# Default verifying-stall threshold — 4h. Overridable via env so a faster
# feedback config (e.g. a debugging session) doesn't require code changes.
_DEFAULT_THRESHOLD_HOURS = 4.0


def _threshold_seconds() -> float:
    """Read the configured threshold from env, with a 4h default.

    Module-level (not class state) so tests can monkeypatch
    ``OPS_AGENT_VERIFYING_STALL_HOURS`` cleanly. Negative or unparseable
    values fall back to the default rather than raising — the daemon must
    keep running on a misconfigured env var.
    """
    raw = os.environ.get("OPS_AGENT_VERIFYING_STALL_HOURS")
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
class StallSnapshot:
    """Slice of a goal relevant to O3.

    Narrow on purpose — same boundary-discipline reasoning as the O1/O2
    snapshot dataclasses.
    """

    goal_id: str
    objective: str
    phase: str
    last_progress_at: datetime | None
    last_progress_at_raw: str | None


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


def read_snapshot(goal_dir: Path) -> StallSnapshot | None:
    """Load just the bits we need from one goal folder for O3.

    Returns ``None`` for malformed goals (no goal.yaml, no STATUS.md, no
    usable ``last_progress_at`` at all) — same skip-not-crash discipline
    as :func:`no_progress.read_snapshot`.
    """
    if not (goal_dir / "goal.yaml").is_file():
        return None
    status_path = goal_dir / "STATUS.md"
    if not status_path.is_file():
        return None
    fm = _read_frontmatter(status_path.read_text())
    phase = str(fm.get("phase", "idle"))
    raw_last = fm.get("last_progress_at")
    last_progress_at = _parse_iso_to_utc(raw_last)
    return StallSnapshot(
        goal_id=goal_dir.name,
        objective=_read_goal_objective(goal_dir),
        phase=phase,
        last_progress_at=last_progress_at,
        last_progress_at_raw=raw_last if isinstance(raw_last, str) else None,
    )


def _iter_goal_dirs(goals_dir: Path):
    """Yield real goal subdirs, mirroring :func:`no_progress.iter_goal_dirs`."""
    if not goals_dir.exists():
        return []
    return (p for p in sorted(goals_dir.iterdir()) if p.is_dir() and not p.name.startswith("."))


class VerifyingStallDetector:
    """Stateless O3 detector — all state lives in the IncidentStore.

    Threshold is read from the environment on every scan so a live config
    change takes effect on the next polling cycle without a daemon restart.
    """

    trigger = "O3"

    def scan(self, goals_dir: Path, *, now: datetime) -> list[Incident]:
        """Return one incident per stall-phase goal that's exceeded the threshold.

        Only goals in :data:`_STALL_PHASES` are considered — terminal /
        idle phases don't stall by definition. Goals missing
        ``last_progress_at`` are skipped (nothing useful to compare).
        """
        threshold_s = _threshold_seconds()
        incidents: list[Incident] = []
        for goal_dir in _iter_goal_dirs(goals_dir):
            snap = read_snapshot(goal_dir)
            if snap is None:
                continue
            if snap.phase not in _STALL_PHASES:
                continue
            if snap.last_progress_at is None:
                continue
            age_s = (now - snap.last_progress_at).total_seconds()
            if age_s < threshold_s:
                continue
            last_progress_iso = snap.last_progress_at.isoformat(timespec="seconds")
            payload: dict[str, Any] = {
                "objective": snap.objective,
                "phase": snap.phase,
                "last_progress_at": last_progress_iso,
                "threshold_hours": threshold_s / 3600.0,
                "age_hours": age_s / 3600.0,
                # dedup_key: pin to (phase, last_progress_at). A phase
                # transition out of the stall — or any new progress — shifts
                # the fingerprint and naturally resets the dedup window.
                "dedup_key": f"phase={snap.phase}|last_progress_at={last_progress_iso}",
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

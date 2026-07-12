"""O1 — no-progress watchdog detector.

Devclaw's goal layer already flips ``no_progress_notified=True`` in a goal's
``STATUS.md`` frontmatter when the per-goal wall-clock timer (default 6h)
expires without progress. Our job is the simplest possible thing: poll every
goal directory, read that flag, and emit an incident the first time we see
it inside the dedup window.

We deliberately do NOT replicate devclaw's no-progress threshold logic here
— that would couple two processes to the same constant and invite drift.
Devclaw owns "is this goal stuck"; we own "this stuck-ness should reach the
owner via the ops loop."
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..incident import Incident

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# A goal parked in this phase is owned by O5 (blocked-goal question-answerer),
# not O1: re-evaluating a blocked goal just re-blocks it with the same
# question (the failure that pinged the owner at 2am on 2026-07-11). O1 skips
# it so the two triggers don't both fire on one blocked goal.
_BLOCKED_PHASE = "blocked"


@dataclass(frozen=True)
class GoalSnapshot:
    """The slice of a goal we care about for O1 detection.

    Kept narrower than devclaw's full ``GoalStatus`` on purpose — the ops
    agent reads from disk through a tiny stable surface so we don't import
    devclaw at all (boundary discipline).
    """

    goal_id: str
    objective: str
    phase: str
    no_progress_notified: bool
    last_progress_at: str | None
    last_tick_at: str | None


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


def read_snapshot(goal_dir: Path) -> GoalSnapshot | None:
    """Load just the bits we need from one goal folder.

    Returns ``None`` for malformed goals (no goal.yaml, no STATUS.md, etc.) —
    we'd rather skip a half-written goal than crash the daemon polling loop.
    """
    if not (goal_dir / "goal.yaml").is_file():
        return None
    status_path = goal_dir / "STATUS.md"
    if not status_path.is_file():
        return None
    fm = _read_frontmatter(status_path.read_text())
    return GoalSnapshot(
        goal_id=goal_dir.name,
        objective=_read_goal_objective(goal_dir),
        phase=str(fm.get("phase", "idle")),
        no_progress_notified=bool(fm.get("no_progress_notified", False)),
        last_progress_at=fm.get("last_progress_at") or None,
        last_tick_at=fm.get("last_tick_at") or None,
    )


def iter_goal_dirs(goals_dir: Path) -> Iterable[Path]:
    """All goal subdirectories that look real.

    Hidden dirs (``.seen`` etc.) skipped — keeps the goal store and the
    incident store safely co-locatable in tests.
    """
    if not goals_dir.exists():
        return []
    return (p for p in sorted(goals_dir.iterdir()) if p.is_dir() and not p.name.startswith("."))


class NoProgressDetector:
    """Stateless detector — all state lives in the IncidentStore."""

    trigger = "O1"

    def scan(self, goals_dir: Path, *, now: datetime) -> list[Incident]:
        """Return one incident per goal whose ``no_progress_notified`` is set.

        Dedup is the caller's job (``IncidentStore.is_deduped``) — keeps this
        method pure so it's trivially testable.
        """
        incidents: list[Incident] = []
        for goal_dir in iter_goal_dirs(goals_dir):
            snap = read_snapshot(goal_dir)
            if snap is None or not snap.no_progress_notified:
                continue
            # Blocked goals belong to O5, not O1 — see _BLOCKED_PHASE.
            if snap.phase == _BLOCKED_PHASE:
                continue
            payload: dict[str, Any] = {
                "objective": snap.objective,
                "phase": snap.phase,
                "last_progress_at": snap.last_progress_at,
                "last_tick_at": snap.last_tick_at,
                # dedup_key: hash on a flag that changes when the stuck-state
                # is "freshly stuck again." Tying it to last_progress_at means
                # an underlying make-progress + re-stick cycle DOES re-fire,
                # while a goal sitting stuck forever stays one incident
                # (within the window).
                "dedup_key": f"no_progress_notified={snap.no_progress_notified}"
                f"|last_progress_at={snap.last_progress_at or ''}",
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

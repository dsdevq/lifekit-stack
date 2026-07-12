"""O5 — blocked-goal question-answerer trigger.

The failure this closes (confirmed live 2026-07-11): a devclaw goal goes
``phase=blocked`` with a concrete ``blocked_on`` question the evaluator or
tick raised — e.g. *"is the frontend package-lock.json out of sync on main,
OR is the sandbox npm/node version mismatched? — pick which to chase"* — and
the ops-agent's existing triggers can't ANSWER it. O1 (no-progress) fires on
the same goal but only re-evaluates direction, which for a blocked goal just
re-blocks with the same question; the goal stays wedged and pings the owner
at 2am.

Where the signal lives
-----------------------
When devclaw blocks a goal it writes the STATUS.md frontmatter:

    phase:      blocked
    lifecycle:  executing        (or "firming" for a firming-blocked goal)
    blocked_on: <the question / the real error / the reason>

``blocked_on`` is the load-bearing field — it's the exact string devclaw's
tick/evaluator put the question in (see devclaw/goal/tick.py's
``phase="blocked", blocked_on=q`` on a ``needs_human``/``stalled`` verdict).

Firing conditions
-----------------
The detector fires on a goal iff:

  - ``phase == "blocked"``, AND
  - ``blocked_on`` is a non-empty string.

The detector deliberately does NOT try to classify answerable-by-ops vs
must-escalate — that's a cognition concern the playbook owns (boundary
discipline: the detector layer stays dumb + cheap). Every blocked goal with
a question becomes one incident; the playbook then decides answer vs escalate.

Dedup is keyed on the ``blocked_on`` text. A NEW blocking question (the goal
got unblocked, ran, and re-blocked on something else) shifts the fingerprint
and re-fires; the SAME question sitting blocked stays one incident inside the
window.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..incident import Incident

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# The phase devclaw parks a goal in when it raises a question it can't answer
# itself. Module-level so a devclaw phase rename is a one-line surface update.
_BLOCKED_PHASE = "blocked"


@dataclass(frozen=True)
class BlockedSnapshot:
    """Slice of a goal relevant to O5.

    Narrow on purpose — same boundary-discipline reasoning as the O1/O2/O3
    snapshot dataclasses. ``workspace_dir`` is carried so the playbook /
    daemon can resolve the goal's repo checkout for evidence-gathering.
    """

    goal_id: str
    objective: str
    phase: str
    lifecycle: str | None
    blocked_on: str
    last_eval_verdict: str | None
    last_eval_note: str
    workspace_dir: str


def _read_frontmatter(text: str) -> dict[str, Any]:
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    parsed = yaml.safe_load(m.group(1))
    return parsed if isinstance(parsed, dict) else {}


def _read_goal_yaml(goal_dir: Path) -> dict[str, Any]:
    goal_yaml = goal_dir / "goal.yaml"
    if not goal_yaml.is_file():
        return {}
    try:
        raw = yaml.safe_load(goal_yaml.read_text()) or {}
    except yaml.YAMLError:
        return {}
    return raw if isinstance(raw, dict) else {}


def read_snapshot(goal_dir: Path) -> BlockedSnapshot | None:
    """Load just the bits we need from one goal folder for O5.

    Returns ``None`` for malformed goals (no goal.yaml, no STATUS.md) or for
    goals that aren't blocked-with-a-question — same skip-not-crash discipline
    as the other detectors.
    """
    if not (goal_dir / "goal.yaml").is_file():
        return None
    status_path = goal_dir / "STATUS.md"
    if not status_path.is_file():
        return None
    fm = _read_frontmatter(status_path.read_text())
    phase = str(fm.get("phase", "idle"))
    blocked_on = str(fm.get("blocked_on") or "").strip()
    gy = _read_goal_yaml(goal_dir)
    return BlockedSnapshot(
        goal_id=goal_dir.name,
        objective=str(gy.get("objective", "")).strip(),
        phase=phase,
        lifecycle=str(fm.get("lifecycle") or "") or None,
        blocked_on=blocked_on,
        last_eval_verdict=str(fm.get("last_eval_verdict") or "") or None,
        last_eval_note=str(fm.get("last_eval_note") or "").strip(),
        workspace_dir=str(gy.get("workspace_dir", "")).strip(),
    )


def _iter_goal_dirs(goals_dir: Path) -> Iterable[Path]:
    """Yield real goal subdirs, mirroring :func:`no_progress.iter_goal_dirs`."""
    if not goals_dir.exists():
        return []
    return (p for p in sorted(goals_dir.iterdir()) if p.is_dir() and not p.name.startswith("."))


class BlockedNeedsAnswerDetector:
    """Stateless O5 detector — all state lives in the IncidentStore.

    Fires one incident per goal parked in ``phase == "blocked"`` with a
    non-empty ``blocked_on`` question.
    """

    trigger = "O5"

    def scan(self, goals_dir: Path, *, now: datetime) -> list[Incident]:
        incidents: list[Incident] = []
        for goal_dir in _iter_goal_dirs(goals_dir):
            snap = read_snapshot(goal_dir)
            if snap is None:
                continue
            if snap.phase != _BLOCKED_PHASE:
                continue
            if not snap.blocked_on:
                continue
            # dedup on the question text so a re-block on a DIFFERENT question
            # re-fires while the same standing question stays one incident.
            q_hash = hashlib.sha256(snap.blocked_on.encode("utf-8")).hexdigest()[:16]
            payload: dict[str, Any] = {
                "objective": snap.objective,
                "phase": snap.phase,
                "lifecycle": snap.lifecycle,
                "blocked_on": snap.blocked_on,
                "last_eval_verdict": snap.last_eval_verdict,
                "last_eval_note": snap.last_eval_note,
                "workspace_dir": snap.workspace_dir,
                "dedup_key": f"blocked_on={q_hash}",
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

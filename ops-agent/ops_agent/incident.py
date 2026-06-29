"""Incident dataclass + on-disk writer.

Layout per the design contract:

    <incidents_dir>/
    ├── log.md                            one-line summary, append-only
    ├── .seen/<goal_id>-<status_hash>.touch    dedup marker (24h window default)
    └── <ts>-<trigger>-<goal_id>/
        ├── trigger.json                  full detection payload
        └── outcome.md                    L0 baseline note (ops-PR1)

The trigger folder is the durable record; .seen is the cheap re-fire guard
(an empty marker file whose mtime is the only signal). Keeping them separate
means a corrupt marker never destroys an incident's evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# ISO-ish timestamp safe for folder names. We replace ':' and '+' so the
# folder works on every filesystem that has ever spoken FAT.
_TS_UNSAFE = re.compile(r"[:+]")


def _ts_for_folder(now: datetime) -> str:
    return _TS_UNSAFE.sub("-", now.isoformat(timespec="seconds"))


@dataclass(frozen=True)
class Incident:
    """A single detected incident, ready to persist.

    ``payload`` is detector-specific — for O1 it includes the goal status
    fingerprint we hashed for dedup, plus the goal-yaml objective + phase.
    """

    trigger: str  # e.g. "O1"
    goal_id: str
    detected_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)

    def folder_name(self) -> str:
        return f"{_ts_for_folder(self.detected_at)}-{self.trigger}-{self.goal_id}"

    def fingerprint(self) -> str:
        """Stable hash over (trigger, goal_id, payload's dedup-relevant fields).

        We hash the JSON-canonical form so a re-run with the same status shape
        produces the same key. Detectors put dedup-relevant state under
        ``payload['dedup_key']`` so we don't accidentally key on something
        volatile (timestamps, counters).
        """
        dedup_key = self.payload.get("dedup_key", "")
        material = f"{self.trigger}|{self.goal_id}|{dedup_key}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


class IncidentStore:
    """Filesystem-backed incident writer with dedup."""

    def __init__(self, root: Path, *, dedup_window_s: float):
        self._root = root
        self._dedup_window_s = dedup_window_s

    def _seen_dir(self) -> Path:
        return self._root / ".seen"

    def _marker_path(self, incident: Incident) -> Path:
        return self._seen_dir() / f"{incident.goal_id}-{incident.fingerprint()}.touch"

    def is_deduped(self, incident: Incident, *, now: datetime) -> bool:
        """True if a fresh marker exists for this incident shape.

        Older markers fall outside the window and DON'T suppress — they're
        garbage we leave alone (no GC in PR1; cheap to add later if it ever
        matters, which on a small VPS it won't for years).
        """
        marker = self._marker_path(incident)
        if not marker.exists():
            return False
        age_s = now.timestamp() - marker.stat().st_mtime
        return age_s < self._dedup_window_s

    def write(self, incident: Incident) -> Path:
        """Persist the incident folder + dedup marker + log entry.

        Returns the incident folder path. Idempotent re-write inside the same
        second (unlikely; if it ever happens we'd overwrite — acceptable for
        L0 baseline since there's no downstream action being interrupted).
        """
        self._root.mkdir(parents=True, exist_ok=True)
        self._seen_dir().mkdir(parents=True, exist_ok=True)

        folder = self._root / incident.folder_name()
        folder.mkdir(parents=True, exist_ok=True)

        trigger_payload = {
            "trigger": incident.trigger,
            "goal_id": incident.goal_id,
            "detected_at": incident.detected_at.isoformat(),
            "fingerprint": incident.fingerprint(),
            "payload": incident.payload,
        }
        (folder / "trigger.json").write_text(
            json.dumps(trigger_payload, indent=2, sort_keys=True, default=str) + "\n"
        )
        (folder / "outcome.md").write_text(
            f"# {incident.folder_name()} — outcome\n\n"
            "L0 — no action taken (ops-PR1 baseline). The agent only detects + records "
            "incidents at this PR; auto-poke, steer-injection, and devclaw fix-PRs land "
            "in ops-PR2..ops-PR4. See ~/memory/projects/devclaw/plan.md "
            "('Operations agent' section) for the escalation ladder.\n"
        )

        marker = self._marker_path(incident)
        marker.write_text("")
        # touch — explicit mtime so tests with an injected clock are deterministic.
        os.utime(marker, (incident.detected_at.timestamp(), incident.detected_at.timestamp()))

        self._append_log(incident)
        return folder

    def _append_log(self, incident: Incident) -> None:
        log_path = self._root / "log.md"
        if not log_path.exists():
            log_path.write_text("# ops-agent — incident log\n\n")
        objective = incident.payload.get("objective", "")
        # Keep one-liner tight; truncate the objective so a chatty goal can't
        # blow the line out.
        if len(objective) > 80:
            objective = objective[:77] + "..."
        ts = incident.detected_at.isoformat(timespec="seconds")
        line = f"- [{ts}] {incident.trigger} — {incident.goal_id}: {objective}\n"
        with log_path.open("a") as fh:
            fh.write(line)


def to_dict(incident: Incident) -> dict[str, Any]:
    """Helper for tests + log emission. Datetime → isoformat for JSON."""
    d = asdict(incident)
    d["detected_at"] = incident.detected_at.isoformat()
    return d

"""O4 — trend-detector self-escalation consumer.

Devclaw's trend detector writes retrospective entries to
``<workspace>/.devclaw/trends.md`` (one entry per signal firing, dated). When
the SAME signal fires ≥N consecutive daily runs, that's the detector telling
us "this observation isn't going away — the pattern has legs; ops should look."
Field-validated 2026-07-02 (audit): the R2 signal self-escalated on day 3
across four closeloop-family goals; this detector wires that signal into the
ops loop rather than leaving it for a human to notice.

Boundary discipline (same as the other O detectors): we don't import devclaw
and we don't touch the trend detector itself. We only READ trends.md from the
mounted workspaces dir. When the trend detector improves its output format,
this consumer stays put.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from ..incident import Incident

#: Regex matching one trends.md header line:
#: ``## [2026-07-03] R2 — recurrence``. Captures date, signal_id, category.
_TREND_HEADER = re.compile(
    r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s+([A-Za-z0-9_.-]+)\s+—\s+(.+?)\s*$",
    re.MULTILINE,
)

#: Regex matching the "**Proposed action:** ..." line in an entry body — the
#: detector's own suggested fix. We surface it to the playbook so cognition has
#: the retrospective's proposed remediation, not just "signal X keeps firing."
_PROPOSED_ACTION = re.compile(
    r"^\*\*Proposed action:\*\*\s*(.+?)\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class TrendEntry:
    """One parsed entry from a trends.md file."""

    date: date
    signal_id: str
    category: str
    #: The section body between this header and the next ``---`` or next header.
    body: str
    #: The ``**Proposed action:**`` line's payload, or empty if the entry
    #: reported none. The trend detector emits ``_(none — pattern noted…)_``
    #: for entries with no recommended action; we normalize that to "".
    proposed_action: str


@dataclass(frozen=True)
class TrendSignalStreak:
    """One signal's longest consecutive-daily streak inside a trends.md."""

    signal_id: str
    category: str
    first_fired: date
    latest_fired: date
    repeat_count: int
    latest_proposed_action: str


def _parse_trend_entries(text: str) -> list[TrendEntry]:
    """Parse the entries out of one trends.md file.

    The file's header preamble (## title + intro prose) is ignored. Each
    ``## [YYYY-MM-DD] <signal> — <cat>`` starts an entry; the body runs until
    the next ``## [``-prefixed header (or EOF). Malformed dates are dropped
    silently rather than crashing the daemon — trends.md is written by an
    LLM pass and we can't assume it's perfect.
    """
    entries: list[TrendEntry] = []
    matches = list(_TREND_HEADER.finditer(text))
    for i, m in enumerate(matches):
        try:
            entry_date = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        signal_id = m.group(2).strip()
        category = m.group(3).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        action_match = _PROPOSED_ACTION.search(body)
        raw_action = action_match.group(1).strip() if action_match else ""
        # Normalize the "no action" placeholder to empty — the playbook uses
        # the emptiness to decide whether the retrospective suggested a fix.
        if raw_action.startswith("_(") or raw_action.lower().startswith("(none"):
            raw_action = ""
        entries.append(TrendEntry(
            date=entry_date, signal_id=signal_id, category=category,
            body=body, proposed_action=raw_action,
        ))
    return entries


def _longest_consecutive_streaks(entries: Iterable[TrendEntry]) -> dict[str, TrendSignalStreak]:
    """For each ``signal_id``, return the longest run of consecutive-day fires.

    "Consecutive" = date deltas of exactly 1 day. Two entries on the SAME day
    for the same signal don't add to the count (a rerun on the same day is not
    a new day's evidence — otherwise a chatty tick doubles the score).
    """
    by_signal: dict[str, list[TrendEntry]] = {}
    for e in entries:
        by_signal.setdefault(e.signal_id, []).append(e)

    streaks: dict[str, TrendSignalStreak] = {}
    for signal_id, batch in by_signal.items():
        # dedupe on (signal_id, date), then walk sorted asc looking for the
        # longest consecutive-day run.
        seen: dict[date, TrendEntry] = {}
        for e in batch:
            seen.setdefault(e.date, e)  # keep the FIRST entry we saw for the day
        sorted_dates = sorted(seen)
        best_len = 1
        best_first = sorted_dates[0]
        best_last = sorted_dates[0]
        cur_len = 1
        cur_first = sorted_dates[0]
        for prev, nxt in zip(sorted_dates, sorted_dates[1:]):
            if (nxt - prev).days == 1:
                cur_len += 1
            else:
                cur_len = 1
                cur_first = nxt
            if cur_len > best_len:
                best_len = cur_len
                best_first = cur_first
                best_last = nxt
        # For the "latest" entry we surface to the playbook, prefer the last
        # day of the winning streak — that's the freshest observation of the
        # pattern the detector is worried about.
        latest_entry = seen[best_last]
        streaks[signal_id] = TrendSignalStreak(
            signal_id=signal_id,
            category=latest_entry.category,
            first_fired=best_first,
            latest_fired=best_last,
            repeat_count=best_len,
            latest_proposed_action=latest_entry.proposed_action,
        )
    return streaks


def _read_goal_workspace_dir(goal_dir: Path) -> str:
    """Extract ``workspace_dir`` from a goal's ``goal.yaml``.

    Returns "" when the file is missing / malformed / has no field — the
    detector then skips this goal rather than crashing the daemon.
    """
    goal_yaml = goal_dir / "goal.yaml"
    if not goal_yaml.is_file():
        return ""
    try:
        parsed = yaml.safe_load(goal_yaml.read_text()) or {}
    except yaml.YAMLError:
        return ""
    return str(parsed.get("workspace_dir", "")).strip()


def _iter_goal_dirs(goals_dir: Path) -> Iterable[Path]:
    """All goal subdirectories that look real. Hidden dirs excluded — matches
    the no-progress detector's convention so goal store + incident store stay
    safely co-locatable in tests."""
    if not goals_dir.exists():
        return []
    return (
        p for p in sorted(goals_dir.iterdir())
        if p.is_dir() and not p.name.startswith(".")
    )


def _resolve_trends_path(
    workspace_dir: str, workspaces_root: Path | None
) -> Path | None:
    """Map a goal's ``workspace_dir`` (as devclaw writes it — the CONTAINER
    path visible to the devclaw process) into a path the ops-agent can read.

    Devclaw writes ``/var/lib/devclaw/workspaces/<name>``; ops-agent mounts
    the same host workspaces volume at ``workspaces_root`` (e.g.
    ``/data/workspaces``). We take the basename and re-anchor. When
    ``workspaces_root`` is None (compose mount not yet added), the detector
    returns None → gracefully no-ops.
    """
    if not workspace_dir or workspaces_root is None:
        return None
    name = Path(workspace_dir).name
    if not name:
        return None
    return workspaces_root / name / ".devclaw" / "trends.md"


class TrendSignalRepeatDetector:
    """O4 — fires an incident when devclaw's trend detector has flagged the
    SAME signal on ≥ ``threshold`` consecutive daily runs for one goal.

    Reads ``<workspaces_root>/<name>/.devclaw/trends.md`` per goal. When the
    trends file doesn't exist (project has no retrospective history yet) the
    goal is silently skipped — no observation, no incident.
    """

    trigger = "O4"

    def __init__(
        self,
        *,
        threshold: int = 3,
        workspaces_root: Path | None = None,
    ) -> None:
        # Threshold of 3 matches the field-validated 2026-07-02 escalation
        # (R2 self-escalated at day 3 across four closeloop-family goals).
        # Values ≤ 1 would emit on every fire — treat as "detector disabled"
        # rather than silent spam.
        self._threshold = max(2, int(threshold))
        self._workspaces_root = workspaces_root

    def scan(self, goals_dir: Path, *, now: datetime) -> list[Incident]:
        """Emit one incident per goal whose trends.md has a ≥threshold streak."""
        incidents: list[Incident] = []
        for goal_dir in _iter_goal_dirs(goals_dir):
            workspace_dir = _read_goal_workspace_dir(goal_dir)
            trends_path = _resolve_trends_path(workspace_dir, self._workspaces_root)
            if trends_path is None or not trends_path.is_file():
                continue
            try:
                text = trends_path.read_text()
            except OSError:
                continue
            entries = _parse_trend_entries(text)
            if not entries:
                continue
            streaks = _longest_consecutive_streaks(entries)
            # Sort by repeat_count desc so the playbook sees the most acute
            # signal first if a goal has multiple. Only the winners cross
            # threshold; the rest are noise for this tick.
            winners = sorted(
                (s for s in streaks.values() if s.repeat_count >= self._threshold),
                key=lambda s: (-s.repeat_count, s.signal_id),
            )
            for streak in winners:
                payload: dict[str, Any] = {
                    "objective": _read_goal_objective(goal_dir),
                    "signal_id": streak.signal_id,
                    "category": streak.category,
                    "repeat_count": streak.repeat_count,
                    "first_fired": streak.first_fired.isoformat(),
                    "latest_fired": streak.latest_fired.isoformat(),
                    "threshold": self._threshold,
                    "proposed_action": streak.latest_proposed_action,
                    # dedup_key: the streak grows over time — re-fire only
                    # when the count crosses to a NEW value (day 3 → day 4).
                    # Keeps the log honest without spamming when a signal
                    # keeps re-firing day after day for a week.
                    "dedup_key": (
                        f"trend_signal_repeat={streak.signal_id}"
                        f"|repeat_count={streak.repeat_count}"
                    ),
                }
                incidents.append(Incident(
                    trigger=self.trigger,
                    goal_id=goal_dir.name,
                    detected_at=now,
                    payload=payload,
                ))
        return incidents


def _read_goal_objective(goal_dir: Path) -> str:
    """Cheap objective read — mirrors no_progress.py's helper so the O4
    incident log line reads like every other trigger's."""
    goal_yaml = goal_dir / "goal.yaml"
    if not goal_yaml.is_file():
        return ""
    try:
        raw = yaml.safe_load(goal_yaml.read_text()) or {}
    except yaml.YAMLError:
        return ""
    return str(raw.get("objective", "")).strip()

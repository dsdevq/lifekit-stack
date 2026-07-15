"""O4 (trend-signal-repeat) detector unit tests.

Coverage axes:
  - workspaces_root unset → detector is a no-op (compose mount not added yet)
  - trends.md absent for a goal → skipped
  - single signal fired 3 consecutive days → one incident with the shape
  - two-day streak on a threshold=3 detector → no incident
  - non-consecutive fires (gap day) → no incident
  - two signals firing, only one crosses threshold → only the winner
  - same-day repeats don't inflate the streak
  - dedup_key reflects repeat_count so a growing streak re-fires
  - proposed_action extracted verbatim from the entry body
  - malformed dates in trends.md are dropped rather than crashing
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from textwrap import dedent

from ops_agent.detectors import TrendSignalRepeatDetector

from .conftest import write_goal


def _write_trends(workspaces_root: Path, workspace_name: str, entries: list[str]) -> Path:
    """Materialize a workspace + `.devclaw/trends.md` from an entry list.

    Each entry is the raw text of one ``## [YYYY-MM-DD] SIG — CAT`` section
    (without the trailing ``---``); the writer joins with ``---`` separators
    the same way devclaw's ``_append_entry`` does.
    """
    ws_dir = workspaces_root / workspace_name / ".devclaw"
    ws_dir.mkdir(parents=True, exist_ok=True)
    body = "\n---\n\n".join(entries) + "\n---\n"
    (ws_dir / "trends.md").write_text("# trends — devclaw trend detector (per-project)\n\n" + body)
    return ws_dir / "trends.md"


def _entry(*, date: str, signal: str, category: str, observation: str, action: str = "") -> str:
    """One trends.md entry rendered in devclaw's format."""
    parts = [f"## [{date}] {signal} — {category}", "", observation, ""]
    if action:
        parts.append(f"**Proposed action:** {action}\n")
    else:
        parts.append("**Proposed action:** _(none — pattern noted, no action recommended)_\n")
    return "\n".join(parts)


def test_no_workspaces_root_yields_no_incidents(goals_dir: Path) -> None:
    """When the compose mount hasn't been added yet, O4 should silently no-op
    rather than crash — the detector still ships alongside the config option."""
    write_goal(goals_dir, "g1", workspace_dir="/var/lib/devclaw/workspaces/g1")
    incidents = TrendSignalRepeatDetector(threshold=3, workspaces_root=None).scan(
        goals_dir, now=datetime(2026, 7, 3, tzinfo=UTC)
    )
    assert incidents == []


def test_trends_absent_for_goal_yields_no_incidents(goals_dir: Path, tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    write_goal(goals_dir, "g1", workspace_dir="/var/lib/devclaw/workspaces/g1")
    # No trends.md written for g1.
    incidents = TrendSignalRepeatDetector(threshold=3, workspaces_root=workspaces).scan(
        goals_dir, now=datetime(2026, 7, 3, tzinfo=UTC)
    )
    assert incidents == []


def test_three_consecutive_days_fire_one_incident(goals_dir: Path, tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    write_goal(
        goals_dir,
        "closeloop",
        objective="ship closeloop",
        workspace_dir="/var/lib/devclaw/workspaces/closeloop",
    )
    _write_trends(
        workspaces,
        "closeloop",
        [
            _entry(
                date="2026-07-01",
                signal="R2",
                category="recurrence",
                observation="same AGENTS.md-fix commits three days in a row.",
                action="promote the fix to a lint rule; stop fixing per PR.",
            ),
            _entry(
                date="2026-07-02",
                signal="R2",
                category="recurrence",
                observation="still seeing it.",
            ),
            _entry(
                date="2026-07-03",
                signal="R2",
                category="recurrence",
                observation="stop re-firing; escalate to Denys.",
            ),
        ],
    )
    incidents = TrendSignalRepeatDetector(threshold=3, workspaces_root=workspaces).scan(
        goals_dir, now=datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    )
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.trigger == "O4"
    assert inc.goal_id == "closeloop"
    p = inc.payload
    assert p["signal_id"] == "R2"
    assert p["category"] == "recurrence"
    assert p["repeat_count"] == 3
    assert p["first_fired"] == "2026-07-01"
    assert p["latest_fired"] == "2026-07-03"
    # latest_proposed_action = the LAST day's action (fresher wins for the
    # playbook context); it's "" here since the last entry had no action.
    assert p["proposed_action"] == ""
    assert p["objective"] == "ship closeloop"


def test_two_day_streak_below_threshold_no_fire(goals_dir: Path, tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    write_goal(goals_dir, "g", workspace_dir="/var/lib/devclaw/workspaces/g")
    _write_trends(
        workspaces,
        "g",
        [
            _entry(
                date="2026-07-01", signal="R2", category="recurrence", observation="first fire."
            ),
            _entry(
                date="2026-07-02", signal="R2", category="recurrence", observation="second fire."
            ),
        ],
    )
    incidents = TrendSignalRepeatDetector(threshold=3, workspaces_root=workspaces).scan(
        goals_dir, now=datetime(2026, 7, 2, tzinfo=UTC)
    )
    assert incidents == []


def test_gap_day_resets_streak(goals_dir: Path, tmp_path: Path) -> None:
    """Non-consecutive fires (day 1, day 3, day 4) should NOT be a 3-streak.
    The streak resets on a gap; the longest here is 2 (days 3+4)."""
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    write_goal(goals_dir, "g", workspace_dir="/var/lib/devclaw/workspaces/g")
    _write_trends(
        workspaces,
        "g",
        [
            _entry(date="2026-07-01", signal="R2", category="recurrence", observation="first."),
            _entry(
                date="2026-07-03",
                signal="R2",
                category="recurrence",
                observation="not consecutive.",
            ),
            _entry(
                date="2026-07-04",
                signal="R2",
                category="recurrence",
                observation="now consecutive with the previous.",
            ),
        ],
    )
    incidents = TrendSignalRepeatDetector(threshold=3, workspaces_root=workspaces).scan(
        goals_dir, now=datetime(2026, 7, 4, tzinfo=UTC)
    )
    assert incidents == []


def test_two_signals_only_the_streaked_one_fires(goals_dir: Path, tmp_path: Path) -> None:
    """One signal has a 3-day streak; the other has isolated single fires.
    Only the streaked signal should produce an incident."""
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    write_goal(goals_dir, "g", workspace_dir="/var/lib/devclaw/workspaces/g")
    _write_trends(
        workspaces,
        "g",
        [
            _entry(date="2026-07-01", signal="R2", category="recurrence", observation="r2 day 1."),
            _entry(
                date="2026-07-01", signal="D4", category="staleness", observation="d4 single fire."
            ),
            _entry(date="2026-07-02", signal="R2", category="recurrence", observation="r2 day 2."),
            _entry(
                date="2026-07-03",
                signal="R2",
                category="recurrence",
                observation="r2 day 3.",
                action="rename repeated AGENTS.md items into a linter.",
            ),
        ],
    )
    incidents = TrendSignalRepeatDetector(threshold=3, workspaces_root=workspaces).scan(
        goals_dir, now=datetime(2026, 7, 3, tzinfo=UTC)
    )
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.payload["signal_id"] == "R2"
    assert inc.payload["proposed_action"] == "rename repeated AGENTS.md items into a linter."


def test_same_day_repeat_does_not_inflate_streak(goals_dir: Path, tmp_path: Path) -> None:
    """Two entries for the same signal on the same day are one day's
    evidence, not two. Otherwise a chatty tick would double the score."""
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    write_goal(goals_dir, "g", workspace_dir="/var/lib/devclaw/workspaces/g")
    _write_trends(
        workspaces,
        "g",
        [
            _entry(
                date="2026-07-01",
                signal="R2",
                category="recurrence",
                observation="first-of-the-day.",
            ),
            _entry(
                date="2026-07-01",
                signal="R2",
                category="recurrence",
                observation="re-fired same day.",
            ),
            _entry(date="2026-07-02", signal="R2", category="recurrence", observation="day 2."),
        ],
    )
    incidents = TrendSignalRepeatDetector(threshold=3, workspaces_root=workspaces).scan(
        goals_dir, now=datetime(2026, 7, 2, tzinfo=UTC)
    )
    assert incidents == []


def test_dedup_key_reflects_repeat_count(goals_dir: Path, tmp_path: Path) -> None:
    """Day 3 (repeat_count=3) and day 4 (repeat_count=4) should fingerprint
    differently, so the ops-agent re-fires when the streak grows past a new
    daily boundary."""
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    write_goal(goals_dir, "g", workspace_dir="/var/lib/devclaw/workspaces/g")
    _write_trends(
        workspaces,
        "g",
        [
            _entry(date="2026-07-01", signal="R2", category="recurrence", observation="1."),
            _entry(date="2026-07-02", signal="R2", category="recurrence", observation="2."),
            _entry(date="2026-07-03", signal="R2", category="recurrence", observation="3."),
        ],
    )
    day3 = TrendSignalRepeatDetector(threshold=3, workspaces_root=workspaces).scan(
        goals_dir, now=datetime(2026, 7, 3, tzinfo=UTC)
    )

    # Simulate the next day: the trend detector fires again on day 4.
    _write_trends(
        workspaces,
        "g",
        [
            _entry(date="2026-07-01", signal="R2", category="recurrence", observation="1."),
            _entry(date="2026-07-02", signal="R2", category="recurrence", observation="2."),
            _entry(date="2026-07-03", signal="R2", category="recurrence", observation="3."),
            _entry(date="2026-07-04", signal="R2", category="recurrence", observation="4."),
        ],
    )
    day4 = TrendSignalRepeatDetector(threshold=3, workspaces_root=workspaces).scan(
        goals_dir, now=datetime(2026, 7, 4, tzinfo=UTC)
    )

    assert len(day3) == 1 and len(day4) == 1
    assert day3[0].payload["dedup_key"] != day4[0].payload["dedup_key"]
    assert day3[0].fingerprint() != day4[0].fingerprint()


def test_malformed_dates_are_dropped_silently(goals_dir: Path, tmp_path: Path) -> None:
    """A bad date shouldn't crash the daemon — trends.md is written by an
    LLM pass and we can't assume perfect formatting."""
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    write_goal(goals_dir, "g", workspace_dir="/var/lib/devclaw/workspaces/g")
    (workspaces / "g" / ".devclaw").mkdir(parents=True)
    (workspaces / "g" / ".devclaw" / "trends.md").write_text(
        dedent("""\
        # trends

        ## [not-a-date] R2 — recurrence

        broken header, should be skipped.

        ---

        ## [2026-07-01] R2 — recurrence

        real one.

        ---
        """)
    )
    incidents = TrendSignalRepeatDetector(threshold=3, workspaces_root=workspaces).scan(
        goals_dir, now=datetime(2026, 7, 1, tzinfo=UTC)
    )
    # Only one date parsed → streak length 1 → below threshold; no incident.
    assert incidents == []

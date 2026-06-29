"""Test helpers — goal-folder fixtures that mirror devclaw's on-disk layout.

We hand-write the YAML frontmatter (instead of importing GoalStore) to keep
the boundary clean: the ops-agent must not import devclaw, and the tests
must not either. If devclaw's on-disk shape changes, these fixtures break
loudly — which is exactly the contract we want.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest


def write_goal(
    goals_dir: Path,
    goal_id: str,
    *,
    objective: str = "test goal",
    phase: str = "idle",
    no_progress_notified: bool = False,
    last_progress_at: str | None = None,
    last_tick_at: str | None = None,
) -> Path:
    """Create a goal folder with the frontmatter shape devclaw writes."""
    goal_dir = goals_dir / goal_id
    goal_dir.mkdir(parents=True, exist_ok=True)
    (goal_dir / "goal.yaml").write_text(f"objective: {objective}\ncadence: 1d\n")
    fm_lines = [
        "---",
        f"phase: {phase}",
        f"no_progress_notified: {str(no_progress_notified).lower()}",
    ]
    if last_progress_at is not None:
        fm_lines.append(f"last_progress_at: '{last_progress_at}'")
    if last_tick_at is not None:
        fm_lines.append(f"last_tick_at: '{last_tick_at}'")
    fm_lines.extend(["---", "", f"# {goal_id} — status", ""])
    (goal_dir / "STATUS.md").write_text("\n".join(fm_lines) + "\n")
    return goal_dir


@pytest.fixture
def goals_dir(tmp_path: Path) -> Path:
    d = tmp_path / "goals"
    d.mkdir()
    return d


@pytest.fixture
def incidents_dir(tmp_path: Path) -> Path:
    d = tmp_path / "incidents"
    d.mkdir()
    return d


@pytest.fixture
def fixed_now() -> datetime:
    # Frozen clock for deterministic dedup-window assertions.
    return datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)

"""Env-driven configuration.

Keep this module pure: read env, return a frozen dataclass. No I/O, no
filesystem checks — those happen in the daemon at startup so they can be
surfaced as structured errors instead of import-time crashes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OpsConfig:
    """Resolved runtime config for the ops-agent daemon."""

    goals_dir: Path
    incidents_dir: Path
    poll_interval_s: float
    # Re-fire suppression window. Same incident shape inside this window is
    # deduped against the .seen marker — keeps the log honest when an
    # underlying goal sits stuck for hours.
    dedup_window_s: float


def _env_path(name: str, default: str) -> Path:
    raw = os.environ.get(name, default)
    # Expand ~ so the same env var works in-container (absolute) and on a
    # workstation (where defaults reach back to $HOME).
    return Path(os.path.expanduser(raw)).resolve()


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def load_config() -> OpsConfig:
    """Build an ``OpsConfig`` from the process environment.

    Env contract (matches compose service block):
      OPS_AGENT_GOALS_DIR        — where devclaw writes goal folders (RO mount)
      OPS_AGENT_INCIDENTS_DIR    — where we persist incidents (RW)
      OPS_AGENT_POLL_INTERVAL_S  — daemon loop period
      OPS_AGENT_DEDUP_WINDOW_S   — re-fire suppression window for same incident shape
    """
    return OpsConfig(
        goals_dir=_env_path("OPS_AGENT_GOALS_DIR", "~/memory/goals"),
        incidents_dir=_env_path("OPS_AGENT_INCIDENTS_DIR", "~/memory/projects/ops-agent/incidents"),
        poll_interval_s=_env_float("OPS_AGENT_POLL_INTERVAL_S", 60.0),
        dedup_window_s=_env_float("OPS_AGENT_DEDUP_WINDOW_S", 24 * 3600.0),
    )

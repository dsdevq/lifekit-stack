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
    # O3 (verifying-stall) threshold — hours a goal may sit in
    # phase ∈ {"verifying","in_flight"} without progress before the
    # watchdog fires. Default 4h — picked from the closeloop incident
    # where a goal was stuck in verifying for 10+ hours. Defaulted on the
    # field so existing OpsConfig() constructors in tests don't break.
    verifying_stall_hours: float = 4.0
    # docker_restart action — comma-separated allowlist of compose service
    # names the action is permitted to restart. Default restricts to
    # devclaw-mcp; anything else gets rejected with service_not_allowlisted.
    docker_restart_allowlist: tuple[str, ...] = ("compose-devclaw-mcp-1",)
    # Subprocess timeout (seconds) for the `docker restart` invocation.
    # Bounded so a hung docker socket can't wedge a tick.
    docker_timeout_s: float = 30.0


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


def _env_csv_tuple(name: str, default: str) -> tuple[str, ...]:
    """Parse a comma-separated env var into a tuple of stripped non-empty strings.

    Used for service allowlists where the env value is shell-friendly CSV
    (``"a,b,c"``) but the runtime wants a frozen sequence. Empty entries are
    dropped — defensive against ``"a,,b"`` typos and trailing commas.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        raw = default
    parts = [p.strip() for p in raw.split(",")]
    return tuple(p for p in parts if p)


def load_config() -> OpsConfig:
    """Build an ``OpsConfig`` from the process environment.

    Env contract (matches compose service block):
      OPS_AGENT_GOALS_DIR                — where devclaw writes goal folders (RO mount)
      OPS_AGENT_INCIDENTS_DIR            — where we persist incidents (RW)
      OPS_AGENT_POLL_INTERVAL_S          — daemon loop period
      OPS_AGENT_DEDUP_WINDOW_S           — re-fire suppression window for same incident shape
      OPS_AGENT_VERIFYING_STALL_HOURS    — O3 watchdog threshold (default 4h)
      OPS_AGENT_DOCKER_RESTART_ALLOWLIST — CSV of restartable services
      OPS_AGENT_DOCKER_TIMEOUT_S         — bound on `docker restart` subprocess
    """
    return OpsConfig(
        goals_dir=_env_path("OPS_AGENT_GOALS_DIR", "~/memory/goals"),
        incidents_dir=_env_path("OPS_AGENT_INCIDENTS_DIR", "~/memory/projects/ops-agent/incidents"),
        poll_interval_s=_env_float("OPS_AGENT_POLL_INTERVAL_S", 60.0),
        dedup_window_s=_env_float("OPS_AGENT_DEDUP_WINDOW_S", 24 * 3600.0),
        verifying_stall_hours=_env_float("OPS_AGENT_VERIFYING_STALL_HOURS", 4.0),
        docker_restart_allowlist=_env_csv_tuple(
            "OPS_AGENT_DOCKER_RESTART_ALLOWLIST", "compose-devclaw-mcp-1"
        ),
        docker_timeout_s=_env_float("OPS_AGENT_DOCKER_TIMEOUT_S", 30.0),
    )

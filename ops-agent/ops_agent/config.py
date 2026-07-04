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
    # O4 (trend-signal-repeat) — mounted RO from the host workspaces dir so
    # the detector can read <workspace>/.devclaw/trends.md per goal. When
    # unset, O4 gracefully no-ops (no incident emitted, no crash). Default
    # matches the compose mount added alongside this row; systemd deploys
    # can override.
    workspaces_dir: Path | None = None
    # O4 threshold — number of consecutive daily fires on the same signal
    # before O4 emits an incident. Default 3 matches the field-validated
    # 2026-07-02 escalation (R2 self-escalated on day 3 across four
    # closeloop-family goals). ≤1 is treated as 2 in the detector so a
    # single fire never counts as a "streak."
    trend_repeat_threshold: int = 3
    # L3 (ops-PR4) — devclaw-bug-fix-ticket. Off by default: L3 fires the
    # fix_bug MCP tool against devclaw's OWN repo when the devclaw-defect
    # classifier matches on an O2 incident. This is the most powerful
    # action ops-agent has, so we ship it opt-in until the classifier's
    # signature set has field calibration. When ``l3_enabled=True`` but
    # ``devclaw_repo_path`` is None, L3 short-circuits to a failed outcome
    # (typed) rather than firing at anything else.
    l3_enabled: bool = False
    # Absolute path to devclaw's source repo on the host — becomes
    # ``workspace_dir`` on the fix_bug MCP call. Loaded from
    # ``OPS_AGENT_DEVCLAW_REPO_PATH``. None disables L3 in practice even
    # if ``l3_enabled=True`` (the action returns typed-failed).
    devclaw_repo_path: Path | None = None


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


def _env_optional_path(name: str) -> Path | None:
    """Return a resolved Path when the env var is set + non-empty, else None.
    Used for optional mounts (e.g. workspaces_dir for O4): when the operator
    hasn't added the compose mount, the detector should silently no-op rather
    than crash on a missing directory."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return Path(os.path.expanduser(raw)).resolve()


def _env_int(name: str, default: int) -> int:
    """Same tolerance as _env_float, but coerced to int for count-based flags."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var. Truthy: 1/true/yes/on (case-insensitive).

    Anything else — including missing / blank — falls back to ``default``.
    Errs on the side of the default so a fat-fingered env value can't
    silently activate an aggressive feature flag (L3 in particular).
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUTHY


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
      OPS_AGENT_WORKSPACES_DIR           — RO mount of the host workspaces
                                           volume; enables O4 trend-signal
                                           consumer (unset → O4 no-ops)
      OPS_AGENT_TREND_REPEAT_THRESHOLD   — O4 consecutive-day threshold (def 3)
      OPS_AGENT_L3_ENABLED               — enable L3 devclaw-bug-fix-ticket
                                           playbook (default off — opt-in
                                           until classifier is calibrated)
      OPS_AGENT_DEVCLAW_REPO_PATH        — absolute path to devclaw's own
                                           source repo on the host; becomes
                                           workspace_dir on the fix_bug MCP
                                           call. Unset → L3 short-circuits.
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
        workspaces_dir=_env_optional_path("OPS_AGENT_WORKSPACES_DIR"),
        trend_repeat_threshold=_env_int("OPS_AGENT_TREND_REPEAT_THRESHOLD", 3),
        l3_enabled=_env_bool("OPS_AGENT_L3_ENABLED", False),
        devclaw_repo_path=_env_optional_path("OPS_AGENT_DEVCLAW_REPO_PATH"),
    )

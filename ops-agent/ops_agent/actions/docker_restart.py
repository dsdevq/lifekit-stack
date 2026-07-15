"""docker_restart action — ``docker restart <service>`` via the host socket.

ops-PR4. Used when the verifying-stall playbook decides the goal is wedged
on *infrastructure* (devclaw-mcp itself is frozen) rather than on direction.
This is the first ops-agent action that does NOT go through the MCP
client — it shells out to the docker CLI against the host socket
bind-mounted into the container (see compose/docker-compose.yml).

Authority discipline
--------------------
docker.sock = effective root on the host. Mitigations layered into this
codepath:

  - the action wraps the ``docker restart`` subcommand ONLY; no other
    docker verb is reachable from here.
  - the service name MUST appear in
    ``OPS_AGENT_DOCKER_RESTART_ALLOWLIST`` (default:
    ``"compose-devclaw-mcp-1"``). Non-allowlisted names short-circuit to a
    ``failed`` outcome BEFORE any subprocess is spawned.
  - belt-and-braces: a hard refusal to restart ``compose-ops-agent-1``
    (us) even if the allowlist mis-includes it. The daemon can't usefully
    docker-restart its own container — the kill would land before the
    decision.json write.
  - the service name is pattern-checked against ``^[A-Za-z0-9_-]+$`` so
    nothing shell-injectable ever reaches ``create_subprocess_exec``.
  - subprocess timeout via ``OPS_AGENT_DOCKER_TIMEOUT_S`` (default 30s) —
    a hung docker socket can't wedge a tick.

This action never raises — every failure becomes :class:`ActionOutcome`
with ``status="failed"`` and a typed ``error_reason``. Same daemon-
availability discipline as the L1/L2 actions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid the circular import at runtime — actions and the package init
    # both reference each other.
    from . import ActionOutcome

_log = logging.getLogger("ops_agent.actions.docker_restart")

# Service-name strict pattern: matches docker-compose's default naming
# (alphanumerics, underscores, hyphens). Anything else short-circuits to
# a failed outcome BEFORE we shell out. Prevents shell-injection even
# though create_subprocess_exec doesn't go through a shell — defense in
# depth in case a future refactor moves to a shell-invoking variant.
_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_SERVICE_NAME_CHARS = 200

# Self-name guard: if someone mis-allowlists our own container, refuse
# explicitly. The actual container name on the lifekit-stack deploy
# (compose project ``compose``) is below; if the project name changes
# the env-driven allowlist remains the primary gate.
_OPS_AGENT_SELF_NAMES: frozenset[str] = frozenset({"compose-ops-agent-1"})

_DEFAULT_TIMEOUT_S = 30.0


def _timeout_seconds() -> float:
    """Read the configured timeout from env, with a 30s default.

    Module-level (not class state) so tests can monkeypatch
    ``OPS_AGENT_DOCKER_TIMEOUT_S`` cleanly. Negative / unparseable values
    fall back to the default rather than raising.
    """
    raw = os.environ.get("OPS_AGENT_DOCKER_TIMEOUT_S")
    if raw is None or raw.strip() == "":
        return _DEFAULT_TIMEOUT_S
    try:
        v = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_S
    if v <= 0:
        return _DEFAULT_TIMEOUT_S
    return v


def _read_allowlist() -> frozenset[str]:
    """Read the comma-separated allowlist from env.

    Defaults to ``{"compose-devclaw-mcp-1"}`` — the one service this PR is
    motivated to restart. Empty entries dropped (defensive against
    trailing commas).
    """
    raw = os.environ.get("OPS_AGENT_DOCKER_RESTART_ALLOWLIST")
    if raw is None or raw.strip() == "":
        raw = "compose-devclaw-mcp-1"
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


async def perform_docker_restart(
    service_name: str,
    mcp: object = None,
) -> ActionOutcome:
    """Invoke ``docker restart <service_name>``; return a structured outcome.

    Args:
      service_name: the docker service to restart. Must match
        ``^[A-Za-z0-9_-]+$``, be ≤200 chars, and appear in the env-driven
        allowlist (default: ``"compose-devclaw-mcp-1"``).
      mcp:          accepted-but-unused. Kept in the signature so the
        action-dispatch in :mod:`..main` can stay uniform across all
        action types (L1 / L2 / docker_restart) — they all take ``mcp``
        even when only some of them need it. Documenting it here so a
        future refactor doesn't accidentally drop it from the surface
        and break dispatch.

    Returns:
      :class:`ActionOutcome` — ``status="ok"`` with the docker stdout/
      stderr in ``detail``, or ``status="failed"`` with a typed
      ``error_reason`` and human-readable ``error_message``. Never raises.
    """
    # Local import to break the circular dependency the package init creates
    # by re-exporting both this function AND ActionOutcome.
    from . import ActionOutcome

    detail: dict[str, object] = {"service_name": service_name}

    # ---- input validation (fail fast, before any subprocess) ----------
    if not isinstance(service_name, str) or not service_name.strip():
        return ActionOutcome(
            action="docker_restart",
            status="failed",
            detail=detail,
            error_reason="invalid_service_name",
            error_message="service_name must be a non-empty string",
        )
    if len(service_name) > _MAX_SERVICE_NAME_CHARS:
        return ActionOutcome(
            action="docker_restart",
            status="failed",
            detail=detail,
            error_reason="invalid_service_name",
            error_message=f"service_name exceeded {_MAX_SERVICE_NAME_CHARS} chars",
        )
    if not _SERVICE_NAME_RE.match(service_name):
        return ActionOutcome(
            action="docker_restart",
            status="failed",
            detail=detail,
            error_reason="invalid_service_name",
            error_message="service_name failed pattern ^[A-Za-z0-9_-]+$",
        )

    # ---- self-preservation: don't ever restart ourselves -------------
    if service_name in _OPS_AGENT_SELF_NAMES:
        return ActionOutcome(
            action="docker_restart",
            status="failed",
            detail=detail,
            error_reason="cannot_restart_self",
            error_message=f"refusing to docker-restart self ({service_name})",
        )

    # ---- allowlist gate ---------------------------------------------
    allowlist = _read_allowlist()
    if service_name not in allowlist:
        return ActionOutcome(
            action="docker_restart",
            status="failed",
            detail={**detail, "allowlist": sorted(allowlist)},
            error_reason="service_not_allowlisted",
            error_message=(f"{service_name!r} not in OPS_AGENT_DOCKER_RESTART_ALLOWLIST"),
        )

    # ---- shell out --------------------------------------------------
    timeout_s = _timeout_seconds()
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "restart",
            service_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        # The docker CLI itself isn't on $PATH inside the container — a
        # deploy-config defect, not a transient failure.
        _log.warning("docker CLI not found service=%s", service_name)
        return ActionOutcome(
            action="docker_restart",
            status="failed",
            detail=detail,
            error_reason="docker_cli_missing",
            error_message=f"FileNotFoundError: {exc}",
        )
    except Exception as exc:  # defensive — never leak a subprocess-spawn failure
        _log.exception("docker_restart subprocess spawn failed service=%s", service_name)
        return ActionOutcome(
            action="docker_restart",
            status="failed",
            detail=detail,
            error_reason="subprocess_spawn_failed",
            error_message=f"{type(exc).__name__}: {exc}",
        )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        # Kill the proc so we don't leak it; swallow any cleanup error so
        # we don't double-fail on the kill path.
        try:
            proc.kill()
            await proc.wait()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        return ActionOutcome(
            action="docker_restart",
            status="failed",
            detail=detail,
            error_reason="timeout",
            error_message=f"docker restart exceeded {timeout_s}s",
        )
    except Exception as exc:  # defensive — communicate() can raise on EOF mishaps
        _log.exception("docker_restart subprocess communicate failed service=%s", service_name)
        return ActionOutcome(
            action="docker_restart",
            status="failed",
            detail=detail,
            error_reason="subprocess_error",
            error_message=f"{type(exc).__name__}: {exc}",
        )

    stdout = (stdout_b or b"").decode(errors="replace").strip()
    stderr = (stderr_b or b"").decode(errors="replace").strip()
    detail_final = {**detail, "stdout": stdout, "stderr": stderr, "returncode": proc.returncode}

    if proc.returncode != 0:
        return ActionOutcome(
            action="docker_restart",
            status="failed",
            detail=detail_final,
            error_reason="nonzero_exit",
            error_message=f"docker restart exited {proc.returncode}: {stderr or stdout}",
        )

    return ActionOutcome(
        action="docker_restart",
        status="ok",
        detail=detail_final,
    )

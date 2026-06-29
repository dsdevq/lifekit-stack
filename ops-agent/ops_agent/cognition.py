"""Cognition layer — async wrapper around ``claude --print``.

Mirrors devclaw's :mod:`devclaw.planner` ``call_claude`` shape: subprocess
spawn, OAuth-env hygiene (strip ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN``
so the Pro session is never bypassed), bounded timeout, structured failure.

Boundary: this module knows ZERO about devclaw or MCP. It takes a prompt,
returns stdout. The playbook layer above translates incident context into
prompts and parses the response.

Defensive contract: every failure path raises :class:`CognitionError` with a
machine-readable ``reason``. The daemon loop catches and records it; the
playbook then falls back to a ``noop`` decision. The daemon NEVER crashes on
a cognition failure — that's the load-bearing property the ops-agent design
hangs on (a Claude outage must not silence the watchdog).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

_log = logging.getLogger("ops_agent.cognition")


@dataclass(frozen=True)
class CognitionError(Exception):
    """Typed cognition failure.

    ``reason`` is one of ``spawn_failed`` / ``timeout`` / ``non_zero_exit`` so
    the playbook can choose how to report it (we log all three; the daemon
    keeps polling regardless). ``stderr`` and ``stdout_tail`` are best-effort
    captures from the subprocess.
    """

    reason: str
    message: str
    stderr: str = ""
    stdout_tail: str = ""

    def __str__(self) -> str:  # pragma: no cover — string form is debug-only
        return f"CognitionError({self.reason}): {self.message}"


@dataclass(frozen=True)
class CognitionCall:
    """Return shape from :func:`call_claude` — stdout + the metadata the
    playbook needs to write a useful ``decision.json``."""

    stdout: str
    model: str | None
    latency_ms: int
    argv_head: str


def _bin() -> str:
    return os.environ.get("OPS_AGENT_CLAUDE_BIN", "claude")


def _default_model() -> str | None:
    # Mirrors devclaw: ``sonnet`` is the cheapest model that reliably returns
    # structured JSON. Override per-call (e.g. a noisy playbook can request
    # ``haiku`` for cost; a finicky one can demand ``opus``).
    raw = os.environ.get("OPS_AGENT_CLAUDE_MODEL", "sonnet").strip()
    return raw or None


def _default_timeout_s() -> int:
    raw = os.environ.get("OPS_AGENT_CLAUDE_TIMEOUT_S", "60").strip()
    try:
        return max(1, int(float(raw)))
    except ValueError:
        return 60


def _build_argv(bin_path: str, prompt: str, model: str | None) -> list[str]:
    """Argv for a ``claude --print`` call. ``--model`` only when provided.

    Pure → unit-testable. Mirrors devclaw's :func:`devclaw.planner._build_claude_argv`.
    """
    argv = [bin_path, "--print", "--output-format=text"]
    if model:
        argv += ["--model", model]
    argv.append(prompt)
    return argv


def _scrubbed_env() -> dict[str, str]:
    """Process env with the API-key escape hatches stripped.

    A leaked ``ANTHROPIC_API_KEY`` would silently route the call through API
    credits instead of the Pro OAuth session — burning quota the owner thinks
    is on the subscription tier. Strip it every call.
    """
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


async def call_claude(
    prompt: str,
    *,
    role: str = "ops-agent",
    model: str | None = None,
    timeout_s: int | None = None,
) -> CognitionCall:
    """Spawn ``claude --print`` with ``prompt``; return stdout + metadata.

    Args:
      prompt: full prompt text. Caller is responsible for shape — this layer
              does no templating.
      role:   trace label (informational; logged for audit. The ops-agent has
              one role for now; future playbooks may want their own.)
      model:  override; default reads ``OPS_AGENT_CLAUDE_MODEL`` (``sonnet``).
      timeout_s: override; default reads ``OPS_AGENT_CLAUDE_TIMEOUT_S`` (60).

    Returns:
      :class:`CognitionCall` — stdout + the latency for the playbook log.

    Raises:
      :class:`CognitionError` — one of ``spawn_failed`` / ``timeout`` /
      ``non_zero_exit``. The daemon loop catches this so a Claude outage never
      kills polling.
    """
    bin_path = _bin()
    effective_model = model if model is not None else _default_model()
    effective_timeout = timeout_s if timeout_s is not None else _default_timeout_s()

    argv = _build_argv(bin_path, prompt, effective_model)
    argv_head = f"{bin_path} --print" + (f" --model {effective_model}" if effective_model else "")
    env = _scrubbed_env()

    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        _log.warning("cognition spawn failed role=%s err=%s", role, exc)
        raise CognitionError(
            reason="spawn_failed",
            message=f"Failed to spawn {bin_path}: {exc}",
        ) from exc

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
    except TimeoutError:
        proc.kill()
        with _suppress_wait_error():
            await proc.wait()
        latency_ms = int((time.monotonic() - started) * 1000)
        _log.warning(
            "cognition timeout role=%s argv=%s timeout=%ss", role, argv_head, effective_timeout
        )
        raise CognitionError(
            reason="timeout",
            message=f"claude --print timed out after {effective_timeout}s",
        ) from None

    stdout = stdout_b.decode("utf-8", "replace")
    stderr = stderr_b.decode("utf-8", "replace")
    latency_ms = int((time.monotonic() - started) * 1000)

    if proc.returncode != 0:
        _log.warning(
            "cognition non-zero exit role=%s code=%s argv=%s",
            role,
            proc.returncode,
            argv_head,
        )
        # stdout tail is included because Claude's usage-limit messages land
        # on stdout with an empty stderr — same trap devclaw's planner has.
        raise CognitionError(
            reason="non_zero_exit",
            message=f"claude --print exited {proc.returncode}",
            stderr=stderr[:500],
            stdout_tail=stdout[-500:],
        )

    return CognitionCall(
        stdout=stdout,
        model=effective_model,
        latency_ms=latency_ms,
        argv_head=argv_head,
    )


class _suppress_wait_error:
    """Context manager that swallows benign ``proc.wait()`` errors after a kill."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        # ProcessLookupError if the process raced and exited before .wait().
        return exc_type is not None and issubclass(exc_type, ProcessLookupError | OSError)

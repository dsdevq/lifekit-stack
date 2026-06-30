"""Resident daemon entrypoint.

One process, one asyncio loop. Wakes every ``poll_interval_s``, asks each
detector for incidents, runs the playbook + action layer on new incidents,
and persists everything to the incident folder.

ops-PR2 wired the cognition + action layer on top of ops-PR1's L0 baseline
for the O1 (no-progress) trigger. ops-PR3 adds the O2 (no-steering)
trigger + the L2 (steer_goal) action — both reuse the same per-incident
lifecycle, dispatched to the right playbook by ``incident.trigger``:

  1. Each detector scans goals; new (non-deduped) incidents become
     incident folders on disk — same as PR1.
  2. For each new incident, build the trigger-specific playbook prompt
     (stuck-goal-evaluate for O1; drifting-goal-steer for O2).
  3. Call ``claude --print`` for a structured decision.
  4. Dispatch the decision: ``evaluate_goal`` → L1 MCP call,
     ``steer_goal`` → L2 MCP call, ``noop`` → record only.
  5. Write ``decision.json`` + a richer ``outcome.md`` into the incident
     folder; append the action-taken summary to ``log.md``.

Idempotency: an incident whose folder already has ``decision.json`` is NOT
re-decided. This matters when the daemon restarts mid-incident — we don't
want to repeat an MCP call that might have already mutated state.

Failure containment: every cognition + MCP failure is caught and recorded.
The polling loop NEVER crashes on a downstream outage. The systemd / compose
restart policy is the safety net for true daemon defects.

Exit shape: SIGINT/SIGTERM cancels the loop; the process exits 0.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import actions, playbooks
from .actions import ActionOutcome, outcome_to_dict
from .cognition import CognitionError, call_claude
from .config import OpsConfig, load_config
from .detectors import NoProgressDetector, NoSteeringDetector
from .incident import Incident, IncidentStore
from .mcp_client import DevclawMCPClient

# Discriminated union of the two playbook-decision types this PR routes.
# Kept here (not in playbooks/__init__) because it's a daemon-layer concern
# — the playbook layer itself doesn't know about cross-playbook dispatch.
PlaybookDecision = playbooks.StuckGoalDecision | playbooks.DriftingGoalDecision

_log = logging.getLogger("ops_agent")


def _utcnow() -> datetime:
    # Kept as a module-level shim so tests can monkeypatch a deterministic clock
    # without dragging asyncio into the picture.
    return datetime.now(UTC)


def _build_prompt_for(incident: Incident) -> str:
    """Render the right playbook prompt for ``incident.trigger``.

    O1 → stuck-goal-evaluate; O2 → drifting-goal-steer. Unknown triggers
    raise — that's a programming bug (the detector layer is the only
    surface that mints triggers and main wires them in).
    """
    if incident.trigger == "O1":
        return playbooks.build_stuck_goal_prompt(
            goal_id=incident.goal_id,
            objective=str(incident.payload.get("objective", "")),
            phase=str(incident.payload.get("phase", "")),
            last_progress_at=incident.payload.get("last_progress_at"),
            last_tick_at=incident.payload.get("last_tick_at"),
            detected_at=incident.detected_at.isoformat(timespec="seconds"),
        )
    if incident.trigger == "O2":
        # Threshold is recorded by the detector so the prompt + the
        # incident folder agree on what the watchdog tripped at.
        threshold_hours = float(incident.payload.get("threshold_hours", 24.0))
        return playbooks.build_drifting_goal_prompt(
            goal_id=incident.goal_id,
            objective=str(incident.payload.get("objective", "")),
            phase=str(incident.payload.get("phase", "")),
            last_steering_at=incident.payload.get("last_steering_at"),
            threshold_hours=threshold_hours,
            detected_at=incident.detected_at.isoformat(timespec="seconds"),
        )
    raise ValueError(f"no playbook wired for trigger={incident.trigger!r}")


def _parse_decision_for(incident: Incident, raw: str) -> PlaybookDecision:
    """Parse a Claude response with the right playbook for ``incident.trigger``."""
    if incident.trigger == "O1":
        return playbooks.parse_stuck_goal_decision(raw)
    if incident.trigger == "O2":
        return playbooks.parse_drifting_goal_decision(raw)
    raise ValueError(f"no playbook wired for trigger={incident.trigger!r}")


def _noop_decision_for(incident: Incident, reasoning: str) -> PlaybookDecision:
    """Build a typed noop decision for ``incident.trigger`` (used on cognition failure)."""
    if incident.trigger == "O1":
        return playbooks.StuckGoalDecision(action="noop", reasoning=reasoning, raw_response="")
    if incident.trigger == "O2":
        return playbooks.DriftingGoalDecision(
            action="noop", message="", reasoning=reasoning, raw_response=""
        )
    raise ValueError(f"no playbook wired for trigger={incident.trigger!r}")


async def _dispatch_action(
    decision: PlaybookDecision,
    incident: Incident,
    mcp: DevclawMCPClient | None,
) -> ActionOutcome | None:
    """Map a decision's ``action`` onto the right MCP call (or skip on noop).

    Returns ``None`` for a ``noop`` decision; an :class:`ActionOutcome`
    otherwise. A missing MCP client produces a ``failed`` outcome rather
    than crashing — the decision still records what we'd have done.
    """
    if decision.action == "noop":
        return None

    if decision.action == "evaluate_goal":
        if mcp is None:
            return ActionOutcome(
                action="evaluate_goal",
                status="failed",
                detail={"goal_id": incident.goal_id},
                error_reason="no_mcp_client",
                error_message="ops-agent has no MCP client configured",
            )
        return await actions.perform_evaluate_goal(incident.goal_id, mcp)

    if decision.action == "steer_goal":
        # ``message`` is only meaningful on a DriftingGoalDecision; the
        # parser guarantees it's a non-empty trimmed string when the
        # action is steer_goal.
        message = getattr(decision, "message", "")
        if mcp is None:
            return ActionOutcome(
                action="steer_goal",
                status="failed",
                detail={"goal_id": incident.goal_id, "message": message},
                error_reason="no_mcp_client",
                error_message="ops-agent has no MCP client configured",
            )
        return await actions.perform_steer_goal(incident.goal_id, message, mcp)

    # Belt-and-suspenders: the playbook parsers already collapse unknown
    # actions to noop. If one slips through we record a failure rather
    # than firing an MCP call we can't validate.
    return ActionOutcome(
        action=str(decision.action),
        status="failed",
        detail={"goal_id": incident.goal_id},
        error_reason="unknown_action",
        error_message=f"no dispatch wired for action={decision.action!r}",
    )


async def _decide_and_act(
    incident: Incident,
    folder: Path,
    mcp: DevclawMCPClient | None,
) -> tuple[PlaybookDecision | None, ActionOutcome | None, CognitionError | None]:
    """Run the playbook + action layer for one fresh incident.

    Returns a tuple (decision, outcome, cognition_error). The caller writes
    them all to the incident folder.

    Idempotency: if ``folder/decision.json`` exists, returns (None, None,
    None) and the caller skips. The on-disk shape IS the lock.

    The prompt + parser + action dispatch are all keyed on
    ``incident.trigger`` so a new trigger lands as a small surface
    addition rather than a fork of this function.
    """
    decision_path = folder / "decision.json"
    if decision_path.exists():
        _log.info(
            "incident already decided (decision.json exists) — skip goal=%s folder=%s",
            incident.goal_id,
            folder.name,
        )
        return None, None, None

    prompt = _build_prompt_for(incident)

    # Persist the prompt before the cognition call — if the daemon dies
    # mid-call the prompt is still on disk for forensic review.
    (folder / "prompt.md").write_text(prompt)

    try:
        call_result = await call_claude(prompt, role="ops-agent")
    except CognitionError as cog_err:
        _log.warning(
            "cognition failed reason=%s goal=%s — falling back to noop",
            cog_err.reason,
            incident.goal_id,
        )
        decision = _noop_decision_for(
            incident,
            reasoning=f"cognition_failed: {cog_err.reason} — {cog_err.message}",
        )
        return decision, None, cog_err

    decision = _parse_decision_for(incident, call_result.stdout)
    _log.info(
        "playbook decided trigger=%s goal=%s action=%s latency_ms=%s",
        incident.trigger,
        incident.goal_id,
        decision.action,
        call_result.latency_ms,
    )

    outcome = await _dispatch_action(decision, incident, mcp)
    return decision, outcome, None


def _write_decision(folder: Path, decision: PlaybookDecision) -> None:
    payload: dict[str, Any] = {
        "action": decision.action,
        "reasoning": decision.reasoning,
        "raw_response": decision.raw_response,
    }
    # Drifting-goal decisions carry a steering message; record it so the
    # incident folder has the full audit trail of what got injected.
    message = getattr(decision, "message", None)
    if message:
        payload["message"] = message
    (folder / "decision.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _render_outcome_md(
    incident: Incident,
    decision: PlaybookDecision | None,
    outcome: ActionOutcome | None,
    cog_err: CognitionError | None,
) -> str:
    lines = [f"# {incident.folder_name()} — outcome", ""]
    if cog_err is not None:
        lines.append(
            f"Cognition failed (`{cog_err.reason}`): {cog_err.message}. "
            "Defaulting to `noop`; the next polling cycle will retry."
        )
        lines.append("")
    if decision is not None:
        lines.append(f"- **Decision:** `{decision.action}`")
        lines.append(f"- **Reasoning:** {decision.reasoning}")
        # Surface the steering message on L2 decisions so the human-readable
        # outcome matches what actually got injected.
        message = getattr(decision, "message", None)
        if message:
            lines.append(f"- **Steering message:** {message}")
    else:
        lines.append(
            "- Incident already had a `decision.json` on disk — no new "
            "playbook run on this cycle (idempotency)."
        )
    if outcome is not None:
        lines.append(f"- **Action:** `{outcome.action}` — status `{outcome.status}`")
        if outcome.status == "ok":
            # L1 outcomes carry a verdict dict; L2 outcomes carry devclaw's
            # steer-acknowledgement. Render whichever is present.
            verdict = outcome.detail.get("verdict") or {}
            v = verdict.get("verdict") if isinstance(verdict, dict) else None
            if v:
                lines.append(f"- **Verdict from devclaw:** `{v}`")
            rationale = verdict.get("rationale") if isinstance(verdict, dict) else None
            if rationale:
                lines.append(f"- **Rationale:** {rationale}")
            if outcome.action == "steer_goal":
                lines.append("- Steering injected into devclaw inbox.")
        else:
            lines.append(
                f"- **Action failure:** `{outcome.error_reason}` — {outcome.error_message}"
            )
    elif decision is not None and decision.action == "noop":
        lines.append("- No action taken (`noop`).")
    lines.append("")
    return "\n".join(lines)


def _append_decision_log(
    log_path: Path,
    incident: Incident,
    decision: PlaybookDecision | None,
    outcome: ActionOutcome | None,
) -> None:
    """One-line log entry capturing what we DID on this incident.

    Distinct from the detection-only line :class:`IncidentStore` writes; this
    is the post-cognition follow-up so the log reads top-to-bottom as the
    chronology of one incident's life.
    """
    if decision is None:
        return  # no new decision on this tick (idempotent skip)
    ts = _utcnow().isoformat(timespec="seconds")
    if outcome is None:
        # noop or cognition failure
        line = f"- [{ts}] {incident.trigger} {incident.goal_id} — decided `{decision.action}`\n"
    else:
        line = (
            f"- [{ts}] {incident.trigger} {incident.goal_id} — "
            f"action `{outcome.action}` status `{outcome.status}`\n"
        )
    if not log_path.exists():
        log_path.write_text("# ops-agent — incident log\n\n")
    with log_path.open("a") as fh:
        fh.write(line)


async def _process_incident(
    incident: Incident,
    folder: Path,
    log_path: Path,
    mcp: DevclawMCPClient | None,
) -> None:
    """One incident's full cognition + action lifecycle, on-disk side effects
    included."""
    decision, outcome, cog_err = await _decide_and_act(incident, folder, mcp)
    if decision is None:
        # Idempotent skip — already decided on an earlier tick.
        return
    _write_decision(folder, decision)
    if outcome is not None:
        (folder / "action.json").write_text(
            json.dumps(outcome_to_dict(outcome), indent=2, sort_keys=True) + "\n"
        )
    # Overwrite the L0 outcome.md the IncidentStore wrote with the richer
    # post-decision rendering.
    (folder / "outcome.md").write_text(_render_outcome_md(incident, decision, outcome, cog_err))
    _append_decision_log(log_path, incident, decision, outcome)


async def tick(
    cfg: OpsConfig,
    store: IncidentStore,
    detectors: list[Any],
    mcp: DevclawMCPClient | None,
) -> int:
    """One scan pass across every registered detector.

    Returns the total number of incidents written this tick.

    Broken out from ``run_loop`` so it's directly callable from tests
    without standing up the asyncio scheduler. The ``detectors`` argument
    is a list so adding a third trigger is a one-line registration in
    ``run_loop`` rather than another parameter — kept concrete (no
    MultiDetector abstraction) since the right interface for that will
    emerge only once we have more than two detectors to compare.

    For each new (non-deduped) incident:
      1. Write the L0 incident folder (detection record — defense in depth).
      2. Run the playbook + action layer to fill in decision.json /
         action.json / a richer outcome.md.
    """
    now = _utcnow()
    written = 0
    for detector in detectors:
        for incident in detector.scan(cfg.goals_dir, now=now):
            if store.is_deduped(incident, now=now):
                continue
            folder = store.write(incident)
            _log.info(
                "incident written trigger=%s goal=%s folder=%s",
                incident.trigger,
                incident.goal_id,
                folder,
            )
            written += 1
            try:
                await _process_incident(incident, folder, cfg.incidents_dir / "log.md", mcp)
            except Exception:  # defensive — never let one incident's cognition kill the loop
                _log.exception(
                    "incident cognition crashed goal=%s folder=%s — continuing",
                    incident.goal_id,
                    folder,
                )
    return written


async def run_loop(cfg: OpsConfig, stop: asyncio.Event) -> None:
    store = IncidentStore(cfg.incidents_dir, dedup_window_s=cfg.dedup_window_s)
    # Register every detector here — order is significant only for the
    # order incidents appear in the log on a tick that lights up both.
    detectors = [NoProgressDetector(), NoSteeringDetector()]
    _log.info(
        "ops-agent starting goals_dir=%s incidents_dir=%s poll=%.1fs detectors=%s",
        cfg.goals_dir,
        cfg.incidents_dir,
        cfg.poll_interval_s,
        [d.trigger for d in detectors],
    )
    async with DevclawMCPClient() as mcp:
        while not stop.is_set():
            try:
                await tick(cfg, store, detectors, mcp)
            except Exception:
                # Catch-and-log so a transient FS hiccup doesn't kill the daemon.
                # Real defects surface in the log; the on-failure:5 policy bounds
                # systemic problems.
                _log.exception("tick failed; sleeping and retrying")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=cfg.poll_interval_s)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    def _request_stop() -> None:
        _log.info("shutdown signal received")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows / restricted envs — fall back to the default KeyboardInterrupt path.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)


def run() -> None:
    """Console-script entrypoint."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = load_config()
    cfg.incidents_dir.mkdir(parents=True, exist_ok=True)

    async def _main() -> None:
        stop = asyncio.Event()
        _install_signal_handlers(asyncio.get_running_loop(), stop)
        await run_loop(cfg, stop)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    run()

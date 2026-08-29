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

import httpx

from . import actions, playbooks
from .actions import ActionOutcome, outcome_to_dict
from .classifiers import DevclawDefectMatch, classify_devclaw_defect
from .cognition import CognitionError, call_claude
from .config import OpsConfig, load_config
from .detectors import (
    DaemonLivenessDetector,
    NoProgressDetector,
    NoSteeringDetector,
    TrendSignalRepeatDetector,
    VerifyingStallDetector,
)
from .health_probe import HealthSnapshot, probe_health
from .incident import Incident, IncidentStore
from .mcp_client import DevclawMCPClient

# Discriminated union of the playbook-decision types this PR routes.
# Kept here (not in playbooks/__init__) because it's a daemon-layer concern
# — the playbook layer itself doesn't know about cross-playbook dispatch.
# ops-PR4 adds ``DevclawBugFixDecision`` — routed from O2 incidents when
# the devclaw-defect classifier matches and ``cfg.l3_enabled`` is on.
PlaybookDecision = (
    playbooks.StuckGoalDecision
    | playbooks.DriftingGoalDecision
    | playbooks.VerifyingStallDecision
    | playbooks.DevclawBugFixDecision
)

_log = logging.getLogger("ops_agent")


def _utcnow() -> datetime:
    # Kept as a module-level shim so tests can monkeypatch a deterministic clock
    # without dragging asyncio into the picture.
    return datetime.now(UTC)


def _l3_route_or_none(incident: Incident, cfg: OpsConfig) -> DevclawDefectMatch | None:
    """Decide whether this incident routes to L3 (devclaw-bug-fix-ticket).

    L3 fires only on O2 incidents where:
      1. ``cfg.l3_enabled`` is true (opt-in, off by default);
      2. ``cfg.devclaw_repo_path`` is configured (otherwise fix_bug has
         nowhere to file against);
      3. the devclaw-defect classifier matches a known signature in the
         goal's on-disk state.

    Returns the classifier match on route, else None (fall through to the
    default L2 drifting-goal-steer path). Runs at most once per incident
    per polling cycle — both prompt-building and parsing consult this.
    """
    if incident.trigger != "O2":
        return None
    if not cfg.l3_enabled:
        return None
    if cfg.devclaw_repo_path is None:
        return None
    return classify_devclaw_defect(
        goal_id=incident.goal_id,
        goals_dir=cfg.goals_dir,
    )


def _build_prompt_for(
    incident: Incident,
    cfg: OpsConfig,
    *,
    l3_match: DevclawDefectMatch | None = None,
) -> str:
    """Render the right playbook prompt for ``incident.trigger``.

    O1 → stuck-goal-evaluate; O2 → drifting-goal-steer OR devclaw-bug-
    fix-ticket (when ``l3_match`` is non-None — see :func:`_l3_route_or_none`);
    O3 → verifying-stall-unblock; O4 → trend-signal-escalate. Unknown
    triggers raise — that's a programming bug (the detector layer is the
    only surface that mints triggers and main wires them in).

    ``cfg`` is threaded through so O3's prompt can surface the
    docker_restart allowlist verbatim from the runtime config, and so
    the L3 prompt can surface the devclaw_repo_path verbatim.
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
        # L3 route (ops-PR4) — classifier matched a known devclaw-defect
        # signature and L3 is enabled. Ask Claude to file a fix-PR
        # against devclaw itself instead of steering the goal.
        if l3_match is not None:
            # devclaw_repo_path is guaranteed non-None on the L3 route
            # (_l3_route_or_none checks it), but assert for the type
            # checker + defence-in-depth.
            assert cfg.devclaw_repo_path is not None
            return playbooks.build_devclaw_bug_fix_prompt(
                goal_id=incident.goal_id,
                objective=str(incident.payload.get("objective", "")),
                phase=str(incident.payload.get("phase", "")),
                signature=l3_match.signature,
                evidence=l3_match.evidence,
                confidence=l3_match.confidence,
                devclaw_repo_path=str(cfg.devclaw_repo_path),
                detected_at=incident.detected_at.isoformat(timespec="seconds"),
            )
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
    if incident.trigger == "O3":
        threshold_hours = float(incident.payload.get("threshold_hours", 4.0))
        return playbooks.build_verifying_stall_prompt(
            goal_id=incident.goal_id,
            objective=str(incident.payload.get("objective", "")),
            phase=str(incident.payload.get("phase", "")),
            last_progress_at=incident.payload.get("last_progress_at"),
            detected_at=incident.detected_at.isoformat(timespec="seconds"),
            threshold_hours=threshold_hours,
            allowlisted_services=cfg.docker_restart_allowlist,
        )
    if incident.trigger == "O4":
        return playbooks.build_trend_signal_escalate_prompt(
            goal_id=incident.goal_id,
            objective=str(incident.payload.get("objective", "")),
            signal_id=str(incident.payload.get("signal_id", "")),
            category=str(incident.payload.get("category", "")),
            repeat_count=int(incident.payload.get("repeat_count", 0)),
            first_fired=str(incident.payload.get("first_fired", "")),
            latest_fired=str(incident.payload.get("latest_fired", "")),
            threshold=int(incident.payload.get("threshold", cfg.trend_repeat_threshold)),
            proposed_action=str(incident.payload.get("proposed_action", "")),
            detected_at=incident.detected_at.isoformat(timespec="seconds"),
        )
    raise ValueError(f"no playbook wired for trigger={incident.trigger!r}")


def _parse_decision_for(
    incident: Incident,
    raw: str,
    *,
    l3_match: DevclawDefectMatch | None = None,
) -> PlaybookDecision:
    """Parse a Claude response with the right playbook for ``incident.trigger``.

    ``l3_match`` mirrors the routing decision made at prompt-build time —
    when non-None on an O2 incident we parse against the devclaw-bug-fix
    playbook instead of drifting-goal-steer.
    """
    if incident.trigger == "O1":
        return playbooks.parse_stuck_goal_decision(raw)
    if incident.trigger == "O2":
        if l3_match is not None:
            return playbooks.parse_devclaw_bug_fix_decision(raw)
        return playbooks.parse_drifting_goal_decision(raw)
    if incident.trigger == "O3":
        return playbooks.parse_verifying_stall_decision(raw)
    if incident.trigger == "O4":
        return playbooks.parse_trend_signal_escalate_decision(raw)
    raise ValueError(f"no playbook wired for trigger={incident.trigger!r}")


def _noop_decision_for(
    incident: Incident,
    reasoning: str,
    *,
    l3_match: DevclawDefectMatch | None = None,
) -> PlaybookDecision:
    """Build a typed noop decision for ``incident.trigger`` (used on cognition failure).

    ``l3_match`` mirrors the routing decision — on the L3 route the noop
    fallback must be a ``DevclawBugFixDecision`` so serialisation of the
    incident folder is trigger-shape-consistent.
    """
    if incident.trigger == "O1":
        return playbooks.StuckGoalDecision(action="noop", reasoning=reasoning, raw_response="")
    if incident.trigger == "O2":
        if l3_match is not None:
            return playbooks.DevclawBugFixDecision(
                action="noop",
                description="",
                title="",
                reasoning=reasoning,
                raw_response="",
            )
        return playbooks.DriftingGoalDecision(
            action="noop", message="", reasoning=reasoning, raw_response=""
        )
    if incident.trigger == "O3":
        return playbooks.VerifyingStallDecision(
            action="noop", service_name="", reasoning=reasoning, raw_response=""
        )
    if incident.trigger == "O4":
        # O4 reuses the DriftingGoalDecision shape (steer_goal | noop with
        # a message). Same dispatch path as O2 — no new action type.
        return playbooks.DriftingGoalDecision(
            action="noop", message="", reasoning=reasoning, raw_response=""
        )
    raise ValueError(f"no playbook wired for trigger={incident.trigger!r}")


async def _dispatch_action(
    decision: PlaybookDecision,
    incident: Incident,
    mcp: DevclawMCPClient | None,
    cfg: OpsConfig,
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

    if decision.action == "fix_bug":
        # Only DevclawBugFixDecision carries description/title — the parser
        # coerces empty description to noop, so if we get here they're set.
        # devclaw_repo_path is required at the config layer for the L3 route
        # to be picked; guard again as defence in depth.
        description = getattr(decision, "description", "")
        title = getattr(decision, "title", "")
        if mcp is None:
            return ActionOutcome(
                action="fix_bug",
                status="failed",
                detail={"triggering_goal_id": incident.goal_id},
                error_reason="no_mcp_client",
                error_message="ops-agent has no MCP client configured",
            )
        if cfg.devclaw_repo_path is None:
            return ActionOutcome(
                action="fix_bug",
                status="failed",
                detail={"triggering_goal_id": incident.goal_id},
                error_reason="no_devclaw_repo_path",
                error_message="devclaw_repo_path unset — L3 cannot fire",
            )
        return await actions.perform_fix_bug(
            devclaw_repo_path=str(cfg.devclaw_repo_path),
            description=description,
            title=title,
            triggering_goal_id=incident.goal_id,
            mcp=mcp,
        )

    if decision.action == "docker_restart":
        # ``service_name`` is only meaningful on a VerifyingStallDecision;
        # the parser guarantees it's a non-empty trimmed pattern-validated
        # string when the action is docker_restart. mcp is unused but
        # passed for dispatch-signature uniformity (see the action's
        # docstring).
        service_name = getattr(decision, "service_name", "")
        return await actions.perform_docker_restart(service_name, mcp=mcp)

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
    cfg: OpsConfig,
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

    # Decide the L3 route ONCE per incident — the classifier reads disk, so
    # the same match is shared across prompt-build, parser, and noop-fallback.
    # None on all non-O2 triggers and on O2 when L3 is opt-out or classifier
    # doesn't match; the O2 default path (drifting-goal-steer) fires then.
    l3_match = _l3_route_or_none(incident, cfg)
    if l3_match is not None:
        _log.info(
            "L3 route selected goal=%s signature=%s confidence=%s",
            incident.goal_id,
            l3_match.signature,
            l3_match.confidence,
        )

    prompt = _build_prompt_for(incident, cfg, l3_match=l3_match)

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
            l3_match=l3_match,
        )
        return decision, None, cog_err

    decision = _parse_decision_for(incident, call_result.stdout, l3_match=l3_match)
    _log.info(
        "playbook decided trigger=%s goal=%s action=%s latency_ms=%s",
        incident.trigger,
        incident.goal_id,
        decision.action,
        call_result.latency_ms,
    )

    outcome = await _dispatch_action(decision, incident, mcp, cfg)
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
    # Verifying-stall docker_restart decisions carry the service name; record
    # it so the incident folder shows exactly what service was targeted.
    service_name = getattr(decision, "service_name", None)
    if service_name:
        payload["service_name"] = service_name
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
        service_name = getattr(decision, "service_name", None)
        if service_name:
            lines.append(f"- **docker restart target:** `{service_name}`")
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
    cfg: OpsConfig,
) -> None:
    """One incident's full cognition + action lifecycle, on-disk side effects
    included."""
    decision, outcome, cog_err = await _decide_and_act(incident, folder, mcp, cfg)
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


def _post_text(url: str, text: str, *, timeout_s: float = 10.0) -> None:
    """Fire one plain-text notify (the same relay shape devclaw uses)."""
    httpx.post(url, json={"text": text}, timeout=timeout_s)


async def _notify_o5(incident: Incident, folder: Path, cfg: OpsConfig) -> None:
    """O5's whole action layer: one mechanical owner ping. ZERO cognition and
    ping-only by decision (observability-maxout Resolved O1/O2) — when devclaw
    itself may be down, an LLM playbook is the least trustworthy component in
    the room, so O5 never consults one. Restart is a named follow-up gated on
    O5 having correct detections on record."""
    condition = str(incident.payload.get("condition", "unknown"))
    detail = str(incident.payload.get("detail", ""))
    text = f"🛑 [ops-agent O5] {condition}: {detail}"
    delivered = False
    error = ""
    if cfg.notify_url:
        try:
            await asyncio.to_thread(_post_text, cfg.notify_url, text)
            delivered = True
        except Exception as exc:  # noqa: BLE001 — a dead relay must not kill the loop
            error = f"{type(exc).__name__}: {exc}"
            _log.warning("O5 notify failed (%s) — incident folder still has it", error)
    else:
        _log.warning("O5 %s (no OPS_AGENT_NOTIFY_URL — log + folder only)", text)
    (folder / "outcome.md").write_text(
        f"# O5 outcome — mechanical, ping-only\n\n"
        f"- condition: {condition}\n"
        f"- detail: {detail}\n"
        f"- notify_url configured: {bool(cfg.notify_url)}\n"
        f"- delivered: {delivered}\n"
        + (f"- error: {error}\n" if error else "")
        + "\nNo playbook, no cognition, no restart — Resolved O1 (ping-only first).\n"
    )


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

    When ``cfg.health_url`` is set, the tick opens with ONE /health probe
    (bounded, off-thread, never raises) whose snapshot feeds two things:

      - the O5 daemon-liveness detector (``scan_health`` instead of the
        goal-folder ``scan``) — its incidents bypass the cognition layer
        entirely and go straight to the mechanical owner ping;
      - the O3 held≠stalled suppression: while devclaw reports dispatch is
        deliberately held (run window / operator hold), a "verifying-stall"
        on a goal is the hold, not a wedge — suppressed BEFORE dedup so the
        marker isn't burned and O3 can still fire after the window opens.

    For each new (non-deduped) incident:
      1. Write the L0 incident folder (detection record — defense in depth).
      2. O5 → mechanical notify; everything else → the playbook + action
         layer (decision.json / action.json / a richer outcome.md).
    """
    now = _utcnow()
    health: HealthSnapshot | None = None
    if cfg.health_url:
        health = await asyncio.to_thread(
            probe_health, cfg.health_url, timeout_s=cfg.health_timeout_s
        )
    written = 0
    for detector in detectors:
        if isinstance(detector, DaemonLivenessDetector):
            incidents = detector.scan_health(health, now=now)
        else:
            incidents = detector.scan(cfg.goals_dir, now=now)
        for incident in incidents:
            if incident.trigger == "O3" and health is not None and health.dispatch_open is False:
                _log.info(
                    "O3 suppressed goal=%s — dispatch deliberately held (%s); "
                    "held is not stalled",
                    incident.goal_id,
                    health.dispatch_hold_reason or "no reason given",
                )
                continue
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
            if incident.trigger == "O5":
                try:
                    await _notify_o5(incident, folder, cfg)
                except Exception:  # defensive — same containment as cognition
                    _log.exception("O5 notify path crashed folder=%s — continuing", folder)
                continue
            try:
                await _process_incident(incident, folder, cfg.incidents_dir / "log.md", mcp, cfg)
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
    detectors: list[Any] = [
        # O5 first: daemon health is judged before goal-level noise, and its
        # probe result also drives the O3 held≠stalled suppression this tick.
        DaemonLivenessDetector(
            stale_factor=cfg.heartbeat_stale_factor,
            renotify_s=cfg.o5_renotify_s,
            cycle_report_max_age_h=cfg.o5_cycle_report_max_age_h,
        ),
        NoProgressDetector(),
        NoSteeringDetector(),
        VerifyingStallDetector(),
        TrendSignalRepeatDetector(
            threshold=cfg.trend_repeat_threshold,
            workspaces_root=cfg.workspaces_dir,
        ),
    ]
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

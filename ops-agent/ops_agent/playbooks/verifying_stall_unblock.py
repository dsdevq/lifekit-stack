"""Playbook: verifying-stall-unblock.

Trigger: O3 (verifying-stall watchdog fires on a goal sitting in a
transient phase past the threshold).

Action menu given to Claude:

  - ``evaluate_goal`` — call MCP ``evaluate_goal(goal_id)`` to force a
    fresh direction verdict. Choose this when the stall looks like a
    *direction* problem: done-gate keeps returning ``incomplete``,
    transient phase is verifying, the rationale would inform the next
    move.
  - ``docker_restart`` — restart an allowlisted compose service via the
    host docker socket. Choose this when the stall looks like an
    *infrastructure* problem: devclaw-mcp itself appears frozen, the
    goal isn't even making MCP calls. The response MUST include a
    ``service_name`` from the allowlist surfaced in the prompt.
  - ``noop`` — record the incident only; take no action. Choose this
    when neither remediation is clearly indicated from the limited
    context (verbose-but-progressing goal, transient phase that may
    just be slow, etc.).

The playbook is two pure functions:

  - :func:`build_verifying_stall_prompt` — incident context → prompt.
  - :func:`parse_verifying_stall_decision` — Claude stdout → typed decision.

Decision parsing is strict: malformed JSON, missing required fields,
unknown action, missing or invalid ``service_name`` on a docker_restart
all collapse to ``noop`` with the failure surfaced in ``reasoning``.
Belt-and-braces: the docker_restart *action itself* re-validates the
service name and re-checks the allowlist before any subprocess runs —
so even a parser bug here can't escalate authority.
"""

from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

Action = Literal["evaluate_goal", "docker_restart", "noop"]

# Service-name length + pattern gates — mirror the docker_restart action's
# own gates. A response that would fail there fails *here* first, so the
# incident folder records the parse-time rejection instead of bouncing
# through an action call that just refuses.
_MAX_SERVICE_NAME_CHARS = 200
_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class VerifyingStallDecision:
    """Parsed decision from a verifying-stall-unblock playbook run.

    ``action`` is one of the menu values — anything else (or a parse
    failure) collapses to ``noop`` with the failure recorded in
    ``reasoning``.

    ``service_name`` is populated only when ``action == "docker_restart"``
    — it's the compose service the action layer will pass to
    ``docker restart``. For ``evaluate_goal`` and ``noop`` it's empty.

    ``raw_response`` is the original Claude stdout, kept for the incident
    folder's ``decision.json``.
    """

    action: Action
    service_name: str
    reasoning: str
    raw_response: str


_VALID_ACTIONS: frozenset[str] = frozenset({"evaluate_goal", "docker_restart", "noop"})


def build_verifying_stall_prompt(
    *,
    goal_id: str,
    objective: str,
    phase: str,
    last_progress_at: str | None,
    detected_at: str,
    threshold_hours: float,
    allowlisted_services: Sequence[str],
) -> str:
    """Render the playbook prompt for a single O3 incident.

    All strings are taken verbatim into the prompt — caller is responsible
    for sanitizing them. In practice they come from devclaw's STATUS.md
    frontmatter (devclaw owns that surface) and the ops-agent's own
    config (allowlist).

    ``allowlisted_services`` is rendered as a JSON list so the model has an
    unambiguous menu of restartable services. An empty list still renders
    (the prompt notes there's nothing to restart) — keeps the docstring
    contract: no implicit dropping of the option from the menu.
    """
    objective_clean = (objective or "(no objective recorded)").strip()
    phase_clean = (phase or "unknown").strip()
    last_progress = last_progress_at or "(no last_progress_at recorded)"
    threshold_str = f"{threshold_hours:.1f}h"
    # JSON-formatting the list avoids the "did Claude see a tuple or a
    # python repr" ambiguity. sorted() so the prompt is stable across runs.
    services_json = json.dumps(sorted(allowlisted_services))

    return textwrap.dedent(
        f"""\
        You are the devclaw ops-agent. A verifying-stall watchdog has fired
        on a durable goal — it's been sitting in a transient phase
        (verifying or in_flight) for over {threshold_str} without any
        progress timestamp advancing. This is the failure mode the
        closeloop incident hit: the goal got stuck because either the
        evaluator was wedged on *direction* or the devclaw-mcp container
        itself was wedged on *infrastructure*.

        Incident context:
          goal_id:           {goal_id}
          objective:         {objective_clean}
          current phase:     {phase_clean}
          last_progress_at:  {last_progress}
          threshold:         {threshold_str}
          detected_at:       {detected_at}

        Restartable services (allowlist): {services_json}

        Action menu:
          - "evaluate_goal" — call the devclaw MCP tool
            evaluate_goal(goal_id). Forces a fresh direction verdict.
            Choose this when the stall looks like a DIRECTION problem
            (the goal's logic is wrong / the done-gate keeps saying
            incomplete for a substantive reason / the rationale would
            inform the next move).
          - "docker_restart" — restart an allowlisted compose service
            via the host docker socket. Choose this ONLY when the stall
            looks like an INFRASTRUCTURE problem (devclaw-mcp itself
            appears frozen / no MCP calls are getting through / a
            container needs to be reset). The response MUST include a
            "service_name" key matching one of the allowlisted services
            above.
          - "noop" — record the incident only. Choose this when neither
            remediation is clearly indicated from the limited context.

        Respond with EXACTLY one JSON object, no surrounding prose:

          {{"action": "evaluate_goal", "reasoning": "<one sentence>"}}
          {{"action": "docker_restart", "service_name": "<one allowlisted name>",
            "reasoning": "<one sentence>"}}
          {{"action": "noop", "reasoning": "<one sentence>"}}

        Be terse. If unsure, choose "noop".
        """
    )


# Best-effort JSON-object extractor: Claude sometimes wraps responses in
# fences or a leading sentence despite the prompt's instruction. We grab
# the FIRST top-level JSON object — strict parse below validates it.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_verifying_stall_decision(raw: str) -> VerifyingStallDecision:
    """Parse Claude's stdout into a :class:`VerifyingStallDecision`.

    Behavior contract:

      - Valid JSON, ``action == "evaluate_goal"``, string ``reasoning`` →
        that decision (service_name = "").
      - Valid JSON, ``action == "noop"``, string ``reasoning`` → that
        decision (service_name = "").
      - Valid JSON, ``action == "docker_restart"``, string ``reasoning``,
        AND a string ``service_name`` that's non-empty, ≤200 chars, and
        matches ``^[A-Za-z0-9_-]+$`` → that decision.
      - Anything else (malformed JSON, unknown action, missing /
        oversized / pattern-failing service_name, non-string reasoning) →
        ``noop`` with the failure surfaced in ``reasoning``. The raw
        response is always preserved.

    NOTE: this parser does NOT check the allowlist itself — that's the
    action layer's job, and double-gating there is what keeps a
    parser-only bug from escalating authority.
    """
    raw_stripped = (raw or "").strip()
    if not raw_stripped:
        return VerifyingStallDecision(
            action="noop",
            service_name="",
            reasoning="parse_failed: empty response",
            raw_response=raw,
        )

    obj = _try_extract_json(raw_stripped)
    if obj is None:
        return VerifyingStallDecision(
            action="noop",
            service_name="",
            reasoning="parse_failed: no JSON object in response",
            raw_response=raw,
        )

    action = obj.get("action")
    reasoning = obj.get("reasoning", "")

    if not isinstance(action, str) or action not in _VALID_ACTIONS:
        return VerifyingStallDecision(
            action="noop",
            service_name="",
            reasoning=f"parse_failed: unknown action {action!r}",
            raw_response=raw,
        )
    if not isinstance(reasoning, str):
        return VerifyingStallDecision(
            action="noop",
            service_name="",
            reasoning="parse_failed: reasoning was not a string",
            raw_response=raw,
        )

    if action in ("evaluate_goal", "noop"):
        return VerifyingStallDecision(
            action=action,  # type: ignore[arg-type]
            service_name="",
            reasoning=reasoning,
            raw_response=raw,
        )

    # action == "docker_restart" — service_name is mandatory + must pass
    # the pattern + length gates. Anything off falls back to noop so we
    # never hand the action a malformed name.
    service_name = obj.get("service_name", "")
    if not isinstance(service_name, str):
        return VerifyingStallDecision(
            action="noop",
            service_name="",
            reasoning="parse_failed: service_name was not a string",
            raw_response=raw,
        )
    stripped = service_name.strip()
    if not stripped:
        return VerifyingStallDecision(
            action="noop",
            service_name="",
            reasoning="parse_failed: missing service_name on docker_restart",
            raw_response=raw,
        )
    if len(stripped) > _MAX_SERVICE_NAME_CHARS:
        return VerifyingStallDecision(
            action="noop",
            service_name="",
            reasoning=f"parse_failed: service_name exceeded {_MAX_SERVICE_NAME_CHARS} chars",
            raw_response=raw,
        )
    if not _SERVICE_NAME_RE.match(stripped):
        return VerifyingStallDecision(
            action="noop",
            service_name="",
            reasoning="parse_failed: service_name failed pattern ^[A-Za-z0-9_-]+$",
            raw_response=raw,
        )

    return VerifyingStallDecision(
        action="docker_restart",
        service_name=stripped,
        reasoning=reasoning,
        raw_response=raw,
    )


def _try_extract_json(text: str) -> dict[str, Any] | None:
    """Find the first top-level JSON object in ``text`` and parse it."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return None
    return None

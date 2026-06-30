"""Playbook: drifting-goal-steer.

Trigger: O2 (no-steering watchdog fires on an executing goal).
Action menu given to Claude:
  - ``steer_goal`` — call MCP ``steer_goal(goal_id, message)`` to inject
    a one-line steering correction into the goal's ``inbox.md``. The
    next-action planner picks it up on the next tick.
  - ``noop``      — record the incident but take no action (used when
    Claude judges the goal looks fine, or when the right correction
    isn't obvious from the limited context the playbook surfaces).

Distinct from the stuck-goal-evaluate playbook (L1) — *deliberately so*.
L1 asks "should we re-evaluate direction?" and the model picks between
"yes, kick it" and "no". L2 asks "what one-line correction should we
inject?" which is a different cognition concern: the model must produce
a concrete steering string, not just choose a verb. Folding both into
one playbook would either bloat the prompt or muddy the decision shape.

The playbook is two pure functions:

  - :func:`build_drifting_goal_prompt` — incident context → prompt string.
  - :func:`parse_drifting_goal_decision` — Claude's raw stdout → typed
    decision (with the steering message validated).

Decision parsing is strict: malformed JSON, missing fields, unknown
action, empty steering message, or steering message over the length
budget all collapse to ``noop`` with the failure surfaced in
``reasoning``. Claude's worst day must not trigger an unintended MCP
write call (and especially must not inject a multi-paragraph essay into
a goal's inbox).
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass
from typing import Any, Literal

Action = Literal["steer_goal", "noop"]

# Steering messages are appended to inbox.md and surfaced verbatim to the
# next-action planner. A 500-char ceiling keeps the model from drifting
# into multi-paragraph essays (the prompt asks for "one short imperative").
_MAX_MESSAGE_CHARS = 500


@dataclass(frozen=True)
class DriftingGoalDecision:
    """Parsed decision from a drifting-goal-steer playbook run.

    ``action`` is one of the two values in the prompt's menu — any other
    value (or a parse failure) is coerced to ``noop`` with the failure
    recorded in ``reasoning``.

    ``message`` is populated only when ``action == "steer_goal"`` — it's
    the one-line correction that gets injected via the L2 action. For
    ``noop`` decisions it's the empty string.

    ``raw_response`` is the original Claude stdout, kept for the incident
    folder's ``decision.json``.
    """

    action: Action
    message: str
    reasoning: str
    raw_response: str


_VALID_ACTIONS: frozenset[str] = frozenset({"steer_goal", "noop"})


def build_drifting_goal_prompt(
    *,
    goal_id: str,
    objective: str,
    phase: str,
    last_steering_at: str | None,
    threshold_hours: float,
    detected_at: str,
) -> str:
    """Render the playbook prompt for a single O2 incident.

    All strings are taken verbatim into the prompt — caller is responsible
    for sanitizing them. In practice they come from devclaw's STATUS.md
    frontmatter and the inbox.md mtime, both of which devclaw owns.
    """
    objective_clean = (objective or "(no objective recorded)").strip()
    phase_clean = (phase or "unknown").strip()
    last_steering = last_steering_at or "(no last_steering_at recorded)"
    # Format threshold to one decimal — matches the way the detector
    # records it (hours, not seconds) and avoids a 24.0 vs 24 noise diff
    # in the prompt across runs.
    threshold_str = f"{threshold_hours:.1f}h"

    return textwrap.dedent(
        f"""\
        You are the devclaw ops-agent. A no-steering watchdog has fired on
        a durable goal — it's still in the executing phase but no steering
        signal (from Denys or from ops-eval) has arrived in over
        {threshold_str}. The goal may be drifting into the weeds without
        anyone noticing.

        Incident context:
          goal_id:           {goal_id}
          objective:         {objective_clean}
          current phase:     {phase_clean}
          last_steering_at:  {last_steering}
          threshold:         {threshold_str}
          detected_at:       {detected_at}

        Action menu:
          - "steer_goal" — call the devclaw MCP tool
            steer_goal(goal_id, message) to inject a one-line steering
            correction into the goal's inbox. The next-action planner
            picks it up on the next tick. Choose this when, given only
            the objective + the silence, you can articulate a concrete
            one-line nudge that's likely to help (e.g. "check in: is the
            objective still right?" or "summarize current progress and
            blockers before continuing"). The message MUST be a single
            short imperative sentence — no multi-paragraph essays.
          - "noop" — record the incident only; take no action. Choose
            this when no useful one-line correction is obvious from the
            limited context, or when the silence might be intentional.

        Respond with EXACTLY one JSON object, no surrounding prose:

          {{"action": "steer_goal", "message": "<one short imperative>",
            "reasoning": "<one sentence>"}}
          {{"action": "noop", "reasoning": "<one sentence>"}}

        The steering message must be under {_MAX_MESSAGE_CHARS} characters.
        Be terse. If unsure, choose "noop".
        """
    )


# Best-effort JSON-object extractor: Claude sometimes wraps responses in
# fences or a leading sentence despite the prompt's instruction. We grab
# the FIRST top-level JSON object — strict parse below validates it.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_drifting_goal_decision(raw: str) -> DriftingGoalDecision:
    """Parse Claude's stdout into a :class:`DriftingGoalDecision`.

    Behavior contract:
      - Valid JSON with ``action == "steer_goal"`` AND a non-empty
        ``message`` string under :data:`_MAX_MESSAGE_CHARS` AND a string
        ``reasoning`` → that decision.
      - Valid JSON with ``action == "noop"`` AND a string ``reasoning``
        → that decision (message defaults to "").
      - Anything else (malformed JSON, missing fields, unknown action,
        empty / oversized / non-string message on a steer decision) →
        ``noop`` with the failure surfaced in ``reasoning``. The raw
        response is always preserved.
    """
    raw_stripped = (raw or "").strip()
    if not raw_stripped:
        return DriftingGoalDecision(
            action="noop",
            message="",
            reasoning="parse_failed: empty response",
            raw_response=raw,
        )

    obj = _try_extract_json(raw_stripped)
    if obj is None:
        return DriftingGoalDecision(
            action="noop",
            message="",
            reasoning="parse_failed: no JSON object in response",
            raw_response=raw,
        )

    action = obj.get("action")
    reasoning = obj.get("reasoning", "")

    if not isinstance(action, str) or action not in _VALID_ACTIONS:
        return DriftingGoalDecision(
            action="noop",
            message="",
            reasoning=f"parse_failed: unknown action {action!r}",
            raw_response=raw,
        )
    if not isinstance(reasoning, str):
        return DriftingGoalDecision(
            action="noop",
            message="",
            reasoning="parse_failed: reasoning was not a string",
            raw_response=raw,
        )

    if action == "noop":
        return DriftingGoalDecision(
            action="noop",
            message="",
            reasoning=reasoning,
            raw_response=raw,
        )

    # action == "steer_goal" — message is required + must pass the length
    # gate. Anything off falls back to noop so we never call MCP with a
    # malformed steering payload.
    message = obj.get("message", "")
    if not isinstance(message, str):
        return DriftingGoalDecision(
            action="noop",
            message="",
            reasoning="parse_failed: message was not a string",
            raw_response=raw,
        )
    stripped = message.strip()
    if not stripped:
        return DriftingGoalDecision(
            action="noop",
            message="",
            reasoning="parse_failed: empty steering message",
            raw_response=raw,
        )
    if len(stripped) > _MAX_MESSAGE_CHARS:
        return DriftingGoalDecision(
            action="noop",
            message="",
            reasoning=f"parse_failed: steering message exceeded {_MAX_MESSAGE_CHARS} chars",
            raw_response=raw,
        )

    return DriftingGoalDecision(
        action="steer_goal",
        message=stripped,
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

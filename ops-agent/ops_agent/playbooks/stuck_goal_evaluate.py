"""Playbook: stuck-goal-evaluate.

Trigger: O1 (no-progress watchdog fires on a goal).
Action menu given to Claude:
  - ``evaluate_goal`` — call MCP ``evaluate_goal(goal_id)`` to force a fresh
    direction verdict (the L1 action; "kick the goal" + capture a verdict).
  - ``noop``         — record the incident but take no action (used when
    Claude judges the goal is recently stuck-for-a-reason or already being
    evaluated).

The playbook is two pure functions:

  - :func:`build_stuck_goal_prompt` — incident context → prompt string.
  - :func:`parse_stuck_goal_decision` — Claude's raw stdout → typed decision.

Decision parsing is intentionally strict: malformed JSON, missing fields,
unknown action — all fall through to a ``noop`` decision with the parse
error in ``reasoning``. The boundary contract: Claude's worst day must not
trigger an unintended MCP write call.
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass
from typing import Any, Literal

Action = Literal["evaluate_goal", "noop"]


@dataclass(frozen=True)
class StuckGoalDecision:
    """Parsed decision from a stuck-goal-evaluate playbook run.

    ``action`` is one of the two values in the prompt's menu — any other
    value (or a parse failure) is coerced to ``noop`` with the failure
    recorded in ``reasoning``.

    ``raw_response`` is the original Claude stdout, kept for the incident
    folder's ``decision.json``.
    """

    action: Action
    reasoning: str
    raw_response: str


_VALID_ACTIONS: frozenset[str] = frozenset({"evaluate_goal", "noop"})


def build_stuck_goal_prompt(
    *,
    goal_id: str,
    objective: str,
    phase: str,
    last_progress_at: str | None,
    last_tick_at: str | None,
    detected_at: str,
) -> str:
    """Render the playbook prompt for a single O1 incident.

    All strings are taken verbatim into the prompt — caller is responsible
    for sanitizing them. (In practice they come from devclaw's STATUS.md
    frontmatter, which devclaw owns; trust boundary is between devclaw and
    user repos, not here.)
    """
    objective_clean = (objective or "(no objective recorded)").strip()
    phase_clean = (phase or "unknown").strip()
    last_progress = last_progress_at or "(no last_progress_at recorded)"
    last_tick = last_tick_at or "(no last_tick_at recorded)"

    return textwrap.dedent(
        f"""\
        You are the devclaw ops-agent. A no-progress watchdog has fired on a
        durable goal — it hasn't made progress in long enough that devclaw
        decided to escalate to its owner.

        Incident context:
          goal_id:           {goal_id}
          objective:         {objective_clean}
          current phase:     {phase_clean}
          last_progress_at:  {last_progress}
          last_tick_at:      {last_tick}
          detected_at:       {detected_at}

        Action menu:
          - "evaluate_goal" — call the devclaw MCP tool evaluate_goal(goal_id)
            to force a fresh direction evaluation NOW. This both wakes the
            goal heartbeat and produces a verdict the incident log captures.
            Choose this when the goal looks genuinely stuck and a fresh
            evaluation might surface why (or unstick it).
          - "noop" — record the incident only; take no action. Choose this
            when the context suggests action would not help (e.g. the goal
            is in a phase where evaluation has already been recent, or the
            phase indicates an intentional pause).

        Respond with EXACTLY one JSON object, no surrounding prose:

          {{"action": "evaluate_goal" | "noop", "reasoning": "<one sentence>"}}

        Be terse. If unsure, choose "noop".
        """
    )


# Best-effort JSON-object extractor: Claude sometimes wraps responses in
# fences or a leading sentence despite the prompt's instruction. We grab the
# FIRST top-level JSON object — strict parse below validates it.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_stuck_goal_decision(raw: str) -> StuckGoalDecision:
    """Parse Claude's stdout into a :class:`StuckGoalDecision`.

    Behavior contract:
      - Valid JSON with ``action`` ∈ {"evaluate_goal", "noop"} and a
        ``reasoning`` string → that decision.
      - Anything else (malformed JSON, missing fields, unknown action,
        non-string reasoning) → ``noop`` with the failure surfaced in
        ``reasoning``. The raw response is always preserved.
    """
    raw_stripped = (raw or "").strip()
    if not raw_stripped:
        return StuckGoalDecision(
            action="noop",
            reasoning="parse_failed: empty response",
            raw_response=raw,
        )

    obj = _try_extract_json(raw_stripped)
    if obj is None:
        return StuckGoalDecision(
            action="noop",
            reasoning="parse_failed: no JSON object in response",
            raw_response=raw,
        )

    action = obj.get("action")
    reasoning = obj.get("reasoning", "")

    if not isinstance(action, str) or action not in _VALID_ACTIONS:
        return StuckGoalDecision(
            action="noop",
            reasoning=f"parse_failed: unknown action {action!r}",
            raw_response=raw,
        )
    if not isinstance(reasoning, str):
        return StuckGoalDecision(
            action="noop",
            reasoning="parse_failed: reasoning was not a string",
            raw_response=raw,
        )

    return StuckGoalDecision(
        action=action,  # type: ignore[arg-type]
        reasoning=reasoning,
        raw_response=raw,
    )


def _try_extract_json(text: str) -> dict[str, Any] | None:
    """Find the first top-level JSON object in ``text`` and parse it.

    Trying the whole-text parse first is the common path; the regex fallback
    catches fenced-or-prefaced responses.
    """
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

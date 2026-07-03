"""Playbook: trend-signal-escalate.

Trigger: O4 (the trend detector's own retrospective self-escalated: the SAME
signal fired on ≥N consecutive days for one goal). The retrospective often
carries its OWN suggested fix (``**Proposed action:**`` line); we surface it to
the model so cognition has the detector's remediation in front of it — not
just "signal X keeps firing."

Action menu (deliberately the same shape as drifting-goal-steer):
  - ``steer_goal`` — call MCP ``steer_goal(goal_id, message)`` to inject a
    one-line correction into the goal's inbox based on the retrospective's
    proposed action (or the model's own read of the signal, when the
    retrospective's action is absent / not applicable). The next-action
    planner picks it up on the next tick.
  - ``noop`` — record the incident only. Choose when the retrospective's
    signal doesn't warrant steering (e.g. observational-only patterns, or
    when the proposed action is a doc update the human should do, not a
    goal-level correction).

Reusing the ``DriftingGoalDecision`` shape keeps dispatch trivial (main.py's
``_dispatch_action`` already handles ``steer_goal``) and avoids inventing a
new decision type when the outcome is identical.
"""

from __future__ import annotations

import textwrap

from .drifting_goal_steer import (
    DriftingGoalDecision,
    parse_drifting_goal_decision,
)

# Reuse the same message-length ceiling as drifting-goal-steer — same target
# (inbox.md), same budget, so a chatty model can't blow it out here either.
_MAX_MESSAGE_CHARS = 500


def build_trend_signal_escalate_prompt(
    *,
    goal_id: str,
    objective: str,
    signal_id: str,
    category: str,
    repeat_count: int,
    first_fired: str,
    latest_fired: str,
    threshold: int,
    proposed_action: str,
    detected_at: str,
) -> str:
    """Render the playbook prompt for a single O4 incident.

    Strings are passed verbatim — caller is responsible for sanitization.
    In practice they come from devclaw's own trend detector output which we
    only READ; no sanitization needed for internal writers.
    """
    objective_clean = (objective or "(no objective recorded)").strip()
    category_clean = (category or "(uncategorized)").strip()
    action_block = (
        f"          proposed action:   {proposed_action}"
        if proposed_action
        else "          proposed action:   (the retrospective did not suggest one)"
    )
    return textwrap.dedent(
        f"""\
        You are the devclaw ops-agent. The trend detector inside devclaw has
        fired the SAME signal on {repeat_count} consecutive daily retrospectives
        (threshold: {threshold}) for one goal. That's the detector itself
        telling us "this pattern isn't going away — someone should look."
        The signal comes with a category the retrospective assigned, and
        sometimes with a Proposed action the retrospective already reasoned
        about.

        Incident context:
          goal_id:           {goal_id}
          objective:         {objective_clean}
          signal_id:         {signal_id}
          category:          {category_clean}
          repeat_count:      {repeat_count} consecutive days
          first_fired:       {first_fired}
          latest_fired:      {latest_fired}
          detected_at:       {detected_at}
{action_block}

        Action menu:
          - "steer_goal" — call the devclaw MCP tool
            steer_goal(goal_id, message) to inject a one-line steering
            correction into the goal's inbox. Choose this when the
            retrospective's proposed action IS a goal-level correction
            (or when you can articulate one from the signal + category).
            Prefer using the retrospective's own proposed action as the
            message when it exists and is a single imperative; otherwise
            write your own one-line correction. The message MUST be a
            single short imperative sentence.
          - "noop" — record the incident only; take no action. Choose
            when the pattern is observational-only, when the proposed
            action requires a human decision the ops-agent shouldn't
            make, or when no useful one-line correction is obvious.

        Respond with EXACTLY one JSON object, no surrounding prose:

          {{"action": "steer_goal", "message": "<one short imperative>",
            "reasoning": "<one sentence>"}}
          {{"action": "noop", "reasoning": "<one sentence>"}}

        The steering message must be under {_MAX_MESSAGE_CHARS} characters.
        Be terse. If unsure, choose "noop".
        """
    )


def parse_trend_signal_escalate_decision(raw: str) -> DriftingGoalDecision:
    """Parse Claude's stdout using the shared drifting-goal-steer parser.

    The decision shape is identical (steer_goal | noop with a message), so we
    reuse the parser wholesale rather than duplicating validation. Rename via
    an alias so the main dispatcher's tuple type reads as one of the four
    trigger-specific decision types (but is structurally a
    :class:`DriftingGoalDecision`)."""
    return parse_drifting_goal_decision(raw)

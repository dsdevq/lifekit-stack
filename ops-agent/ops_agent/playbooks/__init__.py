"""Playbook layer — prompts that ask Claude to pick an action for an incident.

A playbook is two things:
  1. A function that builds a prompt from incident context.
  2. A function that parses Claude's response into a structured decision.

This split is deliberate: the daemon owns the I/O (cognition subprocess +
MCP call) so the playbook stays pure / testable.

ops-PR2 shipped one playbook (:mod:`.stuck_goal_evaluate`) — for O1
no-progress incidents. ops-PR3 adds :mod:`.drifting_goal_steer` for O2
no-steering incidents. The two stay deliberately separate: distinct
cognition concerns ("re-evaluate direction?" vs "what one-line steering
correction?") and distinct decision shapes.
"""

from __future__ import annotations

from .blocked_question_answer import (
    BlockedQuestionDecision,
    build_blocked_question_prompt,
    parse_blocked_question_decision,
)
from .devclaw_bug_fix_ticket import (
    DevclawBugFixDecision,
    build_devclaw_bug_fix_prompt,
    parse_devclaw_bug_fix_decision,
)
from .drifting_goal_steer import (
    DriftingGoalDecision,
    build_drifting_goal_prompt,
    parse_drifting_goal_decision,
)
from .stuck_goal_evaluate import (
    StuckGoalDecision,
    build_stuck_goal_prompt,
    parse_stuck_goal_decision,
)
from .trend_signal_escalate import (
    build_trend_signal_escalate_prompt,
    parse_trend_signal_escalate_decision,
)
from .verifying_stall_unblock import (
    VerifyingStallDecision,
    build_verifying_stall_prompt,
    parse_verifying_stall_decision,
)

__all__ = [
    "BlockedQuestionDecision",
    "DevclawBugFixDecision",
    "DriftingGoalDecision",
    "StuckGoalDecision",
    "VerifyingStallDecision",
    "build_blocked_question_prompt",
    "build_devclaw_bug_fix_prompt",
    "build_drifting_goal_prompt",
    "build_stuck_goal_prompt",
    "build_trend_signal_escalate_prompt",
    "build_verifying_stall_prompt",
    "parse_blocked_question_decision",
    "parse_devclaw_bug_fix_decision",
    "parse_drifting_goal_decision",
    "parse_stuck_goal_decision",
    "parse_trend_signal_escalate_decision",
    "parse_verifying_stall_decision",
]

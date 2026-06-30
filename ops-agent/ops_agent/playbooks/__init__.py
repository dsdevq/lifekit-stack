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
from .verifying_stall_unblock import (
    VerifyingStallDecision,
    build_verifying_stall_prompt,
    parse_verifying_stall_decision,
)

__all__ = [
    "DriftingGoalDecision",
    "StuckGoalDecision",
    "VerifyingStallDecision",
    "build_drifting_goal_prompt",
    "build_stuck_goal_prompt",
    "build_verifying_stall_prompt",
    "parse_drifting_goal_decision",
    "parse_stuck_goal_decision",
    "parse_verifying_stall_decision",
]

"""Playbook layer — prompts that ask Claude to pick an action for an incident.

A playbook is two things:
  1. A function that builds a prompt from incident context.
  2. A function that parses Claude's response into a structured decision.

This split is deliberate: the daemon owns the I/O (cognition subprocess +
MCP call) so the playbook stays pure / testable.

ops-PR2 ships ONE playbook (:mod:`.stuck_goal_evaluate`) — for O1
no-progress incidents. ops-PR3+ adds more.
"""

from __future__ import annotations

from .stuck_goal_evaluate import (
    StuckGoalDecision,
    build_stuck_goal_prompt,
    parse_stuck_goal_decision,
)

__all__ = [
    "StuckGoalDecision",
    "build_stuck_goal_prompt",
    "parse_stuck_goal_decision",
]

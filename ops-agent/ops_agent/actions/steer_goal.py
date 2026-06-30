"""L2 action — inject a one-line steering correction into a drifting goal.

Companion to L1 (:mod:`.evaluate_goal`). Where L1 asks devclaw to
*evaluate* a stuck goal's direction (and produces a verdict), L2 asks
devclaw to *steer* a drifting goal directly — append a one-line
correction to the goal's ``inbox.md`` so the next-action planner picks
it up on the next tick.

This action does ONE thing: call the MCP client's ``steer_goal``. It
never raises — failures become :class:`ActionOutcome` with
``status=failed``. Same daemon-availability discipline as L1: an MCP
outage records a failed outcome; the next polling cycle will re-detect
the same drifting goal.

Authority discipline (see ``~/memory/projects/devclaw/plan.md``):
adding this method is a deliberate escalation from L1. L2 *writes* to a
goal's inbox; L1 only triggers an evaluation. Reviewers should treat
any future PR that broadens this surface (cancel_goal, transition_phase,
etc.) with the same care.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid the circular import at runtime — actions and the package init
    # both reference each other.
    from . import ActionOutcome

from ..mcp_client import DevclawMCPClient, MCPClientError

_log = logging.getLogger("ops_agent.actions.steer_goal")


async def perform_steer_goal(
    goal_id: str,
    message: str,
    mcp: DevclawMCPClient,
) -> ActionOutcome:
    """Invoke ``steer_goal(goal_id, message)`` over MCP; return a structured outcome.

    Args:
      goal_id: the devclaw goal identifier.
      message: the one-line steering correction to inject. Must be
        non-empty; the playbook validates length before calling here.
      mcp:     an open :class:`DevclawMCPClient`.

    Returns:
      :class:`ActionOutcome` — ``status="ok"`` with devclaw's steer
      response dict in ``detail``, or ``status="failed"`` with the typed
      error reason + message. Never raises.
    """
    # Local import to break the circular dependency the package init creates
    # by re-exporting both this function AND ActionOutcome.
    from . import ActionOutcome

    try:
        result = await mcp.steer_goal(goal_id, message)
    except MCPClientError as exc:
        _log.warning(
            "steer_goal action failed goal=%s reason=%s msg=%s",
            goal_id,
            exc.reason,
            exc.message,
        )
        return ActionOutcome(
            action="steer_goal",
            status="failed",
            detail={"goal_id": goal_id, "message": message},
            error_reason=exc.reason,
            error_message=exc.message,
        )
    except Exception as exc:  # defensive — any leaked exception becomes a failed outcome
        _log.exception("steer_goal action raised unexpectedly goal=%s", goal_id)
        return ActionOutcome(
            action="steer_goal",
            status="failed",
            detail={"goal_id": goal_id, "message": message},
            error_reason="unexpected",
            error_message=f"{type(exc).__name__}: {exc}",
        )

    return ActionOutcome(
        action="steer_goal",
        status="ok",
        detail={"goal_id": goal_id, "message": message, "result": result},
    )

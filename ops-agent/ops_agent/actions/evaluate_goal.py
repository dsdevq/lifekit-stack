"""L1 action — force a direction evaluation on a stuck goal.

The 2026-06-29 design correction (see ``~/memory/projects/devclaw/plan.md``):

  - The earlier plan had L1 = ``poke_goal``. That tool doesn't exist on
    devclaw's MCP surface, and adding it just to "wake the heartbeat" is
    strictly less informative than re-evaluating direction.
  - ``evaluate_goal`` achieves the same "kick the goal" effect AND returns
    a fresh verdict the playbook can log. So L1 in ops-PR2 IS ``evaluate_goal``.

This action does ONE thing: call the MCP client's ``evaluate_goal``. It
never raises — failures become :class:`ActionOutcome` with ``status=failed``.
Why: the daemon must never crash on an MCP outage. The playbook records the
failure and the next polling cycle will re-detect the same stuck goal.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid the circular import at runtime — actions and the package init
    # both reference each other.
    from . import ActionOutcome

from ..mcp_client import DevclawMCPClient, MCPClientError

_log = logging.getLogger("ops_agent.actions.evaluate_goal")


async def perform_evaluate_goal(
    goal_id: str,
    mcp: DevclawMCPClient,
) -> ActionOutcome:
    """Invoke ``evaluate_goal(goal_id)`` over MCP; return a structured outcome.

    Args:
      goal_id: the devclaw goal identifier.
      mcp:     an open :class:`DevclawMCPClient`.

    Returns:
      :class:`ActionOutcome` — ``status="ok"`` with the verdict dict in
      ``detail``, or ``status="failed"`` with the MCP error reason +
      message. Never raises.
    """
    # Local import to break the circular dependency the package init creates
    # by re-exporting both this function AND ActionOutcome.
    from . import ActionOutcome

    try:
        verdict = await mcp.evaluate_goal(goal_id)
    except MCPClientError as exc:
        _log.warning(
            "evaluate_goal action failed goal=%s reason=%s msg=%s",
            goal_id,
            exc.reason,
            exc.message,
        )
        return ActionOutcome(
            action="evaluate_goal",
            status="failed",
            detail={"goal_id": goal_id},
            error_reason=exc.reason,
            error_message=exc.message,
        )
    except Exception as exc:  # defensive — any leaked exception becomes a failed outcome
        _log.exception("evaluate_goal action raised unexpectedly goal=%s", goal_id)
        return ActionOutcome(
            action="evaluate_goal",
            status="failed",
            detail={"goal_id": goal_id},
            error_reason="unexpected",
            error_message=f"{type(exc).__name__}: {exc}",
        )

    return ActionOutcome(
        action="evaluate_goal",
        status="ok",
        detail={"goal_id": goal_id, "verdict": verdict},
    )

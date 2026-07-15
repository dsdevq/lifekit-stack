"""L3 action — file a fix ticket against devclaw's OWN repo.

This is the most powerful action ops-agent has. It calls devclaw's
``fix_bug`` MCP tool with ``workspace_dir`` pointing at devclaw's own
source repo — meaning ops-agent asks devclaw to file a fix-PR against
itself, based on the devclaw-defect classifier's diagnosis.

The plan.md design puts L3 behind a *high* confidence threshold:

    "L3 confidence threshold — the devclaw-defect classifier determines
     when ops-PR4 fires. If too tight: ops-agent silent-fails on real
     devclaw bugs. If too loose: it opens noise PRs."

Two guardrails at this layer (the playbook + classifier are the other
two):

  1. ``devclaw_repo_path`` is required and must be a real path — an
     unconfigured L3 short-circuits to a typed failure rather than
     firing against something else.
  2. The action itself never raises: every failure becomes an
     ``ActionOutcome(status="failed", ...)`` so the daemon can continue.

The l3_enabled config flag is checked at the *daemon* layer (main.py) —
this module only ships the mechanism. Same pattern as the L2 steer_goal
action (the module doesn't decide if L2 fires; main.py does).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid the circular import at runtime — actions and the package init
    # both reference each other.
    from . import ActionOutcome

from ..mcp_client import DevclawMCPClient, MCPClientError

_log = logging.getLogger("ops_agent.actions.fix_bug")


async def perform_fix_bug(
    *,
    devclaw_repo_path: str,
    description: str,
    title: str,
    triggering_goal_id: str,
    mcp: DevclawMCPClient,
) -> ActionOutcome:
    """Invoke devclaw's ``fix_bug`` MCP tool against its own repo.

    Args:
      devclaw_repo_path: absolute path to the devclaw source repo on the
        host — becomes ``workspace_dir`` in the MCP call.
      description:       the structured bug report (classifier evidence
        + symptom + suspected file + reproduction) the playbook parsed.
      title:             optional conventional-commit-shaped one-liner;
        may be empty (devclaw's planner will derive one).
      triggering_goal_id: the goal that surfaced the defect. Recorded in
        the outcome so the incident folder can point back at it.
      mcp:               open :class:`DevclawMCPClient`.

    Returns:
      :class:`ActionOutcome` — ``status="ok"`` with the devclaw response
      dict in ``detail``, or ``status="failed"`` with the MCP error
      reason + message. Never raises.
    """
    from . import ActionOutcome  # local — see evaluate_goal for rationale

    if not devclaw_repo_path or not devclaw_repo_path.strip():
        return ActionOutcome(
            action="fix_bug",
            status="failed",
            detail={"triggering_goal_id": triggering_goal_id},
            error_reason="no_devclaw_repo_path",
            error_message="ops-agent has no devclaw_repo_path configured — L3 skipped",
        )

    if not description or not description.strip():
        return ActionOutcome(
            action="fix_bug",
            status="failed",
            detail={"triggering_goal_id": triggering_goal_id},
            error_reason="empty_description",
            error_message="playbook produced no description — refusing to file an empty ticket",
        )

    try:
        result = await mcp.fix_bug(
            workspace_dir=devclaw_repo_path.strip(),
            description=description.strip(),
            title=(title.strip() or None),
        )
    except MCPClientError as exc:
        _log.warning(
            "fix_bug MCP call failed goal=%s reason=%s msg=%s",
            triggering_goal_id,
            exc.reason,
            exc.message,
        )
        return ActionOutcome(
            action="fix_bug",
            status="failed",
            detail={
                "triggering_goal_id": triggering_goal_id,
                "devclaw_repo_path": devclaw_repo_path,
            },
            error_reason=exc.reason,
            error_message=exc.message,
        )

    return ActionOutcome(
        action="fix_bug",
        status="ok",
        detail={
            "triggering_goal_id": triggering_goal_id,
            "devclaw_repo_path": devclaw_repo_path,
            "response": result,
        },
    )

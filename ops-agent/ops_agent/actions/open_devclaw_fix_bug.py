"""L3 action — file a fix_bug ticket against devclaw's own repository.

This is the highest-authority action in the ops-agent's ladder: it asks
devclaw to open a PR against ITSELF fixing a defect the classifier
identified.  The plan (``~/memory/projects/devclaw/plan.md`` §Open
calibration points) explicitly flags this as a calibration hazard —
"if too tight: silent-fails on real devclaw bugs; if too loose: opens
noise PRs".

Two safety gates ride on top of :class:`DevclawMCPClient.fix_bug`:

1. **Classification confidence.**  The caller supplies a
   :class:`DefectClassification`.  If ``is_defect`` is False or
   ``confidence`` is below :data:`_L3_CONFIDENCE_FLOOR`, the action
   short-circuits with ``status="skipped"`` and does NOT call MCP.
2. **Global enable flag.**  Even a high-confidence classification does
   nothing unless ``OPS_AGENT_L3_ENABLED`` is truthy.  Default is OFF.
   When disabled, the action records what it *would* have filed as a
   ``status="dry_run"`` outcome so operators can review the recommendation
   catalogue before flipping the switch.

The dry-run record is deliberately rich (draft description, classifier
evidence, threshold used) — the whole point of the calibration window
is that operators can eyeball the recommendations against reality
BEFORE any PR files.  When the classifier's precision looks acceptable,
setting the env flag switches every future incident from dry-run to real.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import ActionOutcome

from ..classifiers import DefectClassification
from ..classifiers.devclaw_defect import L3_CONFIDENCE_THRESHOLD
from ..mcp_client import DevclawMCPClient, MCPClientError

_log = logging.getLogger("ops_agent.actions.open_devclaw_fix_bug")

#: The confidence floor a classification must clear for the L3 action to
#: consider firing.  Reads :data:`L3_CONFIDENCE_THRESHOLD` from the
#: classifier module by default so the source of truth lives with the
#: signatures; override at call-time for meta-tests.
_L3_CONFIDENCE_FLOOR = L3_CONFIDENCE_THRESHOLD

#: Env flag: L3 filing is opt-in.  Default OFF for the calibration window.
#: Accepts "1" / "true" / "yes" (case-insensitive) as enabling values.
_L3_ENABLED_ENV = "OPS_AGENT_L3_ENABLED"

#: Env: the workspace path devclaw's OWN repo is mounted at inside the
#: devclaw-mcp container.  ``fix_bug`` calls need this because the tool
#: opens the sandbox against the given workspace.
_L3_DEVCLAW_WORKSPACE_ENV = "OPS_AGENT_DEVCLAW_WORKSPACE_DIR"
_L3_DEVCLAW_WORKSPACE_DEFAULT = "/repos/devclaw"


def _l3_enabled() -> bool:
    """True iff the L3 opt-in flag is set to an enabling value.  Any other
    value (unset, ``"0"``, ``"false"``, gibberish) reads as OFF — the
    calibration-window default."""
    raw = os.environ.get(_L3_ENABLED_ENV, "")
    return raw.strip().lower() in {"1", "true", "yes"}


def _devclaw_workspace() -> str:
    """Where devclaw's own source lives inside the target sandbox.  Env-
    overridable so a debug session can point it at a fixture repo."""
    return os.environ.get(_L3_DEVCLAW_WORKSPACE_ENV, _L3_DEVCLAW_WORKSPACE_DEFAULT).strip() \
        or _L3_DEVCLAW_WORKSPACE_DEFAULT


async def perform_open_devclaw_fix_bug(
    classification: DefectClassification,
    mcp: DevclawMCPClient,
    *,
    confidence_floor: float | None = None,
    workspace_dir: str | None = None,
) -> ActionOutcome:
    """Attempt to file a devclaw-side fix-PR based on ``classification``.

    Returns an :class:`ActionOutcome` in one of four shapes:

      - ``skipped`` — classifier said "not a defect" OR confidence too low.
        No MCP call.  ``detail`` explains why.
      - ``dry_run`` — classification cleared the gate but the L3 env flag
        is OFF (default).  ``detail`` carries the draft description +
        classifier evidence so an operator can review offline.
      - ``ok`` — MCP call succeeded; ``detail`` holds devclaw's response
        (task_id + initial status).
      - ``failed`` — MCP call errored; ``error_reason`` + ``error_message``
        carry the typed failure.

    Never raises.  The daemon's non-crashing discipline applies uniformly.
    """
    from . import ActionOutcome

    floor = confidence_floor if confidence_floor is not None else _L3_CONFIDENCE_FLOOR
    workspace = workspace_dir or _devclaw_workspace()

    if not classification.is_defect:
        _log.info("L3 skipped — classifier reports no devclaw defect")
        return ActionOutcome(
            action="open_devclaw_fix_bug",
            status="skipped",
            detail={
                "reason": "not_a_defect",
                "classification": asdict(classification),
                "confidence_floor": floor,
            },
        )

    if classification.confidence < floor:
        _log.info(
            "L3 skipped — classifier confidence %.4f below floor %.4f (signature=%s)",
            classification.confidence, floor, classification.signature,
        )
        return ActionOutcome(
            action="open_devclaw_fix_bug",
            status="skipped",
            detail={
                "reason": "below_confidence_floor",
                "classification": asdict(classification),
                "confidence_floor": floor,
            },
        )

    if not _l3_enabled():
        _log.info(
            "L3 dry-run — %s not enabled, would have filed fix_bug against %s "
            "(signature=%s, confidence=%.4f)",
            _L3_ENABLED_ENV, workspace,
            classification.signature, classification.confidence,
        )
        return ActionOutcome(
            action="open_devclaw_fix_bug",
            status="dry_run",
            detail={
                "reason": "l3_disabled_would_have_filed",
                "workspace_dir": workspace,
                "description_draft": classification.description_draft,
                "classification": asdict(classification),
                "confidence_floor": floor,
                "hint": (
                    f"set {_L3_ENABLED_ENV}=1 to file real fix_bug tickets "
                    "after the classifier's recommendations have been "
                    "eyeballed against real incidents."
                ),
            },
        )

    try:
        result = await mcp.fix_bug(
            workspace_dir=workspace,
            description=classification.description_draft,
            open_pr=True,
        )
    except MCPClientError as exc:
        _log.warning(
            "L3 fix_bug failed reason=%s message=%s", exc.reason, exc.message,
        )
        return ActionOutcome(
            action="open_devclaw_fix_bug",
            status="failed",
            detail={
                "workspace_dir": workspace,
                "classification": asdict(classification),
            },
            error_reason=exc.reason,
            error_message=exc.message,
        )

    _log.info(
        "L3 fix_bug filed workspace=%s signature=%s task_id=%s",
        workspace, classification.signature, result.get("task_id"),
    )
    return ActionOutcome(
        action="open_devclaw_fix_bug",
        status="ok",
        detail={
            "workspace_dir": workspace,
            "classification": asdict(classification),
            "mcp_response": result,
        },
    )

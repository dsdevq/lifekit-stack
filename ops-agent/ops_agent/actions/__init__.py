"""Action layer — the small set of side effects ops-agent is authorized to
perform on devclaw, one module per authority level.

ops-PR2 shipped L1 (:mod:`.evaluate_goal`). ops-PR3 adds L2
(:mod:`.steer_goal` — inbox injection on drifting goals). L3
(devclaw-bug-fix-ticket) stays deferred — see
``~/memory/projects/devclaw/plan.md``.

Each action returns an :class:`ActionOutcome` regardless of success / failure
so the playbook layer above can serialize the result to ``outcome.md``
without branching on exceptions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .evaluate_goal import perform_evaluate_goal
from .steer_goal import perform_steer_goal

__all__ = [
    "ActionOutcome",
    "perform_evaluate_goal",
    "perform_steer_goal",
    "outcome_to_dict",
]


@dataclass(frozen=True)
class ActionOutcome:
    """Outcome of one action attempt — never raises out of the action layer.

    ``status``:
      - ``ok``      — the MCP call succeeded and we have a verdict (or
                      whatever the action's success shape is).
      - ``failed``  — the action threw; ``error_reason`` + ``error_message``
                      carry the typed failure.

    ``action`` is the human-readable name (e.g. ``"evaluate_goal"``) so the
    incident log can summarize without consulting the playbook.
    """

    action: str
    status: str  # "ok" | "failed"
    detail: dict[str, Any] = field(default_factory=dict)
    error_reason: str | None = None
    error_message: str | None = None


def outcome_to_dict(outcome: ActionOutcome) -> dict[str, Any]:
    """Helper for tests + serialization — keeps the dataclass private."""
    return asdict(outcome)

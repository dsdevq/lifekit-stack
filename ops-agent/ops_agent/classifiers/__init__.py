"""Classifiers — deterministic pattern-matchers that turn incident context
into a typed verdict the higher-authority playbooks (L3) can gate on.

The plan's escalation ladder (``~/memory/projects/devclaw/plan.md`` §Operations
agent) reserves L3 — filing a fix-PR against devclaw itself — for cases where
the incident matches a *devclaw-side* defect signature with high confidence.
The primitive that makes that gate meaningful is here: a classifier that says
"yes, this is one of the known devclaw failure modes" or "no, this looks like
a user-repo problem" — never handwaving into ambiguity.

Classifiers are strictly pattern-matching: no cognition call, no side effects,
pure functions over incident context. That's what makes them trustworthy under
the daemon's non-crashing discipline.
"""

from .devclaw_defect import (
    DefectClassification,
    classify_devclaw_defect,
    known_signatures,
)

__all__ = [
    "DefectClassification",
    "classify_devclaw_defect",
    "known_signatures",
]

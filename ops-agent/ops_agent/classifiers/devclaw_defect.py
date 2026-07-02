"""Devclaw-defect classifier — the L3 confidence gate.

The plan's calibration point (``~/memory/projects/devclaw/plan.md`` §Open
calibration points): *if too tight, silent-fails on real devclaw bugs; if
too loose, opens noise PRs*.  This module's job is to be **narrow and
transparent** rather than clever — every match is grounded in a specific
substring family drawn from a real incident, so a false positive is
diagnosable from the ``evidence`` field alone.

Signature catalogue (v1)
------------------------

The catalogue below is drawn straight from the incidents in devclaw's log
and the 2026-06-29 closeloop post-mortem.  Each entry is:

    ``signature``        stable slug used by playbook + description drafts
    ``pattern``          case-insensitive substring that indicates the mode
    ``confidence``       floor for a single-hit match (repeats can only raise)
    ``description``      the fix_bug description this signature would file

Adding a signature is a deliberate PR: the catalog IS the L3 authority
surface.  Widening it without evidence is the fast way to turn L3 into
noise.

Design choices
--------------

- **Deterministic**: no cognition call; no probabilistic scoring beyond the
  count-of-hits arithmetic below.  Deterministic makes calibration a
  matter of adding/removing signatures, not tuning a model.
- **No side effects**: pure function over strings.  The action layer
  gates the actual fix_bug call on ``is_defect and confidence >= threshold``.
- **Evidence-first**: every classification carries the exact substrings
  that triggered it — an operator can audit a decision in seconds.

The classifier ONLY produces a recommendation; the L3 action still
gates it behind ``OPS_AGENT_L3_ENABLED`` (default off) so no PR ever
files without deliberate opt-in during the calibration window.
"""

from __future__ import annotations

from dataclasses import dataclass, field


#: Default threshold the L3 action uses to decide whether to fire.  Kept
#: here (module-level) rather than in the action so tests + prod share the
#: same source of truth.  The plan flags this as an empirical calibration
#: point; ship high, lower as the corpus grows.
L3_CONFIDENCE_THRESHOLD = 0.75


@dataclass(frozen=True)
class _Signature:
    """One devclaw-defect pattern.  Kept private; the catalog is exposed
    through :func:`known_signatures` for tests + docs."""

    slug: str
    patterns: tuple[str, ...]
    #: single-hit confidence.  N hits raise this asymptotically toward 1.0
    #: via ``1 - (1 - base) ** hits`` — one hit is a strong signal already
    #: when the pattern is specific enough (``review was cut off``).
    base_confidence: float
    description_draft: str
    rationale: str


# The catalog is intentionally small in v1.  Every entry has appeared in a
# real incident.  Ordering does not matter (classify() considers all).
_CATALOG: tuple[_Signature, ...] = (
    _Signature(
        slug="eval_truncation",
        patterns=(
            "review was cut off",
            "review cut off mid-exploration",
            "cut off mid-exploration",
        ),
        base_confidence=0.85,
        description_draft=(
            "The evaluator is truncating its own review report and returning "
            "phantom 'review cut off' verdicts.  This is the 2026-06-29 "
            "closeloop failure mode: the done-gate evaluator's review-report "
            "extraction is cutting off the actual per-clause evidence section. "
            "Look at ``devclaw/goal/evaluator.py`` — ``_REVIEW_REPORT_KEEP`` "
            "and ``_extract_review_report``.  Verify the tail-of-report "
            "extraction finds the LAST ``## Per-clause evidence`` header and "
            "keeps enough characters to fit the filled-in report."
        ),
        rationale=(
            "Evaluator rationale repeatedly claims the review was cut off, "
            "which is a devclaw-side extraction defect (the reviewer's output "
            "was intact; devclaw truncated it before showing the evaluator)."
        ),
    ),
    _Signature(
        slug="planner_loop",
        patterns=(
            "same action for the third time",
            "planner keeps proposing",
            "next-action planner is looping",
            "loop: same action",
        ),
        base_confidence=0.70,
        description_draft=(
            "The next-action planner is proposing the same action repeatedly. "
            "This suggests the planner isn't seeing the results of its own "
            "previous dispatches (recent-log window too small, delivery "
            "summary not being included, or the state_store isn't advancing).  "
            "Instrument ``devclaw/goal/planner.py`` around ``build_prompt`` — "
            "confirm the ``deliveries`` string reflects the most recent PR."
        ),
        rationale=(
            "Repeated identical proposals from the planner point at a devclaw-"
            "side context-building defect, not a user-repo problem."
        ),
    ),
    _Signature(
        slug="stub_disguise",
        patterns=(
            "not_yet_available stub",
            "unauthorized stub — evidence",
        ),
        base_confidence=0.55,
        description_draft=(
            "The evaluator is downgrading clauses because they're satisfied "
            "by an unauthorized ``not_yet_available`` stub.  This is the "
            "safety net firing correctly, but the *repeated* trigger points "
            "at a decomposer that keeps emitting stubs the goal didn't "
            "authorize.  Look at ``devclaw/goal/decomposer.py`` — the "
            "prompt should FORBID stubs unless ``stub_acceptable`` names "
            "the tool.  Verify the current prompt reflects that."
        ),
        rationale=(
            "A run of stub-authorization downgrades means the decomposer is "
            "producing unauthorized stubs — decomposer-side, not user-repo."
        ),
    ),
    _Signature(
        slug="workspace_break_storm",
        patterns=(
            "workspace break tripped",
            "workspace_break_tripped",
        ),
        base_confidence=0.60,
        description_draft=(
            "The workspace circuit-breaker keeps tripping for the same "
            "workspace.  Beyond a single trip (which is the intended "
            "protection), repeated trips mean the underlying failure isn't "
            "self-healing.  Diagnose whether the failure mode is inside "
            "devclaw (e.g. sandbox provisioning) or inside the target repo "
            "(e.g. verify-cmd broken).  If devclaw-side, look at the "
            "sandbox layer + queue retry loop."
        ),
        rationale=(
            "Repeated circuit-breaker trips indicate a persistent failure "
            "the queue can't clear by itself — usually devclaw's sandbox "
            "or execution layer."
        ),
    ),
)


@dataclass(frozen=True)
class DefectClassification:
    """Verdict returned by :func:`classify_devclaw_defect`.

    ``is_defect`` is True iff *any* signature matched.  ``confidence`` is
    the max across matches — a stronger match dominates.  ``signature`` is
    the slug of the strongest match, or ``""`` when nothing matched.

    ``description_draft`` is the fix_bug description the L3 action would
    file if it fired.  Reviewing draft strings in isolation is the primary
    calibration read.

    ``evidence`` is every substring that triggered a match, in order.  A
    reviewer can grep the incident folder against this list to confirm
    each signal is real.
    """

    is_defect: bool
    signature: str
    confidence: float
    description_draft: str
    rationale: str
    evidence: list[str] = field(default_factory=list)


def known_signatures() -> list[dict]:
    """Introspection hook for tests + docs.  Returns each signature's slug,
    patterns, and single-hit base confidence.  Kept as plain dicts so a
    downstream reviewer can dump the catalog to stdout without importing
    ops_agent internals."""
    return [
        {
            "slug": s.slug,
            "patterns": list(s.patterns),
            "base_confidence": s.base_confidence,
        }
        for s in _CATALOG
    ]


def _hits(haystack: str, patterns: tuple[str, ...]) -> list[str]:
    """Return every pattern that appears in ``haystack`` (case-insensitive).
    Duplicates from repeated matches ARE included — the count feeds the
    confidence bump for a signature that fired multiple times."""
    low = haystack.lower()
    found: list[str] = []
    for p in patterns:
        # ``count`` because a signature that fires 3 times across the corpus
        # is stronger evidence than a single hit; the confidence bump reflects
        # that below.
        count = low.count(p.lower())
        found.extend([p] * count)
    return found


def _raise_confidence(base: float, hits: int) -> float:
    """Combine independent hit evidence: ``1 - (1 - base) ** hits``.  One hit
    is base; two hits is asymptotic toward 1.0 but never exceeds 1.0.
    Capped at 1.0 defensively (float ops can go slightly over)."""
    if hits <= 0:
        return 0.0
    conf = 1.0 - (1.0 - base) ** hits
    return min(conf, 1.0)


def classify_devclaw_defect(
    text_corpus: list[str],
) -> DefectClassification:
    """Match the incident's aggregated text against the signature catalog.

    ``text_corpus`` is the pool of strings the classifier reads — typically
    recent evaluator rationales, correction lists, error tails from failed
    tasks.  The caller assembles the corpus so the classifier stays pure.

    Behaviour:

    - Every signature is scored against the concatenated corpus.
    - The strongest match (highest confidence) wins.  ``is_defect`` is True
      iff any signature had at least one hit.
    - ``evidence`` accumulates the exact substrings that matched.
    - When nothing matches, returns an ``is_defect=False`` verdict with an
      empty signature slug — safe to hand to the L3 gate (it won't fire).
    """
    haystack = "\n".join(s for s in text_corpus if isinstance(s, str) and s)
    if not haystack.strip():
        return DefectClassification(
            is_defect=False,
            signature="",
            confidence=0.0,
            description_draft="",
            rationale="no incident text supplied",
        )

    best: tuple[float, _Signature, list[str]] | None = None
    for sig in _CATALOG:
        hits = _hits(haystack, sig.patterns)
        if not hits:
            continue
        conf = _raise_confidence(sig.base_confidence, len(hits))
        if best is None or conf > best[0]:
            best = (conf, sig, hits)

    if best is None:
        return DefectClassification(
            is_defect=False,
            signature="",
            confidence=0.0,
            description_draft="",
            rationale="no signature in the catalog matched the incident text",
        )
    conf, sig, evidence = best
    return DefectClassification(
        is_defect=True,
        signature=sig.slug,
        confidence=round(conf, 4),
        description_draft=sig.description_draft,
        rationale=sig.rationale,
        evidence=evidence,
    )

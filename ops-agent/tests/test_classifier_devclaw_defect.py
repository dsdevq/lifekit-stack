"""Tests for the devclaw-defect classifier — the L3 confidence gate.

Deterministic pattern-matching, so every test is a substring check.  These
tests exist to keep the *catalog* honest — a signature that fires on
prose the corpus never contains, or misses prose it should, is a false
positive/negative one edit away.
"""
from __future__ import annotations

import pytest

from ops_agent.classifiers import (
    DefectClassification,
    classify_devclaw_defect,
    known_signatures,
)
from ops_agent.classifiers.devclaw_defect import L3_CONFIDENCE_THRESHOLD


def test_empty_corpus_is_not_a_defect():
    verdict = classify_devclaw_defect([])
    assert verdict.is_defect is False
    assert verdict.signature == ""
    assert verdict.confidence == 0.0
    assert "no incident text" in verdict.rationale.lower()


def test_whitespace_only_corpus_is_not_a_defect():
    verdict = classify_devclaw_defect(["   ", "", "\n\n"])
    assert verdict.is_defect is False


def test_no_matches_returns_clean_verdict_without_firing():
    verdict = classify_devclaw_defect([
        "The evaluator returned achieved cleanly.",
        "PR #42 merged; verify gate green.",
    ])
    assert verdict.is_defect is False
    assert verdict.signature == ""
    assert verdict.confidence == 0.0
    assert verdict.description_draft == ""
    assert verdict.evidence == []


def test_eval_truncation_signature_matches_closeloop_incident_prose():
    """The 2026-06-29 closeloop incident's evaluator rationale said 'review
    was cut off mid-exploration' repeatedly.  That exact phrase must fire
    the eval_truncation signature high enough to clear the L3 threshold."""
    corpus = [
        "review was cut off mid-exploration — cannot judge done_when clauses",
        "review was cut off — the report ended before the per-clause evidence section",
        "review was cut off; verdict is off_track pending a fresh read",
    ]
    verdict = classify_devclaw_defect(corpus)
    assert verdict.is_defect is True
    assert verdict.signature == "eval_truncation"
    # 3 hits of a base-0.85 pattern → confidence approaches 1.0
    assert verdict.confidence >= L3_CONFIDENCE_THRESHOLD
    assert "review was cut off" in "\n".join(verdict.evidence).lower()
    assert "evaluator" in verdict.description_draft.lower()


def test_planner_loop_signature_fires_but_below_threshold_on_a_single_hit():
    """The planner_loop signature has a lower base confidence (0.70) because
    the phrases are less specific than eval_truncation.  A single hit
    should register as a defect but stay BELOW the default L3 threshold —
    the action layer will short-circuit to `skipped`.  Repeated hits push
    it over the line."""
    # single hit: corpus mentions exactly one planner_loop pattern
    single = classify_devclaw_defect([
        "the planner keeps proposing action X yet again",
    ])
    assert single.is_defect is True
    assert single.signature == "planner_loop"
    # 0.70 base, single hit → 0.70 < 0.75 default floor
    assert single.confidence == pytest.approx(0.70, abs=1e-4)
    assert single.confidence < L3_CONFIDENCE_THRESHOLD

    # multiple hits push past the threshold
    repeated = classify_devclaw_defect([
        "the planner keeps proposing the same tool call",
        "another verdict: same action for the third time now",
    ])
    assert repeated.confidence > single.confidence
    assert repeated.confidence >= L3_CONFIDENCE_THRESHOLD


def test_workspace_break_storm_signature_matches_the_trip_event_shape():
    """Multiple workspace_break_tripped events on the same workspace
    look like a persistent devclaw-side defect (the failure isn't self-
    clearing).  Classifier picks it up."""
    verdict = classify_devclaw_defect([
        "task 1: workspace_break_tripped for /repos/target",
        "task 2: workspace_break_tripped for /repos/target — 3rd in 15min",
    ])
    assert verdict.is_defect is True
    assert verdict.signature == "workspace_break_storm"
    assert verdict.confidence > 0.0
    assert "circuit-breaker" in verdict.description_draft.lower()


def test_strongest_signature_wins_when_multiple_match():
    """A corpus that carries evidence for multiple signatures should
    report the highest-confidence one — that's the actionable finding."""
    verdict = classify_devclaw_defect([
        "review was cut off mid-exploration — cannot judge",
        "workspace_break_tripped once during the incident",
    ])
    # eval_truncation (0.85 base) beats workspace_break_storm (0.60 base)
    # even on a single hit each.
    assert verdict.signature == "eval_truncation"
    # Both matches contribute to the evidence field so the reviewer sees
    # what else was going on.
    ev = "\n".join(verdict.evidence).lower()
    assert "review was cut off" in ev


def test_case_insensitive_matching():
    """The prose comes from LLM output + logs — casing varies.  Classifier
    matches case-insensitively."""
    verdict = classify_devclaw_defect([
        "REVIEW WAS CUT OFF MID-EXPLORATION — capitalized log line",
    ])
    assert verdict.is_defect is True
    assert verdict.signature == "eval_truncation"


def test_known_signatures_matches_catalog_size():
    """Introspection hook: the exposed catalog list must stay in sync with
    the internal tuple.  Prevents an accidental slug rename from silently
    dropping a signature."""
    catalog = known_signatures()
    # v1 catalog has 4 signatures; if we grow it, bump the assertion + add
    # a fixture for the new one to force a review of the addition.
    assert len(catalog) == 4
    slugs = {entry["slug"] for entry in catalog}
    assert slugs == {
        "eval_truncation",
        "planner_loop",
        "stub_disguise",
        "workspace_break_storm",
    }
    for entry in catalog:
        assert entry["patterns"], f"{entry['slug']} has no patterns"
        assert 0.0 < entry["base_confidence"] <= 1.0


def test_stub_disguise_matches_validator_downgrade_prose():
    """The evaluator's validate() writes 'unauthorized stub — evidence (…)'
    corrections when downgrading a stub-disguised clause.  Classifier
    should recognize that recurring string as a decomposer defect."""
    verdict = classify_devclaw_defect([
        "correction 1: unauthorized stub — evidence (not_yet_available payload)",
        "correction 2: unauthorized stub — evidence (get_x returns not_yet_available)",
    ])
    assert verdict.is_defect is True
    assert verdict.signature == "stub_disguise"
    assert "decomposer" in verdict.description_draft.lower()

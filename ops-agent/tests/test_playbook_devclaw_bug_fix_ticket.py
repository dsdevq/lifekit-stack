"""Unit tests for the L3 devclaw-bug-fix-ticket playbook (ops-PR4).

The playbook is two pure functions:
  * ``build_devclaw_bug_fix_prompt`` — pins the prompt shape (evidence + repo
    path + decision-rules block).
  * ``parse_devclaw_bug_fix_decision`` — must coerce malformed / empty / oversized
    responses to noop rather than firing an L3 fix-PR on garbage.
"""

from __future__ import annotations

import json

from ops_agent.playbooks import (
    build_devclaw_bug_fix_prompt,
    parse_devclaw_bug_fix_decision,
)

# ── prompt shape ───────────────────────────────────────────────────────────


def test_prompt_includes_devclaw_repo_path_and_signature():
    prompt = build_devclaw_bug_fix_prompt(
        goal_id="g1",
        objective="build the thing",
        phase="executing",
        signature="eval_truncation",
        evidence=("last_eval_verdict=stalled", "note contains truncation marker"),
        confidence="medium",
        devclaw_repo_path="/srv/devclaw",
        detected_at="2026-07-04T12:00:00+00:00",
    )
    assert "/srv/devclaw" in prompt
    assert "eval_truncation" in prompt
    assert "medium" in prompt
    assert "last_eval_verdict=stalled" in prompt
    assert "note contains truncation marker" in prompt
    # The prompt must be explicit that noop is the safer default; the wording
    # is verified so a future edit can't quietly remove it.
    assert "Bias to noop" in prompt


def test_prompt_handles_empty_objective_and_phase():
    prompt = build_devclaw_bug_fix_prompt(
        goal_id="g1",
        objective="",
        phase="",
        signature="planner_loop",
        evidence=(),
        confidence="medium",
        devclaw_repo_path="/srv/devclaw",
        detected_at="2026-07-04T12:00:00+00:00",
    )
    assert "(no objective recorded)" in prompt
    assert "unknown" in prompt


# ── parser: happy paths ────────────────────────────────────────────────────


def test_parse_fix_bug_with_all_fields():
    description = (
        "Symptom: goal blocked with `review cut off` verdict. "
        "Evidence: last_eval_note contains truncation marker. "
        "Suspected file: devclaw/goal/evaluator.py. "
        "Repro: seed a fixture with a mid-response cut and re-run the eval fixture."
    )
    reasoning = "classifier hit eval_truncation with a concrete note excerpt — worth filing"
    raw = json.dumps(
        {
            "action": "fix_bug",
            "title": "fix(evaluator): handle truncated cognition output",
            "description": description,
            "reasoning": reasoning,
        }
    )
    d = parse_devclaw_bug_fix_decision(raw)
    assert d.action == "fix_bug"
    assert d.title.startswith("fix(evaluator)")
    assert "Symptom" in d.description
    assert "classifier hit" in d.reasoning


def test_parse_noop_with_reasoning():
    raw = json.dumps(
        {
            "action": "noop",
            "title": "",
            "description": "",
            "reasoning": "evidence too thin — signature match on a single log line",
        }
    )
    d = parse_devclaw_bug_fix_decision(raw)
    assert d.action == "noop"
    assert d.description == ""
    assert "evidence too thin" in d.reasoning


def test_parse_extracts_json_from_prose_wrapper():
    body = '{"action":"noop","title":"","description":"","reasoning":"fine"}'
    raw = f"Sure! Here is my decision:\n{body}\nThanks."
    d = parse_devclaw_bug_fix_decision(raw)
    assert d.action == "noop"


# ── parser: safety collapses ───────────────────────────────────────────────


def test_parse_empty_response_is_noop():
    d = parse_devclaw_bug_fix_decision("")
    assert d.action == "noop"
    assert "empty response" in d.reasoning


def test_parse_unparseable_json_is_noop():
    d = parse_devclaw_bug_fix_decision("this is not json at all")
    assert d.action == "noop"
    assert "not parseable" in d.reasoning


def test_parse_unknown_action_is_noop():
    raw = json.dumps({"action": "delete_repo", "description": "x", "reasoning": "chaos"})
    d = parse_devclaw_bug_fix_decision(raw)
    assert d.action == "noop"
    assert "unknown action" in d.reasoning


def test_parse_fix_bug_with_empty_description_is_noop():
    # This is the load-bearing safety collapse — an empty description means
    # the model didn't have a specific enough diagnosis. Firing L3 on an
    # empty ticket is the worst-case behaviour.
    raw = json.dumps(
        {
            "action": "fix_bug",
            "title": "fix: something",
            "description": "",
            "reasoning": "sure why not",
        }
    )
    d = parse_devclaw_bug_fix_decision(raw)
    assert d.action == "noop"
    assert "empty description" in d.reasoning


def test_parse_fix_bug_with_oversized_description_is_noop():
    huge = "x" * 3000
    raw = json.dumps({"action": "fix_bug", "title": "t", "description": huge, "reasoning": "r"})
    d = parse_devclaw_bug_fix_decision(raw)
    assert d.action == "noop"
    assert "exceeded" in d.reasoning


def test_parse_fix_bug_with_oversized_title_is_truncated_not_dropped():
    long_title = "fix(x): " + ("word " * 40).strip()  # > 100 chars
    raw = json.dumps(
        {
            "action": "fix_bug",
            "title": long_title,
            "description": "a real description here",
            "reasoning": "r",
        }
    )
    d = parse_devclaw_bug_fix_decision(raw)
    assert d.action == "fix_bug"
    assert len(d.title) <= 100
    assert d.title.startswith("fix(x):")

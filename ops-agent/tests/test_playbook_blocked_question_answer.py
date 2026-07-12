"""Unit tests for the O5 blocked-question-answer playbook (prompt + parser)."""

from __future__ import annotations

from ops_agent.playbooks import (
    build_blocked_question_prompt,
    parse_blocked_question_decision,
)
from ops_agent.playbooks.blocked_question_answer import _MAX_ANSWER_CHARS

# ---- build_blocked_question_prompt -------------------------------------


def test_prompt_includes_all_context_fields() -> None:
    p = build_blocked_question_prompt(
        goal_id="finance-sentry",
        objective="ship the UI library",
        blocked_on="is package-lock.json out of sync on main?",
        last_eval_verdict="needs_human",
        last_eval_note="cannot decide without the lockfile",
        detected_at="2026-07-12T02:00:00+00:00",
        evidence_repo_path="/data/workspaces/finance-sentry",
    )
    assert "finance-sentry" in p
    assert "ship the UI library" in p
    assert "package-lock.json" in p
    assert "needs_human" in p
    assert "2026-07-12T02:00:00+00:00" in p
    # Authority framing + action menu present.
    assert "steer_goal" in p
    assert "escalate" in p
    assert "noop" in p
    assert "AUTHORITY" in p


def test_prompt_evidence_mode_mentions_tools_and_repo() -> None:
    p = build_blocked_question_prompt(
        goal_id="g",
        objective="o",
        blocked_on="q",
        last_eval_verdict=None,
        last_eval_note="",
        detected_at="t",
        evidence_repo_path="/data/workspaces/g",
    )
    assert "/data/workspaces/g" in p
    assert "read-only tools" in p
    assert "EVIDENCE" in p


def test_prompt_no_evidence_mode_forces_escalate() -> None:
    p = build_blocked_question_prompt(
        goal_id="g",
        objective="o",
        blocked_on="q",
        last_eval_verdict=None,
        last_eval_note="",
        detected_at="t",
        evidence_repo_path=None,
    )
    assert "NO EVIDENCE" in p
    assert "must escalate" in p.lower()
    # No repo path claim in no-evidence mode.
    assert "read-only tools" not in p


def test_prompt_handles_missing_optional_fields() -> None:
    p = build_blocked_question_prompt(
        goal_id="g",
        objective="",
        blocked_on="",
        last_eval_verdict=None,
        last_eval_note="",
        detected_at="t",
        evidence_repo_path=None,
    )
    assert "no objective recorded" in p
    assert "no blocked_on recorded" in p


# ---- parse — valid responses -------------------------------------------


def test_parse_valid_steer_answer() -> None:
    raw = (
        '{"action": "steer_goal", '
        '"answer": "package-lock.json IS out of sync; regenerate with npm install", '
        '"reasoning": "diff showed react pinned 18.2 but node_modules has 18.3"}'
    )
    d = parse_blocked_question_decision(raw)
    assert d.action == "steer_goal"
    assert "out of sync" in d.answer
    assert d.escalation_reason == ""
    assert "18.2" in d.reasoning


def test_parse_valid_escalate() -> None:
    raw = (
        '{"action": "escalate", '
        '"escalation_reason": "this is a product-priority call", '
        '"reasoning": "asks whether the objective is still right"}'
    )
    d = parse_blocked_question_decision(raw)
    assert d.action == "escalate"
    assert "product-priority" in d.escalation_reason
    assert d.answer == ""


def test_parse_valid_noop() -> None:
    d = parse_blocked_question_decision('{"action": "noop", "reasoning": "context too thin"}')
    assert d.action == "noop"


def test_parse_escalate_without_reason_defaults() -> None:
    d = parse_blocked_question_decision('{"action": "escalate", "reasoning": "human call"}')
    assert d.action == "escalate"
    assert d.escalation_reason == "(no escalation reason given)"


def test_parse_leading_prose_and_fences() -> None:
    raw = 'Here is my decision:\n```json\n{"action": "noop", "reasoning": "r"}\n```'
    d = parse_blocked_question_decision(raw)
    assert d.action == "noop"


# ---- parse — defensive fallbacks (bias: never auto-answer on garbage) ---


def test_parse_empty_falls_back_to_noop() -> None:
    d = parse_blocked_question_decision("")
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning
    assert "empty" in d.reasoning


def test_parse_non_json_falls_back_to_noop() -> None:
    d = parse_blocked_question_decision("not json at all")
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_unknown_action_falls_back_to_noop() -> None:
    raw = '{"action": "docker_restart", "reasoning": "wrong playbook"}'
    d = parse_blocked_question_decision(raw)
    assert d.action == "noop"
    assert "unknown action" in d.reasoning


def test_parse_steer_with_empty_answer_falls_back_to_noop() -> None:
    raw = '{"action": "steer_goal", "answer": "   ", "reasoning": "r"}'
    d = parse_blocked_question_decision(raw)
    assert d.action == "noop"
    assert "empty answer" in d.reasoning


def test_parse_steer_with_missing_answer_falls_back_to_noop() -> None:
    raw = '{"action": "steer_goal", "reasoning": "forgot the answer"}'
    d = parse_blocked_question_decision(raw)
    assert d.action == "noop"
    assert "empty answer" in d.reasoning


def test_parse_steer_with_oversized_answer_falls_back_to_noop() -> None:
    big = "x" * (_MAX_ANSWER_CHARS + 1)
    raw = f'{{"action": "steer_goal", "answer": "{big}", "reasoning": "r"}}'
    d = parse_blocked_question_decision(raw)
    assert d.action == "noop"
    assert "exceeded" in d.reasoning


def test_parse_steer_with_non_string_answer_falls_back_to_noop() -> None:
    raw = '{"action": "steer_goal", "answer": 123, "reasoning": "r"}'
    d = parse_blocked_question_decision(raw)
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_non_string_reasoning_falls_back_to_noop() -> None:
    raw = '{"action": "escalate", "reasoning": 5}'
    d = parse_blocked_question_decision(raw)
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_raw_response_always_preserved() -> None:
    raw = "garbage-in"
    d = parse_blocked_question_decision(raw)
    assert d.raw_response == raw

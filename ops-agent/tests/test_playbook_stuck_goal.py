"""Unit tests for the stuck-goal-evaluate playbook (prompt + parser)."""

from __future__ import annotations

from ops_agent.playbooks import (
    build_stuck_goal_prompt,
    parse_stuck_goal_decision,
)

# ---- build_stuck_goal_prompt ---------------------------------------------


def test_prompt_includes_all_context_fields() -> None:
    p = build_stuck_goal_prompt(
        goal_id="g1",
        objective="ship X",
        phase="in_flight",
        last_progress_at="2026-06-29T06:00:00+00:00",
        last_tick_at="2026-06-29T11:55:00+00:00",
        detected_at="2026-06-29T12:00:00+00:00",
    )
    assert "g1" in p
    assert "ship X" in p
    assert "in_flight" in p
    assert "2026-06-29T06:00:00+00:00" in p
    assert "2026-06-29T11:55:00+00:00" in p
    assert "2026-06-29T12:00:00+00:00" in p
    # Action menu is part of the prompt.
    assert "evaluate_goal" in p
    assert "noop" in p
    # Response shape is part of the prompt.
    assert "JSON" in p


def test_prompt_handles_missing_optional_fields() -> None:
    p = build_stuck_goal_prompt(
        goal_id="g",
        objective="",
        phase="",
        last_progress_at=None,
        last_tick_at=None,
        detected_at="2026-06-29T12:00:00+00:00",
    )
    # Empty objective falls back to a placeholder so the prompt stays readable.
    assert "no objective recorded" in p
    assert "no last_progress_at recorded" in p
    assert "no last_tick_at recorded" in p


# ---- parse_stuck_goal_decision — valid responses ------------------------


def test_parse_valid_evaluate_goal_decision() -> None:
    raw = '{"action": "evaluate_goal", "reasoning": "Stuck >6h, fresh eval may unblock"}'
    d = parse_stuck_goal_decision(raw)
    assert d.action == "evaluate_goal"
    assert "Stuck" in d.reasoning
    assert d.raw_response == raw


def test_parse_valid_noop_decision() -> None:
    raw = '{"action": "noop", "reasoning": "Recently evaluated"}'
    d = parse_stuck_goal_decision(raw)
    assert d.action == "noop"


def test_parse_response_with_leading_prose() -> None:
    raw = 'Here is my decision: {"action": "noop", "reasoning": "x"}'
    d = parse_stuck_goal_decision(raw)
    assert d.action == "noop"


def test_parse_response_with_fenced_json() -> None:
    raw = '```json\n{"action": "evaluate_goal", "reasoning": "yep"}\n```'
    d = parse_stuck_goal_decision(raw)
    assert d.action == "evaluate_goal"


def test_parse_response_with_extra_fields() -> None:
    raw = '{"action": "noop", "reasoning": "r", "extra": "ignored"}'
    d = parse_stuck_goal_decision(raw)
    assert d.action == "noop"


# ---- parse_stuck_goal_decision — defensive fallbacks --------------------


def test_parse_empty_response_falls_back_to_noop() -> None:
    d = parse_stuck_goal_decision("")
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning
    assert "empty" in d.reasoning


def test_parse_whitespace_only_falls_back_to_noop() -> None:
    d = parse_stuck_goal_decision("   \n\n  ")
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_non_json_falls_back_to_noop() -> None:
    d = parse_stuck_goal_decision("hello, this is not json")
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_invalid_json_falls_back_to_noop() -> None:
    d = parse_stuck_goal_decision('{"action": "noop", "reasoning":')
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_unknown_action_falls_back_to_noop() -> None:
    raw = '{"action": "cancel_goal", "reasoning": "too aggressive"}'
    d = parse_stuck_goal_decision(raw)
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning
    assert "cancel_goal" in d.reasoning


def test_parse_missing_action_falls_back_to_noop() -> None:
    d = parse_stuck_goal_decision('{"reasoning": "no action key"}')
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_non_string_reasoning_falls_back_to_noop() -> None:
    raw = '{"action": "evaluate_goal", "reasoning": 123}'
    d = parse_stuck_goal_decision(raw)
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_raw_response_always_preserved() -> None:
    raw = "garbage"
    d = parse_stuck_goal_decision(raw)
    assert d.raw_response == raw


def test_parse_array_instead_of_object_falls_back() -> None:
    d = parse_stuck_goal_decision('["action", "evaluate_goal"]')
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_action_with_missing_reasoning_uses_empty_string() -> None:
    # Reasoning is optional — empty string is a valid value.
    raw = '{"action": "evaluate_goal"}'
    d = parse_stuck_goal_decision(raw)
    # No reasoning key → defaults to "" which is a string → action accepted.
    assert d.action == "evaluate_goal"
    assert d.reasoning == ""

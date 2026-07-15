"""Unit tests for the drifting-goal-steer playbook (prompt + parser)."""

from __future__ import annotations

from ops_agent.playbooks import (
    build_drifting_goal_prompt,
    parse_drifting_goal_decision,
)
from ops_agent.playbooks.drifting_goal_steer import _MAX_MESSAGE_CHARS

# ---- build_drifting_goal_prompt -----------------------------------------


def test_prompt_includes_all_context_fields() -> None:
    p = build_drifting_goal_prompt(
        goal_id="g1",
        objective="ship X",
        phase="executing",
        last_steering_at="2026-06-28T06:00:00+00:00",
        threshold_hours=24.0,
        detected_at="2026-06-29T12:00:00+00:00",
    )
    assert "g1" in p
    assert "ship X" in p
    assert "executing" in p
    assert "2026-06-28T06:00:00+00:00" in p
    assert "24.0h" in p
    assert "2026-06-29T12:00:00+00:00" in p
    # Action menu is part of the prompt.
    assert "steer_goal" in p
    assert "noop" in p
    # Response shape is part of the prompt.
    assert "JSON" in p
    assert "message" in p


def test_prompt_handles_missing_optional_fields() -> None:
    p = build_drifting_goal_prompt(
        goal_id="g",
        objective="",
        phase="",
        last_steering_at=None,
        threshold_hours=24.0,
        detected_at="2026-06-29T12:00:00+00:00",
    )
    assert "no objective recorded" in p
    assert "no last_steering_at recorded" in p


def test_prompt_renders_non_default_threshold() -> None:
    p = build_drifting_goal_prompt(
        goal_id="g",
        objective="x",
        phase="executing",
        last_steering_at="t",
        threshold_hours=2.5,
        detected_at="t2",
    )
    assert "2.5h" in p


# ---- parse_drifting_goal_decision — valid responses --------------------


def test_parse_valid_steer_decision() -> None:
    raw = '{"action": "steer_goal", "message": "summarize progress and blockers", "reasoning": "drifted silently"}'  # noqa: E501
    d = parse_drifting_goal_decision(raw)
    assert d.action == "steer_goal"
    assert d.message == "summarize progress and blockers"
    assert "drifted" in d.reasoning
    assert d.raw_response == raw


def test_parse_valid_noop_decision() -> None:
    raw = '{"action": "noop", "reasoning": "nothing obvious"}'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "noop"
    assert d.message == ""


def test_parse_steer_with_leading_prose() -> None:
    raw = 'Decision: {"action": "steer_goal", "message": "check in", "reasoning": "r"}'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "steer_goal"
    assert d.message == "check in"


def test_parse_steer_with_fenced_json() -> None:
    raw = '```json\n{"action": "steer_goal", "message": "do X", "reasoning": "r"}\n```'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "steer_goal"
    assert d.message == "do X"


def test_parse_steer_strips_whitespace_from_message() -> None:
    raw = '{"action": "steer_goal", "message": "  re-check direction  ", "reasoning": "r"}'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "steer_goal"
    assert d.message == "re-check direction"


def test_parse_response_with_extra_fields() -> None:
    raw = '{"action": "noop", "reasoning": "r", "extra": "ignored"}'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "noop"


# ---- parse_drifting_goal_decision — defensive fallbacks ----------------


def test_parse_empty_response_falls_back_to_noop() -> None:
    d = parse_drifting_goal_decision("")
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning
    assert "empty" in d.reasoning


def test_parse_whitespace_only_falls_back_to_noop() -> None:
    d = parse_drifting_goal_decision("   \n\n  ")
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_non_json_falls_back_to_noop() -> None:
    d = parse_drifting_goal_decision("hello, this is not json")
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_invalid_json_falls_back_to_noop() -> None:
    d = parse_drifting_goal_decision('{"action": "steer_goal",')
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_unknown_action_falls_back_to_noop() -> None:
    raw = '{"action": "evaluate_goal", "reasoning": "wrong playbook"}'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning
    assert "evaluate_goal" in d.reasoning


def test_parse_steer_with_empty_message_falls_back_to_noop() -> None:
    raw = '{"action": "steer_goal", "message": "", "reasoning": "r"}'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "noop"
    assert "empty steering message" in d.reasoning


def test_parse_steer_with_whitespace_only_message_falls_back_to_noop() -> None:
    raw = '{"action": "steer_goal", "message": "   ", "reasoning": "r"}'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "noop"
    assert "empty steering message" in d.reasoning


def test_parse_steer_with_missing_message_falls_back_to_noop() -> None:
    raw = '{"action": "steer_goal", "reasoning": "forgot the field"}'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "noop"
    assert "empty steering message" in d.reasoning


def test_parse_steer_with_oversized_message_falls_back_to_noop() -> None:
    long_msg = "x" * (_MAX_MESSAGE_CHARS + 1)
    raw = f'{{"action": "steer_goal", "message": "{long_msg}", "reasoning": "r"}}'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "noop"
    assert "exceeded" in d.reasoning


def test_parse_steer_with_message_at_max_chars_is_accepted() -> None:
    msg = "x" * _MAX_MESSAGE_CHARS
    raw = f'{{"action": "steer_goal", "message": "{msg}", "reasoning": "r"}}'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "steer_goal"
    assert len(d.message) == _MAX_MESSAGE_CHARS


def test_parse_steer_with_non_string_message_falls_back_to_noop() -> None:
    raw = '{"action": "steer_goal", "message": 123, "reasoning": "r"}'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_missing_action_falls_back_to_noop() -> None:
    d = parse_drifting_goal_decision('{"reasoning": "no action key"}')
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_non_string_reasoning_falls_back_to_noop() -> None:
    raw = '{"action": "steer_goal", "message": "do X", "reasoning": 123}'
    d = parse_drifting_goal_decision(raw)
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_raw_response_always_preserved() -> None:
    raw = "garbage"
    d = parse_drifting_goal_decision(raw)
    assert d.raw_response == raw


def test_parse_array_instead_of_object_falls_back() -> None:
    d = parse_drifting_goal_decision('["action", "steer_goal"]')
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning

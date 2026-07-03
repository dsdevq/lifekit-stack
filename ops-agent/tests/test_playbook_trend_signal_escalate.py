"""Unit tests for the trend-signal-escalate playbook (prompt + parser).

The parser is a thin wrapper over drifting-goal-steer's — same decision
shape — so we only cover the prompt-shape rendering and one round-trip
through the wrapper. Full parser coverage lives with the underlying
drifting-goal-steer tests."""

from __future__ import annotations

from ops_agent.playbooks import (
    build_trend_signal_escalate_prompt,
    parse_trend_signal_escalate_decision,
)


def test_prompt_includes_all_context_fields() -> None:
    p = build_trend_signal_escalate_prompt(
        goal_id="closeloop",
        objective="ship the pipeline board UI",
        signal_id="R2",
        category="recurrence",
        repeat_count=3,
        first_fired="2026-07-01",
        latest_fired="2026-07-03",
        threshold=3,
        proposed_action="stop re-firing; escalate to Denys.",
        detected_at="2026-07-03T12:00:00+00:00",
    )
    assert "closeloop" in p
    assert "ship the pipeline board UI" in p
    assert "R2" in p
    assert "recurrence" in p
    assert "3 consecutive daily" in p
    assert "2026-07-01" in p
    assert "2026-07-03" in p
    assert "stop re-firing; escalate to Denys." in p
    assert "steer_goal" in p and "noop" in p


def test_prompt_falls_back_when_no_proposed_action() -> None:
    """An entry without a Proposed action must still render — the playbook
    surfaces the absence to the model so it doesn't hallucinate one."""
    p = build_trend_signal_escalate_prompt(
        goal_id="g", objective="", signal_id="D4", category="staleness",
        repeat_count=4, first_fired="2026-06-30", latest_fired="2026-07-03",
        threshold=3, proposed_action="",
        detected_at="2026-07-03T00:00:00+00:00",
    )
    assert "the retrospective did not suggest one" in p


def test_parse_steer_goal_roundtrips() -> None:
    """The parser is DriftingGoalDecision's — verify one steer_goal case
    survives verbatim so the wrapper doesn't silently drop the message."""
    raw = (
        '{"action": "steer_goal", '
        '"message": "check whether the R2 pattern warrants a lint rule", '
        '"reasoning": "3-day streak"}'
    )
    d = parse_trend_signal_escalate_decision(raw)
    assert d.action == "steer_goal"
    assert d.message == "check whether the R2 pattern warrants a lint rule"


def test_parse_noop_roundtrips() -> None:
    raw = '{"action": "noop", "reasoning": "observation only"}'
    d = parse_trend_signal_escalate_decision(raw)
    assert d.action == "noop"
    assert d.message == ""


def test_parser_defaults_malformed_to_noop() -> None:
    """A malformed model response must not fire an MCP steer_goal call."""
    d = parse_trend_signal_escalate_decision("garbage that isn't JSON")
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning

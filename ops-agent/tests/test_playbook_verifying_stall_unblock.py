"""Unit tests for the verifying-stall-unblock playbook (prompt + parser)."""

from __future__ import annotations

from ops_agent.playbooks import (
    build_verifying_stall_prompt,
    parse_verifying_stall_decision,
)
from ops_agent.playbooks.verifying_stall_unblock import _MAX_SERVICE_NAME_CHARS

# ---- build_verifying_stall_prompt ---------------------------------------


def test_prompt_includes_all_context_fields() -> None:
    p = build_verifying_stall_prompt(
        goal_id="g1",
        objective="ship the done-gate",
        phase="verifying",
        last_progress_at="2026-06-29T06:00:00+00:00",
        detected_at="2026-06-29T12:00:00+00:00",
        threshold_hours=4.0,
        allowlisted_services=["compose-devclaw-mcp-1"],
    )
    assert "g1" in p
    assert "ship the done-gate" in p
    assert "verifying" in p
    assert "2026-06-29T06:00:00+00:00" in p
    assert "2026-06-29T12:00:00+00:00" in p
    assert "4.0h" in p
    # Action menu is present.
    assert "evaluate_goal" in p
    assert "docker_restart" in p
    assert "noop" in p
    # Allowlist is surfaced so Claude knows what service_names are valid.
    assert "compose-devclaw-mcp-1" in p
    # Response shape is part of the prompt.
    assert "JSON" in p
    assert "service_name" in p


def test_prompt_handles_missing_optional_fields() -> None:
    p = build_verifying_stall_prompt(
        goal_id="g",
        objective="",
        phase="",
        last_progress_at=None,
        detected_at="2026-06-29T12:00:00+00:00",
        threshold_hours=4.0,
        allowlisted_services=[],
    )
    assert "no objective recorded" in p
    assert "no last_progress_at recorded" in p
    # Empty allowlist still renders — the model gets [].
    assert "[]" in p


def test_prompt_renders_non_default_threshold() -> None:
    p = build_verifying_stall_prompt(
        goal_id="g",
        objective="x",
        phase="verifying",
        last_progress_at="t",
        detected_at="t2",
        threshold_hours=12.5,
        allowlisted_services=["a"],
    )
    assert "12.5h" in p


def test_prompt_sorts_allowlist_for_stability() -> None:
    p = build_verifying_stall_prompt(
        goal_id="g",
        objective="x",
        phase="verifying",
        last_progress_at="t",
        detected_at="t2",
        threshold_hours=4.0,
        allowlisted_services=["z-service", "a-service", "m-service"],
    )
    # Sorted ordering so prompt content is stable across runs.
    assert p.index("a-service") < p.index("m-service") < p.index("z-service")


# ---- parse — valid responses ------------------------------------------


def test_parse_valid_evaluate_goal_decision() -> None:
    raw = '{"action": "evaluate_goal", "reasoning": "direction smells off"}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "evaluate_goal"
    assert d.service_name == ""
    assert "direction" in d.reasoning
    assert d.raw_response == raw


def test_parse_valid_noop_decision() -> None:
    raw = '{"action": "noop", "reasoning": "context too thin"}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "noop"
    assert d.service_name == ""


def test_parse_valid_docker_restart_decision() -> None:
    raw = (
        '{"action": "docker_restart", "service_name": "compose-devclaw-mcp-1", '
        '"reasoning": "devclaw-mcp appears frozen"}'
    )
    d = parse_verifying_stall_decision(raw)
    assert d.action == "docker_restart"
    assert d.service_name == "compose-devclaw-mcp-1"
    assert "frozen" in d.reasoning


def test_parse_docker_restart_strips_whitespace_from_service_name() -> None:
    raw = (
        '{"action": "docker_restart", "service_name": "  compose-devclaw-mcp-1  ", '
        '"reasoning": "r"}'
    )
    d = parse_verifying_stall_decision(raw)
    assert d.action == "docker_restart"
    assert d.service_name == "compose-devclaw-mcp-1"


def test_parse_response_with_leading_prose() -> None:
    raw = 'Decision: {"action": "evaluate_goal", "reasoning": "r"}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "evaluate_goal"


def test_parse_response_with_fenced_json() -> None:
    raw = '```json\n{"action": "noop", "reasoning": "r"}\n```'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "noop"


def test_parse_response_with_extra_fields() -> None:
    raw = '{"action": "noop", "reasoning": "r", "extra": "ignored"}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "noop"


# ---- parse — defensive fallbacks --------------------------------------


def test_parse_empty_response_falls_back_to_noop() -> None:
    d = parse_verifying_stall_decision("")
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning
    assert "empty" in d.reasoning


def test_parse_whitespace_only_response_falls_back_to_noop() -> None:
    d = parse_verifying_stall_decision("   \n   ")
    assert d.action == "noop"


def test_parse_non_json_falls_back_to_noop() -> None:
    d = parse_verifying_stall_decision("hello, not json")
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_invalid_json_falls_back_to_noop() -> None:
    d = parse_verifying_stall_decision('{"action": "docker_restart",')
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_unknown_action_falls_back_to_noop() -> None:
    raw = '{"action": "steer_goal", "reasoning": "wrong playbook"}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning
    assert "steer_goal" in d.reasoning


def test_parse_missing_action_falls_back_to_noop() -> None:
    d = parse_verifying_stall_decision('{"reasoning": "no action key"}')
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_non_string_reasoning_falls_back_to_noop() -> None:
    raw = '{"action": "evaluate_goal", "reasoning": 123}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_array_instead_of_object_falls_back() -> None:
    d = parse_verifying_stall_decision('["action", "noop"]')
    assert d.action == "noop"


def test_parse_raw_response_always_preserved() -> None:
    raw = "garbage"
    d = parse_verifying_stall_decision(raw)
    assert d.raw_response == raw


# ---- docker_restart-specific validation -------------------------------


def test_parse_docker_restart_with_missing_service_name_falls_back() -> None:
    raw = '{"action": "docker_restart", "reasoning": "forgot the field"}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "noop"
    assert "missing service_name" in d.reasoning


def test_parse_docker_restart_with_empty_service_name_falls_back() -> None:
    raw = '{"action": "docker_restart", "service_name": "", "reasoning": "r"}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "noop"
    assert "missing service_name" in d.reasoning


def test_parse_docker_restart_with_whitespace_service_name_falls_back() -> None:
    raw = '{"action": "docker_restart", "service_name": "   ", "reasoning": "r"}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "noop"
    assert "missing service_name" in d.reasoning


def test_parse_docker_restart_with_non_string_service_name_falls_back() -> None:
    raw = '{"action": "docker_restart", "service_name": 123, "reasoning": "r"}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "noop"
    assert "parse_failed" in d.reasoning


def test_parse_docker_restart_with_oversized_service_name_falls_back() -> None:
    big = "a" * (_MAX_SERVICE_NAME_CHARS + 1)
    raw = f'{{"action": "docker_restart", "service_name": "{big}", "reasoning": "r"}}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "noop"
    assert "exceeded" in d.reasoning


def test_parse_docker_restart_with_invalid_pattern_falls_back() -> None:
    """Anything outside ^[A-Za-z0-9_-]+$ → noop. Belt-and-braces against
    shell-injectable payloads even though the action layer re-checks."""
    raw = '{"action": "docker_restart", "service_name": "service; rm -rf /", "reasoning": "r"}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "noop"
    assert "pattern" in d.reasoning


def test_parse_docker_restart_with_space_in_name_falls_back() -> None:
    raw = '{"action": "docker_restart", "service_name": "a b", "reasoning": "r"}'
    d = parse_verifying_stall_decision(raw)
    assert d.action == "noop"
    assert "pattern" in d.reasoning

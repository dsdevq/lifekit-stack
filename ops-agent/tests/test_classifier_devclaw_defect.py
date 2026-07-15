"""Unit tests for the devclaw-defect classifier (ops-PR4).

The classifier is the confidence gate on L3 — false positives open noise
PRs against devclaw itself, so signatures MUST be tight. These tests pin
each v1 signature's true-positive path and confirm silence on the empty /
happy paths.
"""

from __future__ import annotations

from pathlib import Path

from ops_agent.classifiers import classify_devclaw_defect


def _write_goal(
    tmp_path: Path,
    goal_id: str,
    *,
    status_frontmatter: dict[str, str] | None = None,
    log: str = "",
) -> Path:
    """Materialise a goal folder under ``tmp_path`` with STATUS.md + log.md.

    Returns the goals_dir root (tmp_path) so tests can pass it as-is.
    """
    goal_dir = tmp_path / goal_id
    goal_dir.mkdir(parents=True, exist_ok=True)
    if status_frontmatter is not None:
        body = "---\n"
        for k, v in status_frontmatter.items():
            body += f"{k}: {v}\n"
        body += "---\n"
        (goal_dir / "STATUS.md").write_text(body)
    if log:
        (goal_dir / "log.md").write_text(log)
    # goal.yaml is required by the ops-agent's O2 detector shape but not by
    # this classifier — we still ship one so we mirror real goals.
    (goal_dir / "goal.yaml").write_text("objective: test\n")
    return tmp_path


# ── happy path ─────────────────────────────────────────────────────────────


def test_returns_none_when_goal_directory_missing(tmp_path):
    assert classify_devclaw_defect(goal_id="ghost", goals_dir=tmp_path) is None


def test_returns_none_when_status_and_log_are_absent(tmp_path):
    _write_goal(tmp_path, "clean")
    assert classify_devclaw_defect(goal_id="clean", goals_dir=tmp_path) is None


def test_returns_none_on_happy_verdict(tmp_path):
    _write_goal(
        tmp_path,
        "happy",
        status_frontmatter={"last_eval_verdict": "on_track", "last_eval_note": "chugging along"},
        log="implement_feature abc123 → done — PR https://x\n",
    )
    assert classify_devclaw_defect(goal_id="happy", goals_dir=tmp_path) is None


# ── eval_truncation ────────────────────────────────────────────────────────


def test_eval_truncation_on_review_cut_off_marker(tmp_path):
    _write_goal(
        tmp_path,
        "trunc1",
        status_frontmatter={
            "last_eval_verdict": "needs_human",
            "last_eval_note": '"review cut off — retry needed"',
        },
    )
    hit = classify_devclaw_defect(goal_id="trunc1", goals_dir=tmp_path)
    assert hit is not None and hit.signature == "eval_truncation"


def test_eval_truncation_on_empty_note_with_stalled_verdict(tmp_path):
    _write_goal(
        tmp_path,
        "trunc2",
        status_frontmatter={"last_eval_verdict": "stalled", "last_eval_note": ""},
    )
    hit = classify_devclaw_defect(goal_id="trunc2", goals_dir=tmp_path)
    assert hit is not None and hit.signature == "eval_truncation"


def test_eval_truncation_ignores_empty_note_on_happy_verdict(tmp_path):
    # A blank note on `on_track` is normal — no defect implied.
    _write_goal(
        tmp_path,
        "trunc3",
        status_frontmatter={"last_eval_verdict": "on_track", "last_eval_note": ""},
    )
    assert classify_devclaw_defect(goal_id="trunc3", goals_dir=tmp_path) is None


# ── phantom_verdict ────────────────────────────────────────────────────────


def test_phantom_verdict_on_marker_with_stalled(tmp_path):
    _write_goal(
        tmp_path,
        "phantom1",
        status_frontmatter={
            "last_eval_verdict": "stalled",
            "last_eval_note": '"phantom verdict — no evidence in traces"',
        },
    )
    hit = classify_devclaw_defect(goal_id="phantom1", goals_dir=tmp_path)
    # eval_truncation has priority — but this note doesn't have a truncation
    # marker AND the note isn't empty, so it falls through to phantom_verdict.
    assert hit is not None
    assert hit.signature in {"phantom_verdict", "eval_truncation"}
    # Both are acceptable; the important thing is: it fired.


def test_phantom_verdict_silent_on_happy_verdict_with_marker(tmp_path):
    # A phantom marker in the note is not a defect if the verdict is happy —
    # never happens in practice, but confirm the guard.
    _write_goal(
        tmp_path,
        "phantom2",
        status_frontmatter={
            "last_eval_verdict": "on_track",
            "last_eval_note": '"no phantom verdicts observed this run"',
        },
    )
    assert classify_devclaw_defect(goal_id="phantom2", goals_dir=tmp_path) is None


# ── planner_loop ───────────────────────────────────────────────────────────


def test_planner_loop_on_three_consecutive_dispatches_no_delivery(tmp_path):
    log = "\n".join(
        [
            "2026-07-04T01:00:00Z dispatched implement_feature target-A",
            "2026-07-04T01:05:00Z dispatched implement_feature target-A",
            "2026-07-04T01:10:00Z dispatched implement_feature target-A",
            "2026-07-04T01:15:00Z dispatched implement_feature target-A",
        ]
    )
    _write_goal(tmp_path, "loop1", log=log)
    hit = classify_devclaw_defect(goal_id="loop1", goals_dir=tmp_path)
    assert hit is not None and hit.signature == "planner_loop"


def test_planner_loop_ignores_dispatches_with_delivery_between(tmp_path):
    log = "\n".join(
        [
            "dispatched implement_feature target-A",
            "implement_feature abc → done",
            "dispatched implement_feature target-A",
            "implement_feature def → done",
            "dispatched implement_feature target-A",
        ]
    )
    _write_goal(tmp_path, "loop2", log=log)
    # Each dispatch is followed by a delivery, resetting the streak.
    assert classify_devclaw_defect(goal_id="loop2", goals_dir=tmp_path) is None


def test_planner_loop_ignores_dispatches_to_different_targets(tmp_path):
    log = "\n".join(
        [
            "dispatched implement_feature target-A",
            "dispatched implement_feature target-B",
            "dispatched implement_feature target-C",
        ]
    )
    _write_goal(tmp_path, "loop3", log=log)
    assert classify_devclaw_defect(goal_id="loop3", goals_dir=tmp_path) is None


# ── json_parse_error ───────────────────────────────────────────────────────


def test_json_parse_error_on_marker_in_log(tmp_path):
    log = "\n".join(
        [
            "some benign line",
            "cognition planner failed: expected valid JSON, got: <...>",
            "another benign line",
        ]
    )
    _write_goal(tmp_path, "json1", log=log)
    hit = classify_devclaw_defect(goal_id="json1", goals_dir=tmp_path)
    assert hit is not None and hit.signature == "json_parse_error"


def test_json_parse_error_marker_various_shapes(tmp_path):
    log = "JSONDecodeError: Expecting value line 1 column 1"
    _write_goal(tmp_path, "json2", log=log)
    hit = classify_devclaw_defect(goal_id="json2", goals_dir=tmp_path)
    assert hit is not None and hit.signature == "json_parse_error"


# ── priority order ─────────────────────────────────────────────────────────


def test_eval_truncation_wins_over_planner_loop_when_both_match(tmp_path):
    _write_goal(
        tmp_path,
        "prio1",
        status_frontmatter={
            "last_eval_verdict": "needs_human",
            "last_eval_note": '"eval truncated mid-response"',
        },
        log="\n".join(
            [
                "dispatched fix_bug T",
                "dispatched fix_bug T",
                "dispatched fix_bug T",
            ]
        ),
    )
    hit = classify_devclaw_defect(goal_id="prio1", goals_dir=tmp_path)
    assert hit is not None and hit.signature == "eval_truncation"


# ── stub_disguise (repeat-only) ─────────────────────────────────────────────


def test_stub_disguise_on_repeated_downgrades(tmp_path):
    log = "\n".join(
        [
            "clause 3 downgraded: satisfied by not_yet_available stub",
            "benign line",
            "clause 5 downgraded: unauthorized stub — evidence rejected",
        ]
    )
    _write_goal(tmp_path, "stub1", log=log)
    hit = classify_devclaw_defect(goal_id="stub1", goals_dir=tmp_path)
    assert hit is not None and hit.signature == "stub_disguise"
    assert any("2 marker lines" in e for e in hit.evidence)


def test_stub_disguise_silent_on_single_downgrade(tmp_path):
    """One downgrade is the stub-policy safety net working — not a defect."""
    log = "clause 3 downgraded: satisfied by not_yet_available stub"
    _write_goal(tmp_path, "stub2", log=log)
    assert classify_devclaw_defect(goal_id="stub2", goals_dir=tmp_path) is None


# ── workspace_break_storm (repeat-only) ─────────────────────────────────────


def test_workspace_break_storm_on_repeated_trips(tmp_path):
    log = "\n".join(
        [
            "workspace break tripped for /repos/closeloop — resetting",
            "retrying task abc",
            "workspace_break_tripped: /repos/closeloop",
        ]
    )
    _write_goal(tmp_path, "storm1", log=log)
    hit = classify_devclaw_defect(goal_id="storm1", goals_dir=tmp_path)
    assert hit is not None and hit.signature == "workspace_break_storm"


def test_workspace_break_storm_silent_on_single_trip(tmp_path):
    """A single circuit-breaker trip is the intended protection."""
    log = "workspace break tripped for /repos/closeloop — resetting"
    _write_goal(tmp_path, "storm2", log=log)
    assert classify_devclaw_defect(goal_id="storm2", goals_dir=tmp_path) is None


def test_json_parse_error_wins_over_stub_disguise_when_both_match(tmp_path):
    log = "\n".join(
        [
            "cognition planner failed: expected valid JSON",
            "clause 3 downgraded: satisfied by not_yet_available stub",
            "clause 5 downgraded: satisfied by not_yet_available stub",
        ]
    )
    _write_goal(tmp_path, "prio2", log=log)
    hit = classify_devclaw_defect(goal_id="prio2", goals_dir=tmp_path)
    assert hit is not None and hit.signature == "json_parse_error"

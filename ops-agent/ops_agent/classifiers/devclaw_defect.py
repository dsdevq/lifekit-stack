"""Classifier — does this stuck / drifting goal look like a devclaw-side bug?

ops-PR4 (L3) fires ``devclaw-bug-fix-ticket`` when this classifier matches on
an O2 incident. The signature set is *deliberately narrow* at v1 — we would
rather miss a real defect (fall through to L2 steer) than open a spurious
fix-PR against devclaw. Confidence bar is high, per the plan.md design:

    "L3 confidence threshold — the devclaw-defect classifier determines
     when ops-PR4 fires. If too tight: ops-agent silent-fails on real
     devclaw bugs. If too loose: it opens noise PRs. Empirical,
     post-deployment."

v1 signatures — every one is a real closeloop-family incident pattern:

  - ``eval_truncation`` — the direction-eval note is empty / truncated /
    contains a "cut off" marker. The 2026-06-29 closeloop incident's
    root cause: the evaluator produced 4 phantom "review cut off"
    verdicts, blocking the goal for 10+ hours.
  - ``phantom_verdict`` — the last direction verdict is a stalled /
    needs_human variant AND the note contains "phantom" or "review cut
    off" or similar boilerplate absent of grounded evidence.
  - ``planner_loop`` — recent log has ≥N consecutive dispatch lines for
    the same tool/target without a completed delivery in between,
    suggesting the planner is stuck on the same action.
  - ``json_parse_error`` — cognition output failed to parse; appears in
    the log as an ``expected valid JSON`` / ``could not parse`` line.

Anything else = ``None`` = fall through to L2. Multiple matches roll up
into the first one hit (order == priority: eval_truncation >
phantom_verdict > planner_loop > json_parse_error).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# The classifier reads at most this many trailing lines from log.md — bounded
# so a goal with a huge log doesn't blow memory. 200 lines is roughly the
# last 3–5 ticks of dispatch/delivery/verdict churn.
_LOG_TAIL_LINES = 200

# A minimum for planner_loop detection — three consecutive dispatch-for-same-
# target lines with no delivery in between is the "planner spinning" pattern
# we saw during the 2026-06-29 closeloop incident.
_PLANNER_LOOP_MIN_REPEATS = 3

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# Eval-truncation markers — either the boilerplate the evaluator falls back
# to when its output was truncated, or a note that's suspiciously empty on
# a non-happy verdict. Case-insensitive to survive minor prompt drift.
_EVAL_TRUNCATION_MARKERS = (
    re.compile(r"review\s+cut\s+off", re.IGNORECASE),
    re.compile(r"eval\s+truncat", re.IGNORECASE),
    re.compile(r"response\s+truncat", re.IGNORECASE),
    re.compile(r"output\s+truncat", re.IGNORECASE),
)

# Phantom-verdict markers — the shape a stalled/needs_human verdict takes
# when the evaluator couldn't ground its judgment in real evidence.
_PHANTOM_VERDICT_MARKERS = (
    re.compile(r"phantom\s+verdict", re.IGNORECASE),
    re.compile(r"no\s+grounded\s+evidence", re.IGNORECASE),
    re.compile(r"cannot\s+verify", re.IGNORECASE),
)

# JSON-parse-error markers in the log tail — a cognition call returned
# something that couldn't be parsed. Cognition failures inside the evaluator
# or planner are load-bearing devclaw defects.
_JSON_ERROR_MARKERS = (
    re.compile(r"expected\s+valid\s+json", re.IGNORECASE),
    re.compile(r"could\s+not\s+parse", re.IGNORECASE),
    re.compile(r"jsondecodeerror", re.IGNORECASE),
    re.compile(r"invalid\s+json\s+response", re.IGNORECASE),
)

# Dispatch-line pattern in log.md — devclaw's `append_log` writes
# "<tool> <task_id> → <status>" for terminal actions and "dispatched <tool>
# <task_id>" or similar for kick-offs. This is intentionally loose; the
# classifier only needs to spot "same tool + same-shape target" repeats.
_DISPATCH_LINE = re.compile(
    r"(?:dispatched\s+|^\s*)"
    r"(?P<tool>implement_feature|fix_bug|review_repository|start_program)"
    r"\s+(?P<target>\S+)",
    re.IGNORECASE,
)

# Signatures in priority order. First match wins — see module docstring.
_SIGNATURE_ORDER = (
    "eval_truncation",
    "phantom_verdict",
    "planner_loop",
    "json_parse_error",
)


@dataclass(frozen=True)
class DevclawDefectMatch:
    """A classifier hit — the daemon uses this to route an O2 incident to L3.

    ``signature`` is one of the v1 named patterns (see module docstring).
    ``evidence`` is a short bullet list of the specific lines / snippets
    that triggered the match; goes verbatim into the L3 playbook prompt so
    Claude can diagnose without re-reading the goal's disk state.
    ``confidence`` is a lightweight self-report — the daemon can be
    tuned in ops-PR5+ to require ``high`` before firing. v1 emits
    ``medium`` for the pattern hits below; ``high`` is reserved for
    multi-signature agreement, which v1 doesn't compute.
    """

    signature: str
    evidence: tuple[str, ...]
    confidence: str  # "medium" | "high"


def _read_frontmatter(text: str) -> dict[str, Any]:
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    parsed = yaml.safe_load(m.group(1))
    return parsed if isinstance(parsed, dict) else {}


def _last_lines(text: str, n: int) -> list[str]:
    lines = text.splitlines()
    return lines[-n:] if len(lines) > n else lines


def _detect_eval_truncation(fm: dict[str, Any]) -> DevclawDefectMatch | None:
    verdict = str(fm.get("last_eval_verdict") or "").strip().lower()
    note = str(fm.get("last_eval_note") or "").strip()
    for marker in _EVAL_TRUNCATION_MARKERS:
        if note and marker.search(note):
            return DevclawDefectMatch(
                signature="eval_truncation",
                evidence=(
                    f"last_eval_verdict={verdict or '(unset)'}",
                    f"last_eval_note contains truncation marker: {note[:200]!r}",
                ),
                confidence="medium",
            )
    # A stalled/needs_human verdict with an empty note is highly suspicious.
    # devclaw's evaluator always writes a note on a non-happy verdict; empty
    # → cognition failed to produce structured output.
    if verdict in {"stalled", "needs_human", "off_track"} and not note:
        return DevclawDefectMatch(
            signature="eval_truncation",
            evidence=(
                f"last_eval_verdict={verdict}",
                "last_eval_note is empty — evaluator likely produced no parsable output",
            ),
            confidence="medium",
        )
    return None


def _detect_phantom_verdict(fm: dict[str, Any]) -> DevclawDefectMatch | None:
    verdict = str(fm.get("last_eval_verdict") or "").strip().lower()
    note = str(fm.get("last_eval_note") or "").strip()
    if verdict not in {"stalled", "needs_human", "off_track"}:
        return None
    for marker in _PHANTOM_VERDICT_MARKERS:
        if note and marker.search(note):
            return DevclawDefectMatch(
                signature="phantom_verdict",
                evidence=(
                    f"last_eval_verdict={verdict}",
                    f"last_eval_note matches phantom marker: {note[:200]!r}",
                ),
                confidence="medium",
            )
    return None


def _detect_planner_loop(log_tail: list[str]) -> DevclawDefectMatch | None:
    """Count consecutive dispatch lines for the same (tool, target) with no
    delivery marker in between."""
    if not log_tail:
        return None
    current_key: str | None = None
    run = 0
    best_run = 0
    best_key: str | None = None
    for line in log_tail:
        m = _DISPATCH_LINE.search(line)
        if m:
            key = f"{m.group('tool').lower()}:{m.group('target')}"
            if key == current_key:
                run += 1
            else:
                current_key = key
                run = 1
            if run > best_run:
                best_run = run
                best_key = key
        elif "→ done" in line or "→ failed" in line or "delivery" in line.lower():
            # Terminal / delivery event breaks the run.
            current_key = None
            run = 0
    if best_run >= _PLANNER_LOOP_MIN_REPEATS and best_key is not None:
        return DevclawDefectMatch(
            signature="planner_loop",
            evidence=(
                f"{best_run} consecutive dispatches without a completed delivery",
                f"stuck on: {best_key}",
            ),
            confidence="medium",
        )
    return None


def _detect_json_parse_error(log_tail: list[str]) -> DevclawDefectMatch | None:
    for line in reversed(log_tail):
        for marker in _JSON_ERROR_MARKERS:
            if marker.search(line):
                return DevclawDefectMatch(
                    signature="json_parse_error",
                    evidence=(
                        f"log line: {line.strip()[:200]!r}",
                        "cognition subprocess returned unparsable output",
                    ),
                    confidence="medium",
                )
    return None


def classify_devclaw_defect(
    *,
    goal_id: str,
    goals_dir: Path,
) -> DevclawDefectMatch | None:
    """Run all signatures against one goal's disk state; return the first hit.

    Returns None when nothing matches — the daemon falls back to the
    normal L2 (steer_goal) path.

    The classifier reads at most:
      - ``<goal_dir>/STATUS.md`` frontmatter (for verdict + eval note)
      - ``<goal_dir>/log.md`` tail (last ~200 lines, for planner-loop /
        JSON-parse-error scans)
    Both are optional — a missing STATUS.md or log.md degrades gracefully
    (that signature's detector returns None). Never raises.
    """
    goal_dir = goals_dir / goal_id
    if not goal_dir.is_dir():
        return None

    fm: dict[str, Any] = {}
    status_path = goal_dir / "STATUS.md"
    if status_path.is_file():
        try:
            fm = _read_frontmatter(status_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            fm = {}

    log_tail: list[str] = []
    log_path = goal_dir / "log.md"
    if log_path.is_file():
        try:
            log_tail = _last_lines(
                log_path.read_text(encoding="utf-8", errors="replace"),
                _LOG_TAIL_LINES,
            )
        except OSError:
            log_tail = []

    detectors = {
        "eval_truncation": lambda: _detect_eval_truncation(fm),
        "phantom_verdict": lambda: _detect_phantom_verdict(fm),
        "planner_loop": lambda: _detect_planner_loop(log_tail),
        "json_parse_error": lambda: _detect_json_parse_error(log_tail),
    }
    for name in _SIGNATURE_ORDER:
        hit = detectors[name]()
        if hit is not None:
            return hit
    return None

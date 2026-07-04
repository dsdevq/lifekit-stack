"""Playbook: devclaw-bug-fix-ticket (L3).

Trigger: O2 (no-steering) + the devclaw-defect classifier returned a match.
Action menu given to Claude:
  - ``fix_bug``  — call devclaw's ``fix_bug`` MCP tool against devclaw's
    OWN repo with a structured bug description (root cause + reproduction
    + suggested fix location). Opens a PR against devclaw itself.
  - ``noop``     — record the incident but take no action. Used when
    Claude judges the classifier's signature is a false positive, the
    diagnosis isn't specific enough to file a fix ticket, or the goal
    might resolve on its own.

Distinct from the drifting-goal-steer playbook (L2) — deliberately so.
L2 asks "what one-line correction should we inject into this GOAL's
inbox?" L3 asks "is there a bug in DEVCLAW that would explain this
signature, and if so, describe it well enough to file a fix ticket?"
Both take an O2 incident as input; the daemon picks between them based
on the classifier output.

Decision parsing is strict: malformed JSON, missing fields, unknown
action, empty description, or description over the length budget all
collapse to ``noop`` with the failure surfaced in ``reasoning``. Opening
a fix-PR against devclaw is the most aggressive authority level, so the
prompt is conservative — most requests should exit as ``noop`` unless
the diagnosis is unusually clean.
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass
from typing import Any, Literal

Action = Literal["fix_bug", "noop"]

# fix_bug descriptions are threaded verbatim into the devclaw goal-planner's
# ticket brief. A 2000-char ceiling is generous enough for a reproducible
# bug report (title, symptom, evidence, suspected file, suggested fix) but
# tight enough to keep the fix-PR ticket focused.
_MAX_DESCRIPTION_CHARS = 2000

# Title stays short for clean git history — same discipline as the closeloop
# mission's PR titles. Empty title is allowed; devclaw's planner will derive
# one from the description if we don't supply one.
_MAX_TITLE_CHARS = 100


@dataclass(frozen=True)
class DevclawBugFixDecision:
    """Parsed decision from a devclaw-bug-fix-ticket playbook run.

    ``action`` is one of the two menu values; any other value (or a parse
    failure) is coerced to ``noop`` with the failure recorded in
    ``reasoning``.

    ``description`` is populated only when ``action == "fix_bug"`` — it's
    the structured bug report threaded into the devclaw fix_bug MCP call.
    Empty for ``noop`` decisions.

    ``title`` is optional; when empty devclaw's planner derives one.

    ``raw_response`` is the original Claude stdout, kept for the incident
    folder's ``decision.json``.
    """

    action: Action
    description: str
    title: str
    reasoning: str
    raw_response: str


_VALID_ACTIONS: frozenset[str] = frozenset({"fix_bug", "noop"})


def build_devclaw_bug_fix_prompt(
    *,
    goal_id: str,
    objective: str,
    phase: str,
    signature: str,
    evidence: tuple[str, ...],
    confidence: str,
    devclaw_repo_path: str,
    detected_at: str,
) -> str:
    """Render the L3 playbook prompt.

    Inputs are pre-classified: the ``signature`` names a specific devclaw
    defect pattern (e.g. ``eval_truncation``), and ``evidence`` is the
    classifier's short bullet list of specific lines / snippets that
    matched. The prompt frames the classifier hit as a *hypothesis*, not
    a diagnosis — Claude decides whether the evidence is strong enough to
    justify filing a fix-PR against devclaw.
    """
    objective_clean = (objective or "(no objective recorded)").strip()
    phase_clean = (phase or "unknown").strip()
    evidence_block = "\n".join(f"  - {line}" for line in evidence) or "  - (none)"

    return textwrap.dedent(
        f"""\
        You are the devclaw ops-agent, running L3 (self-heal against devclaw
        defects). A no-steering watchdog fired on the goal below, and the
        devclaw-defect classifier matched a known-signature pattern in the
        goal's on-disk state. Your job: decide whether the evidence justifies
        opening a fix-PR against devclaw itself.

        You have TWO possible actions:

        1. fix_bug — file a fix ticket against devclaw's own repo at
           {devclaw_repo_path}. Use this ONLY when the classifier evidence
           reads like a specific, reproducible devclaw defect and you can
           name a concrete fix direction. Filing a spurious fix-PR is
           worse than filing none (noise beats silence for on-call).

        2. noop — record the incident and take no action. Use this when
           the classifier evidence is thin, the signature is likely a
           false positive, the underlying defect is unclear, or the goal
           will likely self-recover.

        Incident context:
          goal_id:                 {goal_id}
          objective:               {objective_clean}
          current phase:           {phase_clean}
          detected_at:             {detected_at}

        Classifier hit:
          signature:               {signature}
          confidence:              {confidence}
          evidence:
        {evidence_block}

        Decision rules (strict):

        - Bias to noop. Opening fix-PRs against the harness itself is
          expensive; only fire fix_bug when the evidence is genuinely
          load-bearing (specific symptom + specific file/module to
          investigate + specific reproduction shape).
        - If firing fix_bug, the description MUST include: (a) a
          one-sentence symptom summary, (b) the classifier evidence
          verbatim, (c) a suspected devclaw module or file, and (d) a
          reproduction hint. Bias to a short, structured bug report over
          a rambling essay.
        - If firing fix_bug, the title MUST be a conventional-commit-
          shaped one-liner (e.g. "fix(evaluator): handle truncated
          cognition output"). Empty title is fine — the planner will
          derive one from the description.
        - reasoning field must NAME the specific evidence you relied on
          from the classifier hit above. Do not restate the objective.

        Respond with EXACTLY this JSON shape (no prose before or after):

        {{
          "action":      "fix_bug" | "noop",
          "title":       "<conventional-commit one-liner OR empty>",
          "description": "<structured bug report OR empty>",
          "reasoning":   "<why you chose this action — cite classifier evidence>"
        }}
        """
    )


def parse_devclaw_bug_fix_decision(raw: str) -> DevclawBugFixDecision:
    """Best-effort parse of Claude's playbook response.

    Anything unparseable, ambiguously-shaped, or with a bad payload
    (empty description on fix_bug, description over ceiling) collapses
    to ``noop`` with the failure surfaced in ``reasoning``. Aggressive
    coercion here is intentional: an L3 fix_bug call is the most
    powerful action ops-agent has and MUST NOT fire on garbage.
    """
    raw_stripped = (raw or "").strip()
    if not raw_stripped:
        return DevclawBugFixDecision(
            action="noop", description="", title="",
            reasoning="empty response from cognition",
            raw_response=raw or "",
        )

    obj = _extract_json_object(raw_stripped)
    if obj is None:
        return DevclawBugFixDecision(
            action="noop", description="", title="",
            reasoning=f"response was not parseable JSON: {raw_stripped[:200]!r}",
            raw_response=raw,
        )

    action_raw = str(obj.get("action") or "").strip().lower()
    reasoning = str(obj.get("reasoning") or "").strip()
    title = str(obj.get("title") or "").strip()
    description = str(obj.get("description") or "").strip()

    if action_raw not in _VALID_ACTIONS:
        return DevclawBugFixDecision(
            action="noop", description="", title="",
            reasoning=(
                f"unknown action {action_raw!r} — expected fix_bug or noop"
                + (f"; model said: {reasoning}" if reasoning else "")
            ),
            raw_response=raw,
        )

    if action_raw == "noop":
        return DevclawBugFixDecision(
            action="noop", description="", title="",
            reasoning=reasoning or "(no reasoning provided)",
            raw_response=raw,
        )

    # action == "fix_bug" — validate the payload before we commit to an MCP call.
    if not description:
        return DevclawBugFixDecision(
            action="noop", description="", title="",
            reasoning=(
                "fix_bug decision came back with empty description; "
                "coerced to noop to avoid filing an empty ticket"
                + (f" (reasoning: {reasoning})" if reasoning else "")
            ),
            raw_response=raw,
        )
    if len(description) > _MAX_DESCRIPTION_CHARS:
        return DevclawBugFixDecision(
            action="noop", description="", title="",
            reasoning=(
                f"fix_bug description exceeded {_MAX_DESCRIPTION_CHARS} chars "
                f"(got {len(description)}); coerced to noop"
            ),
            raw_response=raw,
        )
    if title and len(title) > _MAX_TITLE_CHARS:
        # Truncate rather than collapse — a too-long title isn't a safety
        # issue, and the fix_bug MCP call will accept a trimmed title fine.
        title = title[:_MAX_TITLE_CHARS].rstrip()

    return DevclawBugFixDecision(
        action="fix_bug",
        description=description,
        title=title,
        reasoning=reasoning or "(no reasoning provided)",
        raw_response=raw,
    )


# ── internals ──────────────────────────────────────────────────────────────


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the outer JSON object out of ``text``.

    Claude sometimes wraps structured output in prose or code fences even
    when the prompt says "no prose before or after". Grab the first
    ``{ ... }`` block greedily and try to parse it.
    """
    m = _JSON_OBJ_RE.search(text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None

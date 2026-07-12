"""Playbook: blocked-question-answer (O5).

Trigger: O5 (a goal is parked ``phase=blocked`` with a ``blocked_on`` question).

This is the first playbook whose job is to ANSWER a question rather than pick
a canned remediation verb. The cognition concern is two-part:

  (a) CLASSIFY the question — is it an OPS/ENGINEERING question the ops-agent
      has the authority + the evidence to answer (env/tool versions,
      dependency/lockfile state, config values like timeouts, repo facts,
      "retry vs split" mechanics), or a PRODUCT/INTENT/PRIORITY question it
      has NO authority to answer ("is this the right thing to build",
      scope/priority calls, changing the objective)?

  (b) If answerable — GATHER EVIDENCE (in the agentic evidence mode the model
      inspects the goal's repo checkout with read-only tools) and COMPOSE a
      grounded answer, which the daemon injects via ``steer_goal(goal_id,
      answer)`` — devclaw records it as steering and UNBLOCKS the goal (see
      devclaw/goal/service.py ``steer_goal``).

Action menu:
  - ``steer_goal`` — inject the grounded answer as steering; unblocks + the
    next-action planner picks the answer up. ONLY for questions the agent
    answered from evidence with high confidence.
  - ``escalate``   — leave the goal blocked for the human. The correct answer
    for product/intent/priority questions, and the safe answer whenever the
    agent could NOT ground its answer in evidence. (devclaw already pinged the
    owner when it blocked, so ``escalate`` = "confirm this is the human's
    call" — it takes no MCP action and does NOT unblock.)
  - ``noop``       — record only. The defensive fallback for any malformed /
    ambiguous cognition response (never auto-answers on garbage).

Decision parsing is strict, and biased toward NOT auto-answering: a
malformed response, an unknown action, or an empty/oversized answer all
collapse to ``noop`` — which leaves the goal blocked (the human still owns
it) rather than risking a wrong steer that sends the goal down a bad path.
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass
from typing import Any, Literal

Action = Literal["steer_goal", "escalate", "noop"]

# The answer is appended to inbox.md and surfaced verbatim to the next-action
# planner. Bigger than the drifting-goal 500-char nudge budget because a
# grounded answer legitimately carries evidence ("package-lock.json IS out of
# sync — node_modules resolves react@18.3 but the lock pins 18.2; regenerate
# with `npm install` and commit"), but still bounded so it stays a directive,
# not an essay.
_MAX_ANSWER_CHARS = 1500

# Escalation reasons are recorded to the incident folder only (no inbox write).
# Kept short — it's an audit note, not a message to the goal.
_MAX_ESCALATION_CHARS = 500


@dataclass(frozen=True)
class BlockedQuestionDecision:
    """Parsed decision from a blocked-question-answer playbook run.

    ``action``:
      - ``steer_goal`` — ``answer`` carries the grounded answer to inject.
      - ``escalate``   — ``escalation_reason`` explains why it's the human's
        call. No MCP action; the goal stays blocked.
      - ``noop``       — parse/validation fallback; goal stays blocked.

    ``raw_response`` is the original Claude stdout, kept for the incident
    folder's ``decision.json``.
    """

    action: Action
    answer: str
    escalation_reason: str
    reasoning: str
    raw_response: str


_VALID_ACTIONS: frozenset[str] = frozenset({"steer_goal", "escalate", "noop"})


def build_blocked_question_prompt(
    *,
    goal_id: str,
    objective: str,
    blocked_on: str,
    last_eval_verdict: str | None,
    last_eval_note: str,
    detected_at: str,
    evidence_repo_path: str | None,
) -> str:
    """Render the O5 playbook prompt.

    ``evidence_repo_path`` toggles the two modes:
      - non-None → EVIDENCE mode: the model is running with read-only tools in
        that repo checkout and is told to inspect before answering.
      - None → NO-EVIDENCE mode: the model has no tools; it is told it MUST
        escalate any question it can't answer from the context alone (it can't
        verify repo/env facts it can't see).
    """
    objective_clean = (objective or "(no objective recorded)").strip()
    question_clean = (blocked_on or "(no blocked_on recorded)").strip()
    verdict_clean = (last_eval_verdict or "(none recorded)").strip()
    note_clean = (last_eval_note or "(none recorded)").strip()

    if evidence_repo_path:
        evidence_block = textwrap.dedent(
            f"""\
            EVIDENCE GATHERING (you have read-only tools):
            You are running inside a checkout of this goal's repository at
            {evidence_repo_path}. Use your read-only tools to GATHER EVIDENCE
            before you answer — read files (package.json, package-lock.json,
            lockfiles, Dockerfiles, CI config, the source in question), grep
            for the symbol/config named in the question, and run read-only
            shell (git status, git diff, git log, `node --version`, `cat`,
            `ls`). Do NOT modify anything. Ground every claim in something you
            actually observed. If, after looking, you STILL cannot verify the
            answer from evidence, escalate — do not guess."""
        )
    else:
        evidence_block = textwrap.dedent(
            """\
            NO EVIDENCE AVAILABLE:
            You have NO tools and cannot inspect any repo, environment, or
            file. You may only answer a question that is fully decidable from
            the context above (a self-contained factual/ops question). If
            answering would require verifying a repo/env/config fact you
            cannot see from here, you MUST escalate — never guess."""
        )

    return textwrap.dedent(
        f"""\
        You are the devclaw ops-agent. A devclaw goal has parked itself in
        phase=blocked with a specific question it could not answer on its own,
        and it is waiting on an answer to proceed. Your job: decide whether
        this is a question you are AUTHORIZED and ABLE to answer from evidence,
        and if so, answer it so the goal can unblock.

        Incident context:
          goal_id:          {goal_id}
          objective:        {objective_clean}
          blocked_on (the question):
            {question_clean}
          last direction verdict: {verdict_clean}
          last direction note:    {note_clean}
          detected_at:      {detected_at}

        {evidence_block}

        YOUR AUTHORITY (read carefully — this is the load-bearing decision):

        You MAY answer (action "steer_goal") ONLY questions that are
        OPERATIONAL / ENGINEERING and decidable by evidence, e.g.:
          - environment / tool / dependency versions (node, npm, python, a
            pinned library) and whether they mismatch;
          - dependency / lockfile state ("is package-lock.json out of sync
            with package.json / node_modules?");
          - config / threshold values ("is the review-gate timeout 90s, and
            is that a known-tight value that should be raised?") — when the
            value and its intent are verifiable;
          - repo facts (does file/symbol X exist; what does config Y say);
          - mechanical "retry vs split vs raise-the-limit" calls where the
            evidence points one way.

        You MUST escalate (action "escalate"), NOT answer, when the question
        is PRODUCT / INTENT / PRIORITY — i.e. it needs the owner's judgment or
        authority, e.g.:
          - "is this the right thing to build / is the objective still right";
          - scope, priority, or sequencing calls between goals;
          - anything that would change WHAT the goal is trying to do;
          - business / product tradeoffs with no evidence-decidable answer.

        Also escalate whenever you could not ground an answer in evidence,
        or your confidence is low. A wrong auto-answer steers the goal down a
        bad path silently; leaving it for the human is always the safe default.

        If you answer (steer_goal), the "answer" MUST:
          - directly resolve the question (pick the branch / state the fact /
            give the concrete next action);
          - cite the specific evidence you relied on;
          - be a concise directive the goal's planner can act on — under
            {_MAX_ANSWER_CHARS} characters, no essay.

        Respond with EXACTLY one JSON object, no surrounding prose:

          {{"action": "steer_goal",
            "answer": "<grounded answer citing evidence>",
            "reasoning": "<why you are authorized + confident to answer>"}}
          {{"action": "escalate",
            "escalation_reason": "<why this needs the human>",
            "reasoning": "<one sentence>"}}
          {{"action": "noop", "reasoning": "<one sentence>"}}

        If unsure, choose "escalate". Never "steer_goal" without evidence.
        """
    )


# Best-effort JSON-object extractor: Claude sometimes wraps responses in
# fences or a leading sentence despite the prompt's instruction. We grab the
# FIRST top-level JSON object — strict parse below validates it.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_blocked_question_decision(raw: str) -> BlockedQuestionDecision:
    """Parse Claude's stdout into a :class:`BlockedQuestionDecision`.

    Behavior contract (biased toward NOT auto-answering):
      - ``steer_goal`` requires a non-empty ``answer`` string ≤
        :data:`_MAX_ANSWER_CHARS` and a string ``reasoning``.
      - ``escalate`` requires a string ``reasoning``; ``escalation_reason`` is
        optional (defaulted) and truncated to :data:`_MAX_ESCALATION_CHARS`.
      - ``noop`` requires a string ``reasoning``.
      - Anything else — malformed JSON, unknown action, empty/oversized/
        non-string answer on a steer, non-string reasoning — collapses to
        ``noop`` with the failure surfaced in ``reasoning``. The raw response
        is always preserved. A collapsed decision leaves the goal blocked;
        it NEVER silently steers.
    """
    raw_stripped = (raw or "").strip()
    if not raw_stripped:
        return _noop("parse_failed: empty response", raw)

    obj = _try_extract_json(raw_stripped)
    if obj is None:
        return _noop("parse_failed: no JSON object in response", raw)

    action = obj.get("action")
    reasoning = obj.get("reasoning", "")

    if not isinstance(action, str) or action not in _VALID_ACTIONS:
        return _noop(f"parse_failed: unknown action {action!r}", raw)
    if not isinstance(reasoning, str):
        return _noop("parse_failed: reasoning was not a string", raw)

    if action == "noop":
        return _noop(reasoning, raw)

    if action == "escalate":
        escalation_reason = obj.get("escalation_reason", "")
        if not isinstance(escalation_reason, str):
            escalation_reason = ""
        escalation_reason = escalation_reason.strip()[:_MAX_ESCALATION_CHARS]
        return BlockedQuestionDecision(
            action="escalate",
            answer="",
            escalation_reason=escalation_reason or "(no escalation reason given)",
            reasoning=reasoning,
            raw_response=raw,
        )

    # action == "steer_goal" — answer is required + must pass the length gate.
    # Anything off falls back to noop so we never inject a malformed answer.
    answer = obj.get("answer", "")
    if not isinstance(answer, str):
        return _noop("parse_failed: answer was not a string", raw)
    stripped = answer.strip()
    if not stripped:
        return _noop("parse_failed: empty answer on steer_goal", raw)
    if len(stripped) > _MAX_ANSWER_CHARS:
        return _noop(f"parse_failed: answer exceeded {_MAX_ANSWER_CHARS} chars", raw)

    return BlockedQuestionDecision(
        action="steer_goal",
        answer=stripped,
        escalation_reason="",
        reasoning=reasoning,
        raw_response=raw,
    )


def _noop(reasoning: str, raw: str) -> BlockedQuestionDecision:
    return BlockedQuestionDecision(
        action="noop",
        answer="",
        escalation_reason="",
        reasoning=reasoning,
        raw_response=raw or "",
    )


def _try_extract_json(text: str) -> dict[str, Any] | None:
    """Find the first top-level JSON object in ``text`` and parse it."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return None
    return None

"""lifekit-curator — standalone container.

Drains LIFEKIT_QUEUE_FILE (default: /srv/life/queue.jsonl) every wake-up and
calls Claude CLI to extract memorable facts → domain files in /srv/life/domains/.

Vendored from swarm/pipeline/src/curator.py (2026-05-16) with two changes:
  1. The Claude integration uses `claude` CLI via subprocess instead of
     claude_agent_sdk — the SDK is restricted for autonomous use from June 2026.
  2. Paths are explicit env vars (no ~/.personal-agent/ legacy assumptions),
     suited for container deployment with bind mounts.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("lifekit.curator")

LIFE_DIR = Path(os.getenv("LIFEKIT_LIFE_DIR", "/srv/life"))
QUEUE_FILE = Path(os.getenv("LIFEKIT_QUEUE_FILE", str(LIFE_DIR / "queue.jsonl")))
DEAD_LETTER_FILE = Path(
    os.getenv("LIFEKIT_DEAD_LETTER_FILE", str(LIFE_DIR / "queue.dead-letter.jsonl"))
)
CONSOLIDATION_MARKER = Path(
    os.getenv("LIFEKIT_CONSOLIDATION_MARKER", str(LIFE_DIR / ".last_consolidation"))
)
IMPROVEMENTS_FILE = Path(
    os.getenv("LIFEKIT_IMPROVEMENTS_FILE", str(LIFE_DIR / "improvements.md"))
)
CONSOLIDATION_INTERVAL = int(
    os.getenv("LIFEKIT_CONSOLIDATION_INTERVAL_SECONDS", str(24 * 60 * 60))
)
WAKE_INTERVAL = int(os.getenv("LIFEKIT_WAKE_INTERVAL_SECONDS", "900"))  # 15 min
CLAUDE_BIN = os.getenv("LIFEKIT_CLAUDE_BIN", "claude")
CLAUDE_TIMEOUT = int(os.getenv("LIFEKIT_CLAUDE_TIMEOUT_SECONDS", "180"))

_queue_lock = threading.Lock()
_new_work = threading.Event()


MEMORIZE_PROMPT = """\
Extract what's worth remembering from this interaction. Do each task below if relevant.

User: {goal}
Assistant: {response}

─── TASK 0 — USER STATE ─────────────────────────────────────────────────────
If this interaction reveals anything about the user's current focus, ongoing
projects, goals, plans, mood, or frustrations — update the "## Current"
section of {life_dir}/USER.md.

Read USER.md first. If "## Current" doesn't exist, append it at the end.
Keep it as a short bullet list. Replace outdated bullets rather than
accumulating stale entries. Never touch any other section.
If nothing new was revealed, skip entirely.

─── TASK 1 — DOMAIN KNOWLEDGE ───────────────────────────────────────────────
Update {life_dir}/domains/<domain>.md if this interaction contains facts,
preferences, or context worth keeping (broad domains: investing, coding, health,
business, content, work).

Each domain file must start with:
---
summary: <one sentence capturing everything currently known about this domain>
---

Read the file first if it exists. Rewrite the summary to reflect all current
knowledge. Append new facts below. Do not duplicate existing notes.

─── TASK 2 — SKILLS ─────────────────────────────────────────────────────────
If this interaction demonstrates a reusable procedure or approach (e.g. "how to
set up X", "the right way to do Y", "pattern that works for Z"), create or update
{life_dir}/skills/<kebab-case-name>.md

Each skill file must start with:
---
name: <kebab-case-name>
description: <one sentence — what this skill is for>
---

Write clear, specific, reusable instructions. Read the file first if it exists
and update rather than duplicate. Only write a skill if it is genuinely
procedural and reusable — skip simple Q&A or one-off lookups.

─── TASK 3 — REFLECTION ─────────────────────────────────────────────────────
After helping with this interaction, identify what you're still uncertain about
or what information gaps remain in the relevant domain(s).

Update the `gaps:` field in the frontmatter of each affected domain file.
Format: a single line — `gaps: question one | question two | question three`
- If you answered a previously open gap, remove it from the list.
- If there are no gaps, write `gaps: ""` or omit the field entirely.
- Keep gaps concrete and answerable — not vague curiosities.
- Max 3 gaps per domain. Prefer gaps that would help you assist better next time.

─── TASK 4 — SELF-IMPROVEMENT ───────────────────────────────────────────────
Reflect: was there anything in this interaction you couldn't do well, or
something that would have made your answer significantly better?

If yes, append ONE concrete suggestion to {life_dir}/improvements.md.
Format (one line per entry):
[ ] YYYY-MM-DD: <capability or knowledge needed> — <why it would help>

Only write if the gap is specific and actionable. Skip if the interaction was handled well.

─── STOP ─────────────────────────────────────────────────────────────────────
If nothing in this interaction is worth persisting, do nothing and stop.
"""


CONSOLIDATE_PROMPT = """\
Perform a memory consolidation pass over {life_dir}/.

─── DOMAIN FILES ({life_dir}/domains/) ───────────────────────────────────────
For each domain file:
1. Remove duplicate or redundant facts
2. Resolve contradictions — prefer more recent or more specific information
3. Improve clarity and organisation
4. Rewrite the frontmatter summary: to accurately reflect the full current state
   of knowledge in that domain

─── SKILL FILES ({life_dir}/skills/) ─────────────────────────────────────────
For each skill file:
1. Remove redundant steps
2. Improve clarity and actionability
3. Update the description: in frontmatter if the scope has changed
4. If two skills cover the same ground, merge them into the better-named one

─── GAP FILLING ({life_dir}/domains/) ────────────────────────────────────────
For each domain file that has a non-empty `gaps:` field (pipe-separated questions):
1. Read each gap
2. Use web search to research it
3. If you find a good answer, add it to the domain file body and remove that gap
4. If the gap requires information only the user can provide, leave it in `gaps:`
5. After processing, rewrite the `gaps:` field with only unresolved gaps remaining;
   set to `gaps: ""` if all are resolved

─── RULES ────────────────────────────────────────────────────────────────────
- Consolidate and clarify — do not discard genuinely useful information
- Prefer concrete and specific over vague and general
- Keep each domain file focused; split into a new domain if scope has grown too broad
"""


def claude_run(prompt: str, timeout: int = CLAUDE_TIMEOUT) -> str:
    """Invoke `claude` CLI via subprocess with the given prompt on stdin.

    Returns Claude's response. Raises RuntimeError on non-zero exit.
    """
    result = subprocess.run(
        [CLAUDE_BIN, "--print", "--permission-mode", "bypassPermissions"],
        input=prompt,
        cwd=str(LIFE_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed (exit {result.returncode}): {result.stderr[:500]}"
        )
    return result.stdout


def enqueue(goal: str, response: str) -> None:
    """Append a raw turn to the persistent queue. Synchronous, no LLM, never raises."""
    if not goal or not response:
        return
    entry = {
        "id": str(uuid.uuid4()),
        "goal": goal,
        "response": response,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _queue_lock:
            with QUEUE_FILE.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        _new_work.set()
        logger.debug("queued %s", entry["id"][:8])
    except Exception as exc:
        logger.error("failed to write queue entry: %s", exc)


def _entry_id_short(entry: object) -> str:
    """Return a short, log-safe identifier for an entry, even when malformed."""
    if isinstance(entry, dict):
        eid = entry.get("id")
        if isinstance(eid, str) and eid:
            return eid[:8]
    return "<no-id>"


def _log_skip(entry: object, exc: object) -> None:
    logger.warning("skip queue entry %s: %s", _entry_id_short(entry), exc)


def _quarantine(entry: object) -> None:
    """Append a poison entry to the dead-letter file. Never raises."""
    try:
        DEAD_LETTER_FILE.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "quarantined_at": datetime.now(timezone.utc).isoformat(),
            "entry": entry,
        }
        with DEAD_LETTER_FILE.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:
        logger.error("failed to quarantine entry %s: %s", _entry_id_short(entry), exc)


def _process_one(entry: dict) -> None:
    """Run the LLM extraction for a single queue entry. May raise."""
    prompt = MEMORIZE_PROMPT.format(
        goal=entry["goal"],
        response=entry["response"],
        life_dir=LIFE_DIR,
    )
    claude_run(prompt)


def process_queue() -> None:
    if not QUEUE_FILE.exists():
        return

    with _queue_lock:
        raw = QUEUE_FILE.read_text()

    entries: list = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("skipping malformed queue line")

    pending = [e for e in entries if isinstance(e, dict) and not e.get("done")]
    if not pending:
        return

    logger.info("processing %d pending entries", len(pending))

    # Process each entry in isolation. A single poison entry must never take
    # down the loop — that's what kept the container restarting (#a350).
    for entry in pending:
        try:
            _process_one(entry)
            entry["done"] = True
            logger.info("✓ %s", _entry_id_short(entry))
        except Exception as exc:
            _log_skip(entry, exc)
            _quarantine(entry)
            # Mark done so it's dropped from queue.jsonl below — otherwise
            # we'd re-process the same poison on every wake-up.
            entry["done"] = True

    remaining = [e for e in entries if not (isinstance(e, dict) and e.get("done"))]
    with _queue_lock:
        with QUEUE_FILE.open("w") as f:
            for e in remaining:
                f.write(json.dumps(e) + "\n")


def consolidation_due() -> bool:
    if not CONSOLIDATION_MARKER.exists():
        return True
    try:
        last = float(CONSOLIDATION_MARKER.read_text().strip())
        return (datetime.now(timezone.utc).timestamp() - last) > CONSOLIDATION_INTERVAL
    except Exception:
        return True


def run_consolidation() -> None:
    logger.info("dream cycle starting")
    try:
        claude_run(
            CONSOLIDATE_PROMPT.format(life_dir=LIFE_DIR), timeout=CLAUDE_TIMEOUT * 2
        )
        CONSOLIDATION_MARKER.parent.mkdir(parents=True, exist_ok=True)
        CONSOLIDATION_MARKER.write_text(str(datetime.now(timezone.utc).timestamp()))
        logger.info("dream cycle complete")
    except Exception as exc:
        logger.warning("dream cycle failed — %s", exc)


def loop() -> None:
    logger.info(
        "curator started — life_dir=%s queue=%s wake=%ds",
        LIFE_DIR,
        QUEUE_FILE,
        WAKE_INTERVAL,
    )
    process_queue()  # drain leftovers from any previous run
    while True:
        _new_work.wait(timeout=WAKE_INTERVAL)
        _new_work.clear()
        try:
            process_queue()
        except Exception as exc:
            logger.exception("process_queue crashed: %s", exc)
        if consolidation_due():
            try:
                run_consolidation()
            except Exception as exc:
                logger.exception("run_consolidation crashed: %s", exc)


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LIFEKIT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    try:
        loop()
    except KeyboardInterrupt:
        logger.info("shutdown requested")
        return 0
    except Exception:
        logger.exception("fatal error in curator loop")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

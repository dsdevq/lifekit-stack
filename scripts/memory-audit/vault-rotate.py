#!/usr/bin/env python3
"""Vault rotation — enforces the MECHANICAL rotation classes of the vault
README's Rule 3 ("deletion is normal; git history is the archive"). Runs in the
weekly audit AFTER `openclaw wiki compile` (so the claims cache is fresh) and
BEFORE the lint (so the report shows the residual). Emits JSON describing every
action; `--dry-run` prints the same JSON without touching anything.

Mechanical classes implemented (README Rule 3 table):
  - audits/          : keep exactly one report — delete all but today's
  - log.md entries   : > 90 days old -> collapse into the "Compacted history" block
  - sources/ pages   : >= 60 days old AND cited by zero claims AND referenced by
                       zero wiki pages -> delete (failed ingest)
  - proposals ledger : system/proposals.md entries still `new` after 30 days ->
                       entry removed, one line appended to the Decisions record
                       as `expired` (graded-or-die)

Judgment classes (stale STATUS, concluded-project folds, contradictions) are
NEVER handled here — they surface via vault-lint.py / the report.

Hard guards: touches ONLY the four classes above. Never PLAN.md, journal/,
state/, scout/, .obsidian/, generated <!-- openclaw --> blocks, or any frozen
surface outside the classes the contract itself rotates.
"""

import os
import re
import sys
import json
import glob
import datetime

args = [a for a in sys.argv[1:] if not a.startswith("--")]
DRY = "--dry-run" in sys.argv[1:]
VAULT = args[0] if args else "/srv/memory"
TODAY = args[1] if len(args) > 1 else datetime.date.today().isoformat()
today = datetime.date.fromisoformat(TODAY)

LOG_TTL_DAYS = 90
SOURCE_TTL_DAYS = 60
PROPOSAL_TTL_DAYS = 30

actions = []


def log(action, path, detail):
    p = os.path.relpath(path, VAULT) if os.path.isabs(str(path)) else str(path)
    actions.append({"action": action, "path": p, "detail": detail})


def age_days(iso_date):
    try:
        return (today - datetime.date.fromisoformat(iso_date)).days
    except ValueError:
        return None


DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")


# ---------------------------------------------------------------- audits keep-one
def rotate_audits():
    for f in sorted(glob.glob(f"{VAULT}/audits/*-vault-audit.md")):
        m = DATE_PREFIX.match(os.path.basename(f))
        if m and m.group(1) == TODAY:
            continue
        log("delete-audit", f, "superseded weekly report (keep exactly one)")
        if not DRY:
            os.remove(f)


# ---------------------------------------------------------------- log compaction
ENTRY_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\]\s*(.*)$")
COMPACT_RE = re.compile(r"^## Compacted history\b")


def compact_log(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    lines = text.split("\n")
    # locate entry spans: (start, end_exclusive, date, header_rest)
    heads = []
    for i, ln in enumerate(lines):
        m = ENTRY_RE.match(ln)
        if m:
            heads.append((i, m.group(1), m.group(2).strip()))
        elif ln.startswith("## "):
            heads.append((i, None, None))
    spans = []
    for n, (i, date, rest) in enumerate(heads):
        if date is None:
            continue
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        spans.append((i, end, date, rest))

    old = [s for s in spans if (age_days(s[2]) or 0) > LOG_TTL_DAYS]
    if not old:
        return

    bullets = []
    for i, end, date, rest in old:
        line = rest.strip()
        if not line:
            for b in lines[i + 1 : end]:
                if b.strip():
                    line = b.strip()
                    break
        bullets.append(f"- **{date}** — {line[:240]}")

    # remove old spans (back to front so indices stay valid)
    for i, end, _, _ in sorted(old, key=lambda s: -s[0]):
        del lines[i:end]

    # insert bullets into the Compacted history block (create it if absent)
    ci = next((i for i, ln in enumerate(lines) if COMPACT_RE.match(ln)), None)
    if ci is None:
        first_entry = next(
            (i for i, ln in enumerate(lines) if ENTRY_RE.match(ln)), len(lines)
        )
        newest = max(s[2] for s in old)
        block = [f"## Compacted history (through {newest})", ""] + bullets + [""]
        lines[first_entry:first_entry] = block
    else:
        end = next(
            (j for j in range(ci + 1, len(lines)) if lines[j].startswith("## ")),
            len(lines),
        )
        while end > ci + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = bullets

    if not DRY:
        open(path, "w", encoding="utf-8").write("\n".join(lines))
    log(
        "compact-log",
        path,
        f"collapsed {len(old)} entries older than {LOG_TTL_DAYS}d into Compacted history",
    )


def rotate_logs():
    targets = [f"{VAULT}/log.md"] + sorted(glob.glob(f"{VAULT}/projects/*/log.md"))
    for path in targets:
        if os.path.exists(path):
            compact_log(path)


# ---------------------------------------------------------------- uncited sources
def cited_corpus():
    cache = f"{VAULT}/.openclaw-wiki/cache/claims.jsonl"
    cited = set()
    if not os.path.exists(cache):
        return None
    for ln in open(cache, encoding="utf-8", errors="replace"):
        try:
            c = json.loads(ln)
        except json.JSONDecodeError:
            continue
        for s in c.get("sourceIds") or []:
            cited.add(str(s))
        for e in c.get("evidence") or []:
            if e.get("sourceId"):
                cited.add(str(e["sourceId"]))
    return cited


def rotate_sources():
    cited = cited_corpus()
    if cited is None:
        log(
            "skip-class",
            "sources/",
            "claims.jsonl cache missing — uncited-source rotation skipped this run",
        )
        return
    corpus = "\n".join(cited)
    # wiki pages that could legitimately reference a source (non-frozen prose)
    ref_files = [
        f
        for f in glob.glob(f"{VAULT}/**/*.md", recursive=True)
        if not os.path.relpath(f, VAULT).startswith(("sources/", "audits/"))
    ]
    for f in sorted(glob.glob(f"{VAULT}/sources/*.md")):
        stem = os.path.splitext(os.path.basename(f))[0]
        m = DATE_PREFIX.match(stem)
        if not m:
            continue
        age = age_days(m.group(1))
        if age is None or age < SOURCE_TTL_DAYS:
            continue
        if stem in corpus:
            continue
        if any(
            stem in open(rf, encoding="utf-8", errors="replace").read()
            for rf in ref_files
        ):
            continue
        log(
            "delete-source",
            f,
            f"{age}d old, cited by zero claims and referenced by zero wiki pages "
            "(failed ingest — re-clip if ever needed)",
        )
        if not DRY:
            os.remove(f)


# ------------------------------------------------------- proposals graded-or-die
PROP_HEAD = re.compile(r"^### (\d{4}-\d{2}-\d{2})-(\S+)\s*$")
STATUS_LINE = re.compile(r"^- \*\*Status:\*\*\s*new\b")


def rotate_proposals():
    path = f"{VAULT}/system/proposals.md"
    if not os.path.exists(path):
        return
    lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
    dec_i = next(
        (i for i, ln in enumerate(lines) if ln.startswith("## Decisions record")), None
    )
    if dec_i is None:
        log("skip-class", path, "no '## Decisions record' section — not rotating")
        return

    expired = []  # (start, end, date, slug)
    i = 0
    while i < dec_i:
        m = PROP_HEAD.match(lines[i])
        if m:
            end = i + 1
            while end < dec_i and not lines[end].startswith(("### ", "## ")):
                end += 1
            date, slug = m.group(1), m.group(2)
            age = age_days(date)
            ungraded = any(STATUS_LINE.match(lines[j]) for j in range(i, end))
            if age is not None and age > PROPOSAL_TTL_DAYS and ungraded:
                expired.append((i, end, date, slug))
            i = end
        else:
            i += 1

    if not expired:
        return
    for start, end, date, slug in sorted(expired, key=lambda e: -e[0]):
        del lines[start:end]
        record = (
            f"- {date} {slug} -> expired "
            f"(graded-or-die: ungraded {PROPOSAL_TTL_DAYS}d TTL; body in git history)"
        )
        # decisions-record index shifted by the deletion above; recompute
        di = next(
            i for i, ln in enumerate(lines) if ln.startswith("## Decisions record")
        )
        insert = di + 1
        while insert < len(lines) and not lines[insert].startswith("- "):
            insert += 1
        lines.insert(insert, record)
        log(
            "expire-proposal",
            path,
            f"{date}-{slug}: still 'new' after {PROPOSAL_TTL_DAYS}d -> Decisions record as expired",
        )
    if not DRY:
        open(path, "w", encoding="utf-8").write("\n".join(lines))


rotate_audits()
rotate_logs()
rotate_sources()
rotate_proposals()
print(json.dumps(actions, indent=1))

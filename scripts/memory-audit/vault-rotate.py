#!/usr/bin/env python3
"""Mechanical rotation (Pass 0b) for the ~/memory vault - README "Rule 3" enforcement.

Applies ONLY the mechanical rotation classes the contract authorizes the audit
to delete (git history is the archive):

  - bridge dumps        any sources/**/bridge-*.md - machine dumps, delete on sight
  - superseded audits   audits/*-vault-audit.md - keep exactly the newest report
  - uncited sources     sources/*.md cited by zero claims 60+ days after ingest

Judgment classes (log compaction, STATUS staleness, proposal expiry, size caps)
are NOT touched here - vault-lint.py detects them and the report carries them.

Safety rails: refuses to run on a directory that doesn't carry the contract
(README with a vault-structure block); every deletion is confined to sources/
or audits/; aborts (exit 2, nothing deleted) if the plan exceeds --max-deletions.
Emits a JSON list of {action, path, detail} entries to stdout - same shape as
vault-autofix.py output, so the report merges them.
"""

import argparse
import datetime
import glob
import json
import os
import re
import sys

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def frontmatter(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    if not text.startswith("---"):
        return ""
    parts = text.split("---", 2)
    return parts[1] if len(parts) >= 3 else ""


def page_date(path):
    """Best-available age anchor: frontmatter updatedAt/captured, else filename date."""
    head = frontmatter(path)
    for key in ("updatedAt", "captured"):
        m = re.search(rf"^{key}:\s*[\"']?(\d{{4}}-\d{{2}}-\d{{2}})", head, re.M)
        if m:
            return datetime.date.fromisoformat(m.group(1))
    m = DATE_RE.match(os.path.basename(path))
    if m:
        return datetime.date.fromisoformat(m.group(1))
    return None


def cited_source_ids(vault):
    """sourceIds referenced by any claim evidence, from the compiled claims.jsonl.
    Returns None when the cache is missing/unreadable - callers must then SKIP
    the uncited-source class rather than treat everything as uncited."""
    path = os.path.join(vault, ".openclaw-wiki", "cache", "claims.jsonl")
    if not os.path.exists(path):
        return None
    cited = set()
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            cited.update(re.findall(r'"sourceId":\s*"([^"]+)"', line))
    except OSError:
        return None
    return cited


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vault", nargs="?", default="/srv/memory")
    ap.add_argument("--dry-run", action="store_true", help="plan only, delete nothing")
    ap.add_argument(
        "--max-deletions",
        type=int,
        default=25,
        help="abort (exit 2) if the plan exceeds this many files - runaway guard",
    )
    ap.add_argument(
        "--uncited-ttl-days", type=int, default=60, help="README Rule 3 source TTL"
    )
    args = ap.parse_args()
    vault = os.path.abspath(os.path.expanduser(args.vault))

    readme = os.path.join(vault, "README.md")
    if not (os.path.isfile(readme) and "```vault-structure" in open(readme).read()):
        sys.exit(f"refusing to rotate: {vault} does not carry the vault contract")

    plan = []  # (action, abs_path, detail)

    # -- class 1: bridge machine dumps (regression guard - the writer must stay off)
    for f in sorted(glob.glob(f"{vault}/sources/**/bridge-*.md", recursive=True)):
        plan.append(
            (
                "rotate-bridge-dump",
                f,
                "machine dump in sources/ - deleted on sight per README Rule 3 "
                "(fix the memory-wiki bridge writer if these recur)",
            )
        )

    # -- class 2: superseded audit reports (keep exactly the newest)
    audits = sorted(glob.glob(f"{vault}/audits/*-vault-audit.md"))
    for f in audits[:-1]:  # date-prefixed names sort chronologically
        plan.append(
            (
                "rotate-audit-keep-latest",
                f,
                f"superseded by {os.path.basename(audits[-1])}",
            )
        )

    # -- class 3: sources cited by zero claims past the TTL
    cited = cited_source_ids(vault)
    today = datetime.date.today()
    if cited is None:
        print(
            "vault-rotate: claims.jsonl cache missing - skipping uncited-source class",
            file=sys.stderr,
        )
    else:
        for f in sorted(glob.glob(f"{vault}/sources/*.md")):
            base = os.path.basename(f)
            if base in ("README.md", "index.md"):
                continue
            if base.startswith("bridge-"):
                continue  # already class 1
            stem = base[:-3]
            m = re.search(r"^id:\s*[\"']?([^\s\"']+)", frontmatter(f), re.M)
            page_id = m.group(1) if m else f"source.{stem}"
            if page_id in cited or f"source.{stem}" in cited:
                continue
            anchor = page_date(f)
            if anchor is None:
                continue  # no reliable age - conservative keep
            age = (today - anchor).days
            if age >= args.uncited_ttl_days:
                plan.append(
                    (
                        "rotate-uncited-source",
                        f,
                        f"cited by zero claims, {age}d old (TTL {args.uncited_ttl_days}d) - "
                        "ingest never produced claims; re-clip if ever needed",
                    )
                )

    if len(plan) > args.max_deletions:
        print(
            f"vault-rotate: ABORT - plan has {len(plan)} deletions "
            f"(> --max-deletions {args.max_deletions}); nothing deleted. Plan:",
            file=sys.stderr,
        )
        for action, f, _ in plan:
            print(f"  {action}: {os.path.relpath(f, vault)}", file=sys.stderr)
        sys.exit(2)

    entries = []
    for action, f, detail in plan:
        if not args.dry_run:
            os.remove(f)
        entries.append(
            {
                "action": action,
                "path": os.path.relpath(f, vault),
                "detail": detail + (" [dry-run]" if args.dry_run else ""),
            }
        )
    print(json.dumps(entries, indent=1))


if __name__ == "__main__":
    main()

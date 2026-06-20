#!/usr/bin/env python3
"""Vault auto-fix — applies only SAFE, mechanical, additive/format fixes that
need no human judgment, and emits JSON describing what it changed. Run BEFORE
the lint so the audit reports the residual. NEVER deletes; never touches frozen
or optional content; never rewrites prose. Lives in lifekit-stack.

Safe fixes:
  - legacy last_updated: -> updatedAt:
  - STATUS.md  updated: -> updatedAt:
  - malformed frontmatter: quote an unquoted scalar that has an embedded colon
  - missing frontmatter on a content page: prepend a minimal stub
Judgment-needed findings (orphans, broken links, deprecated paths, contradictions)
are left for the report — NOT auto-fixed.
"""

import os
import re
import sys
import json
import glob
import datetime

VAULT = sys.argv[1] if len(sys.argv) > 1 else "/srv/memory"
TODAY = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()
fixes = []


def log(action, path, detail):
    fixes.append(
        {"action": action, "path": os.path.relpath(path, VAULT), "detail": detail}
    )


ALL = glob.glob(f"{VAULT}/**/*.md", recursive=True)


def rel(f):
    return os.path.relpath(f, VAULT)


FROZEN_PREFIX = ("sources/", "journal/", "audits/", "incidents/", "goal-archive/")
FROZEN_SEG = ("/proposals/", "/proposals-approved/", "/tasks/", "/runs/")
OPT = {"recon.md", "conversation.md", "findings.md", "decisions.md", "settings.yaml"}
INDEXISH = {
    "INDEX.md",
    "index.md",
    "README.md",
    "AGENTS.md",
    "WIKI.md",
    "PLAN.md",
    "MEMORY.md",
}


def is_frozen(f):
    r = rel(f)
    return r.startswith(FROZEN_PREFIX) or any(s in "/" + r for s in FROZEN_SEG)


def skip(f):
    return is_frozen(f) or os.path.basename(f) in OPT or os.path.basename(f) in INDEXISH


def split_fm(s):
    if not s.startswith("---"):
        return None, None, s
    parts = s.split("---", 2)
    if len(parts) < 3:
        return None, None, s
    return parts[1], "---" + parts[1] + "---", parts[2]


def valid_fm(text):
    """Self-check: the written content must have intact '---' delimiters (the
    frontmatter block starts and ends on its own line). Guards against ever
    writing a glued closing delimiter."""
    if not text.startswith("---"):
        return False
    parts = text.split("---", 2)
    if len(parts) < 3:
        return False
    head = parts[1]
    return head.startswith("\n") and head.endswith("\n")


def safe_write(path, text):
    if not valid_fm(text):
        log(
            "skipped-unsafe",
            path,
            "auto-fix would produce malformed frontmatter — left unchanged",
        )
        return False
    open(path, "w").write(text)
    return True


KEYLINE = re.compile(r"^([A-Za-z0-9_-]+):\s+(\S.*)$")


def quote_bad_scalars(head):
    changed = []
    out = []
    for ln in head.split(
        "\n"
    ):  # split (not splitlines) preserves the trailing newline before ---
        if ln[:1] in (" ", "\t", "-") or not ln.strip():
            out.append(ln)
            continue
        m = KEYLINE.match(ln)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val[:1] not in (
                "'",
                '"',
                "|",
                ">",
                "[",
                "{",
                "&",
                "*",
                "#",
            ) and re.search(r"\S:\s", val):
                out.append(f'{key}: "{val.replace(chr(34), chr(39))}"')
                changed.append(key)
                continue
        out.append(ln)
    return "\n".join(out), changed


for f in ALL:
    if is_frozen(
        f
    ):  # legacy field rename is safe even on some structural; but skip frozen
        continue
    s = open(f, encoding="utf-8", errors="replace").read()
    head, _, body = split_fm(s)

    # 1) missing frontmatter -> minimal stub (content pages only)
    if (
        head is None
        and not skip(f)
        and rel(f).startswith(
            ("domains/", "system/", "concepts/", "entities/", "syntheses/")
        )
        or (
            head is None
            and os.path.basename(f) in ("plan.md", "log.md", "journal.md")
            and rel(f).startswith("projects/")
        )
    ):
        name = os.path.splitext(os.path.basename(f))[0]
        h1 = re.search(r"^#\s+(.+)$", s, re.M)
        title = h1.group(1).strip() if h1 else name.replace("-", " ").title()
        firstline = ""
        for ln in s.splitlines():
            t = ln.strip()
            if not t or t.startswith(("#", "---", "<!--", ">", "|", "*", "-", "_")):
                continue
            if re.match(
                r"^\*\*(last updated|phase|status|updated|generated|source)", t, re.I
            ):
                continue
            if t.lower().startswith(
                ("source:", "fetched:", "last updated", "updated:", "generated")
            ):
                continue
            if len(t) < 25:
                continue
            firstline = t
            break
        summ = re.sub(r'["\n]', " ", firstline).strip()[:240] or f"{title}."
        stub = (
            f'---\nname: {name}\ntitle: "{title}"\nsummary: "{summ}"\n'
            f"updatedAt: {TODAY}\nstatus: active\ntags: []\n---\n\n"
        )
        if safe_write(f, stub + s):
            log(
                "add-frontmatter",
                f,
                "stubbed name/title/summary/updatedAt/status (review summary)",
            )
        continue

    if head is None:
        continue

    new_head = head
    intents = []
    # 2) legacy last_updated -> updatedAt
    if re.search(r"^last_updated:", new_head, re.M):
        new_head = re.sub(r"^last_updated:", "updatedAt:", new_head, flags=re.M)
        intents.append(("rename-field", "last_updated -> updatedAt"))
    # 3) STATUS.md updated: -> updatedAt:
    if (
        os.path.basename(f) == "STATUS.md"
        and re.search(r"^updated:", new_head, re.M)
        and not re.search(r"^updatedAt:", new_head, re.M)
    ):
        new_head = re.sub(r"^updated:", "updatedAt:", new_head, flags=re.M)
        intents.append(("rename-field", "STATUS updated -> updatedAt"))
    # 4) quote malformed scalars
    new_head, badkeys = quote_bad_scalars(new_head)
    if badkeys:
        intents.append(("quote-yaml", f"quoted scalar(s): {', '.join(badkeys)}"))
    if new_head != head and intents:
        if safe_write(f, "---" + new_head + "---" + body):
            for action, detail in intents:
                log(action, f, detail)

print(json.dumps(fixes, indent=1))

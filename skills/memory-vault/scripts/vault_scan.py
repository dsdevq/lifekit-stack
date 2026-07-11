#!/usr/bin/env python3
"""Contract scanner for the memory vault (structure, links, orphans, frontmatter).

The vault's README.md is the contract; this script mechanizes its lint rules:
  - structure allowlist   (README ```vault-structure fenced block)
  - broken wikilinks      (>=3x targets are contract violations: write or de-link)
  - orphan wiki pages     (no inbound [[links]]; runtime artifacts excluded)
  - missing frontmatter   (wiki-layer pages)
  - legacy last_updated   (renamed vault-wide to updatedAt)
  - project completeness  (projects/<name>/ needs plan.md, log.md, journal.md)

Exit 0 = clean, 1 = findings. Read-only, stdlib-only.
"""

import argparse
import collections
import os
import re
import sys

SKIP_DIRS = {".git", ".obsidian", ".openclaw-wiki"}
WIKI_PREFIXES = (
    "domains/",
    "projects/",
    "system/",
    "concepts/",
    "entities/",
    "syntheses/",
    "incidents/",
)
WIKI_ROOTS = {
    "index.md",
    "log.md",
    "PLAN.md",
    "README.md",
    "AGENTS.md",
    "WIKI.md",
    "inbox.md",
}
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def find_vault(cli):
    for c in (
        cli,
        os.environ.get("MEMORY_VAULT"),
        os.path.expanduser("~/memory"),
        "/srv/memory",
    ):
        if c and os.path.isfile(os.path.join(os.path.expanduser(c), "README.md")):
            return os.path.expanduser(c)
    sys.exit("vault not found (tried --vault, $MEMORY_VAULT, ~/memory, /srv/memory)")


def read_allowlist(vault):
    m = re.search(
        r"```vault-structure\n(.*?)```",
        open(os.path.join(vault, "README.md")).read(),
        re.S,
    )
    if not m:
        return None
    return {
        ln.strip().rstrip("/")
        for ln in m.group(1).splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }


def walk_md(vault):
    for dp, dns, fns in os.walk(vault):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn.endswith(".md"):
                yield os.path.relpath(os.path.join(dp, fn), vault)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault")
    args = ap.parse_args()
    vault = find_vault(args.vault)
    findings = []

    # -- structure allowlist
    allow = read_allowlist(vault)
    if allow is None:
        findings.append(
            "structure: README has no ```vault-structure block — freeze not machine-readable"
        )
    else:
        for entry in sorted(os.listdir(vault)):
            if entry in SKIP_DIRS or entry in {".gitignore", ".DS_Store"}:
                continue
            if entry.rstrip("/") not in allow:
                findings.append(
                    f"structure: top-level '{entry}' not in allowlist — needs a graded proposal (contract: structure freeze)"
                )

    # -- index pages + links
    pages, paths = {}, set()
    for rel in walk_md(vault):
        paths.add(rel.lower())
        paths.add(rel[:-3].lower())
        pages.setdefault(os.path.basename(rel)[:-3].lower(), rel)

    aliases = {}
    for rel in walk_md(vault):
        text = open(os.path.join(vault, rel), encoding="utf-8", errors="replace").read()
        if text.startswith("---"):
            m = re.search(
                r"(?m)^aliases: \[([^\]]*)\]",
                text.split("---", 2)[1] if "---" in text[3:] else "",
            )
            if m:
                for a in m.group(1).split(","):
                    aliases[a.strip().lower()] = rel

    inbound, broken = collections.Counter(), collections.Counter()
    no_fm, legacy = [], []
    for rel in walk_md(vault):
        is_wiki = rel.startswith(WIKI_PREFIXES) or rel in WIKI_ROOTS
        text = open(os.path.join(vault, rel), encoding="utf-8", errors="replace").read()
        if (
            is_wiki
            and rel not in WIKI_ROOTS
            and "index" not in os.path.basename(rel).lower()
        ):
            if (
                not text.startswith("---")
                and "/tasks/" not in rel
                and "/runs/" not in rel
            ):
                no_fm.append(rel)
            if re.search(r"(?m)^last_updated:", text):
                legacy.append(rel)
        if not is_wiki:
            continue
        # strip code fences/spans so schema examples like `[[name]]` don't count
        scrub = re.sub(r"```.*?```", "", text, flags=re.S)
        scrub = re.sub(r"`[^`\n]*`", "", scrub)
        for m in LINK_RE.finditer(scrub):
            t = m.group(1).strip()
            key = t.lower()
            base = key.split("/")[-1]
            if base in pages or key in paths or key + ".md" in paths or key in aliases:
                inbound[pages.get(base, aliases.get(key, key))] += 1
            else:
                broken[t] += 1

    for t, c in broken.most_common():
        if c >= 3:
            findings.append(
                f"broken-link: [[{t}]] referenced {c}x — contract says write the page or de-link"
            )
    for rel in sorted(pages.values()):
        if (
            not rel.startswith(WIKI_PREFIXES)
            or "index" in os.path.basename(rel).lower()
        ):
            continue
        if "/tasks/" in rel or "/runs/" in rel:
            continue
        if inbound.get(rel, 0) == 0:
            findings.append(
                f"orphan: {rel} has no inbound [[links]] — link from an INDEX or related page"
            )
    findings += [f"missing-frontmatter: {r}" for r in no_fm]
    findings += [
        f"legacy-field: {r} still uses last_updated (contract: updatedAt)"
        for r in legacy
    ]

    # -- project completeness
    proj = os.path.join(vault, "projects")
    if os.path.isdir(proj):
        for d in sorted(os.listdir(proj)):
            dd = os.path.join(proj, d)
            if not os.path.isdir(dd):
                continue
            missing = [
                f
                for f in ("plan.md", "log.md", "journal.md")
                if not os.path.exists(os.path.join(dd, f))
            ]
            if missing:
                findings.append(
                    f"project-incomplete: projects/{d}/ missing {', '.join(missing)}"
                )

    total = sum(broken.values())
    print(f"vault: {vault}")
    print(f"wikilinks: broken instances {total}, distinct {len(broken)}")
    if findings:
        print(f"\nFINDINGS ({len(findings)}):")
        for f in findings:
            print(f"  - {f}")
        sys.exit(1)
    print("clean — no contract findings")


if __name__ == "__main__":
    main()

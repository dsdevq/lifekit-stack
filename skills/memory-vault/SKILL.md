---
name: memory-vault
description: Operate the Karpathy-style markdown memory vault (~/memory on workstations, /srv/memory on the VPS). Use for ANY work on the vault — reading, writing pages, auditing, ingesting sources, promoting rules. Encodes sync discipline, the structure/link scanner, new-page checklist, rule-promotion flow, and log-entry formats. The vault's own README.md is the contract; this skill is procedures only and never overrides it.
---

# memory-vault — vault operations

**The contract is the vault's `README.md`. Read it before acting; when this skill and the README disagree, the README wins.** This skill exists so sessions stop re-deriving the same procedures and scanners.

## 0. Locate the vault

In order: `$MEMORY_VAULT` env var → `~/memory` → `/srv/memory`. Everything below calls it `$VAULT`.

## 1. Sync discipline (workstations do NOT auto-sync)

- **Before any work:** `git -C $VAULT pull --ff-only`
- **After any edit:** commit (conventional message, scope = what you touched) and **push**. Unpushed workstation edits are lost to every other machine and agent.
- The VPS auto-syncs every 15 min (`memory-sync.timer`) — on the VPS, avoid long-lived uncommitted state instead.

## 2. Scan (structure, links, orphans, frontmatter)

```bash
python3 scripts/vault_scan.py [--vault $VAULT]
```

Checks, per the contract: structure allowlist (parsed from the README's `vault-structure` block), **scope coverage** (see below), broken wikilinks (report ≥3x targets — contract says write those pages or de-link), orphan wiki pages, missing frontmatter, legacy `last_updated`, per-project `plan.md`/`log.md`/`journal.md` completeness, empty folders, non-kebab / hashed filenames, missing category `INDEX.md` (≥2 pages), and runtime/code files under a knowledge dir (`*.jsonl`/`*.db`/`*.py`/`*.log`; `/tasks/`+`/runs/` scaffold excluded, `system/rotate-extras.py` allowlisted). Exit 0 = clean, 1 = findings. Run it before and after any multi-file change.

**Scan scope is derived from the allowlist, never hand-listed.** Every top-level
allowlist entry must fall into exactly one class, declared at the top of
`vault_scan.py`: `WIKI_DIRS` (fully linted), `GENERATED_DIRS` (machine-written —
`reports/`, `audits/`), `EVIDENCE_DIRS` (`sources/` — cited by `claims[]`, not
wikilinks), `OPAQUE_DIRS` (runtime — `state/`, `scout/`, `Clippings/`), or
`DATA_ROOTS`. Root `.md` files in the allowlist are linted for links
automatically, so `CORE.md` and `CLAUDE.md` are covered without being listed
anywhere.

Two findings guard the classification itself: `scope-unclassified` (an allowlist
entry no class claims) and `scope-stale` (a class naming a path the allowlist
dropped). An unclassified directory falls through to **full wiki lint**, not to
being skipped — unknown paths get more scrutiny, never less. The invariant:
*a directory the contract protects still gets linted*; "protected" and
"unchecked" must never be the same state. Do not silence a `scope-unclassified`
finding by inventing a class for the path — decide what the path IS first.

## 3. New-page checklist (all steps, every time)

1. Right home per the README decision rule (sources / log / project / domain / typed page).
2. Frontmatter per the page's layer (plugin layer: `pageType`/`id`/`claims[]`…; structural layer: `name`/`summary`/`updatedAt`/`status`). `summary` is load-bearing.
3. Add a hook line to the category `INDEX.md` **and** root `index.md`.
4. Wikilinks liberally; `[[path/file|alias]]` when basenames collide.
5. Append the `log.md` entry (formats in §5).

## 4. Rule promotion (dated record → durable rule)

A lesson/rule discovered in a journal entry, incident, or session gets **promoted to `concepts/`**: one page per rule, `claims[]` with evidence pointing at the dated record, honest `confidence` (< 1.0 if reconstructed/inferred — the human raises it after review). Variant spellings of the slug go in frontmatter `aliases:` — **never fix old links by rewriting dated records.**

## 5. Log-entry formats (append-only, newest at bottom)

```
## [YYYY-MM-DD] ingest | <source title> → <pages touched>
## [YYYY-MM-DD] lint | <scope> → <changes>
## [YYYY-MM-DD] audit | <one-line result>. Report: audits/<date>-vault-audit.md
```

## 6. Frozen surfaces — never edit

`sources/` content, `audits/`, `incidents/`, dated `proposals` entries (they are evidence; historical paths stay verbatim). `PLAN.md` and `journal/` are human-curated: propose edits, never apply silently unless the human explicitly directs the change.

## 7. Structure freeze

The README carries a machine-readable `vault-structure` allowlist. **No new top-level folder or artifact pattern without a graded `proposals.md` entry first.** The scanner flags violations; do not "fix" a violation by adding it to the allowlist — file the proposal.

## 8. Per-surface notes

- **Obsidian**: machine surfaces are hidden via `.obsidian/app.json` `userIgnoreFilters` (tracked — keep it updated when adding machine dirs). Use frontmatter `aliases:` for variant slugs; callouts (`> [!note]`) for banners.
- **OpenClaw agents**: read `$VAULT/AGENTS.md` for the claims/evidence discipline; generated blocks between `<!-- openclaw:… -->` markers are plugin-owned.

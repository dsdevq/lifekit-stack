# memory-audit — weekly vault audit + safe auto-fix

Run by the OpenClaw cron `memory_vault_audit` (Sun 03:30 Europe/Dublin) as a
deterministic `--command` job. Deploy-copied to the gateway workspace mount
(`/home/node/.openclaw/workspace/memory-audit/`); this is the source of truth.

- `memory-vault-audit.sh` — wrapper: auto-fix → `openclaw wiki compile` (Pass 1)
  → `vault-rotate.py` (Rule-3 rotation, uses the fresh claims cache)
  → `vault-lint.py` (Pass 2) → writes `audits/<date>-vault-audit.md` + `log.md`,
  prints a one-line Telegram summary. Delivery-required guard: exits non-zero
  unless a fresh dated report was written (a silent no-op registers as failure).
- `vault-autofix.py` — SAFE mechanical fixes only (add missing frontmatter,
  quote malformed YAML, rename legacy fields). NEVER deletes; a `valid_fm()`
  self-check refuses any edit that would not reparse.
- `vault-rotate.py` — the vault README's Rule-3 MECHANICAL rotation classes:
  `audits/` keep-exactly-one · `log.md` entries >90d collapse into the
  "Compacted history" block · `sources/` pages ≥60d with zero citing claims and
  zero wiki references are deleted · `system/proposals.md` entries still `new`
  after 30d move to the Decisions record as `expired` (graded-or-die).
  Deletion happens ONLY inside these classes — git history is the archive.
  Judgment classes (stale STATUS, concluded-project folds) are never rotated
  here; they surface as lint findings. `--dry-run` prints the action JSON
  without touching anything.
- `vault-lint.py` — structural contract-lint (the checks the memory-wiki plugin
  cannot do: facts-vs-state, project triad, orphans, broken links, deprecated
  paths, stale STATUS, architecture-canvas drift). Frozen surfaces (evidence
  auto-fix may not touch) are not re-flagged — no permanent-noise findings.
  Dependency-free.
- `gen-report.py` — assembles the dated report (auto-fixed + rotated + residual
  findings).
- `tests/` — stdlib `unittest` suite over fixture vaults; run with
  `python3 -m unittest discover -s scripts/memory-audit/tests`.

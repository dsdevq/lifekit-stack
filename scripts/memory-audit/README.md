# memory-audit — weekly vault audit + safe auto-fix + mechanical rotation

Run by the OpenClaw cron `memory_vault_audit` (Sun 03:30 Europe/Dublin) as a
deterministic `--command` job. Deploy-copied to the gateway workspace mount
(`/home/node/.openclaw/workspace/memory-audit/`); this repo dir is the source
of truth — after merging changes, rsync it to the VPS workspace:

    rsync -a --delete scripts/memory-audit/ lifekit@lifekit-vps:/srv/openclaw/workspace/memory-audit/

- `memory-vault-audit.sh` — wrapper: auto-fix → vault-rotate (Pass 0b) →
  `openclaw wiki compile` (Pass 1) → `vault-lint.py` (Pass 2) → writes
  `audits/<date>-vault-audit.md` + `log.md`, prints a one-line Telegram summary.
  Delivery-required guard: exits non-zero unless a fresh dated report was
  written (a silent no-op registers as failure).
- `vault-autofix.py` — SAFE mechanical fixes only (add missing frontmatter,
  quote malformed YAML, rename legacy fields). Never deletes; a `valid_fm()`
  self-check refuses any edit that would not reparse.
- `vault-rotate.py` — README "Rule 3" mechanical rotation: deletes bridge-*
  machine dumps, superseded audit reports (keep newest), and sources cited by
  zero claims past the 60d TTL. Git history is the archive. Guards: contract
  sanity check, deletions confined to sources//audits/, runaway cap (default
  25/run, aborts whole plan), `--dry-run`. Judgment classes are NOT here.
- `vault-lint.py` — structural contract-lint (the checks the memory-wiki plugin
  cannot do: facts-vs-state, project triad, orphans, broken links, deprecated
  paths) + rotation-TTL detection for the judgment classes (log compaction due,
  STATUS stale >14d, proposals ungraded >30d, size caps). Dependency-free.
- `gen-report.py` — assembles the dated report (auto-fixed + rotated + residual
  findings).

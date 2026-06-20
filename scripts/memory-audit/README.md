# memory-audit — weekly vault audit + safe auto-fix

Run by the OpenClaw cron `memory_vault_audit` (Sun 03:30 Europe/Dublin) as a
deterministic `--command` job. Deploy-copied to the gateway workspace mount
(`/home/node/.openclaw/workspace/memory-audit/`); this is the source of truth.

- `memory-vault-audit.sh` — wrapper: auto-fix → `openclaw wiki compile` (Pass 1)
  → `vault-lint.py` (Pass 2) → writes `audits/<date>-vault-audit.md` + `log.md`,
  prints a one-line Telegram summary. Delivery-required guard: exits non-zero
  unless a fresh dated report was written (a silent no-op registers as failure).
- `vault-autofix.py` — SAFE mechanical fixes only (add missing frontmatter,
  quote malformed YAML, rename legacy fields). NEVER deletes; a `valid_fm()`
  self-check refuses any edit that would not reparse.
- `vault-lint.py` — structural contract-lint (the checks the memory-wiki plugin
  cannot do: facts-vs-state, project triad, orphans, broken links, deprecated
  paths). Dependency-free.
- `gen-report.py` — assembles the dated report (auto-fixed + residual findings).

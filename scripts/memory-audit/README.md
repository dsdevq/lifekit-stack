# memory-audit — weekly vault audit + safe auto-fix + Rule-3 rotation

Run by the OpenClaw cron `memory_vault_audit` (Sun 03:30 Europe/Dublin) as a
deterministic `--command` job. The cron executes from the gateway workspace
mount (`/home/node/.openclaw/workspace/memory-audit/`, host path
`/srv/openclaw/workspace/memory-audit/`); `deploy.sh` rsyncs this directory
there on every deploy (tests/ excluded). This repo dir is the source of truth —
never edit the workspace copy by hand.

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
  after 30d move to the Decisions record as `expired` (graded-or-die). Only
  `new` expires mechanically; any OTHER status past the TTL is a judgment call
  and surfaces as a `stale-proposal` lint finding instead — age decides who
  looks, status only decides deletion-vs-nag, so no status string can suspend
  the deadline silently.
  Deletion happens ONLY inside these classes — git history is the archive.
  Judgment classes (stale STATUS, concluded-project folds) are never rotated
  here; they surface as lint findings. `--dry-run` prints the action JSON
  without touching anything.
- `vault-lint.py` — structural contract-lint (the checks the memory-wiki plugin
  cannot do: facts-vs-state, project triad, orphans, broken links, deprecated
  paths, stale STATUS, architecture-canvas drift). `deprecated-path` is scanned
  per LINE and skips lines carrying an absolute date - the contract keeps legacy
  `~/.life`//`/srv/life` verbatim inside dated records, so only undated text is a
  forward-looking claim. Exemptions are IMMUTABLE-only:
  `sources/` (clipped evidence), `audits/` (generated reports) and the dated
  per-project `proposals/`/`tasks/`/`runs/` scaffolds — content no fix may touch,
  so a weekly finding on it has no consumer. **Nothing is exempted for being
  curated, protected, or merely unowned**: `journal/` was exempt as "verbatim
  evidence" for three months while holding unowned agent narrative, and the
  exemption made it invisible to the only scheduled linter. Two guards keep that
  from recurring — `stale-exemption` (an exempt prefix the README allowlist no
  longer carries) and an `info`-severity `exemption-census` line naming every
  exemption and its file count in each weekly report.
  Dependency-free.
- `gen-report.py` — assembles the dated report (auto-fixed + rotated + residual
  findings).
- `tests/` — stdlib `unittest` suite over fixture vaults; run with
  `python3 -m unittest discover -s scripts/memory-audit/tests`.

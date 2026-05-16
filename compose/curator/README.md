# lifekit-curator

Standalone container that drains `queue.jsonl` and uses Claude CLI to extract memorable facts into `~/.life/domains/`.

## What it does

1. Watches `LIFEKIT_QUEUE_FILE` (default `/srv/life/queue.jsonl`).
2. On wake-up (interval `LIFEKIT_WAKE_INTERVAL_SECONDS`, default 900s = 15 min), drains pending entries.
3. For each entry, runs the memorize prompt against Claude CLI, which updates `~/.life/domains/*.md`, `~/.life/skills/*.md`, and `~/.life/USER.md` as appropriate.
4. Daily: runs a "dream cycle" consolidation pass over all domain/skill files.

## Build

```bash
docker build -t lifekit-curator:dev compose/curator/
```

## Run (standalone, for testing)

```bash
docker run --rm \
  -v "$HOME/.life:/srv/life" \
  -v "$HOME/.claude:/home/lifekit/.claude:ro" \
  -e LIFEKIT_LOG_LEVEL=INFO \
  lifekit-curator:dev
```

The Claude auth mount (`~/.claude` from the host) lets Claude CLI inside the container reuse the host's logged-in session — no API key handling on the VPS.

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `LIFEKIT_LIFE_DIR` | `/srv/life` | Root of the knowledge layer. |
| `LIFEKIT_QUEUE_FILE` | `<LIFE_DIR>/queue.jsonl` | Append-only queue drained by the curator. |
| `LIFEKIT_CONSOLIDATION_MARKER` | `<LIFE_DIR>/.last_consolidation` | Tracks dream-cycle cadence. |
| `LIFEKIT_CONSOLIDATION_INTERVAL_SECONDS` | `86400` | Time between dream cycles. |
| `LIFEKIT_WAKE_INTERVAL_SECONDS` | `900` | Max sleep between queue checks. |
| `LIFEKIT_CLAUDE_BIN` | `claude` | Override if `claude` is not on PATH. |
| `LIFEKIT_CLAUDE_TIMEOUT_SECONDS` | `180` | Per-call timeout for Claude invocations. |
| `LIFEKIT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

## Provenance

Vendored from [`dsdevq/swarm`](https://github.com/dsdevq/swarm) — `pipeline/src/curator.py`, 2026-05-16. Modifications:

- Replaced `claude_agent_sdk` invocations with `claude` CLI via subprocess. The SDK is restricted for autonomous/AFK use from June 2026 per Anthropic; the CLI is the human-path that remains.
- Made all paths configurable via env vars; removed `~/.personal-agent/` hardcodes.
- Removed the `_send_pending_improvements()` Telegram path — that responsibility belongs to OpenClaw cron now, not the curator.
- Added `main()` entry point for container `CMD`.

## Eventual home

This file may move into the public `dsdevq/lifekit` Python package as `lifekit.curator` and be installed via pip. For v0.x it ships vendored with `lifekit-stack` to keep the deploy self-contained.

# devclaw-mcp container

DevClaw v2 — the MCP server that fronts OpenHands for autonomous coding tasks. Replaces v1's `lifekit-orchestrator` container.

## What this is

- TypeScript MCP server (streamable-http) exposing `implement_feature`, `fix_bug`, `review_repository`, `get_status`, `list_tasks`
- Python runner subprocess that drives the OpenHands SDK
- Inherits Claude Code OAuth from the gateway's bind-mounted `~/.claude` session — no API key

## Build

```bash
docker compose -f compose/docker-compose.yml --env-file /srv/openclaw/config/.env \
  build devclaw-mcp
```

Override the source ref via `--build-arg DEVCLAW_REF=<branch-or-tag>`.

## Verify

```bash
# from inside the compose network
docker compose ... run --rm devclaw-mcp curl -fs http://devclaw-mcp:8000/health

# or from the host (devclaw-mcp is internal-only — no host port binding)
docker compose ... exec devclaw-mcp curl -fs http://127.0.0.1:8000/health
```

Should print `{"ok":true,"name":"devclaw","version":"0.0.3"}`.

## State

SQLite at `/var/lib/devclaw/devclaw.db` inside the container, mounted from the `devclaw-state` named volume. Survives `up --force-recreate`.

## Registering with OpenClaw

`mcp.servers` in `openclaw.json` is an **object keyed by id**, NOT an array (runtime-confirmed 2026-05-25). See `mcp-config.json` for the exact snippet. The `cutover-v2.sh` script does this merge for you.

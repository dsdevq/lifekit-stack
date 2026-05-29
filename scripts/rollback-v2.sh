#!/usr/bin/env bash
#
# rollback-v2.sh — undo cutover-v2.sh. Restores the most recent
# openclaw.json backup, restarts v1 lifekit-orchestrator, stops
# devclaw-mcp, restarts openclaw-gateway.
#
# Usage:
#   scripts/rollback-v2.sh                       # auto-pick latest .bak-cutover-*
#   scripts/rollback-v2.sh /path/to/backup.json  # explicit backup file

set -euo pipefail

REPO_DIR="${REPO_DIR:-/srv/lifekit-stack}"
COMPOSE_FILE="${COMPOSE_FILE:-$REPO_DIR/compose/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-/srv/openclaw/config/.env}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-/srv/openclaw/config/openclaw.json}"
V1_CONTAINER="${V1_CONTAINER:-compose-lifekit-orchestrator-1}"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
say() { printf "\n\033[1;33m[$(ts)] %s\033[0m\n" "$*"; }

BACKUP="${1:-}"
if [[ -z "$BACKUP" ]]; then
  # shellcheck disable=SC2012  # backup filenames are controlled; ls -t newest-first is fine here
  BACKUP="$(ls -t "${OPENCLAW_CONFIG}".bak-cutover-* 2>/dev/null | head -1 || true)"
fi
[[ -n "$BACKUP" && -f "$BACKUP" ]] || { echo "no backup found — pass the path explicitly" >&2; exit 1; }

say "Restoring openclaw.json from $BACKUP"
cp -a "$BACKUP" "$OPENCLAW_CONFIG"

say "Stopping devclaw-mcp"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" stop devclaw-mcp || true

say "Restarting v1 lifekit-orchestrator"
if docker ps -aq --filter "name=^${V1_CONTAINER}$" | grep -q .; then
  docker start "$V1_CONTAINER"
else
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d lifekit-orchestrator
fi

say "Restarting openclaw-gateway with restored config"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --force-recreate openclaw-gateway

say "Rollback done. Verify Telegram still responds + v1 orchestrator is up."

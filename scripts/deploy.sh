#!/usr/bin/env bash
# deploy.sh — incremental update path, runs on the VPS.
# Pulls the latest template, rebuilds containers, verifies health.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/srv/lifekit-stack}"
COMPOSE_FILES=(
  -f "${REPO_DIR}/compose/docker-compose.yml"
  -f "${REPO_DIR}/compose/docker-compose.extra.yml"
)

cd "${REPO_DIR}"

echo "→ Pulling latest..."
git pull --ff-only

echo "→ Rebuilding and restarting affected services..."
docker compose "${COMPOSE_FILES[@]}" up -d --build

echo "→ Health check..."
docker compose "${COMPOSE_FILES[@]}" exec -T openclaw-cli openclaw doctor
docker compose "${COMPOSE_FILES[@]}" exec -T openclaw-cli openclaw health

# Optional: synthetic Telegram self-test.
# Wired by the wizard if user opted in.
if [[ -x "${REPO_DIR}/scripts/healthcheck-telegram.sh" ]]; then
  echo "→ Synthetic Telegram round-trip..."
  "${REPO_DIR}/scripts/healthcheck-telegram.sh"
fi

echo "✓ Deploy complete."

#!/usr/bin/env bash
# TODO: when a second repo needs auto-redeploy on this VPS, lift this into
# /etc/lifekit-redeploy/repos.yaml + a generic loop. See memory
# feedback-n2-generalization-trigger. Don't copy-paste this file.
set -euo pipefail

REPO_DIR="${LIFEKIT_DASHBOARD_DIR:-/srv/lifekit-dashboard}"
COMPOSE_FILE="/srv/lifekit-stack/compose/docker-compose.yml"
ENV_FILE="/srv/openclaw/config/.env"

cd "$REPO_DIR"
git fetch --quiet origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0
fi

echo "redeploy: $LOCAL → $REMOTE"
git pull --ff-only origin main
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --build lifekit-dashboard

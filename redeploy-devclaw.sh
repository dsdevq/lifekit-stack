#!/usr/bin/env bash
# Fast devclaw redeploy: cache-bust only the clone+console+pip layers (~1 min),
# keeping apt/npm/Playwright cached. Full clean rebuild: add --no-cache.
set -euo pipefail
SHA=$(git ls-remote https://github.com/dsdevq/devclaw.git main | cut -f1)
cd /srv/lifekit-stack/compose
docker compose build --build-arg CACHEBUST="$SHA" devclaw-mcp
docker compose up -d devclaw-mcp
echo "deployed devclaw @ ${SHA:0:12}"

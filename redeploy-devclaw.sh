#!/usr/bin/env bash
# Fast devclaw redeploy: cache-bust only the clone+console+pip layers (~1 min),
# keeping apt/npm/Playwright cached. Full clean rebuild: add --no-cache.
#
# GIT_SHA/BUILT_AT bake the build identity into the image (devclaw #494) —
# devclaw surfaces both on /health, so "which code is running" is one curl,
# never ssh + docker inspect.
#
# ⚠ Remember (2026-08-12 lesson): a redeploy resets devclaw's run window to
# disabled — re-arm it with set_run_schedule after every deploy.
set -euo pipefail
SHA=$(git ls-remote https://github.com/dsdevq/devclaw.git main | cut -f1)
BUILT_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cd /srv/lifekit-stack/compose
docker compose build \
  --build-arg CACHEBUST="$SHA" \
  --build-arg GIT_SHA="$SHA" \
  --build-arg BUILT_AT="$BUILT_AT" \
  devclaw-mcp
docker compose up -d devclaw-mcp
echo "deployed devclaw @ ${SHA:0:12} (built ${BUILT_AT})"
echo "⚠ run window resets to disabled on redeploy — re-arm via set_run_schedule"

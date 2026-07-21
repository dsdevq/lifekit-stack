#!/usr/bin/env bash
# deploy-smoke.sh — post-deploy eval smoke: fire ONE eval ticket against the
# freshly deployed devclaw-mcp image, detached (fire-and-forget).
#
# Why: deploys are empirically when regressions land (devclaw
# docs/proposals/continuous-eval-projection.md §5-O4). One sonnet ticket
# (~5 min sandbox run) per deploy buys a continuous regression signal.
#
# Fire-and-forget: `docker compose run -d` returns in seconds; the eval run
# continues in a one-off container and writes its report JSON to
# <workspaces>/.measure/runs/ on the host for later ingest by devclaw's
# `evals ingest` verb.
#
# Opt-out: DEVCLAW_DEPLOY_SMOKE=0 (a repo Actions variable in CI) skips it.
# Missing basket file on the host (fresh host, no eval corpus yet) is a
# skip, not a failure. Everything else fails loud — a docker command that
# can't even launch the container should fail the CI step visibly.

set -euo pipefail

say() { printf '\n\033[1;34m→ %s\033[0m\n' "$*"; }

REPO_DIR="${REPO_DIR:-/srv/lifekit-stack}"
ENV_FILE="${ENV_FILE:-/srv/openclaw/config/.env}"
COMPOSE_FILE="${REPO_DIR}/compose/docker-compose.yml"

# Container-side paths (inside devclaw-mcp). The workspaces tree is
# bind-mounted from the host (compose: LIFEKIT_DEVCLAW_WORKSPACES →
# /var/lib/devclaw/workspaces), so the log + report land host-side too.
MEASURE_DIR="/var/lib/devclaw/workspaces/.measure"
HOST_PREFIX="${LIFEKIT_DEVCLAW_WORKSPACES:-/srv/devclaw/workspaces}"

TICKET="${DEVCLAW_SMOKE_TICKET:-prs-list-sort-by-created}"
BASKET="${DEVCLAW_SMOKE_BASKET:-${MEASURE_DIR}/v01-proof.json}"
# Host view of the (container-path) basket, for the existence guard.
HOST_BASKET="${BASKET/#\/var\/lib\/devclaw\/workspaces/${HOST_PREFIX}}"

if [[ "${DEVCLAW_DEPLOY_SMOKE:-1}" == "0" ]]; then
  say "deploy smoke: disabled (DEVCLAW_DEPLOY_SMOKE=0) — skipping"
  exit 0
fi

if [[ ! -f "${HOST_BASKET}" ]]; then
  say "deploy smoke: basket not found at ${HOST_BASKET} — skipping (no-op)"
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${MEASURE_DIR}/deploy-smoke-${STAMP}.log"

say "deploy smoke: firing 1-ticket eval run (ticket=${TICKET}, detached)"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" \
  run -d --rm --no-deps --entrypoint sh devclaw-mcp -c \
  "cd /app \
   && MEASURE_WORKROOT=${MEASURE_DIR} \
      MEASURE_REPORT_DIR=${MEASURE_DIR}/runs \
      DEVCLAW_SANDBOX_IMAGE=devclaw-sandbox:local \
      DEVCLAW_EXEC_MODEL=claude-sonnet-4-6 \
      python3 evals/measure_passrate.py --basket ${BASKET} --only ${TICKET} \
      >> ${LOG} 2>&1"

echo "  log (host):    ${HOST_PREFIX}/.measure/deploy-smoke-${STAMP}.log"
echo "  report (host): ${HOST_PREFIX}/.measure/runs/ (JSON, written when the run finishes)"

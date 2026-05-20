#!/usr/bin/env bash
# deploy.sh — run on the VPS, after bootstrap-vps.sh has set up the host.
#
# Pulls the latest template, rebuilds containers, runs OpenClaw's health checks.
#
# Prerequisites on the VPS:
#   /srv/lifekit-stack/                  ← cloned by bootstrap-vps.sh
#   /srv/openclaw/config/.env            ← scp'd from your laptop (see .env.example)
#   /srv/openclaw/workspace/skills/      ← rsync'd from your laptop's ~/.openclaw/workspace/skills/
#   /srv/life/                           ← rsync'd from your laptop's ~/.life/
#   /home/lifekit/.claude/               ← either logged in on the VPS via `claude auth login`,
#                                          or rsync'd from your laptop's ~/.claude/
#
# Re-runnable. Idempotent. Restarts only the services with changed images/config.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/srv/lifekit-stack}"
ENV_FILE="${ENV_FILE:-/srv/openclaw/config/.env}"
COMPOSE_FILE="${REPO_DIR}/compose/docker-compose.yml"

say() { printf '\n\033[1;34m→ %s\033[0m\n' "$*"; }

cd "${REPO_DIR}"

# ─── Sanity ──────────────────────────────────────────────────────────────────

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy .env.example and fill it in:" >&2
  echo "  scp .env.example user@vps:${ENV_FILE}" >&2
  exit 1
fi

# ─── Pull latest ─────────────────────────────────────────────────────────────

say "git pull"
git pull --ff-only

# ─── lifekit-dashboard: VPS-local clone ──────────────────────────────────────
#
# The dashboard image is built from a VPS-local clone of dsdevq/lifekit-dashboard
# (not vendored into this repo). gh CLI must be authed on the host as the
# lifekit user so the private-repo clone works.

DASHBOARD_DIR="${LIFEKIT_DASHBOARD_DIR:-/srv/lifekit-dashboard}"
say "lifekit-dashboard: sync ${DASHBOARD_DIR}"
if [ ! -d "${DASHBOARD_DIR}/.git" ]; then
  gh repo clone dsdevq/lifekit-dashboard "${DASHBOARD_DIR}"
else
  git -C "${DASHBOARD_DIR}" pull --ff-only
fi

# ─── Build + start ───────────────────────────────────────────────────────────

say "docker compose up -d --build"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --build

# ─── Skill native-deps install ───────────────────────────────────────────────
#
# Workspace skills are rsync'd from the laptop and may carry a package.json
# with native deps (e.g. nutrition-claw uses `sharp`, which needs a
# linux-arm64 build on the VPS). Running `npm install --omit=dev` inside the
# gateway container — which now bakes python3/make/g++/libvips-dev — produces
# the correct platform binaries. Source: see proposals/2026-05-19-vps-skill-wrappers.md.
#
# Skills without a package.json are skipped. life-state's CLI binary is
# installed -g separately (see SKILL_CLI_INSTALL below).

SKILLS_DIR="${OPENCLAW_WORKSPACE_DIR:-/srv/openclaw/workspace}/skills"
if [[ -d "${SKILLS_DIR}" ]]; then
  say "Installing skill native deps inside openclaw-gateway"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T openclaw-gateway \
    bash -c '
      set -e
      shopt -s nullglob
      for pkg in /home/node/.openclaw/workspace/skills/*/package.json; do
        dir="$(dirname "$pkg")"
        echo "→ npm install --omit=dev in $dir"
        (cd "$dir" && npm install --omit=dev --no-audit --no-fund) \
          || echo "  (npm install failed in $dir — review above)"
      done
    ' || echo "(skill native-dep install reported issues — review above)"
fi

# ─── Skill CLI install (life-state etc.) ─────────────────────────────────────
#
# Some skills shell out to a CLI binary that is NOT shipped from this repo
# (e.g. life-state is published from a separate ~/projects/life-state repo
# on the maintainer's laptop, then `npm install -g`'d on the VPS). The
# 2026-05-16 PC→VPS migration copied the skill prompts but not these external
# CLIs, leaving the gateway with "command not found" errors at runtime.
#
# Until those CLIs are vendored into this repo (or published to npm under
# a stable name), the install is a manual one-shot performed by Denys after
# rsync. See proposals/2026-05-19-vps-skill-wrappers.md for the recovery
# checklist. The block below is a no-op placeholder so the contract is
# documented in code, not just in proposals.
#
# Example (manual, run on the VPS after this script finishes):
#   docker compose -f compose/docker-compose.yml --env-file /srv/openclaw/config/.env \
#     exec openclaw-gateway npm install -g /home/node/.openclaw/workspace/external/life-state

# ─── Health checks ───────────────────────────────────────────────────────────

say "Waiting 10s for services to settle"
sleep 10

say "openclaw doctor"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T openclaw-cli \
  node dist/index.js doctor || echo "(doctor reported issues — review above)"

say "openclaw health"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T openclaw-cli \
  node dist/index.js health || echo "(health reported issues — review above)"

say "container status"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps

say "✓ deploy complete."

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
OPENCLAW_CONFIG_DIR="${OPENCLAW_CONFIG_DIR:-/srv/openclaw/config}"
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

# ─── OpenClaw onboard (first deploy only) ────────────────────────────────────
#
# A fresh host has no /srv/openclaw/config/openclaw.json — without it the
# gateway can't start. `openclaw onboard` materializes it from the .env using
# the same non-interactive flags that produced a working config on cax11.
# Skipped on every subsequent deploy because the file persists in the
# host-mounted config dir (idempotent).
#
# Why openclaw-gateway and not openclaw-cli: openclaw-cli has
# `network_mode: service:openclaw-gateway`, so running it on a fresh host
# would start (and crash) the gateway as a dependency — the gateway needs the
# very openclaw.json this step generates. openclaw-gateway has the same image
# and the same config-dir bind-mount, no inter-service network dep, and with
# `--no-deps --entrypoint openclaw` we get a one-shot CLI invocation that
# only writes the config file and exits.
if [[ ! -f "${OPENCLAW_CONFIG_DIR}/openclaw.json" ]]; then
  say "openclaw onboard (generating ${OPENCLAW_CONFIG_DIR}/openclaw.json)"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" \
    run --rm --no-deps --entrypoint openclaw openclaw-gateway \
      onboard \
        --non-interactive \
        --accept-risk \
        --flow quickstart \
        --mode local \
        --auth-choice skip \
        --gateway-auth token \
        --gateway-token-ref-env OPENCLAW_GATEWAY_TOKEN \
        --gateway-bind loopback \
        --gateway-port 18789
fi

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

# ─── modules.yaml → /srv/life/system/ ───────────────────────────────────────

LIFE_DIR="${LIFEKIT_LIFE_DIR:-/srv/life}"
say "syncing config/modules.yaml → ${LIFE_DIR}/system/modules.yaml"
mkdir -p "${LIFE_DIR}/system"
cp "${REPO_DIR}/defaults/modules.yaml" "${LIFE_DIR}/system/modules.yaml"

# Runtime-state dir — split from /srv/life per proposal
# 2026-05-27-runtime-knowledge-split. Idempotent guard so an in-place upgrade
# (without a fresh bootstrap-vps.sh run) still ends up with the dirs the compose
# bind-mounts expect.
STATE_DIR="${LIFEKIT_STATE_DIR_HOST:-/var/lib/lifekit}"
if [ ! -d "${STATE_DIR}" ]; then
  say "creating ${STATE_DIR} (runtime-state dir, first-time upgrade)"
  sudo install -d -o "$(whoami)" -g "$(whoami)" -m 0750 \
    "${STATE_DIR}" \
    "${STATE_DIR}/tasks" \
    "${STATE_DIR}/.curator-proposed"
fi

# ─── Resolve DEVCLAW_REF to a concrete SHA ──────────────────────────────────
#
# Both devclaw-mcp and devclaw-sandbox have a `RUN git clone --branch
# "${DEVCLAW_REF}"` layer. When DEVCLAW_REF is a moving ref like `main`,
# BuildKit's cache key for that layer doesn't see the upstream SHA move —
# so a `--build` rebuild reuses the cached clone and ships stale code.
# Resolving the ref to a SHA before passing it through makes the cache key
# content-addressed and invalidates exactly when main moves. This is the
# fix for the deploy-was-incomplete failure mode that burned a smoke-test
# cycle on 2026-05-28 (devclaw-mcp got the new runner.py via --no-cache;
# devclaw-sandbox kept the old one because its layer was still cached).
DEVCLAW_REF_INPUT="${DEVCLAW_REF:-main}"
say "resolving devclaw ref ${DEVCLAW_REF_INPUT} → SHA"
DEVCLAW_SHA="$(git ls-remote https://github.com/dsdevq/devclaw.git \
  "refs/heads/${DEVCLAW_REF_INPUT}" "refs/tags/${DEVCLAW_REF_INPUT}" \
  | awk 'NR==1{print $1}')"
if [[ -z "${DEVCLAW_SHA}" ]]; then
  # Not a branch or tag — assume the caller passed a SHA already.
  DEVCLAW_SHA="${DEVCLAW_REF_INPUT}"
fi
export DEVCLAW_REF="${DEVCLAW_SHA}"
echo "  using DEVCLAW_REF=${DEVCLAW_SHA}"

# ─── Build + start ───────────────────────────────────────────────────────────

say "docker compose up -d --build"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --build

# ─── devclaw-sandbox: per-task ephemeral container the MCP spawns ────────────
#
# Not in the default `up` rotation (profile: build-only). Build explicitly
# here so the image stays in sync with devclaw-mcp's runner.py — they share
# v2/python-runner/runner.py and the dashboard event-stream silently breaks
# if they drift.
say "building devclaw-sandbox (per-task isolation image)"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" \
  --profile build-only build devclaw-sandbox

# ─── Verify devclaw-mcp and devclaw-sandbox runner.py match ─────────────────
#
# Both images clone the same DEVCLAW_REF and copy v2/python-runner/runner.py
# in. After a successful build they MUST md5-match — otherwise BuildKit
# cached a stale clone layer in one of them and the sandbox will run a
# different runner.py than the MCP server expects. This catches the exact
# silent failure that burned a smoke-test cycle on 2026-05-28.
say "verifying runner.py matches between devclaw-mcp and devclaw-sandbox"
MCP_MD5=$(docker run --rm --entrypoint md5sum devclaw-mcp:local \
  /app/v2/python-runner/runner.py | awk '{print $1}')
SBX_MD5=$(docker run --rm --entrypoint md5sum devclaw-sandbox:local \
  /opt/devclaw/runner.py | awk '{print $1}')
if [[ "${MCP_MD5}" != "${SBX_MD5}" ]]; then
  cat >&2 <<EOF
✗ runner.py mismatch between devclaw-mcp and devclaw-sandbox:
    devclaw-mcp:local      /app/v2/python-runner/runner.py  md5=${MCP_MD5}
    devclaw-sandbox:local  /opt/devclaw/runner.py           md5=${SBX_MD5}
  Both should clone from DEVCLAW_REF=${DEVCLAW_SHA}; one image kept a
  cached clone layer. Re-run with:
    docker compose -f ${COMPOSE_FILE} build --no-cache devclaw-mcp
    docker compose -f ${COMPOSE_FILE} --profile build-only build \\
      --no-cache devclaw-sandbox
EOF
  exit 1
fi
echo "  ✓ both images carry runner.py md5=${MCP_MD5}"

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
# Some module CLIs are NOT shipped from this repo — they live in separate
# repos (workout-claw, life-state, health-claw) on the maintainer's laptop
# and are rsync'd to /srv/openclaw/workspace/external/ on the VPS. The
# health-claw container installs them at start from those paths.
#
# Before running this deploy script after adding the health-claw service,
# rsync the external sources to the VPS:
#
#   rsync -a ~/projects/workout-claw/ lifekit@lifekit-vps:/srv/openclaw/workspace/external/workout-claw/
#   rsync -a ~/projects/life-state/   lifekit@lifekit-vps:/srv/openclaw/workspace/external/life-state/
#   rsync -a ~/projects/health-claw/  lifekit@lifekit-vps:/srv/openclaw/workspace/external/health-claw/
#
# Also create the workout-claw data dir if it doesn't exist:
#   ssh lifekit@lifekit-vps mkdir -p /srv/workout-claw
#
# And register the health-claw MCP in openclaw.json:
#   (add the entry from compose/health-claw/mcp-config.json to /srv/openclaw/config/openclaw.json)

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

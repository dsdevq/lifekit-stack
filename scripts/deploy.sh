#!/usr/bin/env bash
# deploy.sh — run on the VPS, after bootstrap-vps.sh has set up the host.
#
# Pulls the latest template, rebuilds containers, runs OpenClaw's health checks.
#
# Prerequisites on the VPS:
#   /srv/lifekit-stack/                  ← cloned by bootstrap-vps.sh
#   /srv/openclaw/config/.env            ← scp'd from your laptop (see .env.example)
#   /srv/openclaw/workspace/skills/      ← rsync'd from your laptop's ~/.openclaw/workspace/skills/
#   /srv/memory/                           ← rsync'd from your laptop's ~/memory/
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
#
# fetch + hard-reset is race-proof and branch-agnostic. A bare `git pull
# --ff-only` aborts when the local branch has diverged (e.g. VPS is on
# feat/goalclaw-service while origin/main moved on). The CI workflow does the
# same thing before invoking this script, so this is a no-op in CI and a
# self-update when run directly on the VPS.

say "git pull"
STACK_DEFAULT="$(git remote show origin | sed -n 's/.*HEAD branch: //p' | head -1)"
STACK_DEFAULT="${STACK_DEFAULT:-main}"
git fetch -q origin "${STACK_DEFAULT}"
git reset -q --hard "origin/${STACK_DEFAULT}"

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
# `git pull` runs from a non-interactive `sudo -u lifekit -H bash -lc "..."`
# subshell, where gh's git-credential-helper cannot prompt. Bake the gh token
# into the remote URL as x-access-token so plain `git pull` authenticates
# headlessly. Idempotent: re-running just resets the URL to the same value.
TOKEN="$(gh auth token)"
if [ -d "${DASHBOARD_DIR}/.git" ]; then
  git -C "${DASHBOARD_DIR}" remote set-url origin \
    "https://x-access-token:${TOKEN}@github.com/dsdevq/lifekit-dashboard.git"
  git -C "${DASHBOARD_DIR}" pull --ff-only
else
  gh repo clone dsdevq/lifekit-dashboard "${DASHBOARD_DIR}"
  git -C "${DASHBOARD_DIR}" remote set-url origin \
    "https://x-access-token:${TOKEN}@github.com/dsdevq/lifekit-dashboard.git"
fi

# ─── modules.yaml → /srv/memory/system/ ───────────────────────────────────────

LIFE_DIR="${LIFEKIT_LIFE_DIR:-/srv/memory}"
say "syncing config/modules.yaml → ${LIFE_DIR}/system/modules.yaml"
mkdir -p "${LIFE_DIR}/system"
cp "${REPO_DIR}/defaults/modules.yaml" "${LIFE_DIR}/system/modules.yaml"

# Runtime-state dir — split from /srv/memory per proposal
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

# ─── Log the resolved devclaw SHA ───────────────────────────────────────────
#
# The devclaw-mcp + devclaw-sandbox Dockerfiles take DEVCLAW_REF and feed
# it straight to `git clone --branch`, which only accepts a branch or tag
# name (not a SHA). So we keep DEVCLAW_REF as a ref and only print the
# resolved SHA — useful for the deploy log and the md5-mismatch error
# message at the end.
DEVCLAW_REF_INPUT="${DEVCLAW_REF:-main}"
say "resolving devclaw ref ${DEVCLAW_REF_INPUT} → SHA (for logging)"
DEVCLAW_SHA="$(git ls-remote https://github.com/dsdevq/devclaw.git \
  "refs/heads/${DEVCLAW_REF_INPUT}" "refs/tags/${DEVCLAW_REF_INPUT}" \
  | awk 'NR==1{print $1}')"
DEVCLAW_SHA="${DEVCLAW_SHA:-unknown}"
echo "  DEVCLAW_REF=${DEVCLAW_REF_INPUT}  DEVCLAW_SHA=${DEVCLAW_SHA}"

# ─── Rebuild devclaw-mcp + devclaw-sandbox with --no-cache ──────────────────
#
# Both images have a `RUN git clone --branch "${DEVCLAW_REF}"` layer.
# When DEVCLAW_REF is a moving ref like `main`, BuildKit's cache key for
# that layer doesn't track upstream movement — a normal `--build` reuses
# the cached clone and ships stale code (the silent failure that burned
# a smoke-test cycle on 2026-05-28: devclaw-mcp got the new runner.py
# manually; devclaw-sandbox kept the old one because its layer cached).
#
# `--no-cache` rebuilds these two images deterministically. Pip + npm
# layers add ~3-4 min total per image but that's the price of correctness
# until we either pin DEVCLAW_REF to a SHA at the caller (and modify the
# Dockerfile to handle SHAs) or add a CACHEBUST arg.
#
# Built BEFORE the main `up -d --build` so the subsequent compose call
# is a cache-hit no-op for these images.
say "rebuilding devclaw-mcp --no-cache (avoid stale git-clone layer)"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" \
  build --no-cache devclaw-mcp
say "rebuilding devclaw-sandbox --no-cache (profile=build-only)"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" \
  --profile build-only build --no-cache devclaw-sandbox

# ─── Build + start ───────────────────────────────────────────────────────────

say "docker compose up -d --build"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --build

# ─── Reattach openclaw-cli to new gateway network namespace ──────────────────
#
# openclaw-cli uses network_mode: service:openclaw-gateway. When only the
# gateway's config or image changes, compose recreates the gateway container
# but may leave the CLI container alive with a stale reference to the old
# network namespace — so ws://127.0.0.1:18789 silently fails and openclaw
# commands (cron runs, health checks) can't reach the gateway.
#
# Force-recreate whenever the CLI is running (--profile cli covers the
# profile gate; the command is a no-op if the CLI container is stopped).
say "reattaching openclaw-cli to new gateway network namespace"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" \
  --profile cli up -d --force-recreate openclaw-cli \
  || echo "(openclaw-cli force-recreate skipped — not running)"

# ─── Reset stuck agent sessions ───────────────────────────────────────────────
#
# Killing the gateway mid-conversation (every deploy) leaves agent sessions
# as status=running. The next inbound message hits that session key, finds it
# "running", and the gateway won't start a fresh conversation — so the agent
# goes silent until the session is manually cleared. Reset any stuck sessions
# immediately after the gateway starts so the first post-deploy message always
# gets a clean session.
say "resetting stuck agent sessions (running → aborted)"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T openclaw-gateway \
  python3 -c "
import json, glob, os, sys
stores = glob.glob('/home/node/.openclaw/agents/*/sessions/sessions.json')
total = 0
for path in stores:
    try:
        with open(path) as f:
            data = json.load(f)
        changed = 0
        for key, val in data.items():
            if isinstance(val, dict) and val.get('status') == 'running':
                val['status'] = 'done'
                val['abortedLastRun'] = True
                changed += 1
        if changed:
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            agent = os.path.basename(os.path.dirname(os.path.dirname(path)))
            print(f'  {agent}: reset {changed} stuck session(s)')
            total += changed
    except Exception as e:
        print(f'  warning: {path}: {e}', file=sys.stderr)
if total == 0:
    print('  no stuck sessions found')
else:
    print(f'  total: {total} session(s) reset')
" || echo "(session reset reported issues — review above)"

# ─── Verify image runner.py matches upstream at DEVCLAW_REF ─────────────────
#
# Both devclaw-mcp and devclaw-sandbox clone DEVCLAW_REF and copy
# openhands-runner/runner.py in. After build, the runner.py inside each
# image MUST md5-match the file on github at the resolved SHA. That
# catches TWO failure modes:
#   (a) cross-image drift: BuildKit cached a stale clone layer in one of
#       them (the 2026-05-28 smoke-test miss).
#   (b) stale-default ref: both images consistently built from a stale
#       branch that no longer tracks upstream changes (the 2026-05-28
#       deploy that "succeeded" but shipped pre-fix code because the
#       compose default still pointed at a long-merged feat branch).
# Comparing both image files against the upstream SHA's runner.py
# catches both at once.
say "verifying runner.py matches dsdevq/devclaw@${DEVCLAW_REF_INPUT}"
MCP_MD5=$(docker run --rm --entrypoint md5sum devclaw-mcp:local \
  /app/openhands-runner/runner.py | awk '{print $1}')
SBX_MD5=$(docker run --rm --entrypoint md5sum devclaw-sandbox:local \
  /opt/devclaw/runner.py | awk '{print $1}')
UPSTREAM_URL="https://raw.githubusercontent.com/dsdevq/devclaw/${DEVCLAW_SHA}/openhands-runner/runner.py"
UPSTREAM_MD5=$(curl -fsS "${UPSTREAM_URL}" | md5sum | awk '{print $1}')
if [[ -z "${UPSTREAM_MD5}" || "${UPSTREAM_MD5}" == "d41d8cd98f00b204e9800998ecf8427e" ]]; then
  echo "✗ could not fetch upstream runner.py from ${UPSTREAM_URL}" >&2
  exit 1
fi
if [[ "${MCP_MD5}" != "${UPSTREAM_MD5}" || "${SBX_MD5}" != "${UPSTREAM_MD5}" ]]; then
  cat >&2 <<EOF
✗ runner.py md5 mismatch:
    upstream (${DEVCLAW_REF_INPUT}@${DEVCLAW_SHA:0:7})  md5=${UPSTREAM_MD5}
    devclaw-mcp:local                                 md5=${MCP_MD5}
    devclaw-sandbox:local                             md5=${SBX_MD5}
  One or both images are out of sync with upstream. Common causes:
    - DEVCLAW_REF points at a stale branch (default is 'main')
    - BuildKit cached a clone layer that didn't see upstream move
  Re-run with --no-cache on the stale image(s).
EOF
  exit 1
fi
echo "  ✓ runner.py md5=${UPSTREAM_MD5} matches upstream + both images"

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
#
# Wait 30s instead of 10s: Telegram channels have a 120s connect-grace period
# but are typically connected within 15-20s. 30s gives them time to show as
# "connected" in the channels status output so the deploy log is useful.

say "Waiting 30s for services and Telegram channels to settle"
sleep 30

say "openclaw doctor"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" --profile cli run --rm -T openclaw-cli \
  doctor || echo "(doctor reported issues — review above)"

say "openclaw health"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" --profile cli run --rm -T openclaw-cli \
  health || echo "(health reported issues — review above)"

say "openclaw channels status"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" --profile cli run --rm -T openclaw-cli \
  channels status || echo "(channels status reported issues — review above)"

say "container status"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps

say "✓ deploy complete."

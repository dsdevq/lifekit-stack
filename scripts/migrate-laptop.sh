#!/usr/bin/env bash
# migrate-laptop.sh — push the local OpenClaw + Claude state to a fresh VPS.
#
# Run this ON THE SOURCE MACHINE (the laptop/PC whose OpenClaw daemon you've
# been using). It pushes to the VPS over SSH; the VPS must already be
# bootstrapped (scripts/bootstrap-vps.sh) and reachable.
#
# What it does:
#   1. Validates local sources (~/.openclaw, ~/.claude, ~/.claude.json).
#   2. Stops the VPS gateway so we don't migrate into a running process.
#   3. Backs up the VPS's current /srv/openclaw/config to a timestamped dir.
#   4. Rsyncs Claude CLI session creds (.credentials.json + settings.json
#      from ~/.claude/, plus the sibling ~/.claude.json which holds the
#      oauthAccount field — OpenClaw needs this to treat the session as a
#      Pro subscription rather than a raw API key).
#   5. Rsyncs ~/.openclaw/ to /srv/openclaw/config, excluding host-specific
#      files (openclaw.json, .env, credentials/, telegram/ spool, logs/,
#      and the agent's auth-profiles.json + auth-state.json which are
#      VPS-local).
#   6. Rsyncs ~/.openclaw/workspace/ to /srv/openclaw/workspace, excluding
#      the workspace .git so each host keeps its own history.
#   7. Clears the gateway's auth-state.json so any prior billing/circuit-
#      breaker state doesn't carry over.
#   8. Prints the post-migrate steps (start gateway, models auth login).
#
# Usage:
#   scripts/migrate-laptop.sh [--vps lifekit@HOST] [--dry-run]

set -euo pipefail

VPS="${VPS:-lifekit@lifekit-vps}"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
CLAUDE_JSON="${CLAUDE_JSON:-$HOME/.claude.json}"
DRY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vps) VPS="$2"; shift 2 ;;
    --dry-run) DRY="--dry-run"; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1;34m→ %s\033[0m\n' "$*"; }

# ─── Sanity ──────────────────────────────────────────────────────────────────

[[ -d "$OPENCLAW_HOME" ]] || { echo "Missing $OPENCLAW_HOME"  >&2; exit 1; }
[[ -d "$CLAUDE_HOME"   ]] || { echo "Missing $CLAUDE_HOME"    >&2; exit 1; }
[[ -f "$CLAUDE_JSON"   ]] || { echo "Missing $CLAUDE_JSON"    >&2; exit 1; }
ssh -o BatchMode=yes "$VPS" 'true' \
  || { echo "Cannot SSH to $VPS (need passwordless key auth)" >&2; exit 1; }

TS="$(date +%Y%m%d-%H%M%S)"

# ─── Stop gateway ────────────────────────────────────────────────────────────

say "stopping gateway on $VPS"
ssh "$VPS" 'cd /srv/lifekit-stack/compose && \
  docker compose --env-file /srv/openclaw/config/.env stop openclaw-gateway'

# ─── Backup ──────────────────────────────────────────────────────────────────

say "backing up /srv/openclaw/config -> config.bak-$TS"
ssh "$VPS" "cp -a /srv/openclaw/config /srv/openclaw/config.bak-$TS"

# ─── Claude CLI session ──────────────────────────────────────────────────────
# Minimum payload that makes OpenClaw treat this as a Pro subscription.
# Skips conversation history, plugins/, etc. per no-private-data-in-git.

say "migrating Claude CLI session"
ssh "$VPS" 'mkdir -p ~/.claude && chmod 700 ~/.claude'
rsync -avz $DRY -e ssh \
  "$CLAUDE_HOME/.credentials.json" \
  "$CLAUDE_HOME/settings.json" \
  "$VPS:.claude/"
rsync -avz $DRY -e ssh "$CLAUDE_JSON" "$VPS:.claude.json"

# ─── OpenClaw config (excluding host-specific) ───────────────────────────────

say "migrating OpenClaw state (identity, memory, sessions, flows, tasks, ...)"
rsync -avz $DRY --delete-after -e ssh \
  --exclude='workspace/' \
  --exclude='openclaw.json' \
  --exclude='openclaw.json.bak*' \
  --exclude='openclaw.json.last-good' \
  --exclude='.env' \
  --exclude='credentials/' \
  --exclude='telegram/' \
  --exclude='logs/' \
  --exclude='update-check.json' \
  --exclude='tui/' \
  --exclude='agents/main/agent/auth-profiles.json' \
  --exclude='agents/main/agent/auth-state.json' \
  "$OPENCLAW_HOME/" "$VPS:/srv/openclaw/config/"

# ─── OpenClaw workspace (skills + SOUL/IDENTITY/USER/etc.) ───────────────────

say "migrating OpenClaw workspace (skills + soul files)"
rsync -avz $DRY --delete-after -e ssh \
  --exclude='.git/' \
  "$OPENCLAW_HOME/workspace/" "$VPS:/srv/openclaw/workspace/"

# ─── Reset auth circuit-breaker ──────────────────────────────────────────────

say "clearing auth-state circuit-breaker"
ssh "$VPS" 'echo "{\"version\":1,\"usageStats\":{}}" \
  > /srv/openclaw/config/agents/main/agent/auth-state.json'

# ─── Done ────────────────────────────────────────────────────────────────────

say "✓ migration complete"
cat <<EOF

Next steps on the VPS:

  ssh $VPS
  cd /srv/lifekit-stack/compose
  docker compose --env-file /srv/openclaw/config/.env up -d openclaw-gateway

  # If this is the first migrate (auth-profiles.json doesn't exist), wire
  # the Anthropic provider to use Claude CLI auth (one-time, needs a TTY):
  docker compose --env-file /srv/openclaw/config/.env exec openclaw-gateway sh -c \\
    'export PATH=/home/node/.npm-global/bin:\$PATH; \\
     openclaw models auth login --provider anthropic --method cli --set-default'

Then test by DM'ing your bot.

Backup of pre-migrate VPS state: /srv/openclaw/config.bak-$TS
EOF

#!/usr/bin/env bash
#
# cutover-v2.sh — atomic migration to the v2 stack (single Kit agent +
# DevClaw v2 MCP). Runs on the VPS.
#
# Replaces:
#   - the current default agent (markdown-skill Kit) → single "kit" agent
#     bootstrapped via `openclaw agents add`, with our AGENTS.md + SOUL.md
#   - lifekit-orchestrator container (v1 LangGraph daemon) → devclaw-mcp
#     container (v2 TypeScript MCP server fronting OpenHands)
#   - implicit skill discovery → explicit `openclaw skills install` for
#     workout-claw + life-state + nutrition-claw
#
# v1 is STOPPED, not removed — rollback restarts it. openclaw.json is
# backed up before edits.
#
# Usage:
#   scripts/cutover-v2.sh                 # full atomic cutover
#   scripts/cutover-v2.sh --phase build   # just build the image
#   scripts/cutover-v2.sh --phase skills  # just install Kit + skills (no traffic switch)
#   scripts/cutover-v2.sh --phase switch  # just flip traffic + stop v1
#   scripts/cutover-v2.sh --dry-run       # print steps without running
#
# Idempotent where possible. Safe to re-run on partial failures.

set -euo pipefail

# ── config ───────────────────────────────────────────────────────────────────
REPO_DIR="${REPO_DIR:-/srv/lifekit-stack}"
COMPOSE_FILE="${COMPOSE_FILE:-$REPO_DIR/compose/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-/srv/openclaw/config/.env}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-/srv/openclaw/config/openclaw.json}"
AGENT_HOME="${AGENT_HOME:-/srv/openclaw/agents/kit}"
KIT_DEFAULTS_DIR="${KIT_DEFAULTS_DIR:-$REPO_DIR/defaults/agents/kit/workspace}"
V1_CONTAINER="${V1_CONTAINER:-compose-lifekit-orchestrator-1}"
SKILLS=("workout-claw" "life-state" "nutrition-claw")

DRY_RUN=0
PHASE="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase) PHASE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

# ── helpers ──────────────────────────────────────────────────────────────────
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
say() { printf "\n\033[1;34m[$(ts)] %s\033[0m\n" "$*"; }
run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "    DRY: $*"
  else
    echo "    + $*"
    "$@"
  fi
}

# Run openclaw CLI via the openclaw-cli sidecar container. The CLI talks to
# the long-running gateway over the shared netns so commands like
# `agents add` and `skills install` mutate the gateway's live config.
oclaw() {
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "    DRY: openclaw $*"
    return 0
  fi
  echo "    + openclaw $*"
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    run --rm --no-deps openclaw-cli "$@"
}

# ── pre-flight ───────────────────────────────────────────────────────────────
preflight() {
  say "Pre-flight checks"

  [[ -f "$COMPOSE_FILE" ]] || { echo "compose file not found: $COMPOSE_FILE" >&2; exit 1; }
  [[ -f "$ENV_FILE" ]] || { echo "env file not found: $ENV_FILE" >&2; exit 1; }
  [[ -f "$OPENCLAW_CONFIG" ]] || { echo "openclaw.json not found: $OPENCLAW_CONFIG" >&2; exit 1; }
  [[ -d "$KIT_DEFAULTS_DIR" ]] || { echo "Kit defaults missing: $KIT_DEFAULTS_DIR — git pull first?" >&2; exit 1; }

  for cmd in docker jq; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "missing required cmd: $cmd" >&2; exit 1; }
  done

  echo "    repo dir : $REPO_DIR"
  echo "    config   : $OPENCLAW_CONFIG"
  echo "    agent home: $AGENT_HOME"
  echo "    v1 container: $V1_CONTAINER"
  echo "    phase    : $PHASE"
  echo "    dry-run  : $DRY_RUN"
}

# ── phase: backup ────────────────────────────────────────────────────────────
backup_config() {
  local backup="${OPENCLAW_CONFIG}.bak-cutover-$(date +%s)"
  say "Backing up openclaw.json → $backup"
  run cp -a "$OPENCLAW_CONFIG" "$backup"
}

# ── phase: build ─────────────────────────────────────────────────────────────
build_image() {
  say "Building devclaw-mcp image"
  run docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    build --pull devclaw-mcp
}

# ── phase: kit + skills ──────────────────────────────────────────────────────
bootstrap_kit_agent() {
  say "Bootstrapping Kit agent via openclaw CLI"

  # `agents add` is idempotent on the name — re-running just no-ops or
  # updates the workspace/agentDir/model fields.
  oclaw agents add --non-interactive \
    --workspace "/home/node/.openclaw/agents/kit/workspace" \
    --agent-dir "/home/node/.openclaw/agents/kit/agent" \
    --model anthropic/claude-sonnet-4-6 \
    kit

  say "Installing Kit's AGENTS.md + SOUL.md from repo"
  if [[ $DRY_RUN -eq 0 ]]; then
    install -D -m 644 "$KIT_DEFAULTS_DIR/AGENTS.md" "$AGENT_HOME/workspace/AGENTS.md"
    install -D -m 644 "$KIT_DEFAULTS_DIR/SOUL.md"   "$AGENT_HOME/workspace/SOUL.md"
    echo "    + installed AGENTS.md ($(wc -l <"$AGENT_HOME/workspace/AGENTS.md") lines)"
    echo "    + installed SOUL.md   ($(wc -l <"$AGENT_HOME/workspace/SOUL.md") lines)"
  else
    echo "    DRY: cp AGENTS.md + SOUL.md → $AGENT_HOME/workspace/"
  fi
}

install_skills() {
  say "Installing workspace skills"
  for slug in "${SKILLS[@]}"; do
    oclaw skills install --agent kit "$slug"
  done
}

# ── phase: switch ────────────────────────────────────────────────────────────
edit_openclaw_json() {
  say "Editing openclaw.json — register MCP servers + mark kit default + bind telegram"
  if [[ $DRY_RUN -eq 1 ]]; then
    cat <<'EOF'
    DRY: jq mutations:
      - .mcp.servers.devclaw = {transport, url: http://devclaw-mcp:8000/mcp}
      - .mcp.servers."google-workspace" = {transport, url: http://google-workspace-mcp:8000/mcp/}
      - .agents.list |= map(if .id == "kit" then . + {default: true} else (. - {default}) end)
      - .bindings = [{agentId: "kit", match: {channel: "telegram", accountId: "*"}}]
EOF
    return 0
  fi

  local tmp; tmp="$(mktemp)"
  jq '
    .mcp.servers.devclaw = {
      transport: "streamable-http",
      url: "http://devclaw-mcp:8000/mcp"
    }
    | .mcp.servers["google-workspace"] = {
        transport: "streamable-http",
        url: "http://google-workspace-mcp:8000/mcp/"
      }
    | .agents.list |= map(
        if .id == "kit" then . + {default: true}
        else (. | del(.default))
        end)
    | .bindings = [
        { agentId: "kit", match: { channel: "telegram", accountId: "*" } }
      ]
  ' "$OPENCLAW_CONFIG" > "$tmp"

  # validate before swapping in
  if ! jq -e . "$tmp" >/dev/null; then
    echo "edited config failed jq validation — aborting" >&2
    rm -f "$tmp"
    exit 1
  fi
  mv "$tmp" "$OPENCLAW_CONFIG"
  echo "    + openclaw.json updated"
}

start_devclaw_mcp() {
  say "Starting devclaw-mcp container"
  run docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    up -d --force-recreate devclaw-mcp
}

stop_v1() {
  say "Stopping v1 lifekit-orchestrator (keeping container for rollback)"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "    DRY: docker stop $V1_CONTAINER"
    return 0
  fi
  if docker ps -q --filter "name=^${V1_CONTAINER}$" | grep -q .; then
    run docker stop "$V1_CONTAINER"
  else
    echo "    v1 container not running — nothing to stop"
  fi
}

restart_gateway() {
  say "Restarting openclaw-gateway so it picks up the new config"
  run docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    up -d --force-recreate openclaw-gateway
}

verify() {
  say "Post-cutover sanity checks"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "    DRY: skipping live checks"
    return 0
  fi

  # Wait briefly for gateway to be reachable.
  for i in 1 2 3 4 5; do
    if docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
        exec -T devclaw-mcp curl -fs http://127.0.0.1:8000/health >/dev/null 2>&1; then
      echo "    + devclaw-mcp /health: OK"
      break
    fi
    echo "    devclaw-mcp not ready, retry $i/5…"
    sleep 5
  done

  echo "    agents list (via CLI):"
  oclaw agents list 2>&1 | grep -E "^- " | sed 's/^/      /'

  echo
  echo "    Run the integration test by sending Kit a message on Telegram:"
  echo "    > Use devclaw to write /tmp/kit-postdeploy.txt with 'v2 live'"
  echo "    Then verify /tmp/kit-postdeploy.txt exists on the host."
}

# ── orchestration ────────────────────────────────────────────────────────────
preflight

case "$PHASE" in
  all)
    backup_config
    build_image
    bootstrap_kit_agent
    install_skills
    edit_openclaw_json
    start_devclaw_mcp
    stop_v1
    restart_gateway
    verify
    ;;
  build)
    build_image
    ;;
  skills)
    bootstrap_kit_agent
    install_skills
    ;;
  switch)
    backup_config
    edit_openclaw_json
    start_devclaw_mcp
    stop_v1
    restart_gateway
    verify
    ;;
  *)
    echo "unknown phase: $PHASE (expected: all|build|skills|switch)" >&2
    exit 2
    ;;
esac

say "Done."

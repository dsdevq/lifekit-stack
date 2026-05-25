# DevClaw v2 — MCP server container.
#
# Build context: this directory (compose/devclaw-mcp/).
# The container clones dsdevq/devclaw at build time (public repo) and builds
# the v2/ TypeScript MCP server. Python OpenHands runner is set up in a
# venv during build so the runtime stays cold-start fast.
#
# Auth: inherits Claude Code OAuth from the gateway's bind-mounted
# ~/.claude session — same model as openclaw-gateway. No API key, ever.

FROM node:22-bookworm

# ---- system deps -----------------------------------------------------------
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# ---- claude CLI + ACP bridge (npm-installed globally) ---------------------
# claude-code: the actual CLI binary whose OAuth session we inherit
# claude-agent-acp: the ACP adapter the Python runner spawns
RUN npm install -g \
      @anthropic-ai/claude-code \
      @agentclientprotocol/claude-agent-acp

# ---- DevClaw v2 source -----------------------------------------------------
ARG DEVCLAW_REPO=https://github.com/dsdevq/devclaw.git
ARG DEVCLAW_REF=feat/v2-mcp-server-first-slice

RUN git clone --depth 1 --branch "${DEVCLAW_REF}" "${DEVCLAW_REPO}" /app
WORKDIR /app/v2

# Use npm install instead of `npm ci` — the repo's package-lock.json may be
# slightly behind for transient deps; install is more forgiving.
RUN npm install --no-audit --no-fund \
 && npm run build

# Python venv with openhands-sdk
RUN python3 -m venv python-runner/.venv \
 && python-runner/.venv/bin/pip install --upgrade pip --quiet \
 && python-runner/.venv/bin/pip install --quiet -r python-runner/requirements.txt

# ---- runtime config --------------------------------------------------------
# State directory — mount a named volume here in compose so the SQLite db
# survives container recreates.
RUN mkdir -p /var/lib/devclaw

ENV DEVCLAW_TRANSPORT=http \
    DEVCLAW_PORT=8000 \
    DEVCLAW_DB=/var/lib/devclaw/devclaw.db \
    CLAUDE_CODE_EXECUTABLE=/usr/local/bin/claude \
    CLAUDE_CONFIG_DIR=/home/node/.claude \
    OPENHANDS_SUPPRESS_BANNER=1

EXPOSE 8000

# Drop to a non-root user for the long-running process. The image's `node`
# user owns /app already (npm install ran as root, but node owns /app
# because that's the base image convention).
USER node

CMD ["node", "dist/mcp-server.js"]

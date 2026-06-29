# ops-agent — resident watcher for devclaw incidents.
#
# ops-PR2 adds the cognition + action layer on top of PR1's L0 baseline:
#   - Subprocess wrapper around `claude --print` for playbook decisions.
#   - HTTP MCP client targeting devclaw-mcp:8000/mcp for L1 evaluate_goal.
# See ops-agent/README.md for the full scope + boundary rules.
#
# Build context: this directory (ops-agent/).
FROM node:22-trixie

# Run as the `node` non-root user that ships with the base image (UID 1000)
# — same pattern as compose/devclaw-mcp. ops-agent has no business root in
# its own container; it reads goals RO, writes incidents to a bind-mounted
# dir, and calls devclaw-mcp over the compose-internal network.

# ---- system deps ----------------------------------------------------------
# python3 + venv: the daemon itself.
# (Node.js comes from the base image for the `claude` CLI npm install below.)
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# ---- claude CLI (npm-installed globally) ----------------------------------
# Mirrors compose/devclaw-mcp/Dockerfile so the OAuth-session path is identical:
# the ops-agent inherits ~/.claude from the host (compose mount) and the CLI
# resolved here picks it up. No API key, ever — see cognition._scrubbed_env.
# hadolint ignore=DL3016
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

# ---- python deps ----------------------------------------------------------
# Install pinned-ish runtime deps before the source so source edits don't
# bust the wheel cache.
COPY pyproject.toml ./
# --break-system-packages: Debian trixie PEP 668. No venv needed here.
# hadolint ignore=DL3013
RUN pip install --break-system-packages --no-cache-dir \
      "pyyaml>=6.0" \
      "httpx>=0.27"

# ---- source ---------------------------------------------------------------
COPY ops_agent ./ops_agent
RUN pip install --break-system-packages --no-cache-dir --no-deps .

USER node

# Defaults match the compose service block (compose/docker-compose.yml).
# Override per host via env.
ENV OPS_AGENT_GOALS_DIR=/data/goals \
    OPS_AGENT_INCIDENTS_DIR=/data/incidents \
    OPS_AGENT_POLL_INTERVAL_S=60 \
    OPS_AGENT_DEDUP_WINDOW_S=86400 \
    OPS_AGENT_CLAUDE_BIN=claude \
    OPS_AGENT_CLAUDE_MODEL=sonnet \
    OPS_AGENT_CLAUDE_TIMEOUT_S=60 \
    OPS_AGENT_DEVCLAW_MCP_URL=http://devclaw-mcp:8000/mcp \
    OPS_AGENT_DEVCLAW_MCP_TIMEOUT_S=30 \
    CLAUDE_CONFIG_DIR=/home/node/.claude

# No HEALTHCHECK at this PR — the daemon has no HTTP surface and a stuck
# polling loop wouldn't be caught by a process-liveness probe anyway. The
# compose `restart: on-failure:5` policy is the safety net.

CMD ["ops-agent"]

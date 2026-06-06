# DevClaw — MCP server container (Python host).
#
# Build context: this directory (compose/devclaw-mcp/).
# Clones dsdevq/devclaw at build time (public repo) and pip-installs the Python
# package. As of devclaw's 2026-05-31 rewrite (#49) the MCP host is all-Python
# (FastMCP) — there is no v2/ TypeScript build anymore. The host's job:
#   - serve the MCP tools (streamable-http),
#   - run host-side cognition (planner / grill / judge) via `claude --print`,
#   - spawn one devclaw-sandbox container per task via the host docker socket,
#   - deliver a verified change as a branch + PR (git push + `gh pr create`).
# The OpenHands agent AND the verify gate run INSIDE the sandbox, not here, so
# this image carries NO openhands-sdk and NO language toolchains (e.g. .NET).
#
# Auth: inherits Claude Code OAuth from the bind-mounted ~/.claude session (no
# API key, ever); inherits git push + gh auth from the bind-mounted ~/.config/gh
# + ~/.gitconfig (for PR delivery).

# node:22-trixie (Debian 13, Python 3.13) — node for the claude CLI + ACP bridge.
FROM node:22-trixie

# ---- system deps -----------------------------------------------------------
# docker-ce-cli: the MCP server spawns per-task devclaw-sandbox containers via
#   the host docker socket bind-mounted by compose (daemon stays on the host).
# gh: the GitHub CLI — devclaw's delivery step runs `gh pr create`.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      git curl ca-certificates gnupg \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
 && chmod a+r /etc/apt/keyrings/docker.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian trixie stable" > /etc/apt/sources.list.d/docker.list \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] \
https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce-cli gh \
 && rm -rf /var/lib/apt/lists/*

# ---- claude CLI + ACP bridge (npm-installed globally) ---------------------
# claude-code: the CLI binary whose OAuth session we inherit (host cognition).
# claude-agent-acp: the ACP adapter (sandbox spawns it; kept here to match).
RUN npm install -g \
      @anthropic-ai/claude-code \
      @agentclientprotocol/claude-agent-acp

# ---- DevClaw source (Python) ----------------------------------------------
ARG DEVCLAW_REPO=https://github.com/dsdevq/devclaw.git
ARG DEVCLAW_REF=main
RUN git clone --depth 1 --branch "${DEVCLAW_REF}" "${DEVCLAW_REPO}" /app
WORKDIR /app
# Install the host package (fastmcp + httpx + the `devclaw-mcp` console script).
# Debian 13's Python is externally-managed; --break-system-packages installs the
# small dep set system-wide, no venv needed for the lean host.
RUN pip install --break-system-packages --no-cache-dir .

# State directory — a compose named volume mounts here so the SQLite db survives
# recreates.
RUN mkdir -p /var/lib/devclaw

ENV DEVCLAW_TRANSPORT=http \
    DEVCLAW_PORT=8000 \
    DEVCLAW_DB=/var/lib/devclaw/devclaw.db \
    CLAUDE_CODE_EXECUTABLE=/usr/local/bin/claude \
    CLAUDE_CONFIG_DIR=/home/node/.claude \
    OPENHANDS_SUPPRESS_BANNER=1

EXPOSE 8000

# Drop to the base image's non-root `node` user for the long-running process.
USER node

CMD ["devclaw-mcp"]

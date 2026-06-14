# DevClaw MCP server container.
#
# Build context: this directory (compose/devclaw-mcp/).
# The container clones dsdevq/devclaw at build time (public repo) and installs
# the Python FastMCP server via pip. Node.js is retained from the base image
# for the Claude CLI and claude-agent-acp.
#
# Auth: inherits Claude Code OAuth from the gateway's bind-mounted
# ~/.claude session — same model as openclaw-gateway. No API key, ever.

# node:22-trixie (Debian 13) ships Python 3.13 by default.
# openhands-sdk 1.23+ requires Python >= 3.12.
FROM node:22-trixie

# ---- system deps -----------------------------------------------------------
# docker-ce-cli: MCP server spawns per-task devclaw-sandbox containers via
#   the host docker socket bind-mounted by compose.
# gh: GitHub CLI for devclaw agent tool access, authenticated as dsdevq.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      git curl ca-certificates gnupg \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
 && chmod a+r /etc/apt/keyrings/docker.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian trixie stable" \
      > /etc/apt/sources.list.d/docker.list \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] \
https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce-cli gh \
 && rm -rf /var/lib/apt/lists/*

# ---- claude CLI + ACP bridge (npm-installed globally) ---------------------
# claude-code: the actual CLI binary whose OAuth session we inherit.
# claude-agent-acp: the ACP adapter the Python runner spawns.
RUN npm install -g \
      @anthropic-ai/claude-code \
      @agentclientprotocol/claude-agent-acp

# ---- DevClaw Python source -------------------------------------------------
ARG DEVCLAW_REPO=https://github.com/dsdevq/devclaw.git
ARG DEVCLAW_REF=main

RUN git clone --depth 1 --branch "${DEVCLAW_REF}" "${DEVCLAW_REPO}" /app

WORKDIR /app

# Install the devclaw Python package (FastMCP server + deps).
# --break-system-packages: required on Debian trixie (PEP 668 externally-managed
# Python); no venv needed here — pip installs into the system site-packages.
RUN pip install --break-system-packages --no-cache-dir .

# ---- runtime config --------------------------------------------------------
RUN mkdir -p /var/lib/devclaw

ENV DEVCLAW_TRANSPORT=http \
    DEVCLAW_PORT=8000 \
    DEVCLAW_DB=/var/lib/devclaw/devclaw.db \
    CLAUDE_CODE_EXECUTABLE=/usr/local/bin/claude \
    CLAUDE_CONFIG_DIR=/home/node/.claude \
    OPENHANDS_SUPPRESS_BANNER=1

EXPOSE 8000

USER node

CMD ["devclaw-mcp"]

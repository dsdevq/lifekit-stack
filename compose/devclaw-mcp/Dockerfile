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

# ---- chromium for done-gate E2E -------------------------------------------
# Done-gate's E2E phase (per E2E test layer proposal) spawns the goal target
# in-process and drives it via Playwright. devclaw-mcp must therefore have
# Chromium + its system libs available. Same lib list as openclaw-gateway and
# devclaw-sandbox so we don't drift.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libxcomposite1 libxdamage1 libatspi2.0-0 libdrm2 libxkbcommon0 \
        libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
 && rm -rf /var/lib/apt/lists/*

# ---- tailscale CLI ---------------------------------------------------------
# devclaw's deploy layer (deploy.py) shells out to `tailscale serve` to expose
# durable deploys on the tailnet. ONLY the CLI is used here — tailscaled runs on
# the HOST and is reached over its control socket, bind-mounted by compose. The
# package pulls tailscaled too, but it is never started in this container.
RUN curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg \
      -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
 && curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list \
      -o /etc/apt/sources.list.d/tailscale.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends tailscale \
 && rm -rf /var/lib/apt/lists/*

# ---- claude CLI + ACP bridge (npm-installed globally) ---------------------
# claude-code: the actual CLI binary whose OAuth session we inherit.
# claude-agent-acp: the ACP adapter the Python runner spawns.
RUN npm install -g \
      @anthropic-ai/claude-code \
      @agentclientprotocol/claude-agent-acp \
      @playwright/mcp@latest

# ---- DevClaw Python source -------------------------------------------------
ARG DEVCLAW_REPO=https://github.com/dsdevq/devclaw.git
ARG DEVCLAW_REF=main

RUN git clone --depth 1 --branch "${DEVCLAW_REF}" "${DEVCLAW_REPO}" /app

WORKDIR /app

# ---- console bundle (built here, not committed to the devclaw repo) ---------
# The three-screen web console is a Vite+React SPA served by the Python server
# from devclaw/server/console/dist. Node is already present (this is a node:22
# base for the Claude CLI), so we build the bundle from source AT IMAGE BUILD
# rather than vendoring the minified output into git. Must run BEFORE the pip
# install below so hatchling bakes the freshly-built dist into the wheel
# (pyproject `tool.hatch.build.targets.wheel.artifacts` force-includes it even
# though dist is .gitignored in the source repo).
RUN npm --prefix devclaw/server/console ci \
 && npm --prefix devclaw/server/console run build

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
    OPENHANDS_SUPPRESS_BANNER=1 \
    DEVCLAW_SKILL_LIBRARY=/app/skill-library

EXPOSE 8000

USER node

# Pre-fetch the Playwright Chromium build into the runtime user's cache so
# the done-gate E2E phase doesn't pay a ~150MB download on first invocation.
# Lands in /home/node/.cache/ms-playwright.
RUN npx -y playwright@latest install chromium

CMD ["devclaw-mcp"]

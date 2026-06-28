# devclaw-sandbox image — per-task ephemeral container the devclaw-mcp
# service spawns via the host docker socket for each OpenHands task.
#
# Built once at deploy time, never run as a long-lived service. The
# devclaw-mcp container references it by image tag (devclaw-sandbox:local).
#
# Source of truth is devclaw's own .sandcastle/Dockerfile — we re-build
# it here so the image lands on the VPS without an external registry push.
# The repo is cloned at build time at the same ref as devclaw-mcp so the
# openhands-runner code matches the runner module the MCP server invokes.

ARG DEVCLAW_REF=main

FROM alpine/git AS source
ARG DEVCLAW_REF
ARG DEVCLAW_REPO=https://github.com/dsdevq/devclaw.git
RUN git clone --depth 1 --branch "${DEVCLAW_REF}" "${DEVCLAW_REPO}" /src

FROM python:3.13-slim
ARG AGENT_UID=1000
ARG AGENT_GID=1000
ARG CLAUDE_CLI_VERSION=2.1.139
ARG CLAUDE_AGENT_ACP_VERSION=0.37.0

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid ${AGENT_GID} agent \
 && useradd  --uid ${AGENT_UID} --gid ${AGENT_GID} --create-home --shell /bin/bash agent

# Headless-Chromium system libraries required by the Playwright MCP server the
# agent calls when it needs to drive a real browser (UI verification, E2E).
# Same list openclaw-gateway and devclaw-mcp use — without these the bundled
# Chromium fails to launch on debian-slim with cryptic "shared libraries" errors.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
      libxcomposite1 libxdamage1 libatspi2.0-0 libdrm2 libxkbcommon0 \
      libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=source /src/openhands-runner/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# @playwright/mcp is the MCP server the agent reaches Playwright through
# (runner.py drops a project-scope .mcp.json into /workspace at task start).
RUN npm install -g \
      @anthropic-ai/claude-code@${CLAUDE_CLI_VERSION} \
      @agentclientprotocol/claude-agent-acp@${CLAUDE_AGENT_ACP_VERSION} \
      @playwright/mcp@latest \
 && npm cache clean --force

COPY --from=source --chown=agent:agent /src/openhands-runner/runner.py /opt/devclaw/runner.py
# Sandbox-only MCP config baked from the cloned repo; runner.py copies this to
# /workspace/.mcp.json so claude auto-discovers Playwright MCP WITHOUT mounting
# the host's mcpServers (which the sandcastle allowlist deliberately excludes).
COPY --from=source --chown=agent:agent /src/openhands-runner/sandbox-mcp.json /opt/devclaw/sandbox-mcp.json

USER agent

# Pre-fetch Chromium so the Playwright MCP doesn't pull ~150MB on first call.
RUN npx -y playwright@latest install chromium

WORKDIR /workspace

ENV CLAUDE_CODE_EXECUTABLE=/usr/bin/claude \
    CLAUDE_CONFIG_DIR=/home/agent/.claude \
    HOME=/home/agent \
    PATH=/usr/local/bin:/usr/bin:/bin

ENTRYPOINT ["python3", "/opt/devclaw/runner.py"]

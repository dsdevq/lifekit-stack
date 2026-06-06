# goalclaw container — the goal-layer planner.
#
# `goalclaw serve`: heartbeat + a /wake event endpoint. Advances each durable
# goal under the vault's goals/ dir; before each code action it prepares a
# pristine workspace checkout (git), then dispatches to devclaw-mcp over MCP.
# Cognition is the Claude Agent SDK on the bind-mounted Pro OAuth session (no API
# key). git + gh are for workspace clone/fetch (private repos use gh auth).

FROM node:22-bookworm-slim AS base

# python + git (workspace prep) + gh (private-repo clone/fetch auth).
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        git curl ca-certificates gnupg \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] \
https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

# Claude CLI — the Agent SDK uses its bundled OAuth session at ~/.claude.
RUN npm install -g @anthropic-ai/claude-code@latest

# Install goalclaw from its public repo (code lives in its own repo, not vendored
# here — same model as devclaw-mcp).
ARG GOALCLAW_REPO=https://github.com/dsdevq/goalclaw.git
ARG GOALCLAW_REF=main
RUN pip3 install --break-system-packages "goalclaw @ git+${GOALCLAW_REPO}@${GOALCLAW_REF}"

# Run as non-root. Repurpose the base image's `node` user (UID 1000) as `lifekit`
# so the UID matches the host user owning the bind-mounted /srv/life + creds.
RUN groupmod --new-name lifekit node \
    && usermod --login lifekit --move-home --home /home/lifekit node \
    && passwd --delete lifekit 2>/dev/null || true
USER lifekit

ENV GOALCLAW_GOALS_DIR=/srv/life/goals \
    GOALCLAW_TICK_INTERVAL_SECONDS=900 \
    GOALCLAW_PLANNER_MODEL=sonnet \
    GOALCLAW_WAKE_PORT=8500 \
    PYTHONUNBUFFERED=1

# PID 1 / signal handling supplied by the compose-level `init: true` flag.
CMD ["goalclaw", "serve"]

# goalclaw container — the goal-layer planner.
#
# Resident heartbeat (`goalclaw loop`): ticks every
# GOALCLAW_TICK_INTERVAL_SECONDS, advances each durable goal under the vault's
# goals/ dir, dispatches code actions to devclaw-mcp over HTTP MCP. Mirrors the
# lifekit-curator container shape: node base for the bundled Claude CLI (the
# plan step is `claude --print` over the bind-mounted Pro OAuth session), python
# for goalclaw itself. No API key, ever.

FROM node:22-bookworm-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Claude CLI — installed globally via npm (lands at /usr/local/bin/claude).
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
    PYTHONUNBUFFERED=1

# PID 1 / signal handling supplied by the compose-level `init: true` flag.
CMD ["goalclaw", "loop"]

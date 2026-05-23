# lifekit-curator container
# Installs lifekit from source and runs `lifekit curator daemon`.

FROM node:22-bookworm-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Claude CLI — installed globally via npm
RUN npm install -g @anthropic-ai/claude-code@latest

# Install lifekit (curator lives in the framework, not vendored here).
RUN pip3 install --break-system-packages "lifekit @ git+https://github.com/dsdevq/lifekit.git"

# Curator runs as non-root. The node:22-bookworm-slim base image ships a `node`
# user at UID 1000 already — repurpose it as `lifekit` so the UID matches the
# host user that owns the bind-mounted /srv/life/ on the VPS.
RUN groupmod --new-name lifekit node \
    && usermod --login lifekit --move-home --home /home/lifekit node \
    && passwd --delete lifekit 2>/dev/null || true
USER lifekit

ENV LIFEKIT_ROOT=/srv/life \
    LIFEKIT_WAKE_INTERVAL_SECONDS=900 \
    LIFEKIT_LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

# PID 1 / signal handling is supplied by the compose-level `init: true` flag.
CMD ["lifekit", "curator", "daemon"]

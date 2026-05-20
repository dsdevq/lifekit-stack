# lifekit-curator container
# Drains queue.jsonl → calls Claude CLI → updates ~/.life/domains/

FROM node:22-bookworm-slim AS base

# System deps + Python (Claude CLI is npm-distributed and needs Node 18+).
# tini is intentionally omitted: the compose service declares `init: true`,
# which makes Docker inject docker-init as PID 1. Running our own tini on top
# of that produced the "tini is not running as PID 1" warning on every boot.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Claude CLI — installed globally via npm
RUN npm install -g @anthropic-ai/claude-code@latest

WORKDIR /app
COPY curator.py /app/curator.py

# Curator runs as non-root. The node:22-bookworm-slim base image ships a `node`
# user at UID 1000 already — repurpose it as `lifekit` so the UID matches the
# host user that owns the bind-mounted /srv/life/ on the VPS.
RUN groupmod --new-name lifekit node \
    && usermod --login lifekit --move-home --home /home/lifekit node \
    && passwd --delete lifekit 2>/dev/null || true
USER lifekit

ENV LIFEKIT_LIFE_DIR=/srv/life \
    LIFEKIT_QUEUE_FILE=/srv/life/queue.jsonl \
    LIFEKIT_WAKE_INTERVAL_SECONDS=900 \
    LIFEKIT_LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

# PID 1 / signal handling is supplied by the compose-level `init: true` flag,
# which runs docker-init as PID 1 in front of our process — no need to wrap
# again here.
CMD ["python3", "/app/curator.py"]

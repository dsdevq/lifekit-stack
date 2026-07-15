#!/usr/bin/env bash
# Memory rotation — mechanism half. logrotate (.jsonl) + rotate-extras.py
# (markdown archival + trajectory gzip/prune + footprint report + heartbeat). NO LLM.
#
# Data root  -> /srv/memory (the vault; --root operates ON it, never stores here).
# Runtime state -> /var/lib/lifekit/rotation (per runtime-knowledge-split).
# Rotation CODE (this wrapper + rotate-extras.py + logrotate-memory.conf) lives in
# the lifekit-stack repo at scripts/rotate/ — NOT the vault. Evicted 2026-07-15
# (vault proposal 2026-07-15-retire-or-slim-system-and-tech-config-split): the
# vault is data; code lives in the deploy repo, pulled to /srv/lifekit-stack.
# Override paths via env for other layouts.
set -euo pipefail
ROOT="${MEMORY_ROOT:-/srv/memory}"
STATE="${ROTATE_STATE:-/var/lib/lifekit/rotation}"
AGENTS="${OPENCLAW_AGENTS:-/srv/openclaw/config/agents}"
ROTATE_DIR="${ROTATE_DIR:-/srv/lifekit-stack/scripts/rotate}"
/usr/sbin/logrotate --state "$STATE/logrotate.state" "$ROTATE_DIR/logrotate-memory.conf"
exec /usr/bin/python3 "$ROTATE_DIR/rotate-extras.py" --root "$ROOT" --state-dir "$STATE" --agents-dir "$AGENTS"

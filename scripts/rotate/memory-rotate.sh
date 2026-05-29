#!/usr/bin/env bash
# Memory rotation — mechanism half. logrotate (.jsonl) + rotate-extras.py
# (markdown archival + trajectory gzip/prune + footprint report + heartbeat). NO LLM.
# Runtime state -> /var/lib/lifekit/rotation (per runtime-knowledge-split).
set -euo pipefail
ROOT=/srv/life
STATE=/var/lib/lifekit/rotation
AGENTS=/srv/openclaw/config/agents
/usr/sbin/logrotate --state "$STATE/logrotate.state" "$ROOT/system/logrotate-memory.conf"
exec /usr/bin/python3 "$ROOT/system/rotate-extras.py" --root "$ROOT" --state-dir "$STATE" --agents-dir "$AGENTS"

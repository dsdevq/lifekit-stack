# ops-agent — resident watcher for devclaw incidents.
#
# ops-PR1 baseline: detect O1 (no-progress watchdog) and persist incidents
# to ~/memory/projects/ops-agent/incidents/. NO Claude call, NO MCP call.
# See ops-agent/README.md for the full scope + boundary rules.
#
# Build context: this directory (ops-agent/).
FROM python:3.12-slim-trixie

# Run as a non-root user — the ops-agent has no business root in its own
# container; it reads goals RO and writes incidents to a bind-mounted dir.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin ops

WORKDIR /app

# Layer: deps first so source edits don't bust the wheel cache. PyYAML is
# the only third-party dep at this PR; pin loosely (>=6.0) — see pyproject.
COPY pyproject.toml ./
RUN pip install --no-cache-dir pyyaml>=6.0

# Layer: source. Installed editable so a future bind-mount of the source
# at /app gives a fast inner loop on the VPS during early calibration —
# the package is small enough that even non-editable would be fine.
COPY ops_agent ./ops_agent
RUN pip install --no-cache-dir --no-deps .

USER ops

# Defaults match the compose service block (compose/docker-compose.yml).
# Override per host via env.
ENV OPS_AGENT_GOALS_DIR=/data/goals \
    OPS_AGENT_INCIDENTS_DIR=/data/incidents \
    OPS_AGENT_POLL_INTERVAL_S=60 \
    OPS_AGENT_DEDUP_WINDOW_S=86400

# No HEALTHCHECK at this PR — the daemon has no HTTP surface and a stuck
# polling loop wouldn't be caught by a process-liveness probe anyway. The
# compose `restart: on-failure:5` policy is the safety net.

CMD ["ops-agent"]

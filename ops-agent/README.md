# ops-agent

Resident watcher process that detects when a devclaw goal is stuck, asks
Claude what to do via a small playbook, and (for L1 actions) calls
devclaw's MCP `evaluate_goal` tool. **Separate process by design** —
the closeloop incident (`~/memory/projects/devclaw/plan.md`, "Operations
agent" section) showed that an in-process self-heal handler can share the
same defect that broke devclaw. A sibling process can act when devclaw
itself is broken.

## What this PR ships (ops-PR2)

Builds on ops-PR1's L0 baseline. Adds the cognition + action layer:

- **Detector**: O1 (no-progress watchdog) only — same as PR1.
- **Cognition**: subprocess wrapper around `claude --print`
  (`ops_agent.cognition`). Strips `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`
  so the Pro OAuth session is never bypassed, bounded by an env-driven
  timeout, returns a typed `CognitionError` on any failure.
- **MCP client**: `DevclawMCPClient` (`ops_agent.mcp_client`) — JSON-RPC 2.0
  over streamable-http against devclaw's `/mcp` endpoint. **Exposes exactly
  one method: `evaluate_goal`.** That's the structural boundary contract:
  the surface this PR is authorized to call.
- **Playbook**: `stuck_goal_evaluate` — given an O1 incident's context,
  asks Claude to pick `evaluate_goal` or `noop`. Parser is defensive: any
  malformed response falls back to `noop`.
- **L1 action**: `actions.perform_evaluate_goal` — calls the MCP client,
  returns a structured `ActionOutcome` (never raises out).
- **Wired flow** in `main.tick`: O1 fires → L0 incident folder written →
  prompt persisted → Claude decides → action runs (or noop) →
  `decision.json` + `action.json` + a richer `outcome.md` land in the
  incident folder; the log gets a post-decision line.
- **Defensive cognition**: every Claude / MCP failure is caught + recorded
  in the incident folder. The daemon NEVER crashes on a downstream outage.
- **Idempotency**: incident folders with an existing `decision.json` are
  not re-decided — protects against a daemon restart mid-incident.
- **Tests**: 88 total (69 new + 19 existing). Hermetic: no real Claude
  subprocess, no real MCP network call.

### Explicitly OUT of scope here (deferred to ops-PR3+)

| PR | Scope |
|---|---|
| **ops-PR2 (this)** | O1 + cognition + L1 `evaluate_goal` action |
| ops-PR3 | O2 (repeated verdict shape) + L2 `steer_goal` action + `phantom-verdict-correct` playbook |
| ops-PR4 | O3 (stuck-in-phase) + SSH/docker action wrappers + `deploy-stale-restart` |
| ops-PR5 | L3 `devclaw-bug-fix-ticket` playbook + devclaw-defect classifier |

The MCP client's structural narrowness (one method) is the proof — adding a
write surface (`steer_goal`, `cancel_goal`, etc.) requires deliberately
growing this class. That ranks any future PR doing so as an authority
escalation, not a refactor.

## Boundary rules (load-bearing)

- **Reads only** from devclaw's substrates (goal `STATUS.md`; eventually
  `state_store.db` traces). Compose mounts these as `:ro` and the code
  imports nothing from devclaw itself.
- **Writes** to:
  1. `<incidents_dir>` — its own observability log.
  2. devclaw via MCP — exactly ONE tool: `evaluate_goal`. (Adding more
     tools = an authority escalation requiring deliberate PR review.)
- **Cognition failures default to `noop`.** A Claude outage or malformed
  response must NEVER trigger an unintended MCP write call.

## Local dev

```sh
cd ops-agent
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -v
```

To run against a real goals directory (cognition + MCP optional):

```sh
OPS_AGENT_GOALS_DIR=~/memory/goals \
OPS_AGENT_INCIDENTS_DIR=~/memory/projects/ops-agent/incidents \
OPS_AGENT_POLL_INTERVAL_S=30 \
OPS_AGENT_DEVCLAW_MCP_URL=http://localhost:8000/mcp \
.venv/bin/ops-agent
```

## Config (env vars)

| Var | Default | Purpose |
|---|---|---|
| `OPS_AGENT_GOALS_DIR` | `~/memory/goals` | Where devclaw writes goal folders. RO from the agent. |
| `OPS_AGENT_INCIDENTS_DIR` | `~/memory/projects/ops-agent/incidents` | Where incidents + dedup markers + `log.md` land. RW. |
| `OPS_AGENT_POLL_INTERVAL_S` | `60` | Seconds between scan passes. |
| `OPS_AGENT_DEDUP_WINDOW_S` | `86400` | Suppression window for repeat detections. |
| `OPS_AGENT_CLAUDE_BIN` | `claude` | Path to the `claude` CLI binary. |
| `OPS_AGENT_CLAUDE_MODEL` | `sonnet` | Model tier for playbook cognition. Empty = CLI default. |
| `OPS_AGENT_CLAUDE_TIMEOUT_S` | `60` | Per-call timeout for `claude --print`. |
| `OPS_AGENT_DEVCLAW_MCP_URL` | `http://devclaw-mcp:8000/mcp` | devclaw MCP endpoint. |
| `OPS_AGENT_DEVCLAW_MCP_TOKEN` | _(unset)_ | Bearer token if devclaw's `DEVCLAW_TOKEN` auth is on. |
| `OPS_AGENT_DEVCLAW_MCP_TIMEOUT_S` | `30` | HTTP timeout for MCP calls. |

## On-disk layout (per incident)

```
<incidents_dir>/
├── log.md                            one line per incident + per decision, append-only
├── .seen/<goal_id>-<fingerprint>.touch    dedup marker; mtime is the only signal
└── <ts>-O1-<goal_id>/
    ├── trigger.json                  full detection payload
    ├── prompt.md                     prompt sent to Claude (ops-PR2)
    ├── decision.json                 parsed action + reasoning + raw response (ops-PR2)
    ├── action.json                   MCP outcome (ops-PR2, only when not noop)
    └── outcome.md                    human-readable summary
```

## Deploy

### Compose (primary path)

The `ops-agent` service in `compose/docker-compose.yml` is wired to:

- mount the shared `devclaw-state` volume RO at `/data/devclaw-state`
  (goals are read from there);
- bind-mount `~/.claude` RO so the Pro OAuth session is reused;
- target `http://devclaw-mcp:8000/mcp` on the compose-internal network.

Start alongside the rest of the stack:

```sh
docker compose -f compose/docker-compose.yml up -d ops-agent
```

### Systemd (bare-metal, calibration use)

```sh
sudo cp ops-agent/systemd/ops-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ops-agent
journalctl -u ops-agent -f
```

The unit assumes a `lifekit` system user (matches the rest of
lifekit-stack's account split, see top-level README). Edit `User=`/
`Group=`/the `Environment=` lines to suit a different host layout.

## Open follow-up

ops-PR2 calls `evaluate_goal` over MCP. As of writing this is a
`GoalService` method on devclaw but it is NOT yet decorated with
`@mcp.tool` in `devclaw/server/tools.py`. A tiny follow-up PR on the
devclaw side has to expose it. The client is built and tested
end-to-end with a stub; the moment devclaw ships the decorator, the L1
loop is live.

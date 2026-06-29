# ops-agent

Resident watcher process that detects when a devclaw goal is stuck and
records an incident the owner can act on. **Separate process by design** —
the closeloop incident (`~/memory/projects/devclaw/plan.md`, "Operations
agent" section) showed that an in-process self-heal handler can share the
same defect that broke devclaw. A sibling process can act when devclaw
itself is broken.

## What this PR ships (ops-PR1 baseline)

The minimum-viable vertical slice that closes the design loop end-to-end:

- **O1 detector only** — polls `~/memory/goals/*/STATUS.md` frontmatter,
  fires when `no_progress_notified=true`.
- **L0 action only** — writes the incident folder
  (`<incidents_dir>/<ts>-O1-<goal_id>/{trigger.json,outcome.md}`) and
  appends a one-liner to `<incidents_dir>/log.md`. No Claude call. No MCP
  call. No auto-poke.
- **Dedup window** — same incident shape inside 24h (default) writes once.
  Marker file at `<incidents_dir>/.seen/<goal_id>-<fingerprint>.touch`.
- **Docker + systemd deploy** — container for the compose stack, unit file
  for bare-metal calibration runs.
- **Tests** — 19 pytest cases covering detector logic, the incident
  store, the daemon tick, and the env-driven config.

### Explicitly OUT of scope here (deferred to ops-PR2+)

The full design lives in `~/memory/projects/devclaw/plan.md` under
**Operations agent — close the "stuck → owner-fixes" loop**. The PR
breakdown there is:

| PR | Scope |
|---|---|
| **ops-PR1 (this)** | Skeleton + O1 + L0 — detection only, no action |
| ops-PR2 | O2 (repeated verdict shape) + `phantom-verdict-correct` playbook + L2 steer-injection |
| ops-PR3 | O3 (stuck-in-phase) + SSH/docker action wrappers + `deploy-stale-restart` |
| ops-PR4 | L3 `devclaw-bug-fix-ticket` playbook + devclaw-defect classifier |

The cognition layer (Claude binding, MCP client to devclaw, playbook
prompts, devclaw notifier webhook subscription) **is intentionally not
present yet**. ops-PR1 is the foundation those layers will build on —
ship a clean L0 baseline first so each later PR has one variable.

## Boundary rules (load-bearing)

- **Reads only** from devclaw's substrates (goal STATUS.md, eventually
  `state_store.db` traces). Compose mounts those as `:ro` and the code
  imports nothing from devclaw itself.
- **Writes only** to `<incidents_dir>` — its own observability log. Does
  not touch user repos, does not mutate devclaw state, does not call
  any MCP tool.
- **Boundary enforcement is structural AND code-level.** Read-only mount
  in compose; no `GoalStore` import in `ops_agent/`; no MCP client.

## Local dev

```sh
cd ops-agent
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -v
```

To run against a real goals directory:

```sh
OPS_AGENT_GOALS_DIR=~/memory/goals \
OPS_AGENT_INCIDENTS_DIR=~/memory/projects/ops-agent/incidents \
OPS_AGENT_POLL_INTERVAL_S=30 \
.venv/bin/ops-agent
```

## Config (env vars)

| Var | Default | Purpose |
|---|---|---|
| `OPS_AGENT_GOALS_DIR` | `~/memory/goals` | Where devclaw writes goal folders. RO from the agent's perspective. |
| `OPS_AGENT_INCIDENTS_DIR` | `~/memory/projects/ops-agent/incidents` | Where we persist incidents + the `.seen/` dedup markers + `log.md`. RW. |
| `OPS_AGENT_POLL_INTERVAL_S` | `60` | Seconds between scan passes. |
| `OPS_AGENT_DEDUP_WINDOW_S` | `86400` | Suppression window for repeat detections with the same fingerprint. |

## On-disk layout

```
<incidents_dir>/
├── log.md                            one line per incident, append-only
├── .seen/<goal_id>-<fingerprint>.touch    dedup marker; mtime is the only signal
└── <ts>-O1-<goal_id>/
    ├── trigger.json                  full detection payload
    └── outcome.md                    L0 baseline note (ops-PR1 only)
```

Future PRs add `prompt.md` + `decision.json` to the per-incident folder
once Claude lands in the loop.

## Deploy

### Compose (primary path)

Added to `compose/docker-compose.yml` as the `ops-agent` service. Mounts:

- `${LIFEKIT_DEVCLAW_GOALS:-/srv/devclaw/goals}` → `/data/goals:ro`
- `${LIFEKIT_OPS_AGENT_DIR:-/srv/memory/projects/ops-agent}` → `/data/incidents:rw`

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

# OpenClaw Agent Design

How lifekit-stack uses OpenClaw — the philosophy, the primitives, and why the architecture is designed the way it is.

This document was rewritten 2026-05-25 after local testing exposed a load-bearing gap in our previous design. See [What we tried first and abandoned](#what-we-tried-first-and-abandoned) below for the history; the rest of the document describes the shape we settled on.

---

## Philosophy: single Kit + external DevClaw

lifekit-stack runs **one OpenClaw agent — Kit** — which handles everything synchronously, and delegates **only autonomous coding work** to a separate runtime, **DevClaw**, reached via MCP.

- **Single deployable unit** — one OpenClaw gateway, one `openclaw.json`, one Docker Compose stack.
- **One agent, many skills** — modularity inside Kit comes from **workspace skills** installed via `openclaw skills install`. The skill manifest tells Kit when + how to invoke each domain capability.
- **External MCP only when justified** — DevClaw (async coding runtime) and google-workspace-mcp (third-party MCP server) are the only out-of-process boundaries. Everything else is a skill or a direct CLI call from Kit.

This is not microservices, and it is not multi-agent. Logical separation lives in skill files and the orchestrator's standing instructions (`AGENTS.md`); runtime isolation lives only in DevClaw, where async lifecycle genuinely requires it.

---

## OpenClaw primitives and how they map here

### The Kit agent

Kit is a single OpenClaw agent declared once in `agents.list[]`:

```json5
{
  agents: {
    defaults: { agentRuntime: { id: "claude-cli" } },
    list: [
      {
        id: "kit",
        default: true,
        name: "Kit",
        workspace: "/home/node/.openclaw/agents/kit/workspace",
        agentDir:  "/home/node/.openclaw/agents/kit/agent",
        model: "anthropic/claude-sonnet-4-6"
      }
    ]
  }
}
```

Kit uses the `claude-cli` agent runtime — Claude Code subprocess driven by Denys's Pro subscription OAuth. No API keys (see [[pro-subscription-is-the-design]] in memory).

The concrete config template lives at `defaults/openclaw.single-agent.json5`.

### Kit's workspace

Bootstrapped with `openclaw agents add` and then customized:

```
~/.openclaw/agents/kit/workspace/
├── AGENTS.md       operating instructions — domain handling, hard rules
├── SOUL.md         persona (Kit, coral familiar)
├── IDENTITY.md     name/vibe/avatar (created by `agents add`)
├── USER.md         Denys profile slice
├── MEMORY.md       curated long-term memory (main session only — see security note inside)
├── memory/
│   └── YYYY-MM-DD.md   daily working notes
└── skills/
    ├── workout-claw/SKILL.md
    ├── life-state/SKILL.md
    └── ...
```

Bootstrap files (`AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md`, etc.) are automatically injected into Kit's system prompt by OpenClaw on session start. See [system-prompt docs](https://docs.openclaw.ai/concepts/system-prompt).

### Skills — installed via CLI, not vendored

Skills are domain-specific instructions plus (usually) a backing CLI. Installed via `openclaw skills install <slug>` which drops the `SKILL.md` into `<workspace>/skills/<name>/`. Discovery is automatic on gateway start.

```bash
openclaw skills install workout-claw
openclaw skills install life-state
# restart gateway to pick up the new skills
```

Don't hand-copy `SKILL.md` files; the install command is the canonical path. The CLI binaries (`workout-claw`, `life-state`, etc.) install separately via npm and must be on the gateway's PATH.

Reference: [https://docs.openclaw.ai/tools/skills](https://docs.openclaw.ai/tools/skills)

### MCP servers — only for genuine async boundaries

Registered under `mcp.servers` in `openclaw.json`:

```json5
{
  mcp: {
    servers: [
      { id: "google-workspace", transport: "streamable-http", url: "http://google-workspace-mcp:8000/mcp/" },
      { id: "devclaw",          transport: "streamable-http", url: "http://devclaw-mcp:8000/mcp/" }
    ]
  }
}
```

When and why MCP — see [Boundary rules](#boundary-rules-when-skill-when-mcp) below.

### Memory — workspace files + lifekit domains

Two memory layers coexist:

| Layer | Location | Owner | Purpose |
|---|---|---|---|
| OpenClaw workspace memory | `<workspace>/MEMORY.md` + `<workspace>/memory/YYYY-MM-DD.md` | Kit | Long-term curated facts + daily working notes |
| lifekit domain files | `~/.life/domains/<domain>.md` | lifekit-curator | Curated knowledge extracted from conversations |

Kit reads from both. The curator writes to `~/.life/domains/` independently of OpenClaw's session lifecycle.

### Bindings — single entry point

All inbound channel traffic routes to Kit:

```json5
bindings: [
  { agentId: "kit", match: { channel: "telegram", accountId: "*" } }
]
```

Add additional channels (Discord, WhatsApp, etc.) by adding more bindings, all pointing to Kit.

---

## AGENTS.md — Kit's operating instructions

`AGENTS.md` is loaded into every Kit session. It is the contract for how Kit operates across domains.

### What belongs in Kit's AGENTS.md

- Identity (brief — full persona in `SOUL.md`)
- For each domain: which skill or MCP server is the canonical surface, and any cross-skill rules (e.g. "check life-state before suggesting workout intensity")
- What Kit never does (write to canonical user stores, fabricate paths, etc.)
- Communication style

### What does NOT belong

- Persona detail → `SOUL.md`
- User profile → `USER.md`
- Tool usage instructions for specific skills → those live in each skill's `SKILL.md`
- Implementation details about MCP server endpoints → those live in `openclaw.json`

The template lives at `defaults/agents/kit/workspace/AGENTS.md`.

---

## Boundary rules: when skill, when MCP

Three kinds of boundaries exist in this stack. Picking the wrong one is the most common architectural mistake.

### In-process synchronous → Kit handles directly

Default. If Kit can do it within one chat turn using its built-in Claude Code tools (Bash, Read, Write, Edit, Grep, Glob), it does. No skill needed for one-off tasks; ad-hoc requests get ad-hoc handling.

### Domain capability with a backing CLI → workspace skill

When a domain has a real backing CLI (workout-claw, life-state, future finance tools), install it as a workspace skill. The `SKILL.md` tells Kit when + how to call it. The CLI owns its data (`~/.workout-claw/`, `~/.life/state/`, …); Kit invokes it via Bash and reports the result.

This is also the canonical path for ongoing domain conventions that should be enforced across sessions (e.g. always pass `--muscle` when logging a workout, always tag the morning check-in with `--note "morning"`).

### Out-of-process async → MCP

MCP is reserved for boundaries that can't be crossed in-process. Two qualifying conditions:

1. **Async lifecycle** — work outlives any single Kit session (multi-hour autonomous run, durable state across container restarts, callbacks fire minutes-to-hours after the originating message).
2. **Foreign runtime** — a third-party service that already speaks MCP (e.g. `google-workspace-mcp`). Reimplementing it as a workspace skill would be re-inventing what already exists.

If neither holds, **don't reach for MCP**. A workspace skill + Bash call is simpler, cheaper, and easier to reason about.

### Decision heuristic

| Question | If yes | If no |
|---|---|---|
| Does it need to run for longer than one chat turn? | MCP candidate | Workspace skill or direct handling |
| Does it already exist as a standalone MCP server? | MCP | Skill or direct |
| Could this be a `subprocess.run(...)` inside Kit? | Skill or direct | MCP candidate |

### Examples in this stack

| Module | Boundary | Why |
|---|---|---|
| workout-claw, life-state | Workspace skill | Synchronous CLI invocations. Kit calls via Bash, returns within one turn |
| Future finance CLI | Workspace skill | Same — synchronous queries over local state |
| Ad-hoc questions, lookups, conversation | Direct (no skill) | One-off; no recurring contract to encode |
| **devclaw** | **MCP** | Autonomous OpenHands runs are async, multi-hour, callback-driven. Can't fit inside one Kit session |
| google-workspace | MCP | Pre-existing third-party MCP server. Not re-implementing it |

### What this rules out

- Wrapping a local CLI in an HTTP MCP server just to "be consistent" — workspace skills *are* the consistency.
- Treating every domain as its own OpenClaw agent. Single Kit + skills is the shape.
- Splitting Kit into multiple OpenClaw agents for "isolation". Workspace isolation works at the agent layer but per-agent tool restrictions and `agentToAgent` don't enforce under `claude-cli` runtime (see history below).

---

## DevClaw — the autonomous coding boundary

The only out-of-process runtime in the stack. DevClaw is an autonomous software development runtime that exposes its capabilities as an MCP server. Kit calls it like any other MCP tool:

```
implement_feature(project_id, goal, notify_url)
fix_bug(project_id, description, notify_url)
get_status(task_id)
list_tasks(project_id?)
```

Internally DevClaw orchestrates [OpenHands](https://github.com/All-Hands-AI/OpenHands) — an autonomous coding agent that runs in an isolated Docker sandbox, writes code, runs tests, and opens PRs. Kit never knows OpenHands exists.

### How they connect

```
You (Telegram)
  │
  ▼
OpenClaw Kit
  └── MCP call → DevClaw
                    ├── planner (Goal → Tasks)
                    ├── state store
                    ├── poller
                    └── OpenHands (Docker sandbox + agent loop)
```

### Callback flow

Kit passes a `notify_url` when it kicks off a task. DevClaw calls it when done or blocked. Kit forwards the notification to the originating channel.

```
Kit calls:           implement_feature(goal, notify_url="openclaw.internal/notify/xyz")
DevClaw executes:    OpenHands runs autonomously
DevClaw calls back:  POST notify_url → {status: "done", pr_url: "..."}
Kit delivers:        → Telegram message to you
```

Neither system is coupled to the other's internals. DevClaw is a black box from Kit's perspective.

### Why DevClaw is a separate service, not a skill

OpenClaw skills are synchronous — Kit calls the CLI, gets a result, returns within the chat turn. DevClaw's execution model is different: a goal can run for hours, survive container restarts, and report back asynchronously. That lifecycle doesn't fit inside a Kit session.

See [DevClaw architecture v2](https://github.com/dsdevq/devclaw/blob/main/docs/architecture-v2.md) for the full design.

---

## What we tried first and abandoned

The first v2 design was a **multi-agent modular monolith**: per-domain OpenClaw agents (orchestrator + workspace + health + dev), per-agent workspace + tool restrictions, dispatched via `agentToAgent`. The architecture doc described it; we built it and tested locally 2026-05-25.

**It didn't work** under the `claude-cli` agent runtime — which we have to use because Pro subscription OAuth is the only auth model we accept ([[pro-subscription-is-the-design]] in memory):

- `tools.allow` / `tools.deny` per-agent isn't enforced — the runtime spawns Claude Code with the full default SDK toolbox (Bash/Read/Write/Edit/Glob/Grep/ToolSearch)
- `agentToAgent` isn't exposed as a callable tool to claude-cli — configured + enabled + allow-listed, never appeared in the agent's tool set
- `AGENTS.md` content does reach the system prompt, but the model can choose to ignore routing instructions when it has all the default tools and can just do the work itself
- Concrete failure: the orchestrator agent twice wrote fabricated workout logs into Denys's real `~/memory/` store instead of delegating to the health agent + workout-claw

What's left of multi-agent in the codebase: nothing — the `defaults/agents/{orchestrator,workspace,health,dev}/` dirs and `defaults/openclaw.multi-agent.json5` template were deleted along with this rewrite.

The boundary rule we wrote into this doc earlier — agentToAgent for in-process modularity, MCP for async — still holds in spirit. The change is that the in-process modularity primitive is **workspace skills**, not agentToAgent.

---

## Further reading

- [OpenClaw skills](https://docs.openclaw.ai/tools/skills) — workspace skill discovery + install
- [OpenClaw system prompt](https://docs.openclaw.ai/concepts/system-prompt) — bootstrap file injection
- [OpenClaw agent runtime](https://docs.openclaw.ai/concepts/agent) — workspace contract + session bootstrap
- [DevClaw architecture v2](https://github.com/dsdevq/devclaw/blob/main/docs/architecture-v2.md) — DevClaw + OpenHands design
- Memory: `architecture-openclaw-modular-monolith`, `openhands-execution-engine`, `feedback-boundary-rule-mcp-vs-a2a`

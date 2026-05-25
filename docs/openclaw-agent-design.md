# OpenClaw Agent Design

How lifekit-stack uses OpenClaw — the philosophy, the primitives, and why the architecture is designed the way it is.

---

## Philosophy: modular monolith

lifekit-stack is a **modular monolith**:

- **Single deployable unit** — one OpenClaw gateway, one `openclaw.json`, one Docker Compose stack
- **Internally modular** — each domain (health, finance, dev, ...) is a self-contained module: its own agent, skills, hooks, plugins, and isolated memory
- **Clear boundaries** — modules don't share memory or state, but they all live in the same runtime
- **One orchestrator** — a top-level agent receives every user message, decides which domain agents to invoke, and merges their responses

This is not microservices. There is no inter-service networking, no message queues, no separate gateways per domain. The isolation is logical, not physical.

---

## OpenClaw primitives and how they map here

### Agents — the unit of domain isolation

An agent in OpenClaw is "a fully scoped brain with its own workspace, state directory, session store, auth profiles, and model registry."

**This is the primary isolation primitive.** Each domain in lifekit-stack is a separate OpenClaw agent. Agents are declared as a list under `agents.list` in `openclaw.json`:

```json5
{
  agents: {
    defaults: { agentRuntime: { id: "claude-cli" } },
    list: [
      { id: "orchestrator", default: true, name: "Kit",
        workspace: "/home/node/.openclaw/agents/orchestrator/workspace",
        agentDir:  "/home/node/.openclaw/agents/orchestrator/agent",
        model: "anthropic/claude-sonnet-4-6" },
      { id: "health",    name: "Health",    workspace: "...", agentDir: "...", model: "anthropic/claude-sonnet-4-6" },
      { id: "finance",   name: "Finance",   workspace: "...", agentDir: "...", model: "anthropic/claude-sonnet-4-6" },
      { id: "dev",       name: "Dev",       workspace: "...", agentDir: "...", model: "anthropic/claude-sonnet-4-6" },
      { id: "workspace", name: "Workspace", workspace: "...", agentDir: "...", model: "anthropic/claude-haiku-4-5" }
    ]
  }
}
```

`workspace` is the agent's working directory. `agentDir` is a separate location for agent identity / auth-profiles. Sessions always live at `~/.openclaw/agents/<id>/sessions/` regardless of either path. See the OpenClaw multi-agent docs for the full schema; this stack's concrete template is at `defaults/openclaw.multi-agent.json5`.

Each domain agent's workspace can contain:

```
<workspace>/
├── AGENTS.md       # domain-specific operating instructions (required)
├── SOUL.md         # persona for this domain (optional)
├── USER.md         # relevant user profile for this domain (optional)
├── MEMORY.md       # long-term domain facts
├── memory/
│   └── YYYY-MM-DD.md   # daily working notes
└── skills/         # domain-specific skill overrides
```

Memory is **fully isolated** — health agent never sees finance memory, and vice versa.

Reference: [https://docs.openclaw.ai/concepts/agents](https://docs.openclaw.ai/concepts/agents)

### Skills — instructional, not architectural

Skills are `SKILL.md` files that teach an agent how to use its tools in a specific context. They are loaded on-demand, keeping context lean.

Each domain module can have its own skills. A skill is domain-specific knowledge: how to log a workout, how to analyze spending, how to file a dev task.

Skills are **not** the routing layer — that belongs in the orchestrator's `AGENTS.md`.

Reference: [https://docs.openclaw.ai/concepts/skills](https://docs.openclaw.ai/concepts/skills)

### Memory — per-domain, per-agent

Two memory layers exist side by side:

| Layer | Location | Owner | Purpose |
|---|---|---|---|
| OpenClaw workspace memory | `~/.openclaw/agents/<id>/workspace/MEMORY.md` | Each agent | Long-term facts, preferences, decisions for that domain |
| lifekit domain files | `~/.life/domains/<domain>.md` | lifekit-curator | Curated knowledge extracted from conversations |

Domain agents read from both. The curator writes to `~/.life/domains/` independently of OpenClaw's session lifecycle.

Reference: [https://docs.openclaw.ai/concepts/memory](https://docs.openclaw.ai/concepts/memory)

### agentToAgent — the routing primitive

OpenClaw's `agentToAgent` tool allows one agent to dispatch a task to another agent and receive its response. This is how the orchestrator invokes domain agents:

```
User message
  → orchestrator agent
    → reads AGENTS.md (routing instructions)
    → decides: health + finance relevant
    → agentToAgent(agent: "health", task: "...")
    → agentToAgent(agent: "finance", task: "...")
    → merges both responses
  → single reply to user
```

`agentToAgent` must be explicitly enabled and allowlisted in `openclaw.json`. The allowlist lists agents that may **send** messages (not which may be received):

```json5
tools: {
  agentToAgent: {
    enabled: true,
    allow: ["orchestrator"]    // only the orchestrator dispatches; domain agents return responses only
  }
}
```

Reference: [https://docs.openclaw.ai/concepts/multi-agent](https://docs.openclaw.ai/concepts/multi-agent)

### Bindings — single entry point

Bindings route inbound channel messages to agents. All user messages enter through bindings that point to the orchestrator. The match shape is `{ channel, accountId, peer }` with most-specific-wins precedence:

```json5
bindings: [
  // Every Telegram message lands at the orchestrator.
  { agentId: "orchestrator", match: { channel: "telegram", accountId: "*" } }
]
```

Domain agents are never bound to channels directly — they only receive tasks from the orchestrator via `agentToAgent`.

### Hooks and plugins

OpenClaw provides hooks for intercepting the agent lifecycle (`before_prompt_build`, `before_tool_call`, `subagent_spawning`, etc.). These are the extension points for cross-cutting concerns: logging, cost tracking, rate limiting, routing overrides.

Plugins extend the gateway with new channels, model providers, tools, or skills. Domain-specific plugins live close to their domain module.

Reference: [https://docs.openclaw.ai/concepts/plugins](https://docs.openclaw.ai/concepts/plugins)

---

## Routing: how the orchestrator decides

The orchestrator's routing logic lives in `AGENTS.md` — not in code. Claude's tool selection handles the dispatch:

1. `AGENTS.md` lists each domain agent with a short description of what it covers
2. The orchestrator reads the user message, matches it against domain descriptions
3. Calls `agentToAgent` for each relevant domain (sequential within one turn)
4. Synthesizes all responses into one reply

For cross-domain queries ("what should I eat to stay within my budget this week"), both health and finance agents are called. The orchestrator is responsible for merging their answers coherently.

This keeps routing **declarative and auditable** — change `AGENTS.md`, change routing behavior, no code deploy.

---

## AGENTS.md — the orchestrator's standing instructions

`AGENTS.md` is loaded into every orchestrator session. It is the contract for how the agent operates.

### What belongs here

- Who this agent is and the scope of its authority
- The list of domain agents and what each covers (the routing table)
- How to handle cross-domain queries (call all relevant, merge)
- What the orchestrator never does itself (domain work belongs to domain agents)
- Communication style

### What does NOT belong here

- Domain knowledge → lives in domain agent's `AGENTS.md` and `~/.life/domains/`
- User preferences → `USER.md`
- Persona detail → `SOUL.md`
- Tool usage instructions → `TOOLS.md` or skills

### Example orchestrator AGENTS.md

```markdown
# Kit

You are Kit — Denys's personal AI assistant. You are the single entry point for
everything. You route work to specialist domain agents and synthesize their results.
You do not do the specialist work yourself.

---

## Domain agents

Invoke the right agent(s) using the agentToAgent tool. Match the user's intent
against these descriptions:

- **health** — fitness, workouts, nutrition, sleep, mood, energy, body metrics
- **finance** — spending, budgets, investments, subscriptions, tax, financial goals
- **dev** — code, PRs, bugs, technical research, dev task management
- **workspace** — email, calendar, documents, tasks, scheduling

---

## Routing rules

1. Identify all domains relevant to the user's message.
2. Call each relevant domain agent via agentToAgent. Pass the user's message and
   any context from ~/.life/domains/ that the domain agent would need.
3. For cross-domain queries, call all relevant agents and merge their responses
   into one coherent reply.
4. If no domain covers the intent, handle it directly.

---

## What you never do

- Never do domain-specific work yourself — always delegate to the domain agent.
- Never dump raw agent output at the user — always synthesize.
- Never act on sensitive operations (sending email, making purchases, filing tasks)
  without confirming intent first.
- Never ask more than one clarifying question at a time.

---

## Communication

- Confirm routing in one line before calling: "Checking this with health and finance."
- After agents respond, synthesize into plain language. Be concise.
- Match Denys's register: direct, no filler.
- If a domain agent fails or is blocked, explain clearly and suggest next steps.

---

## Memory

Save to MEMORY.md only what is durable and not already in ~/.life/domains/. Use
today's daily note for working context within this session or week.
```

---

## Adding a new domain module

1. Add the agent config to `openclaw.json` under `agents.<id>`
2. Create the workspace at `~/.openclaw/agents/<id>/workspace/` with `AGENTS.md`, `SOUL.md`, `USER.md`
3. Add any domain-specific skills to the workspace's `skills/` directory
4. Add the agent to the orchestrator's `AGENTS.md` routing table
5. Add it to `agentToAgent.allow` in `openclaw.json`

No code changes. Routing picks it up from `AGENTS.md` on next session.

---

## Boundary rules: when agentToAgent, when MCP

Two kinds of boundaries exist in this stack. Picking the wrong one is the most common architectural mistake — wrapping HTTP around something that should just be a function call, or trying to model an async multi-hour task as a single agent session.

### In-process modular → agentToAgent

The default. Domain agents (health, finance, workspace, ...) live inside the same OpenClaw runtime. They're isolated by workspace, not by network. The orchestrator dispatches to them via `agentToAgent`, they return within one session, the orchestrator synthesizes the reply.

Domain-specific CLIs (workout-claw, life-state, ...) are **tools the agent calls directly** — they live in the agent's workspace `skills/` and are invoked as bash. No HTTP shim, no MCP wrapper, no separate container.

### Out-of-process async → MCP

MCP is reserved for boundaries that can't be crossed in-process. The qualifying conditions:

1. **Async lifecycle** — the work outlives any single agent session (multi-hour autonomous run, durable state across container restarts, callbacks fire minutes-to-hours after the originating message).
2. **Foreign runtime** — a third-party service that already speaks MCP (e.g. google-workspace-mcp). Reimplementing it as an OpenClaw agent would be re-inventing what already exists.

If neither condition holds, **don't reach for MCP**. An in-process agent with workspace skills is simpler, cheaper, and easier to reason about.

### Decision heuristic

| Question | If yes | If no |
|---|---|---|
| Does it need to run for longer than one chat turn? | MCP candidate | agentToAgent |
| Does it already exist as a standalone MCP server? | MCP | agentToAgent |
| Could this be a `subprocess.run(...)` inside an agent? | agentToAgent | MCP candidate |

### Examples in this stack

| Module | Boundary | Why |
|---|---|---|
| health agent | agentToAgent | Synchronous: log a workout, return a PR, all in one turn |
| finance agent | agentToAgent | Same — synchronous queries over local state |
| workspace agent | agentToAgent | The agent itself is in-process; it happens to call the google-workspace MCP server as a tool |
| **devclaw** | **MCP** | Autonomous OpenHands runs are async, multi-hour, callback-driven. Can't fit inside one session |
| google-workspace | MCP | Pre-existing third-party MCP server. Not re-implementing it |

A subtle but important pattern: an in-process OpenClaw agent can itself be an **MCP client**. The workspace agent isn't an MCP server — it's a regular OpenClaw agent that happens to call google-workspace-mcp as a tool. MCP-client and MCP-server roles are separate concerns.

### What this rules out

- Wrapping a local CLI in an HTTP MCP server just to "be consistent" — the modular monolith *is* the consistency. Process boundaries are exceptional, not default.
- Treating every domain as its own service. Domains are agents, not microservices.
- Splitting the runtime to feel cleaner. Logical isolation via workspaces is enough.

---

## DevClaw — the dev domain module

The dev domain is special: it's not just an OpenClaw agent, it's a fully separate service.

**DevClaw** (`dsdevq/devclaw`) is an autonomous software development runtime. From OpenClaw's perspective it looks like any other MCP server — the orchestrator calls it via tools:

```
implement_feature(project_id, goal, notify_url)
fix_bug(project_id, description, notify_url)
get_status(task_id)
list_tasks(project_id?)
```

Internally DevClaw orchestrates [OpenHands](https://github.com/All-Hands-AI/OpenHands) — an autonomous coding agent that runs in an isolated Docker sandbox, writes code, runs tests, and opens PRs. OpenClaw never knows OpenHands exists.

### How they connect

```
You (Telegram)
  │
  ▼
OpenClaw orchestrator
  └── agentToAgent → dev agent
                       └── MCP call → DevClaw
                                        ├── planner (Goal → Tasks)
                                        ├── state store
                                        ├── poller
                                        └── REST → OpenHands (Docker)
                                                      └── sandbox + agent loop
```

### Callback flow

OpenClaw passes a `notify_url` when it kicks off a task. DevClaw calls it when done or blocked. OpenClaw forwards to Telegram.

```
Dev agent calls:    implement_feature(goal, notify_url="openclaw.internal/notify/xyz")
DevClaw executes:   OpenHands runs autonomously
DevClaw calls back: POST notify_url → {status: "done", pr_url: "..."}
OpenClaw delivers:  → Telegram message to you
```

Neither system is coupled to the other's internals. DevClaw is a black box from OpenClaw's perspective.

### Why DevClaw is a separate service, not an OpenClaw agent

OpenClaw agents are conversational and session-based — a session starts with a message, ends with a response. DevClaw's execution model is different: a goal can run for hours across multiple OpenHands sessions, survive container restarts, and report back asynchronously. That lifecycle doesn't fit inside an OpenClaw agent session.

See [DevClaw architecture](https://github.com/dsdevq/devclaw/blob/main/docs/architecture-v2.md) for the full design.

---

## Further reading

- [OpenClaw concepts](https://docs.openclaw.ai/) — agents, skills, memory, tools, bindings, cron
- [OpenClaw multi-agent](https://docs.openclaw.ai/concepts/multi-agent) — agentToAgent, bindings, workspace isolation
- [DevClaw architecture](https://github.com/dsdevq/devclaw/blob/main/docs/architecture-v2.md) — how DevClaw + OpenHands work as the dev execution engine
- [architecture.md](./architecture.md) — the stack-level design

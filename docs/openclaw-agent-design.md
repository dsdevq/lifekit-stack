# OpenClaw Agent Design

How lifekit-stack uses OpenClaw — concepts, primitives, and why the architecture is designed the way it is.

---

## The pattern: modular monolith

lifekit-stack is a **modular monolith**:

- **Single deployable unit** — one OpenClaw gateway, one `openclaw.json`, one Docker Compose stack
- **Internally modular** — each domain (health, dev, workspace) is a self-contained MCP server with its own tools and data
- **Clear boundaries** — modules don't share memory or bleed into each other, but they all live in the same runtime
- **Single brain** — one OpenClaw agent orchestrates everything via skills and MCP tool calls

The alternative would be a microservices approach — a separate OpenClaw gateway per domain, network calls between them. That's operational overkill for a personal assistant.

---

## OpenClaw concepts used in this stack

### Agents

An agent in OpenClaw is "a fully scoped brain with its own workspace, state directory, session store, auth profiles, and model registry."

In lifekit-stack there is **one agent** — the main gateway agent. It receives every user message, loads relevant context from `~/.life/`, and routes work to the appropriate module via the `lifekit-router` skill.

Reference: [https://docs.openclaw.ai/concepts/agents](https://docs.openclaw.ai/concepts/agents)

Multiple OpenClaw agents (via `agentToAgent`) are not used here by design — they add routing complexity and session overhead without a meaningful gain for a single-user personal assistant. One brain, well-instructed via `AGENTS.md`, is simpler and more predictable.

### Skills

Skills in OpenClaw are **instructional** — a `SKILL.md` file that teaches the agent how to use available tools in a specific context. They are not architectural isolators.

`lifekit-router` is the core skill in this stack. It:
1. Reads `~/.life/system/modules.yaml` to know what modules exist and what they do
2. Matches the user's intent against module descriptions and examples
3. Calls the right MCP server's tools
4. Translates the result back to the user

Skills are loaded on-demand (not auto-injected into every session), which keeps context lean.

Reference: [https://docs.openclaw.ai/concepts/skills](https://docs.openclaw.ai/concepts/skills)

### Memory

Each agent has its own isolated memory at `~/.openclaw/agents/<agentId>/workspace/`:

```
workspace/
├── AGENTS.md       # standing operating instructions — loaded every session
├── SOUL.md         # persona and tone
├── USER.md         # who the user is
├── MEMORY.md       # long-term facts loaded at session start
└── memory/
    ├── 2026-05-24.md   # today's working notes (auto-loaded)
    └── 2026-05-23.md   # yesterday's (auto-loaded)
```

`~/.life/domains/` is a **separate, parallel memory layer** owned by the lifekit framework — not OpenClaw's native memory. The router skill bridges these two layers: it reads `~/.life/domains/` to inject domain-specific context into MCP calls.

Reference: [https://docs.openclaw.ai/concepts/memory](https://docs.openclaw.ai/concepts/memory)

### Tools and MCP servers

Tools are callable functions the agent can invoke. Each domain module in this stack is an **MCP server** that exposes tools:

| Module | MCP server | What it does |
|---|---|---|
| `health-claw` | `http://health-claw:8000/mcp/` | Workouts, PRs, mood/energy logging, fitness state |
| `devclaw` | `http://devclaw-mcp:8000/mcp/` | Dev tasks, PRs, technical research |
| `google-workspace` | `http://google-workspace-mcp:8000/mcp/` | Email, calendar, docs, tasks |

New modules are added by:
1. Adding the MCP server to `compose/docker-compose.yml`
2. Wiring it into `openclaw.json` under `mcp.servers`
3. Adding an entry to `defaults/modules.yaml`

The router skill picks up new modules automatically — no code changes needed.

Reference: [https://docs.openclaw.ai/concepts/tools](https://docs.openclaw.ai/concepts/tools)

### Bindings

Bindings route inbound messages to agents. In this stack there is one binding: the Telegram channel routes to the single main agent.

```json5
bindings: [
  { peer: "{{ telegram_chat_id }}", agent: "main" }
]
```

Reference: [https://docs.openclaw.ai/concepts/multi-agent](https://docs.openclaw.ai/concepts/multi-agent)

### Cron and heartbeat

OpenClaw's native cron runs scheduled tasks against the agent. Used in this stack for the curator drain trigger and morning brief.

Heartbeat runs a lightweight periodic turn in the main session (default every 30 min) — used for ambient monitoring and inferred commitment follow-ups.

Reference: [https://docs.openclaw.ai/concepts/cron](https://docs.openclaw.ai/concepts/cron)

---

## Routing: how the agent decides what to call

The routing model is **skill-driven, not code-driven**:

```
User message
  → OpenClaw agent (AGENTS.md loaded)
    → lifekit-router skill activated
      → reads ~/.life/system/modules.yaml
      → reads relevant ~/.life/domains/*.md for context
      → matches intent → picks module(s)
      → calls MCP tools
      → synthesizes response
  → Reply to user
```

For cross-module intents ("book a dentist and block the time in my calendar"), the router handles them **sequentially within one agent turn** — completes the first MCP call, uses its result to inform the second. This is explicit in `skills/lifekit-router/SKILL.md`.

This is intentional. Parallel fan-out adds complexity and makes response synthesis harder. Sequential calls in a single turn are coherent, auditable, and fast enough for a personal assistant.

---

## AGENTS.md — the orchestrator's standing instructions

`AGENTS.md` is the most important file in the workspace. It is loaded into every session and defines how the agent operates. It should be written like a contract, not a prompt.

### What belongs in AGENTS.md

- **Who this agent is and what it owns** — the scope of its authority
- **How it routes work** — explicit instruction to use the router skill and call modules
- **What it never does** — guard rails (never fulfills a request a module covers; never dumps raw JSON)
- **How it communicates** — tone, format, length

### What does NOT belong in AGENTS.md

- Domain knowledge (that's in `~/.life/domains/`)
- Module descriptions (that's in `modules.yaml`)
- User preferences (that's in `USER.md`)
- Persona and tone detail (that's in `SOUL.md`)

### Example AGENTS.md

```markdown
# Kit

You are Kit — Denys's personal AI assistant running on his VPS.

You are the single point of contact for everything: productivity, health, dev work,
communication, finance. You route work to specialist modules and synthesize their
results. You do not do the specialist work yourself.

---

## How you work

At the start of every session, the lifekit-router skill is active. Use it.

For every user message:
1. Read ~/.life/system/modules.yaml to know what modules are available.
2. Read the relevant domain file(s) from ~/.life/domains/ for context.
3. Match the intent to the right module(s). Call their MCP tools.
4. Synthesize the result into one clear, plain-language response.
5. If no module covers the intent, handle it directly using your knowledge and ~/.life/ context.

For cross-module intents, handle sequentially: finish the first module call, use its
result to inform the second.

---

## What you never do

- Never attempt to fulfill a request that a module covers — always call the module.
- Never dump raw JSON or tool output at the user.
- Never ask more than one clarifying question at a time.
- Never make assumptions about sensitive actions (sending emails, booking appointments,
  filing tasks) — confirm intent first, then act.

---

## How you communicate

- Confirm what you're doing in one line before calling a module: "Logging this workout with health-claw."
- After a module responds, summarize in plain language. Be concise.
- Match Denys's register — direct, no filler, no corporate tone.
- If something is blocked or failed, explain clearly and suggest next steps.

---

## Memory

Save to MEMORY.md when you learn something durable about Denys's preferences,
constraints, or goals that isn't already in ~/.life/domains/. Do not duplicate
what's already in the domain files.

Use today's daily note (memory/YYYY-MM-DD.md) for working context — things that
matter this session or this week but not permanently.
```

---

## Adding a new module

1. Build the MCP server (see existing modules in `compose/` for the pattern)
2. Add the service to `compose/docker-compose.yml`
3. Wire it in `openclaw.json` under `mcp.servers`
4. Add an entry to `defaults/modules.yaml` with `id`, `description`, `mcp`, and `examples`
5. The router skill picks it up on next session — no other changes needed

---

## Further reading

- [OpenClaw concepts](https://docs.openclaw.ai/) — agents, skills, memory, tools, bindings, cron
- [OpenClaw multi-agent](https://docs.openclaw.ai/concepts/multi-agent) — why we use one agent, not many
- [lifekit-router skill](../skills/lifekit-router/SKILL.md) — the routing logic
- [modules.yaml](../defaults/modules.yaml) — the routing manifest
- [architecture.md](./architecture.md) — the stack-level design

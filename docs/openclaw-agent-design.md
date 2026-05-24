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

**This is the primary isolation primitive.** Each domain in lifekit-stack is a separate OpenClaw agent:

```
openclaw.json
├── agents
│   ├── orchestrator      ← receives every user message, decides routing
│   ├── health            ← health, fitness, mood, sleep
│   ├── finance           ← spending, budgets, investments
│   ├── dev               ← code, PRs, technical research
│   └── ...
```

Each domain agent has its own workspace:

```
~/.openclaw/agents/<domain>/workspace/
├── AGENTS.md       # domain-specific operating instructions
├── SOUL.md         # persona for this domain
├── USER.md         # relevant user profile for this domain
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

`agentToAgent` must be explicitly enabled and allowlisted in `openclaw.json`:

```json5
tools: {
  agentToAgent: {
    enabled: true,
    allow: ["health", "finance", "dev"]
  }
}
```

Reference: [https://docs.openclaw.ai/concepts/multi-agent](https://docs.openclaw.ai/concepts/multi-agent)

### Bindings — single entry point

Bindings route inbound channel messages to agents. All user messages enter through **one binding** pointing to the orchestrator:

```json5
bindings: [
  { peer: "{{ telegram_chat_id }}", agent: "orchestrator" }
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

## Further reading

- [OpenClaw concepts](https://docs.openclaw.ai/) — agents, skills, memory, tools, bindings, cron
- [OpenClaw multi-agent](https://docs.openclaw.ai/concepts/multi-agent) — agentToAgent, bindings, workspace isolation
- [architecture.md](./architecture.md) — the stack-level design

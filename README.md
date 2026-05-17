# lifekit-stack

> One-command personal-AI stack on your own VPS. Reference deployment for [lifekit](https://github.com/dsdevq/lifekit).

`lifekit-stack` is a starter-template that deploys a complete personal-AI environment to a fresh VPS in under 15 minutes:

- **[OpenClaw](https://openclaw.ai/)** — the runtime gateway (the `RuntimeGateway` adapter port). Chat bot, voice, scheduled briefs, conversational agent, workspace skills.
- **[lifekit](https://github.com/dsdevq/lifekit)** — the Python framework that owns your `~/.life/` knowledge layer, the wizard, and the curator that maintains your domain files.
- **`lifekit-curator`** — sibling container drains `queue.jsonl` and updates your domain files in the background.
- **Workspace skills** — opt-in skills bundled with this template (morning brief, learning coach, brainstorm, calendar/gmail integration, and more — see [`skills/`](./skills/)).
- **Docker Compose + a single bash bootstrap script** — infrastructure-as-code. Reproducible from `git clone`.
- **Mesh-VPN + loopback-only** — no public ingress, no domain, no TLS to manage. Outbound long-polling only.

The design is provider-neutral by construction: every swappable component sits behind an adapter port — `RuntimeGateway`, `BuildEngine`, `Sandbox`, `LocalLLM` — documented in [`docs/architecture.md`](./docs/architecture.md). The [Reference deployment](#reference-deployment) section below names the exact tested combination, but each component is a swap-point, not a hard requirement.

Autonomous build/agent workloads (swarm and similar) are explicitly **not** sibling containers here — when they land in a future release, they run *inside* a NemoClaw sandbox spawned per invocation by OpenClaw. See [`docs/architecture.md`](./docs/architecture.md) for the boundary.

## Status

**Pre-release.** Active development. Not yet ready for general adoption; first cohort of users coming soon. Watch the repo or open an issue if you'd like a heads-up.

## How it fits together

```
                  ┌──────────────────┐
   You (phone) ───┤ Chat transport   │
                  └────────┬─────────┘
                           │ long-polling (or webhook)
                  ┌────────▼─────────────────────────────┐
                  │ Your VPS (Debian/Ubuntu, loopback)   │
                  │                                       │
                  │   ┌─────────────────────────────────┐ │
                  │   │ OpenClaw gateway (loopback)     │ │
                  │   │   channels, cron, skills, agent │ │
                  │   └────┬─────────────────────┬──────┘ │
                  │        │ reads/writes        │ calls   │
                  │        ▼                     ▼         │
                  │   ┌──────────┐    ┌──────────────────┐ │
                  │   │ ~/.life/ │    │ lifekit-curator  │ │
                  │   │ (data)   │◄───┤ drains queue.jsonl│ │
                  │   └──────────┘    └──────────────────┘ │
                  └───────────────────────────────────────┘
                           ▲
                           │ SSHFS over mesh VPN
   You (laptop) ───────────┘
```

Your laptop SSHFS-mounts the VPS's `~/.life/` over a mesh VPN. The VPS is canonical; your laptop is a thin client. The stack survives your laptop being closed.

## Prerequisites

Before you start the wizard, have these ready:

- **A VPS.** Any Debian or Ubuntu 24.04+ host with at least 2GB RAM. (See [Reference deployment](#reference-deployment) for the exact provider/plan we test against.)
- **A mesh-VPN account** with an unattended-join auth key.
- **A chat-transport bot** that supports long-polling (or webhook) delivery, plus your own user ID for the owner allowlist. The [Reference deployment](#reference-deployment) section has the concrete bot-creation steps for the tested transport.
- **Anthropic auth.** Either an active Claude CLI login (`claude login`) or an API key.
- **Optional:** OpenAI API key (for Codex CLI integration), provider keys for finance skills (Plaid, Monobank, IBKR, Binance).

You'll also need [`pipx`](https://pipx.pypa.io/) on your laptop to install the `lifekit` CLI.

## Quickstart

The high-level flow: install the CLI, clone this template, run the wizard. The wizard handles the rest end-to-end. Full walkthrough (with the tested-stack copy-paste commands) lives in [`docs/quickstart.md`](./docs/quickstart.md).

### Reference walkthrough

The exact commands the maintainer runs against the reference deployment:

```bash
# Install the lifekit CLI (one-time)
pipx install lifekit

# Clone this template
git clone https://github.com/dsdevq/lifekit-stack.git
cd lifekit-stack

# Run the wizard — prompts you for tokens + identity, generates configs,
# bootstraps the VPS, starts the stack, verifies a Telegram round-trip
lifekit init-stack
```

Full walkthrough: [`docs/quickstart.md`](./docs/quickstart.md).

## What's inside

```
lifekit-stack/
├── compose/              # docker-compose.extra.yml, Dockerfiles, OpenClaw config template
├── scripts/              # bootstrap-vps.sh, deploy.sh, oclaw
├── skills/               # parameterized workspace skills (opt-in via wizard)
├── docs/                 # quickstart, architecture, runbook, customizing-skills
├── .github/workflows/    # CI: lint, template tests, semver releases
└── PRIVATE.md            # audit checklist — what NEVER belongs in this repo
```

## Updating

```bash
cd lifekit-stack
git pull
lifekit init-stack --target <your-host>   # idempotent — reapplies templates, restarts services
```

The wizard saves your choices to `wizard.yaml` on first run, so re-runs are non-interactive.

## What's NOT in this repo

This repository contains **only code, config templates, and deploy logic**. Personal data and secrets stay out by design — see [`PRIVATE.md`](./PRIVATE.md) for the full audit checklist. Briefly:

- **Your `~/.life/` data** — your journal, domains, knowledge layer. Lives on your VPS. Optionally back it up to your own private git repo, never this one.
- **Secrets** — bot tokens, API keys, encryption keys. Generated locally by the wizard, never committed.
- **Anything PII** — names, schedules, locations, dietary constraints, financial details. These get injected into skills at deploy time via the wizard's user-context.

The wizard enforces this; pre-commit hooks ([gitleaks](https://github.com/gitleaks/gitleaks)) catch slip-ups before they push.

## Customizing skills

Each skill in `skills/` is parameterized via Jinja2 templates that read from a user-context file the wizard generates. Want to write your own? See [`docs/customizing-skills.md`](./docs/customizing-skills.md).

## Architecture, in depth

See [`docs/architecture.md`](./docs/architecture.md) for adapter choices, port catalog, blast-radius topology, and why we made the trade-offs we did.

## Contributing

Issues and PRs welcome. See [`CONTRIBUTING.md`](./CONTRIBUTING.md).

Setups outside the [Reference deployment](#reference-deployment) are not officially supported in v0.x — pull requests adding adapters (new chat transports, new mesh VPNs, new hosts) are very welcome, but we won't promise responsiveness on issues for setups outside the supported path.

## License

[MIT](./LICENSE) © 2026 Denys Sychov.

## Related

- [dsdevq/lifekit](https://github.com/dsdevq/lifekit) — the Python framework + wizard CLI this template uses.
- [OpenClaw](https://openclaw.ai/) — the runtime gateway.
- [The story behind it](#) — _coming soon: blog post on building a file-based framework for personal AI memory._

## Reference deployment

The exact combination this stack is tested against. Every component below is a swap-point via the adapter ports in [`docs/architecture.md`](./docs/architecture.md#2-adapter-pattern-for-every-replaceable-component), not a hard dependency — these are simply the ones the maintainer runs in production.

- **Host:** [Hetzner](https://www.hetzner.com/cloud) CX22 (2 vCPU / 4GB RAM, ≈€4/mo, EU), Ubuntu 24.04. Any other Debian-family VPS with comparable specs should work; the only setup that gets active issue-tracking is this one.
- **Mesh VPN:** [Tailscale](https://tailscale.com/) with an [unattended-join auth key](https://login.tailscale.com/admin/settings/keys). The host's UFW closes all public ports except ICMP; admin access (SSH, SSHFS) goes through the mesh.
- **Chat transport:** Telegram long-polling — the gateway dials out to Telegram, no inbound webhook needed. Create a bot via [@BotFather](https://t.me/BotFather) (grab the token), then DM [@userinfobot](https://t.me/userinfobot) to get your own numeric user ID (this becomes the owner allowlist).
- **Local LLM:** Anthropic Haiku on the same VPS (CPU-only, no GPU required).

Swap any of these by writing a new adapter against the corresponding port — the rest of the stack doesn't know or care.

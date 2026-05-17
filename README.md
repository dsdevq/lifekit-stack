# lifekit-stack

> One-command personal-AI stack on your own VPS. Reference deployment for [lifekit](https://github.com/dsdevq/lifekit).

`lifekit-stack` is a starter-template that deploys a complete personal-AI environment to a fresh VPS in under 15 minutes:

- **[OpenClaw](https://openclaw.ai/)** — the runtime gateway. Telegram bot, voice, scheduled briefs, conversational agent, workspace skills.
- **[lifekit](https://github.com/dsdevq/lifekit)** — the Python framework that owns your `~/.life/` knowledge layer, the wizard, and the curator that maintains your domain files.
- **`lifekit-curator`** — sibling container drains `queue.jsonl` and updates your domain files in the background.
- **Workspace skills** — opt-in skills bundled with this template (morning brief, learning coach, brainstorm, calendar/gmail integration, and more — see [`skills/`](./skills/)).
- **Docker Compose + Ansible** — infrastructure-as-code. Reproducible from `git clone`.
- **Tailscale + loopback-only** — no public ingress, no domain, no TLS to manage. Telegram long-polling outbound only.

Autonomous build/agent workloads (swarm and similar) are explicitly **not** sibling containers here — when they land in a future release, they run *inside* a NemoClaw sandbox spawned per invocation by OpenClaw. See [`docs/architecture.md`](./docs/architecture.md) for the boundary.

## Status

**Pre-release.** Active development. Not yet ready for general adoption; first cohort of users coming soon. Watch the repo or open an issue if you'd like a heads-up.

## How it fits together

```
                  ┌──────────────────┐
   You (phone) ───┤ Telegram         │
                  └────────┬─────────┘
                           │ long-polling
                  ┌────────▼─────────────────────────────┐
                  │ Your VPS (Hetzner CX22 or similar)   │
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
                           │ SSHFS over Tailscale
   You (laptop) ───────────┘
```

Your laptop SSHFS-mounts the VPS's `~/.life/` over Tailscale. The VPS is canonical; your laptop is a thin client. The stack survives your laptop being closed.

## Prerequisites

Before you start the wizard, have these ready:

- **A VPS.** Tested on [Hetzner](https://www.hetzner.com/cloud) CX22 (≈€4/mo, EU). Any Debian or Ubuntu 24.04+ VPS with 2GB RAM should work.
- **A Tailscale account** with an [auth key](https://login.tailscale.com/admin/settings/keys) for unattended joins.
- **A Telegram bot.** Create one via [@BotFather](https://t.me/BotFather), grab the token and your own Telegram user ID.
- **Anthropic auth.** Either an active Claude CLI login (`claude login`) or an API key.
- **Optional:** OpenAI API key (for Codex CLI integration), provider keys for finance skills (Plaid, Monobank, IBKR, Binance).

You'll also need [`pipx`](https://pipx.pypa.io/) on your laptop to install the `lifekit` CLI.

## Quickstart

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
├── skills/               # parameterized workspace skills (opt-in via wizard)
├── scripts/              # bootstrap.sh, deploy.sh
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

Non-Hetzner / non-Telegram setups are not officially supported in v0.x — pull requests adding adapters are very welcome, but we won't promise responsiveness on issues for setups outside the supported path.

## License

[MIT](./LICENSE) © 2026 Denys Sychov.

## Related

- [dsdevq/lifekit](https://github.com/dsdevq/lifekit) — the Python framework + wizard CLI this template uses.
- [OpenClaw](https://openclaw.ai/) — the runtime gateway.
- [The story behind it](#) — _coming soon: blog post on building a file-based framework for personal AI memory._

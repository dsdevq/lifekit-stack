# Quickstart

This walks you through deploying `lifekit-stack` to a fresh Hetzner VPS in about 15 minutes.

> **Status:** wizard is under active development. Some steps below are aspirational until v0.1.0 ships. See the [main README](../README.md) for current status.

## Before you start

You'll need:

1. **A VPS.** This guide uses [Hetzner](https://www.hetzner.com/cloud) CX22 (2 vCPU / 4GB RAM, ≈€4/mo) in an EU region, running Ubuntu 24.04. Other Debian-family VPS providers work but aren't tested in v0.x.
2. **A Tailscale account** with an [auth key](https://login.tailscale.com/admin/settings/keys). Ephemeral or reusable, your call.
3. **A Telegram bot.**
   - DM [@BotFather](https://t.me/BotFather), `/newbot`, choose a name. Save the token.
   - DM [@userinfobot](https://t.me/userinfobot) to get your own numeric Telegram user ID. Save it (this becomes the owner allowlist).
4. **Anthropic auth** — either:
   - A working [Claude CLI](https://docs.claude.com/claude-code) login on your laptop (preferred — no API key needed), or
   - An Anthropic API key.
5. **A laptop with [`pipx`](https://pipx.pypa.io/) installed.**

Optional but recommended:

- A domain name — not needed in v0.x (Telegram long-polling), but if you ever want webhook mode it's required.
- OpenAI API key — only if you want Codex CLI integration for autonomous code builds.

## Step 1 — Install the wizard

```bash
pipx install lifekit
lifekit --version    # should print 0.1.0 or newer
```

## Step 2 — Clone the template

```bash
git clone https://github.com/dsdevq/lifekit-stack.git
cd lifekit-stack
```

## Step 3 — Run the wizard

```bash
lifekit init-stack
```

The wizard will:

1. **Check prerequisites.** Verifies Docker is available on the target, Tailscale auth key is valid, Telegram bot token works, Anthropic auth resolves.
2. **Collect identity.** Your name, email, timezone, preferred language.
3. **Collect tokens.** Telegram, Anthropic, optional OpenAI, optional finance providers.
4. **Let you select skills.** Multi-select from the bundled skills in `skills/`. Sensible defaults; opt out of anything you don't want.
5. **Optionally bootstrap `~/.life/` domains.** If you have a `~/.claude/CLAUDE.md` describing yourself, the wizard can draft your domain files from it (reuse of existing `lifekit onboard`).
6. **Render templates.** Generates real config files from the Jinja2 templates in `compose/`, `ansible/`, and `skills/` using the values you provided.
7. **Provision the VPS.** Runs the Ansible playbook (installs Docker, Tailscale, UFW, creates directories).
8. **Start the stack.** Brings up the Docker Compose stack (`OpenClaw gateway` + `lifekit-curator` + your selected skills).
9. **Verify.** Runs `openclaw doctor`, `openclaw health`, sends a synthetic Telegram message, confirms round-trip.

If anything fails, the wizard surfaces the error and tells you what to try next.

## Step 4 — First message

Open Telegram, find your bot, and say hello. The bot should respond. Try:

- `/help` — see what your Kit can do.
- `Hi Kit, what's on my calendar today?` — if you wired calendar.
- `Log: I went for a 5km run` — capture into `queue.jsonl`.

## Step 5 — Mount `~/.life/` on your laptop (optional but recommended)

If you want to read/edit your knowledge layer from your laptop:

```bash
# On the laptop
sudo apt install sshfs
mkdir -p ~/.life
sshfs lifekit@<your-vps-tailscale-name>:/srv/life ~/.life
```

For auto-mount on boot, see [`docs/runbook.md`](./runbook.md#sshfs-auto-mount).

## Updating

Pull the template and re-run the wizard:

```bash
cd lifekit-stack
git pull
lifekit init-stack --target <your-vps-tailscale-name>
```

The wizard reads `wizard.yaml` from the target (saved on first run), reapplies templates, and restarts only services touched by the changeset. Idempotent.

## Troubleshooting

See [`docs/runbook.md`](./runbook.md) for common issues — failed Tailscale joins, OpenClaw not starting, Telegram silence, queue.jsonl growing without drain, etc.

## Getting help

- [GitHub Issues](https://github.com/dsdevq/lifekit-stack/issues) — bugs and feature requests.
- [GitHub Discussions](https://github.com/dsdevq/lifekit-stack/discussions) — questions and ideas.
- [OpenClaw docs](https://docs.openclaw.ai/) — for runtime-specific questions.

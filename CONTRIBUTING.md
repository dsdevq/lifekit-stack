# Contributing to lifekit-stack

Thanks for thinking about contributing. This project is small, opinionated, and explicitly scoped — please read this before opening a PR.

## Scope

`lifekit-stack` is a deployment template for a personal-AI stack consisting of [OpenClaw](https://openclaw.ai/) + [lifekit](https://github.com/lifekit-hq/lifekit) + workspace skills, deployable to a Hetzner-style VPS via Docker Compose. That's it.

**In scope:**
- Bug fixes in the bootstrap script, compose extras, deploy scripts, wizard integration.
- New parameterized workspace skills under `skills/`, provided they follow the templating discipline (see below) and pass the `PRIVATE.md` audit.
- Documentation improvements.
- Support for additional VPS providers (DigitalOcean, Vultr, etc.) — preferably as additional bash bootstrap variants, not by rewriting the existing one.

**Out of scope for v0.x:**
- Non-Telegram chat channels (Discord, Slack, etc. — OpenClaw supports them, but the template's wizard only validates Telegram for v0.x).
- Webhook-mode Telegram (long-polling is the default; webhook is opt-in via direct config edit, not via the wizard).
- Custom OpenClaw runtimes (we ship the upstream gateway, that's it).
- Tools that bypass the wizard — every adoption path should round-trip through `lifekit init-stack`.

If you want to extend scope, open an issue first to discuss.

## Hard rules

1. **No private data, ever.** Read [`PRIVATE.md`](./PRIVATE.md) before committing anything. The `gitleaks` hook catches secrets; you catch personal context.
2. **Every skill is templated.** No personal data baked into a `SKILL.md`. If you can't templateize a skill cleanly, it doesn't go in `skills/`.
3. **Idempotency.** The bootstrap script, deploy scripts, and the wizard's `init-stack` all must be safely re-runnable. State changes only when needed.
4. **Conventional Commits.** All commits follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`). The `release-please` workflow uses these to derive the changelog.
5. **All PRs must pass CI.** Pre-commit, template-render tests, shellcheck, hadolint. No exceptions.

## Local development setup

```bash
# Clone
git clone https://github.com/lifekit-hq/lifekit-stack.git
cd lifekit-stack

# Install dev dependencies
pipx install pre-commit
pre-commit install

# Verify
pre-commit run --all-files
```

To test a wizard change end-to-end, you'll need a sacrificial VPS or a local VM. Don't test wizard changes against your real personal deployment.

## How to add a new skill

1. Create `skills/<your-skill-name>/` with:
   - `SKILL.md` — the skill prompt, Jinja2 template, reading `{{ user.* }}` for any user-specific values.
   - `defaults.yaml` — sensible default values so the skill works without explicit user input.
   - `README.md` — what the skill does, what user-context it expects, example interactions.
2. Audit against [`PRIVATE.md`](./PRIVATE.md). Run `gitleaks detect` on your branch.
3. Run the template-render test: `python scripts/test-render-skills.py skills/<your-skill-name>/`.
4. Open a PR with `feat(skills): add <skill-name>` as the commit message.

## How to add a VPS-provider adapter

1. New bash bootstrap variant under `scripts/` (e.g. `bootstrap-vps-<provider>.sh`) with the same surface as `scripts/bootstrap-vps.sh` (idempotent host bootstrap, docker install, Tailscale, UFW, dirs).
2. Document the provider's prerequisites in `docs/providers/<provider>.md`.
3. Wire it into the wizard so `lifekit init-stack` can prompt for provider choice.

## Reporting issues

Use the issue templates. Include:

- VPS provider + region + plan.
- OS version (`/etc/os-release`).
- Output of `lifekit init-stack --version` and `docker compose version`.
- The full error message and the last ~50 lines of relevant logs.

**Do not** paste real tokens, real Telegram user IDs, or your real `wizard.yaml`. Sanitize first.

## Code of conduct

By participating, you agree to abide by the [Code of Conduct](./CODE_OF_CONDUCT.md).

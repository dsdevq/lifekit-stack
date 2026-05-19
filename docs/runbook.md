# Runbook

Operational guide. How to update, roll back, back up, and recover when things break.

> **Status:** pre-release. Some procedures are aspirational until v0.1.0 ships.

## Updating

```bash
# On your laptop
cd lifekit-stack
git pull
lifekit init-stack --target <your-vps-tailscale-name>
```

The wizard reads the existing `wizard.yaml` on the target, reapplies templates, and `docker compose up -d --build` restarts only services touched by the changeset. Idempotent — re-running with no upstream changes is a no-op.

If you just want to bump OpenClaw or pull skill updates without changing wizard inputs, you can SSH directly:

```bash
ssh <your-vps-tailscale-name>
cd /srv/lifekit-stack
git pull
docker compose -f compose/docker-compose.yml -f compose/docker-compose.extra.yml up -d --build
docker compose exec -T openclaw-cli openclaw doctor
docker compose exec -T openclaw-cli openclaw health
```

## Rolling back

If a deploy breaks something:

```bash
ssh <your-vps-tailscale-name>
cd /srv/lifekit-stack
git log --oneline -n 5             # find the last-known-good commit
git checkout <good-commit>
docker compose up -d --build
```

For a more disciplined rollback, prefer reverting on `main` in the repo:

```bash
# On your laptop
git revert <bad-commit>
git push
# wait for CI deploy workflow to apply on the VPS
```

## Backups

`~/.life/` is your data. Back it up.

**Recommended:** push to a private git repo from the VPS, on a cron:

```bash
# /srv/life-backup-cron, runs every 6 hours
cd /srv/life
git add -A
git commit -m "snapshot $(date -Iseconds)" || true
git push origin main
```

This gives you point-in-time recovery and an off-machine copy.

**Volumes to back up if you want full disaster recovery:**

- `/srv/life/` — your knowledge data (most important)
- `/srv/openclaw/config/` — OpenClaw config + `.env`
- `/srv/openclaw/secret-key/` — OpenClaw OAuth encryption key (lose this and you re-pair every channel)
- `/srv/openclaw/workspace/` — workspace skills (recoverable from this repo, but having a local copy is faster)

Snapshot these via Hetzner Backups (built-in, ~20% extra/mo) or rsync to another box.

## When Telegram goes silent

Symptom: your bot stops responding, no errors visible.

```bash
ssh <your-vps-tailscale-name>
cd /srv/lifekit-stack
docker compose logs -f openclaw-gateway --tail 100
docker compose exec -T openclaw-cli openclaw doctor
docker compose exec -T openclaw-cli openclaw channels list
```

Common causes:

1. **Telegram token rotated** — check `/srv/openclaw/config/.env` against your BotFather token. Update + restart.
2. **Polling stalled** — `docker compose restart openclaw-gateway`.
3. **OpenClaw OOM** — `dmesg | grep -i oom`. If yes, scale up the VPS.
4. **Anthropic auth expired** — `docker compose exec openclaw-cli claude auth status`. Re-login if needed.

## When a skill says "command not found" or "sharp: missing native binary"

Symptom: a skill that worked on the laptop fails on the VPS with `command not
found: life-state`, or `Error: Could not load the "sharp" module using the
linux-arm64 runtime` (nutrition-claw and any other skill with native deps).

Root cause: `rsync` from the laptop copies the skill source, but skips the
platform-specific bits — `node_modules/` built for the wrong arch, or a CLI
binary that was `npm install -g`'d on the laptop and never replayed on the
VPS.

`scripts/deploy.sh` now reinstalls every skill's `package.json` inside the
gateway container after rsync, and `compose/openclaw-gateway/Dockerfile`
bakes in `python3 / make / g++ / libvips-dev` so native modules (sharp,
better-sqlite3, etc.) can rebuild on-host. That covers the per-skill
`node_modules` case automatically — re-run `scripts/deploy.sh` and the
`sharp` error should clear.

Skill CLIs that live outside this repo (e.g. `life-state`, which is built
from `~/projects/life-state` on the maintainer's laptop) are NOT handled
automatically — they have to be installed once on the VPS after the first
rsync. Inside the gateway container:

```bash
docker compose -f compose/docker-compose.yml --env-file /srv/openclaw/config/.env \
  exec openclaw-gateway npm install -g /home/node/.openclaw/workspace/external/life-state
```

…and then restart the gateway. If `external/life-state` isn't there yet,
rsync it across alongside the skills directory.

## When `queue.jsonl` grows without draining

Symptom: `/srv/life/queue.jsonl` keeps growing; domain files don't update.

```bash
ssh <your-vps-tailscale-name>
docker compose logs -f lifekit-curator --tail 100
```

Common causes:

1. **Curator crashed** — `docker compose restart lifekit-curator`. Check logs for the underlying error.
2. **Claude CLI auth in the curator container failed** — `docker compose exec lifekit-curator claude auth status`.
3. **`~/.life/domains/` not writable** — `docker compose exec lifekit-curator ls -la /srv/life/domains/`.

## SSHFS auto-mount

To auto-mount `/srv/life/` on your laptop at login:

```bash
# Add to /etc/fstab (Linux) or ~/Library/LaunchAgents (macOS)
<vps-tailscale-name>:/srv/life /home/<you>/.life fuse.sshfs \
  noauto,x-systemd.automount,_netdev,user,idmap=user,follow_symlinks,IdentityFile=/home/<you>/.ssh/id_ed25519,allow_other,default_permissions,uid=1000,gid=1000  0 0
```

For macOS use `macFUSE` + the equivalent `LaunchAgent` config. For WSL, a `systemd-user` automount is the cleanest path.

If the mount is flaky over Tailscale, try:

```bash
sshfs -o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 ...
```

## Recovering from a complete VPS loss

You provisioned a Hetzner instance, it died, and you want to come back up on a fresh one.

```bash
# 1. New VPS, fresh Ubuntu 24.04. SSH in (over its temporary public address).
# 2. From your laptop:
cd lifekit-stack
lifekit init-stack --target <new-vps-ip>
# Wizard reuses your saved wizard.yaml (from your private backup, NOT this repo).

# 3. Restore /srv/life/ from your private backup repo:
ssh <new-vps>
cd /srv/life
git clone <your-private-life-repo> .
# OR rsync from a snapshot.

# 4. Restore /srv/openclaw/secret-key/ from your private backup.
#    Without this, you have to re-pair every channel.
```

Total recovery time: ~30 minutes if your backups are current.

## Health check chain

After every deploy, the wizard runs:

```bash
docker compose exec -T openclaw-cli openclaw doctor
docker compose exec -T openclaw-cli openclaw health
# Plus a synthetic Telegram self-message round-trip
```

If any of these fail, the wizard surfaces the error and does NOT mark the deploy as green. Manual `git revert` + re-deploy is the v0.x rollback path; auto-revert is on the v1 roadmap.

## When to scale up

Symptoms of an undersized VPS:

- `dmesg | grep -i oom` shows the kernel killing containers.
- `docker stats` shows persistent CPU saturation.
- OpenClaw logs say "polling timeout" frequently.

Hetzner CX22 → CX32 is a live resize (no data loss). For larger jumps, snapshot first.

## When to ask for help

Open a [GitHub Issue](https://github.com/dsdevq/lifekit-stack/issues) with:

- `lifekit init-stack --version`
- `docker compose version`
- VPS provider + plan
- `docker compose logs --tail 200` (sanitized — never paste tokens)
- What you tried before opening the issue

Non-Hetzner setups are accepted, but no official SLA in v0.x.

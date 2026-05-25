# Health agent skills

This dir holds `SKILL.md` manifests that OpenClaw loads to teach the health agent how + when to call its CLIs. The manifests are **canonically owned by the upstream CLI projects** — they ship in those repos, get versioned there, and are synced into this workspace at deploy time. We don't vendor them here (would drift).

## Skills required

- `workout-claw/SKILL.md` — from `dsdevq/workout-claw`
- `life-state/SKILL.md` — from `dsdevq/life-state`

## Sync at deploy time

When deploying the health agent workspace to the VPS, rsync the `SKILL.md` files from your local CLI checkouts:

```bash
HEALTH_SKILLS=/srv/openclaw/agents/health/workspace/skills

mkdir -p "$HEALTH_SKILLS"/{workout-claw,life-state}
rsync -av ~/projects/workout-claw/SKILL.md "$HEALTH_SKILLS"/workout-claw/
rsync -av ~/projects/life-state/SKILL.md   "$HEALTH_SKILLS"/life-state/
```

(OpenClaw rejects symlinks for skill paths — copy the files.)

## CLI binaries

The `SKILL.md` manifests assume the CLIs themselves are on the gateway container's PATH. Install them in the container (or on the host the container inherits PATH from):

```bash
npm install -g workout-claw life-state
```

## Restart

After adding or updating skills:

```bash
docker compose -f compose/docker-compose.yml --env-file ... \
  up -d --force-recreate openclaw-gateway
```

OpenClaw caches the skill manifest at gateway start, so a restart is required for the agent to discover updated SKILL.md files.

## Adding more skills here

Anything that's a discrete CLI invocable by the health agent: future `nutrition-claw`, sleep-tracker, heart-rate import, etc. Each goes in its own subdir with its canonical `SKILL.md`.

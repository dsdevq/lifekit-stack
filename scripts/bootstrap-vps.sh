#!/usr/bin/env bash
# bootstrap-vps.sh — one-shot host setup. Run this once on a fresh Hetzner VPS.
#
# Idempotent: re-running it is safe. Each step is "install if missing" or
# "ensure-state" — nothing destructive.
#
# Usage on the VPS as root (after SSHing in fresh):
#
#   curl -fsSL https://raw.githubusercontent.com/dsdevq/lifekit-stack/main/scripts/bootstrap-vps.sh | \
#     sudo TAILSCALE_AUTH_KEY=tskey-auth-... TAILSCALE_HOSTNAME=lifekit-vps bash
#
# Or clone the repo first and run:
#   sudo TAILSCALE_AUTH_KEY=... TAILSCALE_HOSTNAME=lifekit-vps ./scripts/bootstrap-vps.sh
#
# After this script: scp your .env to /srv/openclaw/config/.env, then run ./scripts/deploy.sh.

set -euo pipefail

# ─── Required env ─────────────────────────────────────────────────────────────

: "${TAILSCALE_AUTH_KEY:?set TAILSCALE_AUTH_KEY=tskey-auth-... before running}"
: "${TAILSCALE_HOSTNAME:?set TAILSCALE_HOSTNAME=<name> before running}"

LIFEKIT_USER="${LIFEKIT_USER:-lifekit}"
LIFEKIT_UID="${LIFEKIT_UID:-1000}"
REPO_URL="${REPO_URL:-https://github.com/dsdevq/lifekit-stack.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
REPO_DIR="${REPO_DIR:-/srv/lifekit-stack}"

# ─── Helpers ──────────────────────────────────────────────────────────────────

say() { printf '\n\033[1;34m→ %s\033[0m\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ─── Sanity ───────────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
  echo "Must run as root (sudo)." >&2
  exit 1
fi

# ─── Base packages ────────────────────────────────────────────────────────────

say "Installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg ufw git rsync sshfs python3

# ─── lifekit user ─────────────────────────────────────────────────────────────

if ! id -u "$LIFEKIT_USER" >/dev/null 2>&1; then
  say "Creating user $LIFEKIT_USER (uid $LIFEKIT_UID)"
  useradd --create-home --uid "$LIFEKIT_UID" --shell /bin/bash "$LIFEKIT_USER"
else
  say "User $LIFEKIT_USER already exists, skipping"
fi

# ─── Docker ───────────────────────────────────────────────────────────────────

if have docker; then
  say "Docker already installed: $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
else
  say "Installing Docker via get.docker.com"
  curl -fsSL https://get.docker.com | sh
fi

systemctl enable --now docker
usermod -aG docker "$LIFEKIT_USER"

# ─── Tailscale ────────────────────────────────────────────────────────────────

if have tailscale; then
  say "Tailscale already installed: $(tailscale version | head -1)"
else
  say "Installing Tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
fi

say "Joining tailnet as $TAILSCALE_HOSTNAME"
tailscale up --authkey="$TAILSCALE_AUTH_KEY" --hostname="$TAILSCALE_HOSTNAME" --ssh

# ─── UFW: deny everything public, allow SSH only on tailscale0 ────────────────

say "Configuring UFW (Tailscale-only SSH)"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow in on tailscale0 to any port 22 proto tcp
ufw --force enable

# ─── Bind-mount directories ───────────────────────────────────────────────────

say "Creating /srv/{lifekit-stack,life,openclaw/*}"
install -d -o "$LIFEKIT_USER" -g "$LIFEKIT_USER" -m 0750 \
  "$REPO_DIR" \
  /srv/life \
  /srv/openclaw \
  /srv/openclaw/config \
  /srv/openclaw/workspace \
  /srv/openclaw/secret-key

# ─── Clone the stack repo ─────────────────────────────────────────────────────

if [[ -d "$REPO_DIR/.git" ]]; then
  say "Repo already cloned at $REPO_DIR, pulling latest"
  sudo -u "$LIFEKIT_USER" git -C "$REPO_DIR" pull --ff-only
else
  say "Cloning $REPO_URL → $REPO_DIR"
  sudo -u "$LIFEKIT_USER" git clone --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR"
fi

# ─── Done ─────────────────────────────────────────────────────────────────────

say "Host bootstrap complete."
cat <<EOF

Next steps:
  1. From your laptop, scp your real .env onto the host:
       scp .env $LIFEKIT_USER@$TAILSCALE_HOSTNAME:/srv/openclaw/config/.env
  2. Optional: copy your private workspace skills onto the host:
       rsync -a ~/.openclaw/workspace/skills/ $LIFEKIT_USER@$TAILSCALE_HOSTNAME:/srv/openclaw/workspace/skills/
  3. Run the deploy script (on the host, as $LIFEKIT_USER):
       cd $REPO_DIR && ./scripts/deploy.sh
  4. rsync your ~/.life/ data:
       rsync -a ~/.life/ $LIFEKIT_USER@$TAILSCALE_HOSTNAME:/srv/life/

EOF

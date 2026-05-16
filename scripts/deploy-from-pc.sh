#!/usr/bin/env bash
# deploy-from-pc.sh — push current branch and trigger a VPS redeploy.
#
# Run from anywhere inside the lifekit-stack repo. Does:
#   1. git push origin <current-branch>
#   2. ssh to the VPS, git pull, run scripts/deploy.sh
#
# Obsolete once the GH Actions deploy workflow (J) is unblocked — that does
# the same thing automatically on `git push`. Keep this around until then.
#
# Usage:
#   scripts/deploy-from-pc.sh           # push + deploy
#   scripts/deploy-from-pc.sh --no-push # deploy what's already on origin/main

set -euo pipefail

VPS="${VPS:-denys@100.87.201.43}"
REPO_DIR_REMOTE="${REPO_DIR_REMOTE:-/srv/lifekit-stack}"
PUSH=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-push) PUSH=0; shift ;;
    --vps) VPS="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1;34m→ %s\033[0m\n' "$*"; }

# Find repo root from anywhere inside it.
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"

if [[ "$PUSH" == "1" ]]; then
  say "git push origin $BRANCH"
  git push origin "$BRANCH"
fi

say "deploy on $VPS"
ssh "$VPS" "set -e
  cd $REPO_DIR_REMOTE
  git fetch origin main
  git reset --hard origin/main
  sudo -u lifekit bash scripts/deploy.sh || bash scripts/deploy.sh"

say "✓ deployed"

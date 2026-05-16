#!/usr/bin/env bash
# bootstrap.sh — fresh-host one-shot.
# Installs the lifekit CLI on the operator's laptop and kicks off the wizard.
# This is the entrypoint a new adopter runs.

set -euo pipefail

if ! command -v pipx >/dev/null 2>&1; then
  echo "pipx is required. Install: https://pipx.pypa.io/" >&2
  exit 1
fi

# TODO: until lifekit publishes to PyPI, install from git:
# pipx install lifekit
pipx install git+https://github.com/dsdevq/lifekit.git@main

lifekit --version

# Run the wizard. Reads no arguments — interactive prompts.
exec lifekit init-stack --target "${1:-}"

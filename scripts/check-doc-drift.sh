#!/usr/bin/env bash
# check-doc-drift.sh — assert README <-> compose service-count parity.
#
# Two parseable facts (v0 narrow scope):
#   (a) top-level `services:` key count in compose/docker-compose.yml
#       equals the row count of the service table under README.md's
#       `## Services` section.
#   (b) the top-of-compose comment's claim of "N services" matches the
#       actual top-level `services:` key count.
#
# Exits 0 on agreement, 1 with a human-readable message otherwise.
#
# Pure mechanism — no LLM. Do NOT generalize this script. See
# ~/.life/system/proposals.md -> 2026-05-20-doc-drift-automation-three-rung.

set -eu

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
compose_file="${repo_root}/compose/docker-compose.yml"
readme_file="${repo_root}/README.md"

if [ ! -r "$compose_file" ]; then
  echo "doc-drift: cannot read $compose_file" >&2
  exit 1
fi
if [ ! -r "$readme_file" ]; then
  echo "doc-drift: cannot read $readme_file" >&2
  exit 1
fi

# Top-level service names under `services:` — exactly-2-space-indented
# `name:` lines in the block following the `services:` line.
compose_services="$(
  awk '
    /^services:[[:space:]]*$/ { in_svc = 1; next }
    in_svc && /^[^[:space:]#]/ { in_svc = 0 }
    in_svc && /^  [a-zA-Z][a-zA-Z0-9_-]*:[[:space:]]*$/ {
      name = $0
      sub(/^  /, "", name)
      sub(/:.*$/, "", name)
      print name
    }
  ' "$compose_file"
)"

compose_count=0
if [ -n "$compose_services" ]; then
  compose_count=$(printf '%s\n' "$compose_services" | wc -l | tr -d ' ')
fi

# README service rows: under `## Services`, lines starting with `| \`name\` |`,
# stop at the next H2 heading.
readme_services="$(
  awk '
    /^## Services[[:space:]]*$/ { in_svc = 1; next }
    in_svc && /^## / { in_svc = 0 }
    in_svc && /^\| `[a-zA-Z][a-zA-Z0-9_-]*` \|/ {
      name = $0
      sub(/^\| `/, "", name)
      sub(/`.*$/, "", name)
      print name
    }
  ' "$readme_file"
)"

readme_count=0
if [ -n "$readme_services" ]; then
  readme_count=$(printf '%s\n' "$readme_services" | wc -l | tr -d ' ')
fi

# Comment-claimed count from the top of compose. Accept either an English
# number-word (one..ten) or a digit. First match wins.
word_to_num() {
  case "$1" in
    one) printf '1' ;;
    two) printf '2' ;;
    three) printf '3' ;;
    four) printf '4' ;;
    five) printf '5' ;;
    six) printf '6' ;;
    seven) printf '7' ;;
    eight) printf '8' ;;
    nine) printf '9' ;;
    ten) printf '10' ;;
    *) printf '' ;;
  esac
}

claimed_token="$(
  awk '
    /^[^#]/ && !/^[[:space:]]*$/ { exit }
    /^#/ {
      if (match($0, /[Dd]efines [a-z]+ services?/)) {
        s = substr($0, RSTART + length("Defines "), RLENGTH - length("Defines ") - length(" services"))
        sub(/s$/, "", s)
        print s
        exit
      }
      if (match($0, /[Dd]efines [0-9]+ services?/)) {
        s = substr($0, RSTART + length("Defines "), RLENGTH - length("Defines ") - length(" services"))
        sub(/s$/, "", s)
        print s
        exit
      }
    }
  ' "$compose_file"
)"

claimed_count=""
if [ -n "$claimed_token" ]; then
  case "$claimed_token" in
    *[!0-9]*) claimed_count="$(word_to_num "$claimed_token")" ;;
    *) claimed_count="$claimed_token" ;;
  esac
fi

fail=0

if [ -z "$claimed_count" ]; then
  echo "doc-drift: could not parse 'Defines N services' claim from the top comment of compose/docker-compose.yml" >&2
  fail=1
elif [ "$claimed_count" -ne "$compose_count" ]; then
  echo "doc-drift: compose top comment claims ${claimed_count} service(s) but ${compose_count} are defined under 'services:'" >&2
  fail=1
fi

if [ "$readme_count" -ne "$compose_count" ]; then
  compose_sorted="$(printf '%s\n' "$compose_services" | sort)"
  readme_sorted="$(printf '%s\n' "$readme_services" | sort)"
  missing_in_readme="$(comm -23 <(printf '%s\n' "$compose_sorted") <(printf '%s\n' "$readme_sorted") | paste -sd, - | sed 's/,/, /g')"
  extra_in_readme="$(comm -13 <(printf '%s\n' "$compose_sorted") <(printf '%s\n' "$readme_sorted") | paste -sd, - | sed 's/,/, /g')"
  msg="doc-drift: README lists ${readme_count} services but compose defines ${compose_count}"
  if [ -n "$missing_in_readme" ]; then
    msg="${msg}; missing from README: ${missing_in_readme}"
  fi
  if [ -n "$extra_in_readme" ]; then
    msg="${msg}; in README but not compose: ${extra_in_readme}"
  fi
  echo "$msg" >&2
  fail=1
fi

exit "$fail"

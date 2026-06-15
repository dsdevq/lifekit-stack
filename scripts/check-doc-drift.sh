#!/usr/bin/env bash
# check-doc-drift.sh — assert README <-> compose service-count parity.
#
# Two parseable facts (v0 narrow scope):
#   (a) top-level `services:` key count in compose/docker-compose.yml
#       (excluding services with `profiles:`, i.e. opt-in / build-only ones)
#       equals the row count of the service table under README.md's
#       `## Services` section.
#   (b) the top-of-compose comment's claim of "N services" matches the
#       actual top-level `services:` key count (soft check — warning only
#       when the pattern is absent).
#
# Exits 0 on agreement, 1 with a human-readable message otherwise.
#
# Pure mechanism — no LLM. Do NOT generalize this script. See
# ~/.life/system/proposals.md -> 2026-05-20-doc-drift-automation-three-rung.

set -euo pipefail

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

# Top-level service names under `services:` — exactly-2-space-indented keys
# in the block following `services:`. Services with profiles containing only
# "build-only" are excluded (devclaw-sandbox is a build artifact, not a daemon;
# on-demand services like openclaw-cli with profile "cli" ARE counted and
# should appear in the README service table).
compose_services="$(
  awk '
    /^services:[[:space:]]*$/ { in_svc = 1; next }
    in_svc && /^[^[:space:]#]/ { in_svc = 0 }
    in_svc && /^  [a-zA-Z][a-zA-Z0-9_-]*:[[:space:]]*$/ {
      if (current != "" && !build_only) print current
      name = $0
      sub(/^  /, "", name)
      sub(/:.*$/, "", name)
      current = name
      build_only = 0
    }
    in_svc && current != "" && /build-only/ { build_only = 1 }
    END { if (current != "" && !build_only) print current }
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
    one)   printf '1' ;;
    two)   printf '2' ;;
    three) printf '3' ;;
    four)  printf '4' ;;
    five)  printf '5' ;;
    six)   printf '6' ;;
    seven) printf '7' ;;
    eight) printf '8' ;;
    nine)  printf '9' ;;
    ten)   printf '10' ;;
    *)     printf '' ;;
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
    *)        claimed_count="$claimed_token" ;;
  esac
fi

fail=0

if [ -z "$claimed_count" ]; then
  # Soft check: warn when the compose comment doesn't claim a count, but don't
  # fail — the authoritative source of truth is the services block itself.
  echo "doc-drift: note: compose top comment has no 'Defines N services' claim (not enforced)" >&2
elif [ "$claimed_count" -ne "$compose_count" ]; then
  echo "doc-drift: compose top comment claims ${claimed_count} service(s) but ${compose_count} are defined under 'services:' (excluding profiled services)" >&2
  fail=1
fi

if [ "$readme_count" -ne "$compose_count" ]; then
  compose_sorted="$(printf '%s\n' "$compose_services" | sort)"
  readme_sorted="$(printf '%s\n' "$readme_services" | sort)"
  missing_in_readme="$(comm -23 <(printf '%s\n' "$compose_sorted") <(printf '%s\n' "$readme_sorted") | paste -sd, - | sed 's/,/, /g')"
  extra_in_readme="$(comm -13 <(printf '%s\n' "$compose_sorted") <(printf '%s\n' "$readme_sorted") | paste -sd, - | sed 's/,/, /g')"
  msg="doc-drift: README lists ${readme_count} services but compose defines ${compose_count} (excluding profiled services)"
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

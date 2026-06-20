#!/bin/sh
# memory-vault-audit.sh — weekly vault audit + safe auto-fix.
#   0. auto-fix  : apply SAFE mechanical fixes (frontmatter/YAML), record them
#   1. Pass 1    : openclaw wiki compile (typed-layer reports/digest/claims)
#   2. Pass 2    : vault-lint.py (structural contract-lint — the RESIDUAL)
#   3. report    : audits/<date>-vault-audit.md (auto-fixed + remaining), log.md
#   4. summary   : one Telegram line — what was fixed + the top remaining findings
# Deterministic (no model-no-op risk). NEVER deletes. Judgment-needed findings
# are reported, not fixed. Delivery-required guard: exits non-zero unless a fresh
# dated report was written, so a silent no-op registers as a cron failure.
set -eu

VAULT=/home/node/.openclaw/wiki/main
DIR=/home/node/.openclaw/workspace/memory-audit
WORK=/tmp/memory-audit
mkdir -p "$WORK"
DATE=$(date -u +%F)
REPORT="$VAULT/audits/$DATE-vault-audit.md"
mkdir -p "$VAULT/audits"

# 0. Safe auto-fix (mutates the vault; records what changed)
if ! python3 "$DIR/vault-autofix.py" "$VAULT" "$DATE" > "$WORK/autofix.json" 2>"$WORK/autofix-err.txt"; then
  echo "AUDIT FAIL: vault-autofix errored: $(head -1 "$WORK/autofix-err.txt" 2>/dev/null)"; exit 1
fi

# 1. Pass 1 — plugin layer
if ! openclaw wiki compile >/dev/null 2>"$WORK/compile-err.txt"; then
  echo "AUDIT FAIL: openclaw wiki compile errored: $(tail -1 "$WORK/compile-err.txt" 2>/dev/null)"; exit 1
fi

# 2. Pass 2 — residual structural contract-lint
if ! python3 "$DIR/vault-lint.py" "$VAULT" > "$WORK/lint-out.json" 2>"$WORK/lint-err.txt"; then
  echo "AUDIT FAIL: vault-lint errored: $(head -1 "$WORK/lint-err.txt" 2>/dev/null)"; exit 1
fi

# 3. Assemble the dated report (rm first so a re-run replaces a prior-owned file)
rm -f "$REPORT" 2>/dev/null || true
if ! python3 "$DIR/gen-report.py" "$VAULT" "$WORK/lint-out.json" "$DATE" "$WORK/autofix.json" > "$REPORT" 2>"$WORK/gen-err.txt"; then
  echo "AUDIT FAIL: gen-report errored: $(head -1 "$WORK/gen-err.txt" 2>/dev/null)"; exit 1
fi

# delivery-required guard
if [ ! -s "$REPORT" ] || ! head -12 "$REPORT" | grep -q "Vault Audit $DATE"; then
  echo "AUDIT FAIL: no fresh report written for $DATE"; exit 1
fi

CLAIMS=$(python3 -c "import json;print(json.load(open('$VAULT/.openclaw-wiki/cache/agent-digest.json')).get('claimCount',0))")

# log.md entry + Telegram summary (committed + pushed by the host memory-sync.timer)
SUMMARY=$(python3 - "$WORK/lint-out.json" "$WORK/autofix.json" "$DATE" "$CLAIMS" <<'PY'
import json, sys, collections, os
lint = json.load(open(sys.argv[1]))
fixes = json.load(open(sys.argv[2])) if os.path.exists(sys.argv[2]) else []
date, claims = sys.argv[3], sys.argv[4]
sev = collections.Counter(f["severity"] for f in lint)
rules = collections.Counter(f["rule"] for f in lint if f["severity"] in ("high", "medium"))
top = ", ".join(f"{k} {v}" for k, v in rules.most_common(3)) or "none"
fixc = collections.Counter(x["action"] for x in fixes)
fixstr = ", ".join(f"{k} {v}" for k, v in fixc.most_common()) or "none"
print(f"Vault audit {date}: auto-fixed {len(fixes)} ({fixstr}). "
      f"{len(lint)} findings remain ({sev.get('high',0)} high / {sev.get('medium',0)} med) - top: {top}. "
      f"Claims {claims}. Report: audits/{date}-vault-audit.md")
PY
)
printf '\n## [%s] audit | %s\n' "$DATE" "$SUMMARY" >> "$VAULT/log.md"

echo "$SUMMARY"

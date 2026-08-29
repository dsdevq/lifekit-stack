#!/usr/bin/env python3
"""Assemble the dated vault-audit report from Pass-1 (plugin digest/reports)
and Pass-2 (contract-lint JSON). Lives in lifekit-stack; called by the weekly cron."""

import json
import os
import sys
import datetime
import collections
import re

VAULT = sys.argv[1] if len(sys.argv) > 1 else "/srv/memory"
LINT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/lint-out.json"
DATE = sys.argv[3] if len(sys.argv) > 3 else datetime.date.today().isoformat()
FIXES = sys.argv[4] if len(sys.argv) > 4 else None
ROTATED = sys.argv[5] if len(sys.argv) > 5 else None


def load_actions(path):
    if not (path and os.path.exists(path)):
        return []
    try:
        return json.load(open(path))
    except Exception:
        return []


autofixes = load_actions(FIXES)
rotations = load_actions(ROTATED)

digest = json.load(open(f"{VAULT}/.openclaw-wiki/cache/agent-digest.json"))
ch = digest.get("claimHealth", {})
fr = ch.get("freshness", {})
pc = digest.get("pageCounts", {})


def report_line(name):
    p = f"{VAULT}/reports/{name}.md"
    if not os.path.exists(p):
        return "n/a"
    body = open(p).read()
    m = re.search(r":start -->(.*?)<!--", body, re.S)
    txt = (m.group(1) if m else body).strip().splitlines()
    return " ".join(ln.strip("- ").strip() for ln in txt if ln.strip())[:240] or "clean"


lint = json.load(open(LINT))
order = {"high": 0, "medium": 1, "low": 2, "info": 3}
by_rule = collections.OrderedDict()
for f in sorted(lint, key=lambda x: order.get(x["severity"], 4)):
    by_rule.setdefault(f["rule"], []).append(f)
sev = collections.Counter(f["severity"] for f in lint)

RULEDOC = {
    "malformed-frontmatter": "Frontmatter is not valid YAML (quote scalars with embedded colons).",
    "stale-status": "Active STATUS.md untouched >14d (rotation Rule 3) — refresh or conclude.",
    "canvas-drift": "Architecture canvas older than the pages it depicts — re-verify the diagram.",
    "missing-frontmatter": "Content page has no YAML frontmatter (add name/summary/updatedAt/status).",
    "legacy-updatedAt-field": "Uses last_updated: — rename to updatedAt:.",
    "broken-link": "[[link]] referenced >2x but the page does not exist — write it or de-link.",
    "workspace-memory-ref": "Cites an OpenClaw workspace-memory slug that is not a vault page (systemic; promote or de-link).",
    "deprecated-path": "Forward-looking doc references ~/.life or /srv/life (canonical: ~/memory / /srv/memory).",
    "orphan": "No inbound [[links]] — link from an INDEX or related page.",
    "project-triad": "projects/<name>/ missing a required plan/log/journal file.",
    "status-field-name": "STATUS.md uses updated: — align to updatedAt:.",
}

out = []
out.append("---")
out.append("pageType: report")
out.append(f"id: report.vault-audit.{DATE}")
out.append(f"title: Vault Audit {DATE}")
out.append("status: active")
out.append(f"updatedAt: {DATE}T00:00:00Z")
out.append("tags: [audit, vault, generated]")
out.append("---\n")
out.append(f"# Vault Audit — {DATE}\n")
out.append(
    "Two passes: plugin compile (typed layer) + contract-lint (structural layer). Each check maps to a "
    "rule in [[README]]. Safe mechanical fixes and Rule-3 mechanical rotations are auto-applied; "
    "judgment-needed findings are left below for you. Deletion happens ONLY inside the contract's "
    "rotation classes — git history is the archive.\n"
)

# Auto-fix section (what the agent handled this run)
if autofixes:
    import collections as _c

    fc = _c.Counter(x["action"] for x in autofixes)
    out.append(f"## Auto-fixed this run ({len(autofixes)})\n")
    out.append(" · ".join(f"{k}: {v}" for k, v in fc.most_common()) + "\n")
    for x in autofixes[:40]:
        out.append(f"- `{x['action']}` {x['path']} — {x['detail']}")
    if len(autofixes) > 40:
        out.append(f"- … +{len(autofixes) - 40} more")
    out.append("")
else:
    out.append("## Auto-fixed this run (0)\n\nNothing mechanically fixable this run.\n")

# Rotation section (README Rule 3 mechanical classes, applied by vault-rotate.py)
if rotations:
    rc = collections.Counter(x["action"] for x in rotations)
    out.append(f"## Rotated this run ({len(rotations)})\n")
    out.append(" · ".join(f"{k}: {v}" for k, v in rc.most_common()) + "\n")
    for x in rotations[:40]:
        out.append(f"- `{x['action']}` {x['path']} — {x['detail']}")
    if len(rotations) > 40:
        out.append(f"- … +{len(rotations) - 40} more")
    out.append("")
else:
    out.append("## Rotated this run (0)\n\nNo rotation class hit its TTL this run.\n")

out.append("## Pass 1 — plugin layer (`openclaw wiki compile`)\n")
out.append(
    f"- Typed pages: source {pc.get('source', 0)}, entity {pc.get('entity', 0)}, "
    f"concept {pc.get('concept', 0)}, synthesis {pc.get('synthesis', 0)}, report {pc.get('report', 0)}"
)
out.append(
    f"- Claims: **{digest.get('claimCount', 0)}** — fresh {fr.get('fresh', 0)} / aging {fr.get('aging', 0)} "
    f"/ stale {fr.get('stale', 0)} / unknown {fr.get('unknown', 0)}; "
    f"missing-evidence {ch.get('missingEvidence', 0)}; contested {ch.get('contested', 0)}; "
    f"low-confidence {ch.get('lowConfidence', 0)}"
)
out.append("")
out.append("| Report | Result |")
out.append("|---|---|")
for r in [
    "stale-pages",
    "provenance-coverage",
    "claim-health",
    "contradictions",
    "low-confidence",
    "open-questions",
    "privacy-review",
    "relationship-graph",
    "person-agent-directory",
]:
    out.append(f"| {r} | {report_line(r)} |")
out.append("")

out.append("## Pass 2 — structural contract-lint\n")
out.append(
    f"Findings: **{len(lint)}** — high {sev.get('high', 0)}, medium {sev.get('medium', 0)}, "
    f"low {sev.get('low', 0)}, info {sev.get('info', 0)}.\n"
)
for rule, items in by_rule.items():
    out.append(f"### {rule} ({len(items)}) — {RULEDOC.get(rule, '')}")
    for f in items[:30]:
        out.append(f"- `[{f['severity']}]` {f['path']} — {f['detail']}")
    if len(items) > 30:
        out.append(f"- … +{len(items) - 30} more")
    out.append("")

out.append("## Triage notes\n")
out.append(
    "- **system/ frontmatter backlog** (most of `missing-frontmatter`): the `system/` folder predates "
    "the frontmatter convention. Batch-add `name/summary/updatedAt/status`."
)
out.append(
    "- **workspace-memory-ref** (info): `system/proposals.md` cites Kit workspace-memory slugs "
    "(`feedback-*`, `user-*`) as `[[links]]`. Decide per-slug: promote durable rules into `concepts/`, "
    "or drop the brackets. Not a weekly action item."
)
out.append(
    "- **malformed-frontmatter** (high): quote the `summary:`/`next:` scalars that contain colons."
)
print("\n".join(out))

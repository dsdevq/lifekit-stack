#!/usr/bin/env python3
"""Contract-lint (Pass 2) for the ~/memory vault — the structural-layer checks
the memory-wiki plugin cannot do. Read-only; emits JSON findings to stdout.
Dependency-free (no PyYAML) so it runs host- or container-side.

Exemptions are IMMUTABLE-only: sources/ (clipped evidence) and audits/
(generated reports), plus the dated per-project scaffolds proposals/tasks/runs.
Those are content no fix may touch, so a finding on them has no consumer.
Nothing is exempted merely for being unowned — unowned is the thing to surface.
Every exemption is validated against the README structure allowlist and its size
is reported weekly, so one cannot go unnoticed. recon/conversation/findings/etc.
are OPTIONAL artifacts not part of the contract.
"""

import os
import re
import sys
import json
import glob
import datetime
import subprocess

VAULT = sys.argv[1] if len(sys.argv) > 1 else "/srv/memory"
TODAY = datetime.date.today()
findings = []


def add(sev, rule, path, detail):
    findings.append(
        {
            "severity": sev,
            "rule": rule,
            "path": os.path.relpath(path, VAULT) if os.path.isabs(str(path)) else path,
            "detail": detail,
        }
    )


def fm(path):
    s = open(path, encoding="utf-8", errors="replace").read()
    if not s.startswith("---"):
        return None, s
    parts = s.split("---", 2)
    if len(parts) < 3:
        return None, s
    return parts[1], parts[2]


ALL = glob.glob(f"{VAULT}/**/*.md", recursive=True)


def rel(f):
    return os.path.relpath(f, VAULT)


# IMMUTABLE = content no fix may touch, so weekly findings on it have no
# consumer. This list is deliberately short. A path does NOT belong here for
# being curated, protected, or merely unowned: `journal/` sat in this tuple for
# three months as "verbatim evidence" while actually holding unowned agent
# narrative, and the exemption made it invisible to the only scheduled linter.
# Removed 2026-08-21 along with `incidents/` and `goal-archive/`, which named
# directories the vault does not have.
IMMUTABLE_PREFIX = ("sources/", "audits/")
# Dated per-project scaffolds — contract-optional dirs (projects/INDEX.md), so
# they may legitimately be absent from disk without the rule being stale.
IMMUTABLE_SEG = ("/proposals/", "/tasks/", "/runs/")
OPT_BASENAMES = {
    "recon.md",
    "conversation.md",
    "findings.md",
    "decisions.md",
    "settings.yaml",
}
INDEXISH = {
    "INDEX.md",
    "index.md",
    "README.md",
    "AGENTS.md",
    "WIKI.md",
    "PLAN.md",
    "MEMORY.md",
}
ROOT_RUNTIME = {"inbox.md", "log.md"}


def is_frozen(f):
    r = rel(f)
    return r.startswith(IMMUTABLE_PREFIX) or any(
        seg in "/" + r for seg in IMMUTABLE_SEG
    )


def is_optional(f):
    return os.path.basename(f) in OPT_BASENAMES


def skip_content(f):
    return is_frozen(f) or is_optional(f) or os.path.basename(f) in INDEXISH


def read_allowlist():
    """Top-level entries from the README ```vault-structure block (the freeze)."""
    try:
        txt = open(os.path.join(VAULT, "README.md"), encoding="utf-8").read()
    except OSError:
        return None
    m = re.search(r"```vault-structure\n(.*?)```", txt, re.S)
    if not m:
        return None
    return {
        ln.strip().rstrip("/")
        for ln in m.group(1).splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }


# 0) exemption hygiene — an exemption must name a path the contract still
# recognizes, and must state its own size every week. An exemption nobody can
# see is how `journal/` went three months without a single finding.
_allow = read_allowlist()
if _allow is not None:
    for pref in IMMUTABLE_PREFIX:
        if pref.rstrip("/") not in _allow:
            add(
                "medium",
                "stale-exemption",
                pref,
                f"'{pref}' is exempt from lint but is not in the README structure "
                "allowlist — drop the exemption with the directory",
            )

_exempt = [f for f in ALL if is_frozen(f)]
if _exempt:
    _by = {}
    for f in _exempt:
        r = rel(f)
        key = next(
            (p for p in IMMUTABLE_PREFIX if r.startswith(p)),
            next((s2.strip("/") + "/" for s2 in IMMUTABLE_SEG if s2 in "/" + r), "?"),
        )
        _by[key] = _by.get(key, 0) + 1
    add(
        "info",
        "exemption-census",
        ".",
        "lint exemptions this run: "
        + ", ".join(f"{k} {v} file(s)" for k, v in sorted(_by.items()))
        + " — immutable content only; anything else must be linted",
    )


# dependency-free YAML sanity: a top-level "key: value" whose unquoted value
# contains a colon-space breaks YAML ("mapping values are not allowed here").
KEYLINE = re.compile(r"^([A-Za-z0-9_-]+):\s+(\S.*)$")


def yaml_offenders(head):
    bad = []
    for ln in head.splitlines():
        if ln[:1] in (" ", "\t", "-") or not ln.strip():
            continue
        m = KEYLINE.match(ln)
        if not m:
            continue
        val = m.group(2).strip()
        if val[:1] in ("'", '"', "|", ">", "[", "{", "&", "*", "#"):
            continue
        if re.search(r"\S:\s", val):  # a second colon-space in an unquoted scalar
            bad.append(m.group(1))
    return bad


# 1) project-triad completeness
for proj in sorted(glob.glob(f"{VAULT}/projects/*")):
    if not os.path.isdir(proj):
        continue
    for req in ("plan.md", "log.md", "journal.md"):
        if not os.path.exists(os.path.join(proj, req)):
            add(
                "high",
                "project-triad",
                os.path.join(proj, req),
                f"missing required {req}",
            )

# 2) legacy last_updated field + 3) malformed frontmatter (heuristic)
for f in ALL:
    head, _ = fm(f)
    if head is None:
        continue
    if not is_frozen(f) and re.search(r"^last_updated:", head, re.M):
        add(
            "medium",
            "legacy-updatedAt-field",
            f,
            "uses last_updated: — rename to updatedAt:",
        )
    # Frozen surfaces are verbatim evidence: auto-fix is forbidden to touch them,
    # so flagging them weekly is permanent noise with no possible consumer.
    if is_frozen(f):
        continue
    offenders = yaml_offenders(head)
    if offenders:
        add(
            "high",
            "malformed-frontmatter",
            f,
            f"unquoted scalar with embedded colon in: {', '.join(offenders)} (quote the value)",
        )

# 4) STATUS.md field-name alignment
for f in glob.glob(f"{VAULT}/projects/*/STATUS.md"):
    head, _ = fm(f)
    if (
        head
        and re.search(r"^updated:", head, re.M)
        and not re.search(r"^updatedAt:", head, re.M)
    ):
        add(
            "low",
            "status-field-name",
            f,
            "STATUS.md uses updated: — align to updatedAt:",
        )

# 4b) stale STATUS.md — active but untouched past the 14d rotation trigger
UPDATED_AT = re.compile(r"^updatedAt:\s*[\"']?(\d{4}-\d{2}-\d{2})", re.M)
STATUS_FIELD = re.compile(r"^status:\s*[\"']?(\S+)", re.M)


def fm_date(head):
    m = UPDATED_AT.search(head or "")
    if not m:
        return None
    try:
        return datetime.date.fromisoformat(m.group(1))
    except ValueError:
        return None


for f in glob.glob(f"{VAULT}/projects/*/STATUS.md"):
    head, _ = fm(f)
    st = STATUS_FIELD.search(head or "")
    if st and st.group(1) in ("archived", "concluded"):
        continue
    d = fm_date(head)
    if d and (TODAY - d).days > 14:
        add(
            "medium",
            "stale-status",
            f,
            f"active STATUS.md untouched for {(TODAY - d).days}d (>14d rotation "
            "trigger) — refresh it or conclude the project into plan.md",
        )


# 4c) canvas drift — an architecture canvas older than the pages it depicts
def canvas_date(f):
    try:
        out = subprocess.run(
            ["git", "-C", VAULT, "log", "-1", "--format=%cs", "--", rel(f)],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        if out:
            return datetime.date.fromisoformat(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return datetime.date.fromtimestamp(os.path.getmtime(f))


def sibling_page_date(dirpath):
    dates = []
    for name in ("plan.md", "architecture.md"):
        p = os.path.join(dirpath, name)
        if os.path.exists(p):
            d = fm_date(fm(p)[0])
            if d:
                dates.append(d)
    return max(dates) if dates else None


for cv in sorted(glob.glob(f"{VAULT}/projects/*/*.canvas")):
    pages = sibling_page_date(os.path.dirname(cv))
    cvd = canvas_date(cv)
    if pages and (pages - cvd).days > 14:
        add(
            "medium",
            "canvas-drift",
            cv,
            f"project pages moved {(pages - cvd).days}d past this canvas — "
            "re-verify the diagram against plan.md/architecture.md",
        )
for cv in sorted(glob.glob(f"{VAULT}/system/*.canvas")):
    newest = [
        d
        for p in sorted(glob.glob(f"{VAULT}/projects/*"))
        if glob.glob(f"{p}/*.canvas")
        for d in [sibling_page_date(p)]
        if d
    ]
    cvd = canvas_date(cv)
    if newest and (max(newest) - cvd).days > 14:
        add(
            "medium",
            "canvas-drift",
            cv,
            f"project pages moved {(max(newest) - cvd).days}d past this map — "
            "re-verify the overview canvas",
        )

# 5) deprecated path forms in FORWARD-LOOKING content
for f in ALL:
    r = rel(f)
    if not r.startswith(("domains/", "system/", "projects/")):
        continue
    if skip_content(f) or os.path.basename(f) in ("log.md", "journal.md"):
        continue
    body = open(f, encoding="utf-8", errors="replace").read()
    for m in sorted(set(re.findall(r"(~/\.life/|/srv/life/)", body))):
        add(
            "low",
            "deprecated-path",
            f,
            f"references {m} in a forward-looking doc (canonical ~/memory/ | /srv/memory/)",
        )

# 6) content pages missing frontmatter
for sub in ("domains", "system", "concepts", "entities", "syntheses"):
    for f in glob.glob(f"{VAULT}/{sub}/**/*.md", recursive=True):
        if skip_content(f):
            continue
        head, _ = fm(f)
        if head is None:
            add(
                "medium",
                "missing-frontmatter",
                f,
                "content page has no YAML frontmatter",
            )
for f in (
    glob.glob(f"{VAULT}/projects/*/plan.md")
    + glob.glob(f"{VAULT}/projects/*/log.md")
    + glob.glob(f"{VAULT}/projects/*/journal.md")
):
    head, _ = fm(f)
    if head is None:
        add("medium", "missing-frontmatter", f, "triad file has no YAML frontmatter")

# 7) link graph: only consider links AUTHORED in non-frozen wiki pages
pages = {}
for f in ALL:
    pages.setdefault(os.path.splitext(os.path.basename(f))[0], f)
# Canvases and Bases views are legitimate wikilink targets but are not pages:
# they resolve links without being orphan-checked themselves. Without this a
# live [[system/lifekit-map.canvas]] counts as a broken link every week.
NON_PAGE_TARGETS = {
    os.path.splitext(os.path.basename(f))[0]
    for ext in ("canvas", "base")
    for f in glob.glob(f"{VAULT}/**/*.{ext}", recursive=True)
}
inbound = {n: 0 for n in pages}
broken = {}
linkre = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
for f in ALL:
    if is_frozen(f):
        continue
    self_base = os.path.splitext(os.path.basename(f))[0]
    for tgt in linkre.findall(open(f, encoding="utf-8", errors="replace").read()):
        base = os.path.splitext(os.path.basename(tgt.strip()))[0]
        if base in inbound:
            inbound[base] += 1
        elif base in NON_PAGE_TARGETS:
            continue  # live canvas/base target
        elif base != self_base:
            broken[base] = broken.get(base, 0) + 1

DOC_LITERAL = {"name", "summary", "tags", "path/file", "page-name"}
WS = re.compile(
    r"^(feedback[-_]|user[-_]|denys[-_]|kit[-_]|pro[-_]subscription|no[-_]overengineering|life-extension-plan|user-north-star)"
)
for base, cnt in sorted(broken.items(), key=lambda x: -x[1]):
    if cnt <= 2 or base in DOC_LITERAL:
        continue
    if WS.search(base):
        add(
            "info",
            "workspace-memory-ref",
            ".",
            f"[[{base}]] ({cnt}x) cites an OpenClaw workspace-memory slug with no vault page",
        )
    else:
        add(
            "medium",
            "broken-link",
            ".",
            f"[[{base}]] referenced {cnt}x in wiki pages but no page exists",
        )

for name, f in pages.items():
    r = rel(f)
    if skip_content(f) or is_frozen(f):
        continue
    if r.startswith(("reports/", "goals/", "state/", "scout/")):
        continue
    if os.path.basename(f) in ROOT_RUNTIME and os.path.dirname(r) == "":
        continue
    if os.path.basename(f) in ("log.md", "journal.md", "STATUS.md"):
        continue
    if inbound.get(name, 0) == 0:
        add(
            "low",
            "orphan",
            f,
            "no inbound [[links]] — link from an INDEX or related page",
        )

print(json.dumps(findings, indent=1))

#!/usr/bin/env python3
"""Contract-lint (Pass 2) for the ~/memory vault — the structural-layer checks
the memory-wiki plugin cannot do. Read-only; emits JSON findings to stdout.
Dependency-free (no PyYAML) so it runs host- or container-side.

Honors the README contract: sources/journal/audits/incidents/goal-archive and
dated proposals/tasks/runs are FROZEN (verbatim historical evidence); recon/
conversation/findings/etc. are OPTIONAL artifacts not part of the contract.
"""
import os, re, sys, json, glob

VAULT = sys.argv[1] if len(sys.argv) > 1 else "/srv/memory"
findings = []


def add(sev, rule, path, detail):
    findings.append({"severity": sev, "rule": rule,
                     "path": os.path.relpath(path, VAULT) if os.path.isabs(str(path)) else path,
                     "detail": detail})


def fm(path):
    s = open(path, encoding="utf-8", errors="replace").read()
    if not s.startswith("---"):
        return None, s
    parts = s.split("---", 2)
    if len(parts) < 3:
        return None, s
    return parts[1], parts[2]


ALL = glob.glob(f"{VAULT}/**/*.md", recursive=True)
def rel(f): return os.path.relpath(f, VAULT)

FROZEN_PREFIX = ("sources/", "journal/", "audits/", "incidents/", "goal-archive/")
FROZEN_SEG = ("/proposals/", "/proposals-approved/", "/tasks/", "/runs/")
OPT_BASENAMES = {"recon.md", "conversation.md", "findings.md", "decisions.md", "settings.yaml"}
INDEXISH = {"INDEX.md", "index.md", "README.md", "AGENTS.md", "WIKI.md", "PLAN.md", "MEMORY.md"}
ROOT_RUNTIME = {"inbox.md", "log.md"}


def is_frozen(f):
    r = rel(f)
    return r.startswith(FROZEN_PREFIX) or any(seg in "/" + r for seg in FROZEN_SEG)
def is_optional(f): return os.path.basename(f) in OPT_BASENAMES
def skip_content(f): return is_frozen(f) or is_optional(f) or os.path.basename(f) in INDEXISH


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
            add("high", "project-triad", os.path.join(proj, req), f"missing required {req}")

# 2) legacy last_updated field + 3) malformed frontmatter (heuristic)
for f in ALL:
    head, _ = fm(f)
    if head is None:
        continue
    if not is_frozen(f) and re.search(r"^last_updated:", head, re.M):
        add("medium", "legacy-updatedAt-field", f, "uses last_updated: — rename to updatedAt:")
    offenders = yaml_offenders(head)
    if offenders:
        add("high", "malformed-frontmatter", f,
            f"unquoted scalar with embedded colon in: {', '.join(offenders)} (quote the value)")

# 4) STATUS.md field-name alignment
for f in glob.glob(f"{VAULT}/projects/*/STATUS.md"):
    head, _ = fm(f)
    if head and re.search(r"^updated:", head, re.M) and not re.search(r"^updatedAt:", head, re.M):
        add("low", "status-field-name", f, "STATUS.md uses updated: — align to updatedAt:")

# 5) deprecated path forms in FORWARD-LOOKING content
for f in ALL:
    r = rel(f)
    if not r.startswith(("domains/", "system/", "projects/")):
        continue
    if skip_content(f) or os.path.basename(f) in ("log.md", "journal.md"):
        continue
    body = open(f, encoding="utf-8", errors="replace").read()
    for m in sorted(set(re.findall(r"(~/\.life/|/srv/life/)", body))):
        add("low", "deprecated-path", f, f"references {m} in a forward-looking doc (canonical ~/memory/ | /srv/memory/)")

# 6) content pages missing frontmatter
for sub in ("domains", "system", "concepts", "entities", "syntheses"):
    for f in glob.glob(f"{VAULT}/{sub}/**/*.md", recursive=True):
        if skip_content(f):
            continue
        head, _ = fm(f)
        if head is None:
            add("medium", "missing-frontmatter", f, "content page has no YAML frontmatter")
for f in (glob.glob(f"{VAULT}/projects/*/plan.md") + glob.glob(f"{VAULT}/projects/*/log.md")
          + glob.glob(f"{VAULT}/projects/*/journal.md")):
    head, _ = fm(f)
    if head is None:
        add("medium", "missing-frontmatter", f, "triad file has no YAML frontmatter")

# 7) link graph: only consider links AUTHORED in non-frozen wiki pages
pages = {}
for f in ALL:
    pages.setdefault(os.path.splitext(os.path.basename(f))[0], f)
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
        elif base != self_base:
            broken[base] = broken.get(base, 0) + 1

DOC_LITERAL = {"name", "summary", "tags", "path/file", "page-name"}
WS = re.compile(r"^(feedback[-_]|user[-_]|denys[-_]|kit[-_]|pro[-_]subscription|no[-_]overengineering|life-extension-plan|user-north-star)")
for base, cnt in sorted(broken.items(), key=lambda x: -x[1]):
    if cnt <= 2 or base in DOC_LITERAL:
        continue
    if WS.search(base):
        add("info", "workspace-memory-ref", ".", f"[[{base}]] ({cnt}x) cites an OpenClaw workspace-memory slug with no vault page")
    else:
        add("medium", "broken-link", ".", f"[[{base}]] referenced {cnt}x in wiki pages but no page exists")

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
        add("low", "orphan", f, "no inbound [[links]] — link from an INDEX or related page")

print(json.dumps(findings, indent=1))

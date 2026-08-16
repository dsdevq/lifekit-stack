#!/usr/bin/env python3
"""Tests for vault-rotate.py (README Rule 3 mechanical rotation) and the new
vault-lint.py rules (frozen-noise suppression, stale-status, canvas-drift).
stdlib-only; each test builds a fixture vault in a tmpdir and runs the real
scripts via subprocess, exactly as the weekly cron does."""

import os
import json
import shutil
import datetime
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROTATE = os.path.join(HERE, "vault-rotate.py")
LINT = os.path.join(HERE, "vault-lint.py")

TODAY = datetime.date(2026, 8, 16)
T = TODAY.isoformat()


def days_ago(n):
    return (TODAY - datetime.timedelta(days=n)).isoformat()


def run(script, vault, *extra):
    p = subprocess.run(
        ["python3", script, vault, *extra],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def write(vault, relpath, content):
    path = os.path.join(vault, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content)
    return path


class Fixture(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix="vault-fixture-")
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)

    def rotate(self, *extra):
        return run(ROTATE, self.vault, T, *extra)

    def read(self, relpath):
        return open(os.path.join(self.vault, relpath)).read()

    def exists(self, relpath):
        return os.path.exists(os.path.join(self.vault, relpath))


class TestAuditsKeepOne(Fixture):
    def test_deletes_all_but_todays_report(self):
        write(self.vault, f"audits/{days_ago(7)}-vault-audit.md", "# old\n")
        write(self.vault, f"audits/{days_ago(14)}-vault-audit.md", "# older\n")
        write(self.vault, f"audits/{T}-vault-audit.md", "# today\n")
        acts = self.rotate()
        self.assertEqual(2, sum(1 for a in acts if a["action"] == "delete-audit"), acts)
        self.assertFalse(self.exists(f"audits/{days_ago(7)}-vault-audit.md"))
        self.assertTrue(self.exists(f"audits/{T}-vault-audit.md"))


class TestLogCompaction(Fixture):
    LOG = (
        "---\nname: log\nupdatedAt: 2026-08-16\n---\n\n# log\n\n"
        f"## [{days_ago(120)}] lint | ancient entry one\n\nbody line a\n\n"
        f"## [{days_ago(100)}] audit | ancient entry two\n\nbody line b\n\n"
        f"## [{days_ago(5)}] audit | recent entry stays\n\nbody line c\n"
    )

    def test_old_entries_collapse_into_compacted_history(self):
        write(self.vault, "projects/demo/log.md", self.LOG)
        acts = self.rotate()
        self.assertTrue(any(a["action"] == "compact-log" for a in acts), acts)
        out = self.read("projects/demo/log.md")
        self.assertIn("## Compacted history", out)
        self.assertIn(f"- **{days_ago(120)}** — lint | ancient entry one", out)
        self.assertNotIn(f"## [{days_ago(120)}]", out)
        self.assertNotIn("body line a", out)
        self.assertIn(f"## [{days_ago(5)}] audit | recent entry stays", out)
        self.assertIn("body line c", out)

    def test_appends_into_existing_compacted_block(self):
        existing = (
            "# log\n\n## Compacted history (through 2026-01-01)\n\n"
            "- **2026-01-01** — pre-existing line\n\n"
            f"## [{days_ago(120)}] lint | ancient\n\nbody\n\n"
            f"## [{days_ago(5)}] audit | recent\n"
        )
        write(self.vault, "log.md", existing)
        self.rotate()
        out = self.read("log.md")
        self.assertEqual(1, out.count("## Compacted history"))
        self.assertIn("- **2026-01-01** — pre-existing line", out)
        self.assertIn(f"- **{days_ago(120)}** — lint | ancient", out)

    def test_fresh_log_untouched(self):
        fresh = f"# log\n\n## [{days_ago(5)}] audit | recent\n"
        p = write(self.vault, "projects/demo/log.md", fresh)
        before = open(p).read()
        acts = self.rotate()
        self.assertFalse(any(a["action"] == "compact-log" for a in acts))
        self.assertEqual(before, open(p).read())


class TestUncitedSources(Fixture):
    def claims(self, *cited_stems):
        rows = [
            json.dumps(
                {
                    "sourceIds": [f"source.{s}"],
                    "evidence": [{"sourceId": f"source.{s}"}],
                }
            )
            for s in cited_stems
        ]
        write(self.vault, ".openclaw-wiki/cache/claims.jsonl", "\n".join(rows) + "\n")

    def test_old_uncited_source_deleted(self):
        old = f"{days_ago(90)}-dead-clip"
        write(self.vault, f"sources/{old}.md", "---\npageType: source\n---\nx\n")
        self.claims("some-other-source")
        acts = self.rotate()
        self.assertTrue(any(a["action"] == "delete-source" for a in acts), acts)
        self.assertFalse(self.exists(f"sources/{old}.md"))

    def test_cited_or_recent_or_referenced_sources_survive(self):
        cited = f"{days_ago(90)}-cited-clip"
        recent = f"{days_ago(10)}-recent-clip"
        linked = f"{days_ago(90)}-linked-clip"
        for s in (cited, recent, linked):
            write(self.vault, f"sources/{s}.md", "---\npageType: source\n---\nx\n")
        write(self.vault, "concepts/uses.md", f"see [[{linked}]] for detail\n")
        self.claims(cited)
        self.rotate()
        for s in (cited, recent, linked):
            self.assertTrue(self.exists(f"sources/{s}.md"), s)

    def test_missing_claims_cache_skips_class(self):
        old = f"{days_ago(90)}-dead-clip"
        write(self.vault, f"sources/{old}.md", "x\n")
        acts = self.rotate()
        self.assertTrue(any(a["action"] == "skip-class" for a in acts), acts)
        self.assertTrue(self.exists(f"sources/{old}.md"))


class TestProposalsGradedOrDie(Fixture):
    LEDGER = (
        "---\nname: proposals\n---\n\n# System Improvement Proposals\n\n"
        "## Open proposals\n\n"
        f"### {days_ago(45)}-stale-idea\n- **Status:** new\n- **Lens:** system\n"
        "- **What + why:** old ungraded thing.\n\n"
        f"### {days_ago(45)}-being-graded\n- **Status:** evaluating\n"
        "- **What + why:** old but in review.\n\n"
        f"### {days_ago(5)}-fresh-idea\n- **Status:** new\n"
        "- **What + why:** new thing.\n\n"
        "## Decisions record\n\n"
        "One line per terminal proposal.\n\n"
        "- 2026-08-06 prior-decision -> adopted (kept)\n"
    )

    def test_only_old_ungraded_entry_expires(self):
        write(self.vault, "system/proposals.md", self.LEDGER)
        acts = self.rotate()
        self.assertEqual(
            1, sum(1 for a in acts if a["action"] == "expire-proposal"), acts
        )
        out = self.read("system/proposals.md")
        self.assertNotIn("stale-idea\n- **Status:** new", out)
        self.assertIn(f"- {days_ago(45)} stale-idea -> expired", out)
        self.assertIn(f"### {days_ago(45)}-being-graded", out)
        self.assertIn(f"### {days_ago(5)}-fresh-idea", out)
        self.assertIn("- 2026-08-06 prior-decision -> adopted (kept)", out)

    def test_no_decisions_record_means_no_rotation(self):
        write(
            self.vault,
            "system/proposals.md",
            f"# p\n\n### {days_ago(45)}-stale-idea\n- **Status:** new\n",
        )
        acts = self.rotate()
        self.assertFalse(any(a["action"] == "expire-proposal" for a in acts))
        self.assertIn(
            f"### {days_ago(45)}-stale-idea", self.read("system/proposals.md")
        )


class TestDryRunAndGuards(Fixture):
    def test_dry_run_reports_but_changes_nothing(self):
        write(self.vault, f"audits/{days_ago(7)}-vault-audit.md", "# old\n")
        old_src = f"{days_ago(90)}-dead-clip"
        write(self.vault, f"sources/{old_src}.md", "x\n")
        write(self.vault, ".openclaw-wiki/cache/claims.jsonl", "")
        acts = self.rotate("--dry-run")
        self.assertTrue(acts)
        self.assertTrue(self.exists(f"audits/{days_ago(7)}-vault-audit.md"))
        self.assertTrue(self.exists(f"sources/{old_src}.md"))

    def test_untargeted_surfaces_untouched(self):
        keep = {
            "PLAN.md": "# plan\nDenys-curated\n",
            "journal/2026-01-01.md": "frozen journal\n",
            "projects/demo/journal.md": f"## [{days_ago(200)}] old journal entry\n",
            "state/devclaw/runtime.txt": "opaque\n",
        }
        for rel, content in keep.items():
            write(self.vault, rel, content)
        self.rotate()
        for rel, content in keep.items():
            self.assertEqual(content, self.read(rel), rel)


class TestLintRules(Fixture):
    def lint(self):
        return run(LINT, self.vault)

    def test_frozen_frontmatter_not_flagged(self):
        bad = "---\nname: x\nsummary: broken: colon value\n---\nbody\n"
        write(self.vault, "system/proposals/2026-06-01-frozen.md", bad)
        write(self.vault, "system/live-page.md", bad)
        rules = [(f["rule"], f["path"]) for f in self.lint()]
        self.assertIn(("malformed-frontmatter", "system/live-page.md"), rules)
        self.assertNotIn(
            ("malformed-frontmatter", "system/proposals/2026-06-01-frozen.md"), rules
        )

    def test_stale_status_flagged(self):
        write(
            self.vault,
            "projects/demo/STATUS.md",
            f"---\nname: demo-status\nupdatedAt: {days_ago(30)}\nstatus: active\n---\nx\n",
        )
        self.assertIn("stale-status", [f["rule"] for f in self.lint()])

    def test_canvas_drift_flagged_via_mtime_fallback(self):
        write(
            self.vault,
            "projects/demo/plan.md",
            f"---\nname: demo\nsummary: s\nupdatedAt: {days_ago(0)}\n---\nx\n",
        )
        cv = write(self.vault, "projects/demo/architecture.canvas", '{"nodes":[]}')
        stale = datetime.datetime(2026, 6, 1).timestamp()
        os.utime(cv, (stale, stale))
        self.assertIn("canvas-drift", [f["rule"] for f in self.lint()])

    def test_fresh_canvas_not_flagged(self):
        write(
            self.vault,
            "projects/demo/plan.md",
            f"---\nname: demo\nsummary: s\nupdatedAt: {days_ago(5)}\n---\nx\n",
        )
        write(self.vault, "projects/demo/architecture.canvas", '{"nodes":[]}')
        self.assertNotIn("canvas-drift", [f["rule"] for f in self.lint()])


if __name__ == "__main__":
    unittest.main()

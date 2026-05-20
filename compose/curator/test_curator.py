"""Tests for curator.process_queue defensive parsing.

Regression tests for incident 2026-05-20 (#a350): malformed queue entries
crashed the loop, and the error handler itself crashed when 'id' was missing,
restarting the container 201 times in 3 days.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import curator  # noqa: E402


class ProcessQueueDefensiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.queue_file = self.tmpdir / "queue.jsonl"
        self.dead_letter = self.tmpdir / "queue.dead-letter.jsonl"

        self._patches = [
            mock.patch.object(curator, "QUEUE_FILE", self.queue_file),
            mock.patch.object(curator, "DEAD_LETTER_FILE", self.dead_letter),
            # Stub the LLM call so tests run offline and deterministically.
            mock.patch.object(curator, "claude_run", return_value=""),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _write_entries(self, entries: list) -> None:
        self.queue_file.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n"
        )

    def test_missing_goal_does_not_raise(self) -> None:
        """Entry missing 'goal' must be quarantined, not crash the loop."""
        self._write_entries([
            {"id": "aaaaaaaa-1111", "response": "hi"},  # no goal
        ])
        try:
            curator.process_queue()
        except Exception as exc:  # pragma: no cover - failure path
            self.fail(f"process_queue raised on missing-goal entry: {exc!r}")

        self.assertTrue(self.dead_letter.exists(), "poison entry must be quarantined")
        dead = [json.loads(l) for l in self.dead_letter.read_text().splitlines() if l.strip()]
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0]["entry"]["id"], "aaaaaaaa-1111")
        # Quarantined entry must be removed from the live queue.
        remaining = self.queue_file.read_text().strip()
        self.assertEqual(remaining, "")

    def test_missing_id_in_error_path_does_not_raise(self) -> None:
        """The error handler itself must not KeyError when 'id' is missing."""
        self._write_entries([
            {"response": "hi"},  # no id AND no goal — error path is hit
        ])
        try:
            curator.process_queue()
        except Exception as exc:  # pragma: no cover - failure path
            self.fail(f"process_queue raised on missing-id entry: {exc!r}")

        self.assertTrue(self.dead_letter.exists())
        dead = [json.loads(l) for l in self.dead_letter.read_text().splitlines() if l.strip()]
        self.assertEqual(len(dead), 1)
        self.assertNotIn("id", dead[0]["entry"])

    def test_mixed_good_and_poison_entries(self) -> None:
        """Good entries are processed; poison entries are isolated."""
        self._write_entries([
            {"id": "good1111-2222", "goal": "g", "response": "r"},
            {"response": "no goal here"},          # poison: missing goal
            {"id": "good2222-3333", "goal": "g2", "response": "r2"},
            {"id": "incomplete", "goal": "only goal"},  # poison: missing response
        ])

        curator.process_queue()

        # Two LLM invocations for the two valid entries.
        self.assertEqual(curator.claude_run.call_count, 2)

        # Queue file is now empty: 2 good entries done, 2 poison entries dead-lettered.
        self.assertEqual(self.queue_file.read_text().strip(), "")

        dead = [json.loads(l) for l in self.dead_letter.read_text().splitlines() if l.strip()]
        self.assertEqual(len(dead), 2)

    def test_non_string_id_does_not_raise(self) -> None:
        """Even an entry with a non-string 'id' must be handled gracefully."""
        self._write_entries([{"id": 12345, "response": "r"}])  # int id, no goal
        try:
            curator.process_queue()
        except Exception as exc:  # pragma: no cover
            self.fail(f"process_queue raised on non-string id: {exc!r}")
        self.assertTrue(self.dead_letter.exists())


if __name__ == "__main__":
    unittest.main()

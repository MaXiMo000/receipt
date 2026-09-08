"""Run: python tests/test_receipt.py

Covers the property the tool exists for: a command that touches only what
it declared gets PASS; one that touches anything else gets FAIL and names
it; a command run without a declared scope gets UNVERIFIED, never a silent
PASS -- there was nothing to check it against.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from receipt.core import run
from receipt.model import FAIL, PASS, UNVERIFIED
from receipt.snapshot import diff, snapshot


class TestSnapshot(unittest.TestCase):
    def test_diff_finds_added_modified_removed(self):
        before = {"a.txt": "hash1", "b.txt": "hash2"}
        after = {"a.txt": "hash1-changed", "c.txt": "hash3"}
        result = diff(before, after)
        self.assertEqual(result["added"], ["c.txt"])
        self.assertEqual(result["modified"], ["a.txt"])
        self.assertEqual(result["removed"], ["b.txt"])

    def test_snapshot_hashes_real_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "x.txt").write_text("hello")
            result = snapshot(tmp)
            self.assertIn("x.txt", result)
            self.assertEqual(len(result["x.txt"]), 64)  # sha256 hex digest length


class TestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_touching_only_declared_files_passes(self):
        result = run(
            task="write output.txt",
            cmd=["python3", "-c", "open('output.txt', 'w').write('x')"],
            watch_dir=self.dir,
            declared_paths=["output.txt"],
        )
        self.assertEqual(result["status"], PASS)
        self.assertEqual(result["unexpected"], [])

    def test_touching_an_undeclared_file_fails_and_names_it(self):
        result = run(
            task="write output.txt",
            cmd=["python3", "-c", "open('output.txt', 'w').write('x'); open('sneaky.txt', 'w').write('y')"],
            watch_dir=self.dir,
            declared_paths=["output.txt"],
        )
        self.assertEqual(result["status"], FAIL)
        self.assertIn("sneaky.txt", result["unexpected"])

    def test_no_declared_scope_is_unverified_not_a_silent_pass(self):
        result = run(
            task="do something",
            cmd=["python3", "-c", "open('anything.txt', 'w').write('x')"],
            watch_dir=self.dir,
            declared_paths=None,
        )
        self.assertEqual(result["status"], UNVERIFIED)

    def test_command_that_touches_nothing_and_declares_nothing_passes(self):
        result = run(task="no-op", cmd=["python3", "-c", "pass"],
                     watch_dir=self.dir, declared_paths=[])
        self.assertEqual(result["status"], PASS)
        self.assertEqual(result["changes"]["added"], [])


if __name__ == "__main__":
    unittest.main()

"""Run: python tests/test_receipt.py

Covers the property the tool exists for: a command that touches only what
it declared gets PASS; one that touches anything else gets FAIL and names
it; a command run without a declared scope gets UNVERIFIED, never a silent
PASS -- there was nothing to check it against.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from receipt.core import run
from receipt.model import FAIL, PASS, UNVERIFIED
from receipt.redact import redact
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


class TestRedact(unittest.TestCase):
    def test_keyed_secret_is_masked_key_name_kept(self):
        out = redact("API_KEY=sk-supersecret12345")
        self.assertNotIn("sk-supersecret12345", out)
        self.assertIn("API_KEY=", out)
        self.assertIn("[REDACTED]", out)

    def test_quoted_keyed_secret_is_masked(self):
        out = redact('DB_PASSWORD="hunter2trombone"')
        self.assertNotIn("hunter2trombone", out)

    def test_credentialed_url_masks_only_the_password(self):
        out = redact("connecting to postgresql://appuser:s3cr3t@db.internal:5432/prod")
        self.assertNotIn("s3cr3t", out)
        self.assertIn("appuser", out)  # username isn't secret, keep it for debuggability
        self.assertIn("db.internal", out)

    def test_recognizable_token_prefix_is_masked_even_with_no_key_name(self):
        out = redact("printed for debugging: ghp_abcdefghijklmnopqrstuvwxyz0123456789")
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz0123456789", out)

    def test_pem_private_key_block_is_masked(self):
        block = "-----BEGIN PRIVATE KEY-----\nMIIBVQ==\n-----END PRIVATE KEY-----"
        self.assertNotIn("MIIBVQ==", redact(block))

    def test_ordinary_output_is_left_alone(self):
        text = "3 files changed, 12 insertions(+), 4 deletions(-)"
        self.assertEqual(redact(text), text)

    def test_empty_string_is_returned_unchanged(self):
        self.assertEqual(redact(""), "")


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

    def test_a_command_that_echoes_a_secret_does_not_leak_it_into_the_receipt(self):
        # Regression test for the exact scenario confirmed live during the
        # portfolio audit: a wrapped command's own stdout printed a real-
        # shaped API key, and it landed unredacted in the written receipt.
        result = run(
            task="run a script that happens to print its own env",
            cmd=["python3", "-c", "print('API_KEY=sk-supersecret12345')"],
            watch_dir=self.dir,
            declared_paths=[],
        )
        self.assertNotIn("sk-supersecret12345", result["stdout"])
        self.assertNotIn("sk-supersecret12345", json.dumps(result))

    def test_non_utf8_stdout_does_not_crash_the_run(self):
        result = run(
            task="emit garbage bytes",
            cmd=["python3", "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe garbage')"],
            watch_dir=self.dir,
            declared_paths=[],
        )
        self.assertEqual(result["status"], PASS)
        self.assertIn("garbage", result["stdout"])


if __name__ == "__main__":
    unittest.main()

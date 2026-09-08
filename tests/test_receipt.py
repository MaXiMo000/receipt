"""Run: python tests/test_receipt.py

Covers the property the tool exists for: a command that touches only what
it declared gets PASS; one that touches anything else gets FAIL and names
it; a command run without a declared scope gets UNVERIFIED, never a silent
PASS -- there was nothing to check it against.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from receipt.cli import main as cli_main
from receipt.core import run
from receipt.model import FAIL, PASS, UNVERIFIED
from receipt.redact import redact
from receipt.snapshot import diff, snapshot


def _entry(content_hash: str, mode: int = 0o644) -> dict:
    """A snapshot entry, for hand-built before/after fixtures in tests
    below -- real snapshots come from snapshot(), this just matches its
    {"hash", "mode"} shape without needing a real file on disk."""
    return {"hash": content_hash, "mode": mode}


class TestSnapshot(unittest.TestCase):
    def test_diff_finds_added_modified_removed(self):
        before = {"a.txt": _entry("hash1"), "b.txt": _entry("hash2")}
        after = {"a.txt": _entry("hash1-changed"), "c.txt": _entry("hash3")}
        result = diff(before, after)
        self.assertEqual(result["added"], ["c.txt"])
        self.assertEqual(result["modified"], ["a.txt"])
        self.assertEqual(result["removed"], ["b.txt"])

    def test_snapshot_hashes_real_files_and_records_their_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "x.txt"
            path.write_text("hello")
            path.chmod(0o644)
            result = snapshot(tmp)
            self.assertIn("x.txt", result)
            self.assertEqual(len(result["x.txt"]["hash"]), 64)  # sha256 hex digest length
            self.assertEqual(result["x.txt"]["mode"], 0o644)

    def test_a_rename_is_reported_as_a_rename_not_delete_plus_create(self):
        before = {"a.txt": _entry("samehash")}
        after = {"b.txt": _entry("samehash")}
        result = diff(before, after)
        self.assertEqual(result["renamed"], [{"from": "a.txt", "to": "b.txt"}])
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])

    def test_a_genuine_delete_plus_unrelated_create_is_not_mistaken_for_a_rename(self):
        before = {"a.txt": _entry("hash-a")}
        after = {"b.txt": _entry("hash-b")}  # different content -- not the same file moved
        result = diff(before, after)
        self.assertEqual(result["renamed"], [])
        self.assertEqual(result["added"], ["b.txt"])
        self.assertEqual(result["removed"], ["a.txt"])

    def test_two_simultaneous_renames_with_identical_content_pair_deterministically(self):
        before = {"a1.txt": _entry("dup"), "a2.txt": _entry("dup")}
        after = {"b1.txt": _entry("dup"), "b2.txt": _entry("dup")}
        result = diff(before, after)
        self.assertEqual(
            result["renamed"],
            [{"from": "a1.txt", "to": "b1.txt"}, {"from": "a2.txt", "to": "b2.txt"}],
        )

    def test_a_modified_file_alongside_a_rename_is_still_reported_separately(self):
        before = {"a.txt": _entry("h1"), "c.txt": _entry("h-old")}
        after = {"b.txt": _entry("h1"), "c.txt": _entry("h-new")}
        result = diff(before, after)
        self.assertEqual(result["renamed"], [{"from": "a.txt", "to": "b.txt"}])
        self.assertEqual(result["modified"], ["c.txt"])

    def test_a_permission_only_change_is_reported_as_mode_changed_not_modified(self):
        before = {"a.txt": _entry("same", mode=0o644)}
        after = {"a.txt": _entry("same", mode=0o755)}
        result = diff(before, after)
        self.assertEqual(result["mode_changed"], ["a.txt"])
        self.assertEqual(result["modified"], [])

    def test_a_content_change_is_not_double_reported_in_mode_changed(self):
        # If content changed too, `modified` already covers it -- mode_changed
        # exists specifically for the content-identical case that would
        # otherwise be silent, not as a second listing of every touched file.
        before = {"a.txt": _entry("old", mode=0o644)}
        after = {"a.txt": _entry("new", mode=0o755)}
        result = diff(before, after)
        self.assertEqual(result["modified"], ["a.txt"])
        self.assertEqual(result["mode_changed"], [])

    def test_snapshot_records_the_real_permission_bits_of_an_executable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "script.sh"
            path.write_text("#!/bin/sh\necho hi")
            path.chmod(0o755)
            result = snapshot(tmp)
            self.assertEqual(result["script.sh"]["mode"], 0o755)


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

    def test_a_glob_pattern_in_declare_covers_a_whole_directory(self):
        result = run(
            task="write two files under app/",
            cmd=["python3", "-c",
                 "import os; os.mkdir('app')"
                 "; open('app/one.py', 'w').write('x')"
                 "; open('app/two.py', 'w').write('y')"],
            watch_dir=self.dir,
            declared_paths=["app/*.py"],
        )
        self.assertEqual(result["status"], PASS)

    def test_a_glob_pattern_does_not_match_a_file_outside_its_scope(self):
        result = run(
            task="write inside and outside app/",
            cmd=["python3", "-c",
                 "import os; os.mkdir('app')"
                 "; open('app/one.py', 'w').write('x')"
                 "; open('outside.py', 'w').write('y')"],
            watch_dir=self.dir,
            declared_paths=["app/*.py"],
        )
        self.assertEqual(result["status"], FAIL)
        self.assertIn("outside.py", result["unexpected"])

    def test_a_literal_declared_path_still_works_alongside_globs(self):
        # Exact declarations and globs can be mixed in one --declare list.
        result = run(
            task="write a literal file and a globbed one",
            cmd=["python3", "-c",
                 "import os; os.mkdir('app')"
                 "; open('README.md', 'w').write('x')"
                 "; open('app/one.py', 'w').write('y')"],
            watch_dir=self.dir,
            declared_paths=["README.md", "app/*.py"],
        )
        self.assertEqual(result["status"], PASS)

    def test_no_declared_scope_is_unverified_not_a_silent_pass(self):
        result = run(
            task="do something",
            cmd=["python3", "-c", "open('anything.txt', 'w').write('x')"],
            watch_dir=self.dir,
            declared_paths=None,
        )
        self.assertEqual(result["status"], UNVERIFIED)

    def test_renaming_a_declared_file_to_another_declared_name_passes(self):
        # The file must exist *before* run()'s first snapshot for a rename
        # to be observable at all -- a file created and renamed within the
        # same command never appears as anything but a plain new file.
        (pathlib.Path(self.dir) / "output.txt").write_text("x")
        result = run(
            task="rename output.txt to final.txt",
            cmd=["python3", "-c", "import os; os.rename('output.txt', 'final.txt')"],
            watch_dir=self.dir,
            declared_paths=["output.txt", "final.txt"],
        )
        self.assertEqual(result["status"], PASS)
        self.assertEqual(result["changes"]["renamed"], [{"from": "output.txt", "to": "final.txt"}])

    def test_renaming_to_an_undeclared_name_fails_and_names_the_origin(self):
        # This is the false-positive the fix exists to prevent -- without
        # rename detection this would report an undeclared *new* file with
        # no indication it's actually the declared file, just moved.
        (pathlib.Path(self.dir) / "output.txt").write_text("x")
        result = run(
            task="rename output.txt",
            cmd=["python3", "-c", "import os; os.rename('output.txt', 'sneaky.txt')"],
            watch_dir=self.dir,
            declared_paths=["output.txt"],
        )
        self.assertEqual(result["status"], FAIL)
        self.assertIn("sneaky.txt (renamed from output.txt)", result["detail"])

    def test_chmod_on_an_undeclared_file_fails_where_it_used_to_be_invisible(self):
        # This is the exact gap the audit flagged: a permission-only change
        # (content byte-identical) previously produced 0 touched files and
        # a silent PASS. A command flipping a file executable, or loosening
        # permissions on something sensitive, should not be invisible.
        (pathlib.Path(self.dir) / "secret.env").write_text("SECRET=x")
        result = run(
            task="do something unrelated",
            cmd=["python3", "-c", "import os; os.chmod('secret.env', 0o777)"],
            watch_dir=self.dir,
            declared_paths=[],
        )
        self.assertEqual(result["status"], FAIL)
        self.assertIn("secret.env (permissions changed, content unchanged)", result["detail"])
        self.assertEqual(result["changes"]["mode_changed"], ["secret.env"])

    def test_chmod_on_a_declared_file_passes(self):
        (pathlib.Path(self.dir) / "build.sh").write_text("#!/bin/sh\necho hi")
        result = run(
            task="make build.sh executable",
            cmd=["python3", "-c", "import os; os.chmod('build.sh', 0o755)"],
            watch_dir=self.dir,
            declared_paths=["build.sh"],
        )
        self.assertEqual(result["status"], PASS)
        self.assertEqual(result["changes"]["mode_changed"], ["build.sh"])

    def test_command_that_touches_nothing_and_declares_nothing_passes(self):
        result = run(task="no-op", cmd=["python3", "-c", "pass"],
                     watch_dir=self.dir, declared_paths=[])
        self.assertEqual(result["status"], PASS)
        self.assertEqual(result["changes"]["added"], [])

    def test_a_missing_binary_produces_a_receipt_instead_of_crashing(self):
        # Previously this raised FileNotFoundError straight out of run() --
        # no receipt written at all, for exactly the case (something went
        # wrong) that most needs a record.
        result = run(
            task="run something that doesn't exist",
            cmd=["definitely_not_a_real_binary_xyz"],
            watch_dir=self.dir,
            declared_paths=[],
        )
        self.assertEqual(result["status"], FAIL)
        self.assertIn("could not launch the command", result["detail"])
        self.assertIsNone(result["exit_code"])

    def test_a_nonexistent_watch_dir_produces_a_receipt_instead_of_crashing(self):
        result = run(
            task="run in a directory that isn't there",
            cmd=["python3", "-c", "pass"],
            watch_dir=str(pathlib.Path(self.dir) / "does" / "not" / "exist"),
            declared_paths=[],
        )
        self.assertEqual(result["status"], FAIL)
        self.assertIn("could not launch the command", result["detail"])

    def test_launch_failure_receipt_still_gets_written_to_disk_by_the_cli(self):
        # End to end through the real CLI, not just core.run() -- the point
        # is that write_receipt() is still reached, since run_task() no
        # longer raises.
        out, err = io.StringIO(), io.StringIO()
        argv = ["run", "--task", "x", "--out", str(pathlib.Path(self.dir) / "receipts"),
                "--", "definitely_not_a_real_binary_xyz"]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(argv)
        self.assertEqual(code, 1)
        written = list((pathlib.Path(self.dir) / "receipts").glob("*.json"))
        self.assertEqual(len(written), 1)
        self.assertIn("could not launch", json.loads(written[0].read_text())["receipt"]["detail"])

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


class TestEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_writes_in_the_same_second_do_not_overwrite_each_other(self):
        # Reproduces, as a regression test, exactly what was confirmed live:
        # two receipt.run()s started within the same wall-clock second used
        # to collide on an identical filename and silently overwrite.
        from unittest import mock

        from receipt.evidence import write

        with mock.patch("time.strftime", return_value="20260101T000000Z"):
            path1 = write({"task": "first"}, self.tmp.name)
            path2 = write({"task": "second"}, self.tmp.name)

        self.assertNotEqual(path1, path2)
        self.assertTrue(path1.exists())
        self.assertTrue(path2.exists())
        self.assertEqual(json.loads(path1.read_text())["receipt"]["task"], "first")
        self.assertEqual(json.loads(path2.read_text())["receipt"]["task"], "second")

    def test_written_record_carries_a_schema_version(self):
        from receipt.evidence import SCHEMA_VERSION, write

        path = write({"task": "x"}, self.tmp.name)
        record = json.loads(path.read_text())
        self.assertEqual(record["schema_version"], SCHEMA_VERSION)

    def test_the_sha256_actually_matches_what_a_reader_would_recompute(self):
        # The tamper-evidence claim is only real if this holds -- a test
        # that just checks a "sha256" key exists proves nothing about
        # whether it's the *right* hash.
        import hashlib

        from receipt.evidence import write

        path = write({"task": "x", "status": "pass"}, self.tmp.name)
        record = json.loads(path.read_text())
        recomputed = hashlib.sha256(
            json.dumps(record["receipt"], indent=2, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        self.assertEqual(record["sha256"], recomputed)

    def test_a_tampered_receipt_is_detectable_by_recomputing_the_hash(self):
        from receipt.evidence import write

        path = write({"task": "x", "status": "pass"}, self.tmp.name)
        record = json.loads(path.read_text())
        record["receipt"]["status"] = "fail"  # tamper with it after the fact
        path.write_text(json.dumps(record))

        import hashlib
        recomputed = hashlib.sha256(
            json.dumps(record["receipt"], indent=2, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        self.assertNotEqual(record["sha256"], recomputed)


if __name__ == "__main__":
    unittest.main()

"""Run: python tests/test_ledger.py"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from receipt_evidence.evidence import write
from receipt_evidence.ledger import LEDGER, SIGNATURE, verify

HAS_SSH = shutil.which("ssh-keygen") is not None


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.dir = self.tmp / "receipts"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _three(self, key=None):
        return [write({"task": f"step {i}"}, self.dir, sign_key=key) for i in range(3)]

    def test_intact_chain_passes(self):
        self._three()
        problems, summary = verify(self.dir)
        self.assertEqual((problems, summary["receipts"]), ([], 3))

    def test_edit_delete_and_insert_are_each_named(self):
        paths = self._three()
        paths[0].write_text(paths[0].read_text(encoding="utf-8").replace("step 0", "step zero"),
                            encoding="utf-8")
        paths[1].unlink()
        (self.dir / "smuggled.json").write_text("{}", encoding="utf-8")
        problems, _ = verify(self.dir)
        self.assertEqual(len(problems), 3)
        self.assertIn("changed after it was written", problems[0])
        self.assertIn("deleted", problems[1])
        self.assertIn("added afterwards", problems[2])

    def test_rewritten_history_breaks_the_chain(self):
        self._three()
        ledger = self.dir / LEDGER
        lines = ledger.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace('"sha256": "', '"sha256": "0')
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        problems, _ = verify(self.dir)
        self.assertTrue(any("does not follow" in p for p in problems))

    @unittest.skipUnless(HAS_SSH, "ssh-keygen not available")
    def test_only_the_signature_catches_a_consistently_rebuilt_ledger(self):
        key, other = self.tmp / "key", self.tmp / "other"
        for k, who in ((key, "ci@example.com"), (other, "intruder")):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", who, "-f", str(k)], check=True)
        allowed = self.tmp / "allowed_signers"
        allowed.write_text("ci@example.com " + " ".join(
            (key.with_suffix(".pub")).read_text(encoding="utf-8").split()[:2]) + "\n", encoding="utf-8")

        paths = self._three(key=str(key))
        problems, summary = verify(self.dir, str(allowed))
        self.assertEqual((problems, summary["signed_by"]), ([], "ci@example.com"))

        # Edit a receipt and rebuild the ledger so the chain is consistent.
        paths[1].write_text(paths[1].read_text(encoding="utf-8").replace("step 1", "step one"),
                            encoding="utf-8")
        prev, lines = "0" * 64, []
        for line in (self.dir / LEDGER).read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            entry["sha256"] = hashlib.sha256((self.dir / entry["file"]).read_bytes()).hexdigest()
            entry["prev"] = prev
            text = json.dumps(entry, sort_keys=True)
            lines.append(text)
            prev = hashlib.sha256(text.encode()).hexdigest()
        (self.dir / LEDGER).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        self.assertEqual(verify(self.dir)[0], [])  # the chain alone is fooled
        problems, _ = verify(self.dir, str(allowed))
        self.assertIn("does not match the ledger", problems[0])

        (self.dir / SIGNATURE).unlink()
        subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(other), "-n", "receipt-ledger",
                        str(self.dir / LEDGER)], check=True)
        problems, _ = verify(self.dir, str(allowed))
        self.assertIn("not in the allowed signers", problems[0])


class TestSummary(unittest.TestCase):
    def test_failures_first_and_the_ledger_state(self):
        from receipt_evidence.summary import render
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            write({"task": "fine", "status": "pass", "detail": "touched only a.txt"}, d)
            write({"task": "bad", "status": "fail", "detail": "touched c.txt"}, d)
            text, ok = render(d)
            self.assertFalse(ok)
            self.assertIn("2 receipt(s): 1 pass, 1 fail", text)
            self.assertIn("chain intact", text)
            self.assertLess(text.index("| bad |"), text.index("| fine |"))


if __name__ == "__main__":
    unittest.main()

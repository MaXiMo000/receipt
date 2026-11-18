"""Run: python tests/test_agent_hooks.py

custody-hook against real files in a temp directory, with events shaped
like Codex's PreToolUse/PostToolUse (https://developers.openai.com/codex/hooks)
and Cursor's afterFileEdit/postToolUse/sessionStart
(https://cursor.com/docs/hooks).
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

from receipt_evidence.claude import hook


def _run(event: dict) -> str:
    """Runs the hook; returns what it printed (it must print nothing)."""
    real_stdin, out = sys.stdin, io.StringIO()
    sys.stdin = io.StringIO(json.dumps(event))
    try:
        with contextlib.redirect_stdout(out):
            assert hook.main() == 0
    finally:
        sys.stdin = real_stdin
    return out.getvalue()


class _Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name
        for name in ("a.py", "b.py"):
            pathlib.Path(self.cwd, name).write_text(f"{name}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str, text: str) -> None:
        pathlib.Path(self.cwd, name).write_text(text)

    def receipts(self) -> list[dict]:
        folder = pathlib.Path(self.cwd, ".custody", "receipts")
        return [json.loads(p.read_text())["payload"] for p in sorted(folder.glob("*.json"))]


PATCH = """*** Begin Patch
*** Update File: a.py
@@
-a.py
+a.py changed
*** End Patch
"""


class TestCodexApplyPatch(_Workspace):
    def _pair(self, tool_name: str = "apply_patch", patch: str = PATCH):
        pre = {"session_id": "s1", "turn_id": "t1", "cwd": self.cwd, "hook_event_name": "PreToolUse",
               "model": "gpt", "permission_mode": "default", "tool_name": tool_name,
               "tool_use_id": "call_1", "tool_input": {"command": patch}}
        self.assertEqual(_run(pre), "")
        return pre

    def _post(self, pre: dict) -> dict:
        self.assertEqual(_run({**pre, "hook_event_name": "PostToolUse", "tool_response": {}}), "")
        [receipt] = self.receipts()
        return receipt

    def test_a_patch_that_changed_exactly_what_it_named_is_pass(self):
        pre = self._pair()
        self.write("a.py", "a.py changed\n")
        receipt = self._post(pre)
        self.assertEqual((receipt["status"], receipt["declared_scope"]), ("pass", ["a.py"]))

    def test_a_change_to_a_file_the_patch_did_not_name_is_fail(self):
        pre = self._pair()
        self.write("a.py", "a.py changed\n")
        self.write("b.py", "sneaky\n")
        receipt = self._post(pre)
        self.assertEqual(receipt["status"], "fail")
        self.assertIn("b.py", receipt["detail"])

    def test_a_patch_whose_file_did_not_change_is_unverified(self):
        receipt = self._post(self._pair())
        self.assertEqual(receipt["status"], "unverified")

    def test_added_deleted_and_moved_files_are_all_in_scope(self):
        patch = ("*** Begin Patch\n*** Add File: new/c.py\n+c\n*** Delete File: b.py\n"
                 "*** Update File: a.py\n*** Move to: moved.py\n*** End Patch\n")
        pre = self._pair(patch=patch)
        self.assertEqual(json.loads(next(pathlib.Path(self.cwd, ".custody", "pending").glob("*.json"))
                                    .read_text())["scope"], ["a.py", "b.py", "moved.py", "new/c.py"])
        pathlib.Path(self.cwd, "new").mkdir()
        self.write("new/c.py", "c\n")
        pathlib.Path(self.cwd, "b.py").unlink()
        pathlib.Path(self.cwd, "a.py").rename(pathlib.Path(self.cwd, "moved.py"))
        self.assertEqual(self._post(pre)["status"], "pass")

    def test_a_patch_matched_under_its_edit_alias_is_still_read_as_a_patch(self):
        pre = self._pair(tool_name="Edit")
        self.write("a.py", "a.py changed\n")
        self.assertEqual(self._post(pre)["status"], "pass")

    def test_codex_bash_is_recorded_like_claude_bash(self):
        pre = {"session_id": "s1", "cwd": self.cwd, "hook_event_name": "PreToolUse",
               "tool_name": "Bash", "tool_use_id": "call_2", "tool_input": {"command": "echo x >> b.py"}}
        _run(pre)
        self.write("b.py", "b.py\nx\n")
        receipt = self._post(pre)
        self.assertEqual((receipt["status"], receipt["changes"]["modified"]), ("unverified", ["b.py"]))


class TestCursor(_Workspace):
    def common(self, event: str, **extra) -> dict:
        return {"conversation_id": "c1", "generation_id": "g1", "hook_event_name": event,
                "cursor_version": "2.0", "workspace_roots": [self.cwd], **extra}

    def edit(self, new_string: str, old_string: str = "a.py") -> dict:
        return self.common("afterFileEdit", file_path=str(pathlib.Path(self.cwd, "a.py")),
                           edits=[{"old_string": old_string, "new_string": new_string}])

    def shell(self, exit_code: int = 0) -> dict:
        return self.common("postToolUse", tool_name="Shell", tool_use_id="tu_1", cwd=self.cwd,
                           tool_input={"command": "make"}, duration=12,
                           tool_output=json.dumps({"exitCode": exit_code, "stdout": ""}))

    def test_an_edit_that_is_on_disk_is_pass(self):
        pathlib.Path(self.cwd, "a.py").write_bytes(b"a.py changed\r\n")  # CRLF on disk is not a difference
        self.assertEqual(_run(self.edit("a.py changed\n")), "")
        self.assertEqual(self.receipts()[0]["status"], "pass")

    def test_an_edit_that_is_not_on_disk_is_fail(self):
        _run(self.edit("a.py changed"))
        self.assertEqual(self.receipts()[0]["status"], "fail")

    def test_a_deletion_is_unverified_not_guessed(self):
        _run(self.edit(""))
        self.assertEqual(self.receipts()[0]["status"], "unverified")

    def test_a_shell_command_is_diffed_against_the_session_baseline(self):
        self.assertEqual(_run(self.common("sessionStart")), "")
        self.write("b.py", "built\n")
        self.assertEqual(_run(self.shell(exit_code=2)), "")
        [receipt] = self.receipts()
        self.assertEqual(receipt["status"], "unverified")
        self.assertEqual(receipt["changes"]["modified"], ["b.py"])
        self.assertIs(receipt["tool_reported_success"], False)

    def test_the_agents_own_edit_is_not_blamed_on_the_next_shell_command(self):
        _run(self.common("sessionStart"))
        self.write("a.py", "a.py changed\n")
        _run(self.edit("a.py changed"))
        _run(self.shell())
        shell = [r for r in self.receipts() if r["tool_name"] == "Shell"][0]
        self.assertEqual(shell["detail"], "no files changed")

    def test_each_shell_command_only_sees_its_own_changes(self):
        _run(self.common("sessionStart"))
        self.write("a.py", "first\n")
        _run({**self.shell(), "tool_use_id": "tu_1"})
        self.write("b.py", "second\n")
        _run({**self.shell(), "tool_use_id": "tu_2"})
        second = json.loads(pathlib.Path(self.cwd, ".custody", "receipts", "tu_2.json").read_text())["payload"]
        self.assertEqual(second["changes"]["modified"], ["b.py"])

    def test_a_shell_command_without_a_baseline_is_unverified(self):
        _run(self.shell())
        [receipt] = self.receipts()
        self.assertEqual(receipt["status"], "unverified")
        self.assertIn("no baseline", receipt["detail"])

    def test_other_cursor_tools_are_ignored(self):
        _run(self.common("postToolUse", tool_name="Read", tool_use_id="tu_9", cwd=self.cwd))
        self.assertFalse(pathlib.Path(self.cwd, ".custody", "receipts").exists())


if __name__ == "__main__":
    unittest.main()

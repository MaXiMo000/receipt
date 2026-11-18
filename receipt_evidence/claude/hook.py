"""stdin/stdout glue for coding-agent hooks: Claude Code, Codex and Cursor.

Claude Code and Codex send the same PreToolUse/PostToolUse events
(https://docs.claude.com/en/docs/claude-code/hooks,
https://developers.openai.com/codex/hooks). Edit and Write are checked
against the file they name, a Codex apply_patch against the files its patch
names, and a shell command's changes are recorded. Cursor
(https://cursor.com/docs/hooks) is read from its after-the-fact events only:
afterFileEdit, postToolUse for Shell, and sessionStart for a baseline.
Every call gets one Providence-conformant receipt in `.custody/receipts/`.

Never blocks a tool call: no `permissionDecision` is ever emitted, and this
always exits 0. This is a witness, not a gate -- the same non-blocking
posture `receipt` itself has (it judges a command after the fact, it
doesn't stop one from running).
"""
from __future__ import annotations

import json
import pathlib
import re
import stat
import sys
import uuid

from ..snapshot import diff as diff_dirs
from ..snapshot import snapshot as snapshot_dir

from ..providence.spec import make_bundle
from . import core

# Bash gets no declared scope at all (see core.bash_receipt) but is still
# hooked, so "what changed" is on record even when "was it in scope" isn't
# an answerable question -- see README.md's "What custody does not do".
# PowerShell: Claude Code's other shell tool, same shape as Bash.
# apply_patch: Codex's file-editing tool.
WATCHED_TOOLS = {"Edit", "Write", "Bash", "PowerShell", "apply_patch"}

_PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)


def _state_dir(cwd: str) -> pathlib.Path:
    return pathlib.Path(cwd) / ".custody" / "pending"


def _receipts_dir(cwd: str) -> pathlib.Path:
    return pathlib.Path(cwd) / ".custody" / "receipts"



def _claimed_text(tool_name: str, response) -> str | None:
    """The full file text the tool says it left on disk, from its own
    response: Write reports `content`; Edit reports `originalFile` plus the
    `oldString` -> `newString` replacement it made. None if the response
    doesn't carry enough to say."""
    if not isinstance(response, dict):
        return None
    if tool_name == "Write":
        content = response.get("content")
        return content if isinstance(content, str) else None
    original, old, new = (response.get(k) for k in ("originalFile", "oldString", "newString"))
    if not all(isinstance(v, str) for v in (original, old, new)) or old not in original:
        return None
    return original.replace(old, new, -1 if response.get("replaceAll") else 1)


def _patch(event: dict) -> str | None:
    """A Codex apply_patch call's patch text, or None for any other call."""
    command = (event.get("tool_input") or {}).get("command")
    return command if isinstance(command, str) and "*** Begin Patch" in command else None


def _patch_scope(patch: str, cwd: str) -> list[str]:
    """Every path the patch names, relative to cwd with forward slashes,
    the same form snapshot() keys files by."""
    root = pathlib.Path(cwd).resolve()
    scope = set()
    for raw in _PATCH_FILE.findall(patch):
        path = (root / raw).resolve()
        try:
            scope.add(path.relative_to(root).as_posix())
        except ValueError:
            scope.add(path.as_posix())  # outside cwd: kept, and can never match a snapshot path
    return sorted(scope)


def _write_receipt(cwd: str, receipt_id: str, payload: dict) -> None:
    receipts_dir = _receipts_dir(cwd)
    receipts_dir.mkdir(parents=True, exist_ok=True)
    path = receipts_dir / f"{receipt_id}.json"
    path.write_text(json.dumps(make_bundle("custody", payload), indent=2), encoding="utf-8")
    from ..ledger import LedgerError, append
    try:
        append(receipts_dir, path)
    except LedgerError as exc:
        # The hook must not break the agent's tool call; say it loudly.
        sys.stderr.write(f"custody-hook: {exc}\n")


def pre_tool_use(event: dict) -> None:
    tool_name = event.get("tool_name")
    if tool_name not in WATCHED_TOOLS:
        return
    tool_use_id = event["tool_use_id"]
    cwd = event.get("cwd", ".")

    patch = _patch(event)
    if patch is not None:
        state = {"tool_name": tool_name, "cwd": cwd, "before": snapshot_dir(cwd),
                 "scope": _patch_scope(patch, cwd)}
    elif tool_name in ("Edit", "Write"):
        file_path = event["tool_input"]["file_path"]
        state = {"tool_name": tool_name, "file_path": file_path,
                  "before_hash": core.hash_file(file_path)}
    else:  # Bash
        state = {"tool_name": tool_name, "cwd": cwd, "before": snapshot_dir(cwd)}

    state_dir = _state_dir(cwd)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{tool_use_id}.json").write_text(json.dumps(state))


def post_tool_use(event: dict) -> None:
    tool_name = event.get("tool_name")
    if tool_name not in WATCHED_TOOLS:
        return
    tool_use_id = event["tool_use_id"]
    cwd = event.get("cwd", ".")
    state_path = _state_dir(cwd) / f"{tool_use_id}.json"

    if not state_path.exists():
        # A crash between the two hooks, or the hook being added mid-session,
        # leaves nothing to diff against -- unverified, never a guessed pass.
        payload = {
            "event": "PostToolUse", "tool_name": tool_name, "tool_use_id": tool_use_id,
            "session_id": event.get("session_id"),
            "declared_scope": None, "status": core.UNVERIFIED,
            "detail": ("no matching PreToolUse snapshot found for this tool_use_id -- "
                       "the hook may have been registered mid-session, or the "
                       "PreToolUse call for it failed silently"),
            "tool_reported_success": None,
        }
    else:
        state = json.loads(state_path.read_text())
        tool_response = event.get("tool_response")
        # Claude Code only sends PostToolUse after a tool *succeeded* (a
        # failure is a separate PostToolUseFailure event), and real Edit/
        # Write/Bash responses carry no `success` field at all -- measured
        # live. So for Edit/Write a PostToolUse is itself the success claim;
        # an explicit `success: false`, if one ever appears, still wins.
        # Bash stays null: whether a non-zero exit counts as tool failure
        # hasn't been measured, and bash_receipt() doesn't judge on it.
        tool_success = None
        if isinstance(tool_response, dict):
            implied = True if tool_name in ("Edit", "Write") else None
            tool_success = tool_response.get("success", implied)

        if "scope" in state:  # apply_patch
            result = core.patch_receipt(before=state["before"], after=snapshot_dir(state["cwd"]),
                                        scope=state["scope"], diff_fn=diff_dirs)
        elif tool_name in ("Edit", "Write"):
            after_hash = core.hash_file(state["file_path"])
            claimed = _claimed_text(tool_name, tool_response)
            claim_matches = None
            if claimed is not None and after_hash is not None:
                claim_matches = core.matches_claim(claimed, pathlib.Path(state["file_path"]).read_bytes())
            result = core.file_receipt(
                file_path=state["file_path"], before_hash=state["before_hash"],
                after_hash=after_hash, tool_name=tool_name, tool_success=tool_success,
                claim_matches=claim_matches)
        else:  # Bash
            after = snapshot_dir(state["cwd"])
            result = core.bash_receipt(before=state["before"], after=after,
                                        tool_success=tool_success, diff_fn=diff_dirs)

        payload = {
            "event": "PostToolUse", "tool_name": tool_name, "tool_use_id": tool_use_id,
            "session_id": event.get("session_id"), "duration_ms": event.get("duration_ms"),
            **result,
        }
        state_path.unlink(missing_ok=True)

    _write_receipt(cwd, tool_use_id, payload)


# -- Cursor ------------------------------------------------------------------
# Cursor's before-hooks (preToolUse, beforeShellExecution) are permission
# checks: output a witness can't honestly give would block or approve the
# call. So Cursor is read from its after-events only, against a rolling
# baseline: a snapshot taken at sessionStart and refreshed after every
# recorded event, so each shell command is diffed against the state the
# previous event left behind.

def _cursor_cwd(event: dict) -> str:
    roots = event.get("workspace_roots") or []
    return event.get("cwd") or (roots[0] if roots else ".")


def _baseline_path(cwd: str) -> pathlib.Path:
    return pathlib.Path(cwd) / ".custody" / "cursor-baseline.json"


def _save_baseline(cwd: str, files: dict) -> None:
    path = _baseline_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(files), encoding="utf-8")


def cursor_session_start(event: dict) -> None:
    cwd = _cursor_cwd(event)
    _save_baseline(cwd, snapshot_dir(cwd))


def cursor_after_file_edit(event: dict) -> None:
    cwd = _cursor_cwd(event)
    file_path = event["file_path"]
    target = pathlib.Path(file_path)
    disk = target.read_bytes() if target.is_file() else None
    result = core.edits_receipt(file_path=file_path, edits=event.get("edits") or [], disk_bytes=disk)
    payload = {"event": "afterFileEdit", "tool_name": "Edit", "tool_use_id": None,
               "session_id": event.get("conversation_id"), **result}
    _write_receipt(cwd, f"cursor-{uuid.uuid4().hex}", payload)

    # The agent's own edit is accounted for: keep it out of the next shell diff.
    baseline_path = _baseline_path(cwd)
    if not baseline_path.exists():
        return
    try:
        rel = target.resolve().relative_to(pathlib.Path(cwd).resolve()).as_posix()
    except ValueError:
        return
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if disk is None:
        baseline.pop(rel, None)
    else:
        baseline[rel] = {"hash": core.hash_file(file_path), "mode": stat.S_IMODE(target.stat().st_mode)}
    _save_baseline(cwd, baseline)


def cursor_post_tool_use(event: dict) -> None:
    if event.get("tool_name") != "Shell":
        return  # file edits arrive as afterFileEdit
    cwd = _cursor_cwd(event)
    after = snapshot_dir(cwd)
    try:
        exit_code = json.loads(event.get("tool_output") or "{}").get("exitCode")
    except (ValueError, AttributeError):
        exit_code = None
    tool_success = exit_code == 0 if isinstance(exit_code, int) else None
    baseline_path = _baseline_path(cwd)
    if baseline_path.exists():
        before = json.loads(baseline_path.read_text(encoding="utf-8"))
        result = core.bash_receipt(before=before, after=after, tool_success=tool_success, diff_fn=diff_dirs)
    else:
        result = {"declared_scope": None, "status": core.UNVERIFIED,
                  "detail": ("no baseline snapshot -- the sessionStart hook isn't registered, "
                             "or the hook was added mid-session"),
                  "tool_reported_success": tool_success}
    payload = {"event": "postToolUse", "tool_name": "Shell", "tool_use_id": event.get("tool_use_id"),
               "session_id": event.get("conversation_id"), "duration_ms": event.get("duration"), **result}
    _write_receipt(cwd, event.get("tool_use_id") or f"cursor-{uuid.uuid4().hex}", payload)
    _save_baseline(cwd, after)


_HANDLERS = {"PreToolUse": pre_tool_use, "PostToolUse": post_tool_use,
             "sessionStart": cursor_session_start, "afterFileEdit": cursor_after_file_edit,
             "postToolUse": cursor_post_tool_use}


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        # Malformed stdin is exactly the kind of thing a witness has to
        # survive too -- this used to be the one path that raised a
        # traceback out of main() instead of failing quietly like every
        # handler already does below.
        sys.stderr.write(f"custody: could not parse hook input as JSON: {exc}\n")
        return 0
    handler = _HANDLERS.get(event.get("hook_event_name"))
    if handler:
        try:
            handler(event)
        except Exception as exc:  # noqa: BLE001 -- a hook that crashes must never break the agent's turn
            sys.stderr.write(f"custody: {type(exc).__name__}: {exc}\n")
    return 0  # never blocks: this is a witness, not a gate


if __name__ == "__main__":
    sys.exit(main())

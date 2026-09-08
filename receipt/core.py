"""Run a command, snapshot its working directory before and after, and check
what it actually touched against what it was declared to touch.

This is the whole tool: everything else (CLI, evidence writer) is plumbing
around this one function.
"""
from __future__ import annotations

import subprocess
import time

from . import model
from .snapshot import diff as diff_snapshots
from .snapshot import snapshot


def run(task: str, cmd: list[str], watch_dir: str = ".",
        declared_paths: list[str] | None = None) -> dict:
    """Execute `cmd` in `watch_dir`, and report what changed there.

    declared_paths, if given, is the exact set of relative paths the task
    claimed it would touch. Anything touched outside that set is a `fail`.
    If declared_paths is None, no claim was made -- the receipt still
    records exactly what happened, but the status is `unverified`: there's
    nothing to check the touched files against.
    """
    before = snapshot(watch_dir)
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=watch_dir, capture_output=True, text=True)
    seconds = time.monotonic() - started
    after = snapshot(watch_dir)

    changes = diff_snapshots(before, after)
    touched = sorted(set(changes["added"]) | set(changes["modified"]) | set(changes["removed"]))

    if declared_paths is None:
        status = model.UNVERIFIED
        unexpected: list[str] = []
        detail = f"{len(touched)} file(s) touched; no declared scope to check against"
    else:
        declared = set(declared_paths)
        unexpected = [p for p in touched if p not in declared]
        if unexpected:
            status = model.FAIL
            detail = f"touched {len(unexpected)} undeclared file(s): {', '.join(unexpected)}"
        else:
            status = model.PASS
            detail = f"touched only what was declared ({len(touched)} file(s))"

    return {
        "task": task,
        "command": cmd,
        "watch_dir": watch_dir,
        "exit_code": proc.returncode,
        "seconds": round(seconds, 3),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "declared_paths": declared_paths,
        "changes": changes,
        "unexpected": unexpected,
        "status": status,
        "detail": detail,
    }

"""Run a command, snapshot its working directory before and after, and check
what it actually touched against what it was declared to touch.

This is the whole tool: everything else (CLI, evidence writer) is plumbing
around this one function.
"""
from __future__ import annotations

import fnmatch
import subprocess
import time

from . import model
from .redact import redact
from .snapshot import diff as diff_snapshots
from .snapshot import snapshot


def _is_declared(path: str, declared: set[str]) -> bool:
    """A path is covered if it's an exact declared entry, or matches one as
    a glob (`fnmatch`, case-sensitive on every platform -- consistent
    matching regardless of OS matters more here than following whatever
    case convention the local filesystem happens to use).

    Exact match is checked first and separately so a literal declared path
    containing an unintentional glob character (`[`, `]`, `?`, `*` in a
    real filename) still matches itself even if it also happens to be a
    strange glob pattern -- globs are additive, not a replacement for
    exact matching.
    """
    if path in declared:
        return True
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in declared)


def run(task: str, cmd: list[str], watch_dir: str = ".",
        declared_paths: list[str] | None = None) -> dict:
    """Execute `cmd` in `watch_dir`, and report what changed there.

    declared_paths, if given, is the set of relative paths (or glob
    patterns, e.g. `app/*.py`) the task claimed it would touch. Anything
    touched that doesn't match one of them is a `fail`. If declared_paths
    is None, no claim was made -- the receipt still records exactly what
    happened, but the status is `unverified`: there's nothing to check the
    touched files against.
    """
    before = snapshot(watch_dir)
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=watch_dir, capture_output=True, text=True, errors="replace")
    except OSError as exc:
        # The command never ran at all -- `--dir` doesn't exist, the binary
        # isn't found, no permission to execute it. The one promise this
        # tool makes is "get a receipt for what actually happened," and
        # that has to hold here too: report it as a receipt, don't crash
        # before any evidence exists at all. Nothing ran, so nothing was
        # touched -- but the declared promise clearly wasn't kept either,
        # which is a fail, not "nothing to check" (that's what an absent
        # --declare means, a different situation from this one).
        return {
            "task": task,
            "command": [redact(part) for part in cmd],
            "watch_dir": watch_dir,
            "exit_code": None,
            "seconds": round(time.monotonic() - started, 3),
            "stdout": "",
            "stderr": "",
            "declared_paths": declared_paths,
            "changes": {"added": [], "modified": [], "removed": [], "renamed": [], "mode_changed": []},
            "unexpected": [],
            "status": model.FAIL,
            "detail": f"could not launch the command: {exc}",
        }
    seconds = time.monotonic() - started
    after = snapshot(watch_dir)

    changes = diff_snapshots(before, after)
    renamed_endpoints = {r["from"] for r in changes["renamed"]} | {r["to"] for r in changes["renamed"]}
    touched = sorted(set(changes["added"]) | set(changes["modified"]) | set(changes["removed"])
                      | renamed_endpoints | set(changes["mode_changed"]))

    rename_from_by_to = {r["to"]: r["from"] for r in changes["renamed"]}
    mode_only_changed = set(changes["mode_changed"])

    def _annotate(p: str) -> str:
        if p in rename_from_by_to:
            # Name a renamed file's origin too -- "touched b.txt" alone
            # hides that it's actually the declared a.txt under a new
            # name, which is exactly the context someone needs to see
            # this isn't an undeclared *new* file appearing from nowhere.
            return f"{p} (renamed from {rename_from_by_to[p]})"
        if p in mode_only_changed:
            return f"{p} (permissions changed, content unchanged)"
        return p

    if declared_paths is None:
        status = model.UNVERIFIED
        unexpected: list[str] = []
        detail = f"{len(touched)} file(s) touched; no declared scope to check against"
    else:
        declared = set(declared_paths)
        unexpected = [p for p in touched if not _is_declared(p, declared)]
        if unexpected:
            status = model.FAIL
            detail = f"touched {len(unexpected)} undeclared file(s): {', '.join(_annotate(p) for p in unexpected)}"
        else:
            status = model.PASS
            detail = f"touched only what was declared ({len(touched)} file(s))"

    return {
        "task": task,
        "command": [redact(part) for part in cmd],
        "watch_dir": watch_dir,
        "exit_code": proc.returncode,
        "seconds": round(seconds, 3),
        "stdout": redact(proc.stdout),
        "stderr": redact(proc.stderr),
        "declared_paths": declared_paths,
        "changes": changes,
        "unexpected": unexpected,
        "status": status,
        "detail": detail,
    }

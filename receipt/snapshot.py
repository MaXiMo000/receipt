"""Snapshot a directory tree as {relative_path: {hash, mode}}, and diff two
of them.

Deliberately not `git diff` -- an agent's action might touch a repo that has
no git, is mid-rebase, or is a subdirectory that isn't its own repo root.
Walking the filesystem directly means the receipt works everywhere the same
way, at the cost of being slower than git on a huge tree -- fine here, since
this snapshots one task's working directory, not a monorepo.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import stat

# Directories never worth snapshotting: version control internals and the
# tool's own output would make every run look like it touched itself.
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "receipts"}


def snapshot(root: str | pathlib.Path) -> dict[str, dict]:
    """Returns {relpath: {"hash": sha256, "mode": permission bits}}."""
    root = pathlib.Path(root)
    files: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            path = pathlib.Path(dirpath) / name
            rel = str(path.relative_to(root))
            try:
                content_hash = _hash_file(path)
                mode = stat.S_IMODE(path.stat().st_mode)
            except OSError:
                # Gone by the time we got to it (a temp file, a race) --
                # skip rather than fail the whole snapshot over one file.
                continue
            files[rel] = {"hash": content_hash, "mode": mode}
    return files


def _hash_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def diff(before: dict[str, dict], after: dict[str, dict]) -> dict[str, list]:
    """added/modified/removed/renamed, plus mode_changed -- a path whose
    content is byte-identical but whose permission bits changed.

    Without mode_changed, a command flipping a file executable, or
    loosening permissions on something sensitive, is completely invisible
    to a content-hash-only diff: a real, security-relevant change that
    would silently read as "nothing happened here."

    renamed pairs a removed path with an added path sharing the same
    content hash -- without it, `mv a.txt b.txt` is indistinguishable from
    "deleted a.txt, created an entirely new, undeclared b.txt", exactly
    the false-positive FAIL that erodes trust in a scope-check tool
    fastest, since the command did nothing wrong. A file whose *content*
    also changed is reported via `modified`, not `mode_changed` -- that
    case is already visible; mode_changed exists specifically for the
    content-identical case that would otherwise be silent.
    """
    added_paths = set(after) - set(before)
    removed_paths = set(before) - set(after)
    both = set(before) & set(after)
    modified = sorted(p for p in both if before[p]["hash"] != after[p]["hash"])
    mode_changed = sorted(
        p for p in both
        if before[p]["hash"] == after[p]["hash"] and before[p]["mode"] != after[p]["mode"]
    )

    by_hash_removed: dict[str, list[str]] = {}
    for p in removed_paths:
        by_hash_removed.setdefault(before[p]["hash"], []).append(p)
    by_hash_added: dict[str, list[str]] = {}
    for p in added_paths:
        by_hash_added.setdefault(after[p]["hash"], []).append(p)

    renamed = []
    still_added, still_removed = set(added_paths), set(removed_paths)
    for content_hash, old_paths in by_hash_removed.items():
        new_paths = by_hash_added.get(content_hash)
        if not new_paths:
            continue
        # Deterministic pairing when duplicate-content files move at once:
        # sort both sides, zip pairs off in order rather than guessing
        # which specific old path became which specific new path.
        for old_p, new_p in zip(sorted(old_paths), sorted(new_paths)):
            renamed.append({"from": old_p, "to": new_p})
            still_removed.discard(old_p)
            still_added.discard(new_p)

    return {
        "added": sorted(still_added),
        "modified": modified,
        "removed": sorted(still_removed),
        "renamed": sorted(renamed, key=lambda r: r["from"]),
        "mode_changed": mode_changed,
    }

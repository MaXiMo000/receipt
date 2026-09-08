"""Snapshot a directory tree as {relative_path: sha256}, and diff two of them.

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

# Directories never worth snapshotting: version control internals and the
# tool's own output would make every run look like it touched itself.
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "receipts"}


def snapshot(root: str | pathlib.Path) -> dict[str, str]:
    root = pathlib.Path(root)
    files: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            path = pathlib.Path(dirpath) / name
            rel = str(path.relative_to(root))
            try:
                files[rel] = _hash_file(path)
            except OSError:
                # Gone by the time we got to it (a temp file, a race) --
                # skip rather than fail the whole snapshot over one file.
                continue
    return files


def _hash_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def diff(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(p for p in set(before) & set(after) if before[p] != after[p])
    return {"added": added, "modified": modified, "removed": removed}

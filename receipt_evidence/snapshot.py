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
from concurrent.futures import ThreadPoolExecutor

# Directories never worth snapshotting: version control internals and the
# tool's own output would make every run look like it touched itself.
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "receipts"}


def snapshot(root: str | pathlib.Path) -> dict[str, dict]:
    """Returns {relpath: {"hash": sha256, "mode": permission bits}}."""
    root = pathlib.Path(root)
    found: list[tuple[str, int]] = []
    stack = [str(root)]
    while stack:
        try:
            entries = os.scandir(stack.pop())
        except OSError:
            continue
        with entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in _SKIP_DIRS:
                        stack.append(entry.path)
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    # Gone by the time we got to it (a temp file, a race) --
                    # skip rather than fail the whole snapshot over one file.
                    continue
                if not stat.S_ISREG(st.st_mode):
                    # A FIFO, socket, or device node isn't something `open()`
                    # can be trusted to return from: a FIFO with no writer on
                    # the other end blocks forever. Content hashing only makes
                    # sense for regular files anyway.
                    continue
                found.append((entry.path, stat.S_IMODE(st.st_mode)))

    # Hashing is bound by opening files, not by sha256 -- on Windows each
    # open is also scanned by Defender -- and hashlib releases the GIL, so a
    # thread pool is most of the speedup. Measured on home-assistant/core
    # (28k files): 39s for a no-op command before, see README for after.
    with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 4)) as pool:
        hashes = list(pool.map(_try_hash, (path for path, _ in found)))

    files: dict[str, dict] = {}
    for (path, mode), content_hash in zip(found, hashes):
        if content_hash is None:
            continue
        # .as_posix(), not str(): declared scopes are always written with
        # forward slashes ("app/*.py"), and core._is_declared glob-matches
        # against this string directly. str() on Windows returns
        # backslashes, which turns legitimate declared changes into a
        # false `fail`.
        files[pathlib.Path(path).relative_to(root).as_posix()] = {"hash": content_hash, "mode": mode}
    return files


def _try_hash(path: str) -> str | None:
    try:
        return _hash_file(path)
    except OSError:
        return None


def _hash_file(path) -> str:
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

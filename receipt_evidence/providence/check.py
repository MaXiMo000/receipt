"""Conformance checking: does a bundle match the Providence v1 shape, and
does every hash it claims actually match the bytes on disk.

Returns a list of issue strings -- empty means conformant. Never raises on
a malformed bundle; a malformed bundle is exactly what this is for finding.
"""
from __future__ import annotations

import json
import pathlib

from .spec import PROVIDENCE_VERSION, canonical_hash, file_hash, is_safe_id

REQUIRED_ENVELOPE = ("providence_version", "generated_at", "tool")


def _check_envelope(doc, where: str) -> list[str]:
    if not isinstance(doc, dict):
        # A hostile or simply broken bundle can be valid JSON that isn't an
        # object at all -- a bare list, string, number, or null. "Never
        # raises on a malformed bundle" (this module's own module docstring)
        # has to cover that shape too, not just a dict missing some keys.
        return [f"{where}: top-level JSON must be an object, got {type(doc).__name__}"]
    issues = []
    for field in REQUIRED_ENVELOPE:
        if field not in doc:
            issues.append(f"{where}: missing required field '{field}'")
    if doc.get("providence_version") != PROVIDENCE_VERSION:
        issues.append(
            f"{where}: providence_version {doc.get('providence_version')!r} "
            f"!= supported {PROVIDENCE_VERSION}"
        )
    return issues


def check_single_file(path: pathlib.Path) -> list[str]:
    """A single JSON file holding `payload` + `sha256` of its canonical form."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{path}: not readable JSON ({exc})"]

    issues = _check_envelope(doc, str(path))
    if not isinstance(doc, dict):
        return issues  # already reported above; nothing dict-shaped left to check
    if "payload" not in doc:
        issues.append(f"{path}: missing required field 'payload'")
        return issues
    if "sha256" not in doc:
        issues.append(f"{path}: missing required field 'sha256'")
        return issues

    got = canonical_hash(doc["payload"])
    if got != doc["sha256"]:
        issues.append(
            f"{path}: sha256 mismatch -- manifest says {doc['sha256']}, "
            f"payload actually hashes to {got} (tampered or corrupted)"
        )
    return issues


def check_bundle_dir(path: pathlib.Path) -> list[str]:
    """A directory with manifest.json + one `<id>.json` file per item."""
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        return [f"{path}: no manifest.json"]

    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{manifest_path}: not readable JSON ({exc})"]

    issues = _check_envelope(doc, str(manifest_path))
    if not isinstance(doc, dict):
        return issues  # already reported above; nothing dict-shaped left to check
    items = doc.get("items")
    if not isinstance(items, list):
        issues.append(f"{manifest_path}: missing or non-list 'items'")
        return issues

    seen_ids = set()
    for i, item in enumerate(items):
        where = f"{manifest_path}: items[{i}]"
        if not isinstance(item, dict):
            issues.append(f"{where}: must be an object, got {type(item).__name__}")
            continue
        item_id = item.get("id")
        if not item_id:
            issues.append(f"{where}: missing 'id'")
            continue
        if not is_safe_id(item_id):
            # The id becomes a filename inside the bundle. "../x" or an
            # absolute path would make the checker hash a file outside the
            # bundle and report PASS for evidence the bundle never contained.
            issues.append(f"{where}: id {item_id!r} is not a plain file name inside the bundle")
            continue
        if item_id in seen_ids:
            issues.append(f"{where}: duplicate id '{item_id}'")
        seen_ids.add(item_id)

        claimed = item.get("sha256")
        if not claimed:
            issues.append(f"{where} (id={item_id}): missing 'sha256'")
            continue

        evidence_path = path / f"{item_id}.json"
        if evidence_path.is_symlink():
            # A bundle vouches for its own bytes; a link can point anywhere.
            issues.append(f"{where} (id={item_id}): {evidence_path.name} is a symlink, not evidence in the bundle")
            continue
        if not evidence_path.exists():
            issues.append(f"{where} (id={item_id}): no file {evidence_path.name}")
            continue

        actual = file_hash(evidence_path)
        if actual != claimed:
            issues.append(
                f"{where} (id={item_id}): sha256 mismatch -- manifest says "
                f"{claimed}, {evidence_path.name} actually hashes to {actual} "
                f"(tampered or corrupted)"
            )

    # Orphan evidence files: on disk, not referenced by the manifest. Not a
    # tamper signal by itself (could just be an unrelated file), but worth
    # surfacing since a bundle is supposed to be self-describing.
    referenced = {f"{i}.json" for i in seen_ids}
    for f in path.glob("*.json"):
        if f.name != "manifest.json" and f.name not in referenced:
            issues.append(f"{path}: {f.name} exists but isn't referenced by manifest.json")

    return issues


def check_bundle(path: str | pathlib.Path) -> list[str]:
    """Check either bundle form. Returns [] if conformant."""
    path = pathlib.Path(path)
    if not path.exists():
        return [f"{path}: does not exist"]
    if path.is_dir():
        return check_bundle_dir(path)
    return check_single_file(path)

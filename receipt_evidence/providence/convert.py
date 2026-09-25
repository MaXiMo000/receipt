"""Turn receipt's and invariant's real, already-shipped evidence output into
a Providence-conformant bundle.

Neither tool needs to change what it writes for this to work -- these read
the actual shape each one produces today (see their own evidence.py) and
re-emit it in the one canonical form. Wiring a tool to write Providence's
shape *directly* instead of converting after the fact is future work, not
done here -- see SPEC.md's "Status" section.
"""
from __future__ import annotations

import json
import pathlib
import time

from .spec import PROVIDENCE_VERSION, canonical_hash, is_safe_id


def convert_invariant(evidence_dir, out_dir) -> pathlib.Path:
    """invariant/evidence.py's write() output: manifest.json with a `checks`
    list ({name, status, detail, seconds, sha256}) plus one `<name>.json`
    per check. Providence's shape is the same thing under `items`/`id` --
    this renames the two fields and copies the evidence files as-is (their
    bytes, and therefore their sha256, are untouched).
    """
    evidence_dir = pathlib.Path(evidence_dir)
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))

    items = []
    for check in manifest.get("checks", []):
        name = check["name"]
        if not is_safe_id(name):
            raise ValueError(f"check name {name!r} is not a plain file name; refusing to "
                             "read or write outside the evidence directories")
        src = evidence_dir / f"{name}.json"
        (out_dir / f"{name}.json").write_bytes(src.read_bytes())
        items.append({
            "id": name,
            "sha256": check["sha256"],
            "status": check.get("status"),
            "detail": check.get("detail", ""),
        })

    out_manifest = {
        "providence_version": PROVIDENCE_VERSION,
        "tool": "invariant",
        "generated_at": manifest.get("generated_at", time.time()),
        "items": items,
    }
    (out_dir / "manifest.json").write_text(json.dumps(out_manifest, indent=2, sort_keys=True), encoding="utf-8")
    return out_dir


def convert_receipt(receipt_file, out_dir) -> pathlib.Path:
    """receipt/evidence.py's write() output: one file, {schema_version,
    receipt: <result>, sha256, written_at} -- sha256 covers `receipt` under
    receipt's own serialization (indent=2, sorted, str-default), not
    Providence's canonical one. This re-hashes `receipt` canonically rather
    than trusting the original digest across the two conventions, and
    writes a single-file Providence bundle.
    """
    receipt_file = pathlib.Path(receipt_file)
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = json.loads(receipt_file.read_text(encoding="utf-8"))
    payload = doc["receipt"]

    out_doc = {
        "providence_version": PROVIDENCE_VERSION,
        "tool": "receipt",
        "generated_at": doc.get("written_at", time.time()),
        "payload": payload,
        "sha256": canonical_hash(payload),
    }
    out_path = out_dir / receipt_file.name
    out_path.write_text(json.dumps(out_doc, indent=2, sort_keys=True), encoding="utf-8")
    return out_path

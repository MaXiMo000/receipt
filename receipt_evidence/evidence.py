"""Write a receipt to disk: the full record plus a sha256, so it can be
checked later without taking the run's word for it. Same idiom as
invariant's evidence.py, one file per receipt instead of one per check.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import secrets
import time

# Bump when the receipt dict's shape changes in a way a consumer parsing
# the JSON would need to know about (a field renamed or removed -- adding
# a new field is not a breaking change and doesn't need a bump).
SCHEMA_VERSION = 1


def write(result: dict, out_dir: str | pathlib.Path) -> pathlib.Path:
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    # A second-resolution timestamp alone collides silently: two runs
    # started within the same second (a script looping `receipt run`, two
    # parallel CI jobs sharing --out) would overwrite each other with no
    # error -- confirmed live, not hypothetical. The random suffix costs
    # nothing and makes every receipt's filename unique regardless of
    # timing.
    suffix = secrets.token_hex(4)
    path = out_dir / f"{stamp}-{suffix}.json"

    blob = json.dumps(result, indent=2, sort_keys=True, default=str)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()

    record = {
        "schema_version": SCHEMA_VERSION,
        "receipt": result,
        "sha256": digest,
        "written_at": time.time(),
    }
    path.write_text(json.dumps(record, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path

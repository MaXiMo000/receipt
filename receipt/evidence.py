"""Write a receipt to disk: the full record plus a sha256, so it can be
checked later without taking the run's word for it. Same idiom as
invariant's evidence.py, one file per receipt instead of one per check.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import time


def write(result: dict, out_dir: str | pathlib.Path) -> pathlib.Path:
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = out_dir / f"{stamp}.json"

    blob = json.dumps(result, indent=2, sort_keys=True, default=str)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()

    record = {"receipt": result, "sha256": digest, "written_at": time.time()}
    path.write_text(json.dumps(record, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path

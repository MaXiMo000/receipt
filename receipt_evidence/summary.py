"""`receipt summary DIR`: one Markdown report of a directory of receipts --
`receipt run` receipts or custody-hook ones -- for a pull request comment
or a job summary. What a reviewer needs from an agent's session: what it
touched, whether each change matched what it said it did, and whether the
record itself is intact.
"""
from __future__ import annotations

import collections
import json
import pathlib

from .ledger import LEDGER, verify

_MARK = {"pass": "✅", "fail": "❌", "unverified": "⚪"}


def _rows(directory: pathlib.Path):
    for path in sorted(directory.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            yield {"status": "fail", "what": path.name, "detail": "unreadable receipt"}
            continue
        body = doc.get("receipt") or doc.get("payload") or {}
        what = body.get("task") or " ".join(filter(None, [body.get("tool_name"),
                                                           body.get("file_path") or ""])) or path.stem
        yield {"status": body.get("status", "unverified"), "what": what, "detail": body.get("detail", "")}


def render(directory: str | pathlib.Path, allowed_signers: str | None = None) -> tuple[str, bool]:
    """(markdown, ok). ok is False when any receipt failed or the record
    itself is not intact."""
    directory = pathlib.Path(directory)
    rows = list(_rows(directory))
    counts = collections.Counter(r["status"] for r in rows)
    lines = ["### receipt", "",
             f"{len(rows)} receipt(s): {counts['pass']} pass, {counts['fail']} fail, "
             f"{counts['unverified']} unverified", ""]
    if (directory / LEDGER).exists():
        problems, summary = verify(directory, allowed_signers)
        if problems:
            lines += ["**The record itself is not intact:**", ""] + [f"- {p}" for p in problems] + [""]
        else:
            signed = f", signed by `{summary['signed_by']}`" if summary.get("signed_by") else ""
            lines += [f"Ledger: chain intact{signed}.", ""]
    else:
        problems = []
        lines += ["Ledger: none -- these receipts are not chained.", ""]
    # Failures first: they are what a reviewer has to read.
    order = {"fail": 0, "unverified": 1, "pass": 2}
    shown = sorted(rows, key=lambda r: order.get(r["status"], 1))
    if shown:
        lines += ["| | what | detail |", "|---|---|---|"]
        for r in shown[:200]:
            detail = r["detail"].replace("|", "\\|").replace("\n", " ")[:200]
            lines.append(f"| {_MARK.get(r['status'], '?')} | {r['what'].replace('|', '/')} | {detail} |")
        if len(shown) > 200:
            lines.append(f"\n{len(shown) - 200} more not shown.")
    return "\n".join(lines) + "\n", not counts["fail"] and not problems

"""A signed, hash-chained ledger beside a directory of receipts.

A receipt's own sha256 proves its bytes match the hash written inside it --
which anyone editing the file can rewrite too. The ledger closes that:

* every receipt written gets one line in `ledger.jsonl`: its file name, the
  sha256 of its bytes, and the sha256 of the previous line, so editing,
  deleting or inserting a receipt -- or rewriting history -- breaks the
  chain at a point `receipt verify` names;
* with a signing key, the ledger is signed after every append with
  `ssh-keygen -Y sign` -- the mechanism git uses to sign commits, with keys
  people already have -- so the chain cannot be rebuilt by someone who can
  write files but does not hold the key. `receipt verify --allowed-signers`
  checks it, the same allowed_signers file git uses.

Concurrent writers to one directory would race on the ledger; one receipts
directory per writer, as both writers here already have.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess

LEDGER = "ledger.jsonl"
SIGNATURE = "ledger.jsonl.sig"
NAMESPACE = "receipt-ledger"
KEY_ENV = "RECEIPT_SIGNING_KEY"
GENESIS = "0" * 64


class LedgerError(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def append(directory: str | pathlib.Path, receipt: str | pathlib.Path, key: str | None = None) -> None:
    directory = pathlib.Path(directory)
    ledger = directory / LEDGER
    lines = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
    entry = {"file": pathlib.Path(receipt).name,
             "sha256": _sha(pathlib.Path(receipt).read_bytes()),
             "prev": _sha(lines[-1].encode("utf-8")) if lines else GENESIS}
    with open(ledger, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    key = key or os.environ.get(KEY_ENV)
    if key:
        sign(directory, key)


def sign(directory: pathlib.Path, key: str) -> None:
    ledger = pathlib.Path(directory) / LEDGER
    sig = ledger.with_name(SIGNATURE)
    sig.unlink(missing_ok=True)  # ssh-keygen refuses to overwrite
    ran = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(key), "-n", NAMESPACE, str(ledger)],
                         capture_output=True, text=True, check=False)
    if ran.returncode != 0:
        # A ledger that should be signed and is not must not look signed.
        raise LedgerError(f"could not sign {ledger}: {(ran.stderr or '').strip()}")


def verify(directory: str | pathlib.Path, allowed_signers: str | None = None) -> tuple[list[str], dict]:
    """(problems, summary). Empty problems: every receipt is in the chain,
    unchanged, nothing removed, and (with allowed_signers) the ledger is
    signed by an allowed key."""
    directory = pathlib.Path(directory)
    ledger = directory / LEDGER
    if not ledger.exists():
        return [f"no {LEDGER} in {directory}: nothing chains these receipts"], {}
    raw = ledger.read_text(encoding="utf-8")
    problems, seen = [], set()
    prev = GENESIS
    for number, line in enumerate(raw.splitlines(), 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"{LEDGER}:{number} is not valid JSON")
            prev = _sha(line.encode("utf-8"))
            continue
        if entry.get("prev") != prev:
            problems.append(f"{LEDGER}:{number} does not follow line {number - 1} -- "
                            "the history before it was changed")
        prev = _sha(line.encode("utf-8"))
        name = entry.get("file", "")
        seen.add(name)
        path = directory / name
        if not path.exists():
            problems.append(f"{name} is in the ledger but missing -- deleted")
        elif _sha(path.read_bytes()) != entry.get("sha256"):
            problems.append(f"{name} was changed after it was written")
    for path in sorted(directory.glob("*.json")):
        if path.name not in seen:
            problems.append(f"{path.name} is not in the ledger -- added afterwards")

    signer = None
    if allowed_signers:
        signer, why = _verify_signature(ledger, allowed_signers)
        if signer is None:
            problems.append(f"{SIGNATURE}: {why}")
    return problems, {"receipts": len(seen), "signed_by": signer}


def _verify_signature(ledger: pathlib.Path, allowed: str) -> tuple[str | None, str]:
    sig = ledger.with_name(SIGNATURE)
    if not sig.exists():
        return None, "the ledger is not signed"
    found = subprocess.run(["ssh-keygen", "-Y", "find-principals", "-s", str(sig), "-f", allowed],
                           capture_output=True, text=True, check=False)
    principal = (found.stdout or "").strip().splitlines()[:1]
    if found.returncode != 0 or not principal:
        return None, "signed by a key that is not in the allowed signers"
    with open(ledger, "rb") as fh:
        ok = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", allowed, "-I", principal[0],
                             "-n", NAMESPACE, "-s", str(sig)], stdin=fh, capture_output=True, check=False)
    if ok.returncode != 0:
        return None, "the signature does not match the ledger -- it was changed after signing"
    return principal[0], ""

"""Best-effort redaction of secret-shaped text before it's written into a
receipt.

Not a guarantee -- a regex sweep can't catch every shape a secret takes --
but it closes the exact failure mode found live during the portfolio audit:
a wrapped command's own stdout/stderr echoing a real credential
(`API_KEY=sk-supersecret12345`) landed verbatim in the receipt JSON on disk.
Receipts are meant to be kept and handed to someone else as evidence; the
evidence artifact itself must not be a credential-leak vector.
"""
from __future__ import annotations

import re

MASK = "[REDACTED]"

# key=value / key: value pairs where the key name says "this is a secret" --
# the most common real leak shape (the exact one confirmed live in the
# audit: an env var echoed by the wrapped command).
_KEYED = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:SECRET|TOKEN|API[_-]?KEY|ACCESS[_-]?KEY|"
    r"PASSWORD|PASSWD|PWD|CREDENTIAL)[A-Za-z0-9_]*)(\s*[:=]\s*)(['\"]?)(\S+)\3"
)

# scheme://user:pass@host -- a DSN or an authenticated URL carrying a
# credential in its userinfo component (the same shape as invariant's DSN
# leak, in case a wrapped command prints a connection string).
_URL_CRED = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)([^:/\s@]+):([^@/\s]+)@")

# Recognizable provider token prefixes -- these are secrets on sight,
# regardless of what surrounds them.
_PREFIXED = re.compile(
    r"\b(sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z\-_]{30,})\b"
)

_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


def redact(text: str) -> str:
    """Returns `text` with secret-shaped substrings replaced by a mask.

    Applied to captured stdout/stderr before a receipt is written. Best
    effort, not exhaustive -- it catches the common shapes (env-var-style
    key=value pairs, credentialed URLs, well-known token prefixes, PEM
    private key blocks), not every possible one.
    """
    if not text:
        return text
    text = _PEM_BLOCK.sub(MASK, text)
    text = _URL_CRED.sub(lambda m: f"{m.group(1)}{m.group(2)}:{MASK}@", text)
    text = _KEYED.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    text = _PREFIXED.sub(MASK, text)
    return text

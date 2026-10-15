# Security

## Reporting a problem

Report vulnerabilities privately through GitHub: **Security -> Report a
vulnerability** on this repository. Please do not open a public issue for
anything exploitable. You will get an acknowledgement within 72 hours.

## What receipt handles

- **Captured output is redacted** for secret-shaped text (tokens, keys,
  passwords in URLs and key/value pairs) before anything is written.
- **Receipts stay local.** They are JSON files in a directory you choose; the
  tool makes no network calls of its own.
- **Tamper-evident, not tamper-proof.** Each receipt carries a sha256 and the
  ledger is hash-chained, so an edit is detected. Someone with write access to
  the directory can still delete the whole set; keep receipts you rely on
  somewhere they cannot.

## Supply chain

- GitHub Actions are pinned to full commit SHAs.
- Releases publish to PyPI through trusted publishing (OIDC); no long-lived
  token is stored in the repository.

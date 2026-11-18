# Changelog

## 0.5.0 - 2026-11-18

- `custody-hook` supports Codex: shell commands are recorded, and an `apply_patch` fails if it changed a file its patch didn't name.
- `custody-hook` supports Cursor: `afterFileEdit` checks each edit is on disk, and shell commands are recorded against a rolling snapshot.
- README: wrapping an Aider run with `receipt run`.

## 0.4.1 - 2026-10-16

- Documentation: a demo of real output at the top of the README, a security policy, a changelog, and issue templates.

## 0.4.0 - 2026-10-08

- Signed, hash-chained ledger and `receipt verify`.
- `receipt summary` and a PR summary action.

## 0.3.0 - 2026-09-27

- Tool cache directories reported apart from the scope check.
- Snapshots about 4x faster.
- PowerShell receipts.

## 0.2.0 - 2026-09-26

- One package: custody, clicked and providence folded in; import as `receipt_evidence`.

## 0.1.2 - 2026-09-26

- `--declare` globs match on Windows.

## 0.1.1 - 2026-09-10

- Snapshots no longer hang on a FIFO.
- Redaction matches quoted key names.

## 0.1.0 - 2026-09-09

- First release: snapshot-diff receipts with declared-scope checks, rename and permission tracking, redaction, and a JSON Schema.

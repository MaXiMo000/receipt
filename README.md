# receipt

**Run a command. Get a receipt for what it actually touched — not just what
it was asked to do.**

An AI agent (or a script, or a CI job) says it's going to fix a bug in one
file. Nothing checks whether that's what it actually did until someone
reviews the diff by hand, if they do at all. `receipt` snapshots the working
directory before and after, and reports `pass`, `fail`, or `unverified` —
same three-status shape as
[invariant](https://github.com/MaXiMo000/invariant),
[firedrill](https://github.com/MaXiMo000/firedrill), and
[carabiner](https://github.com/MaXiMo000/carabiner).

```
$ receipt run --task "fix the auth bug" --declare app/auth.py \
    -- python fix_auth.py
[FAIL] touched 1 undeclared file(s): app/payments.py
receipt written to receipts/20260908T121251Z-4f2c9a1b.json
```

## Three statuses, one of them meaning something different here

`pass` — touched only what was declared. `fail` — touched something outside
the declared scope, named exactly. `unverified` — no scope was declared for
this run at all.

That third one is a deliberate difference from invariant/firedrill/
carabiner, where `unverified` means "a check that should have run, didn't."
Here it means "no promise was made this time" — plain audit logging is a
normal, legitimate use of this tool, not a degraded one. So `unverified`
does **not** fail the build; only a broken declared promise (`fail`) does.

## What it actually does

1. Hashes and permission-bits every file under the watched directory (sha256
   + mode, skipping `.git`, `__pycache__`, etc.).
2. Runs the given command, captures stdout/stderr/exit code/timing, and
   redacts secret-shaped text (env-var-style `API_KEY=...` assignments,
   credentialed URLs, well-known token prefixes, PEM key blocks) before any
   of it is stored — see "What redaction doesn't mean" below. A command that
   never launches at all (bad `--dir`, missing binary) still produces a
   receipt — `fail`, with the launch error as the detail — instead of a
   Python traceback and no evidence.
3. Snapshots the directory again, diffs the two. A removed path and an added
   path with identical content are reported as one `renamed` pair, not an
   unrelated delete-plus-create; a path whose content is byte-identical but
   whose permission bits changed is reported as `mode_changed` — see "What
   `touched` means" below.
4. If a scope was declared (exact paths, or glob patterns like `app/*.py`),
   checks the diff against it.
5. Writes the whole thing — command, task, diff, declared scope, verdict —
   to `receipts/<timestamp>-<random>.json` alongside a sha256 of the receipt
   itself, same evidence-bundle idiom as invariant's `--evidence`.

Zero dependencies — stdlib only (`hashlib`, `subprocess`, `argparse`,
`fnmatch`).

## Install

```bash
pip install receipt-evidence        # the command it installs is `receipt`
```

Or from a checkout, for development:

```bash
pip install -e .
```

## Use

```bash
receipt run --task "what this is supposed to do" \
  --declare path/one.py,app/*.py \
  --dir . --out receipts/ \
  -- your-command --with --args
```

Omit `--declare` to just log what happened without a scope to check it
against (`unverified`, still a written receipt, still exit 0).

## Test

```bash
python tests/test_receipt.py
```

## What `touched` means

`touched` is the union of every file that was added, removed, had its
content modified, was renamed (a removed path and an added path sharing a
content hash), or had its permission bits changed with content otherwise
identical. A rename or a chmod on a path outside the declared scope is a
real `fail`, named clearly — `sneaky.txt (renamed from output.txt)`, or
`secret.env (permissions changed, content unchanged)` — not silently
folded into "nothing happened" the way a plain content-hash diff would.

## What `pass` doesn't mean

`pass` only means "touched nothing outside the declared scope **within
`--dir`**." A write anywhere outside that tree — `/tmp`, `~`, a sibling
directory, an absolute path elsewhere in a monorepo — is invisible to
`receipt` and won't affect the verdict. Point `--dir` at the smallest tree
that actually bounds what the task could legitimately touch; don't read
`pass` as "touched nothing on the filesystem."

## What redaction doesn't mean

Captured stdout/stderr and the command's own argv are swept for
secret-shaped text (`receipt/redact.py`) before a receipt is written — this
closes a real gap found during review: a wrapped command that echoed
`API_KEY=sk-...` landed that value verbatim in the receipt JSON. The sweep
is a regex net for common shapes, not a guarantee. It will not catch a
secret with no recognizable shape (e.g. a bare 40-character hex string with
no key name attached, split across two log lines, or base64-wrapped). If a
command's output might contain something sensitive in an unusual shape,
don't assume the receipt is safe to share as-is — read it first.

## What's deliberately not here yet

No network/API-call capture — only filesystem diffing. "What did this agent
touch" is answerable this way; "what did this agent call" isn't, without
hooking into a specific agent framework's own trace or intercepting
traffic, which is a real, separate, much bigger project.

No policy evaluation or rule composition beyond a flat declared-scope
check — that's deliberately a different tool's job. `receipt` stays the
evidence producer;
[invariant](https://github.com/MaXiMo000/invariant) is where richer policy
(is this evidence actually OK, across multiple runs, with other checks
composed in) belongs.

MIT licensed.

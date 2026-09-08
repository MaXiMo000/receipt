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
receipt written to receipts/20260908T121251Z.json
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

1. Hashes every file under the watched directory (sha256, skipping `.git`,
   `__pycache__`, etc.).
2. Runs the given command, captures stdout/stderr/exit code/timing.
3. Hashes the directory again, diffs the two snapshots.
4. If a scope was declared, checks the diff against it.
5. Writes the whole thing — command, task, diff, declared scope, verdict —
   to `receipts/<timestamp>.json` alongside a sha256 of the receipt itself,
   same evidence-bundle idiom as invariant's `--evidence`.

Zero dependencies — stdlib only (`hashlib`, `subprocess`, `argparse`).

## Install

```bash
pip install -e .
```

## Use

```bash
receipt run --task "what this is supposed to do" \
  --declare path/one.py,path/two.py \
  --dir . --out receipts/ \
  -- your-command --with --args
```

Omit `--declare` to just log what happened without a scope to check it
against (`unverified`, still a written receipt, still exit 0).

## Test

```bash
python tests/test_receipt.py
```

## What's deliberately not here yet

No network/API-call capture — only filesystem diffing. "What did this agent
touch" is answerable this way; "what did this agent call" isn't, without
hooking into a specific agent framework's own trace or intercepting
traffic, which is a real, separate, much bigger project. See `PLAN.md`.

No glob support in `--declare` — exact relative paths only, for now.

MIT licensed.

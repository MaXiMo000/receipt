# receipt

[![ci](https://github.com/MaXiMo000/receipt/actions/workflows/ci.yml/badge.svg)](https://github.com/MaXiMo000/receipt/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/receipt-evidence)](https://pypi.org/project/receipt-evidence/)

**Verified receipts for what actually happened -- not what was claimed.**

A shell command, an AI agent's tool call, a click in a browser: each one
comes with a self-report ("I edited auth.py", "Saved!"). `receipt` checks
the self-report against reality, independently, and writes down what it
found in a tamper-evident file you can hand to someone else.

| what happened | how you capture it | what gets checked |
|---|---|---|
| a shell command | `receipt run --declare ... -- cmd` | every file it touched, against the scope you declared |
| an AI agent's `Edit`/`Write`/`Bash` call | the `custody-hook` Claude Code hook | the bytes on disk are exactly what the tool says it wrote |
| one browser interaction | `with capture(page): ...` (Playwright) | every network request it really made, and optionally the files it caused |

Every receipt is JSON with a sha256 over its content -- the
[Providence](PROVIDENCE_SPEC.md) shape -- and `providence check` tells you
if one was edited after it was written.

```bash
pip install receipt-evidence     # zero dependencies; installs `receipt`, `custody-hook`, `providence`
```

One package, stdlib only. (It used to be four: `receipt-evidence`,
`custody-evidence`, `clicked-evidence` and `providence-evidence`. The old
names still install and point here.) Import it as `receipt_evidence`.

Three statuses throughout, same as
[invariant](https://github.com/MaXiMo000/invariant),
[firedrill](https://github.com/MaXiMo000/firedrill) and
[carabiner](https://github.com/MaXiMo000/carabiner): `pass`, `fail`, and
`unverified` -- a check that couldn't decide is never reported as a pass.

---

## 1. Shell commands: `receipt run`

```
$ receipt run --task "fix the auth bug" --declare app/auth.py \
    -- python fix_auth.py
[FAIL] touched 1 undeclared file(s): app/payments.py
receipt written to receipts/20260908T121251Z-4f2c9a1b.json
```

```bash
receipt run --task "what this is supposed to do" \
  --declare path/one.py,app/*.py \
  --dir . --out receipts/ \
  -- your-command --with --args
```

`pass` -- touched only what was declared. `fail` -- touched something
outside the declared scope, named exactly. `unverified` -- no `--declare`
was given: plain audit logging, a normal use, so it does **not** fail the
build; only a broken declared promise does.

What it does:

1. Hashes and permission-bits every regular file under `--dir` (skipping
   `.git`, `__pycache__`, `node_modules`, `.venv`). A FIFO, socket or device
   node is skipped rather than opened -- `open()` on a FIFO with no writer
   blocks forever (a real bug: it hung two CI runs for 6 hours each).
2. Runs the command, capturing stdout/stderr/exit code/timing, and redacts
   secret-shaped text before storing any of it. A command that never
   launches (bad `--dir`, missing binary) still produces a receipt --
   `fail`, with the launch error -- not a traceback and no evidence.
3. Snapshots again and diffs. A delete plus a create with identical content
   is one `renamed` pair; identical content with changed permission bits is
   `mode_changed`.
4. Checks the diff against the declared scope (exact paths or globs like
   `app/*.py`, matched with `/` on every OS).
5. Writes command, task, diff, scope and verdict to
   `receipts/<timestamp>-<random>.json` with a sha256 of the record.
   Shape: [`schema/receipt.schema.json`](schema/receipt.schema.json).

**Tool caches.** A directory carrying a
[`CACHEDIR.TAG`](https://bford.info/cachedir/) with the spec's signature --
ruff, pytest, mypy and Cargo all write one -- is a tool's own cache.
Changes inside it are listed under `cache` in the receipt and in the
verdict line, never judged against the scope: `ruff format src` on flask
reads `[PASS] touched only what was declared (1 file(s)) (+4 in tagged
cache directories)` instead of failing on `.ruff_cache/`. A tag without
the signature hides nothing.

**Speed.** Every file is hashed before and after, on a thread pool.
home-assistant/core (28k files) costs about 10 s per run on Windows,
down from 39 s; point `--dir` at the smallest tree that bounds the task.

**What `touched` means:** added, removed, modified, renamed (either end),
or permissions changed. A rename or chmod outside the scope is a real
`fail`, named clearly -- `sneaky.txt (renamed from output.txt)`.

**What `pass` doesn't mean:** "touched nothing outside the declared scope
*within `--dir`*." A write to `/tmp`, `~` or a sibling directory is
invisible here. Point `--dir` at the smallest tree that bounds the task.

## 2. AI agent tool calls: the Claude Code hook

`custody-hook` runs on every `Edit`, `Write` and `Bash` call a Claude Code
session makes, and writes one receipt per call to `.custody/receipts/`.

For `Edit`/`Write` it rebuilds the text the tool reports having left on
disk -- `Write`'s `content`, or `Edit`'s `originalFile` with its
`oldString` -> `newString` replacement -- and compares it to the file's
real bytes:

| what's on disk | status |
|---|---|
| exactly what the tool reported writing | `pass` |
| anything else (a partial write, a formatter or another process racing it) | **`fail`** |
| the file is gone after the call | `unverified` |
| not UTF-8, so not comparable as text | falls back to a before/after sha256 |

A file's CRLF line endings and a UTF-8 BOM are not differences: `Edit`
keeps both on disk while reporting normalized text (measured against real
Claude Code, not assumed). `Bash` calls are recorded -- every file the
command changed -- but judged `unverified`: an agent's shell command
declares no scope to check against.

Wire it into `.claude/settings.json` (a project) or
`~/.claude/settings.json` (everywhere):

```json
{
  "hooks": {
    "PreToolUse": [{ "matcher": "Edit|Write|Bash|PowerShell",
      "hooks": [{ "type": "command", "command": "custody-hook" }] }],
    "PostToolUse": [{ "matcher": "Edit|Write|Bash|PowerShell",
      "hooks": [{ "type": "command", "command": "custody-hook" }] }]
  }
}
```

**`custody-hook` must be on the `PATH` Claude Code runs hooks with** -- a
project-local `.venv` isn't, and a hook command that doesn't resolve fails
silently. Use `pipx install receipt-evidence`, or put the absolute path in
`command`. Add `.custody/` to `.gitignore`.

The hook never blocks a tool call and always exits 0: it's a witness, not
a gate. Verified end to end in a real Claude Code session.

## 3. Browser interactions: `capture()`

```python
from receipt_evidence.browser import capture

with capture(page, task="delete account button", watch_dir="./data") as c:
    page.click("#delete-account-button")
    page.wait_for_load_state("networkidle")

c.write("receipt.json")        # a Providence bundle
print(c.result["requests"])    # every request/response actually made
print(c.result["changes"])     # what landed in ./data because of it
```

`c.result["requests"]` is every request made during the block -- URL,
method, POST body, status, or a `failure` reason if it never got a
response. That's what the browser actually sent, not what the page's
JavaScript claims. `watch_dir` (optional) snapshot-diffs a local directory
too, closing the loop from a click to a real backend side effect.

Works with any Playwright `Page` -- Playwright is never imported, only
`.on()`/`.remove_listener()` are called -- so there's no dependency on it
or on any particular version.

## 4. The bundle format: `providence`

```
$ providence check proof/
PASS  proof/ is a conformant Providence bundle

$ providence check proof/   # after someone hand-edits a file in it
FAIL  proof/manifest.json: items[0] (id=no_negative_payments): sha256 mismatch -- ...
```

One versioned shape for "content + sha256(content) + when", in two forms:
a single JSON file (`payload` + `sha256` of its canonical serialization),
or a directory (`manifest.json` + one `<id>.json` per item). Full spec:
[PROVIDENCE_SPEC.md](PROVIDENCE_SPEC.md). `providence check` verifies every
hash, that every file is accounted for, and rejects item ids or symlinks
that would reach outside the bundle.

```bash
providence convert-invariant proof/ out/     # an invariant --evidence dir
providence convert-receipt receipts/x.json out/
```

**What this is not:** a signature. A sha256 proves content wasn't edited
*after* the bundle was written -- not who wrote it, nor that the whole
bundle wasn't regenerated from scratch.

---

## 5. A record you can trust: the signed ledger

A receipt's sha256 proves its bytes match the hash inside it -- and anyone
who edits the file can rewrite that hash too. So every receipt written,
by `receipt run` or by `custody-hook`, also gets a line in the directory's
`ledger.jsonl`: its file name, its bytes' sha256, and the sha256 of the
previous line. With a key, the ledger is signed after every append using
`ssh-keygen -Y sign` -- the mechanism git uses to sign commits, with keys
you already have, and no Python dependency.

```bash
export RECEIPT_SIGNING_KEY=~/.ssh/receipts_ed25519      # or: receipt run --sign KEY
receipt verify receipts/ --allowed-signers allowed_signers
```

Measured with real keys:

| what was done to the directory | `receipt verify` |
|---|---|
| nothing | PASS, signed by `ci@example.com` |
| one receipt edited | FAIL, names it |
| one receipt deleted | FAIL, names it |
| one added afterwards | FAIL, names it |
| a receipt edited **and the whole ledger rebuilt consistently** | PASS without `--allowed-signers`; **FAIL** with it |
| re-signed with someone else's key | FAIL: not in the allowed signers |

The fifth row is the point: the chain alone catches every accident and
any edit that doesn't bother covering its tracks, but someone who can
write the directory can rebuild it. Only the signature -- a key they don't
hold -- stops that, so verify with `--allowed-signers` whenever it matters.

## 6. On the pull request

```yaml
- uses: MaXiMo000/receipt@v0.4.0
  with:
    directory: .custody/receipts          # where custody-hook wrote them
    allowed-signers: .github/allowed_signers
```

One comment on the PR, updated in place, plus the job summary: how many
receipts passed, failed or went unverified, whether the ledger is intact
and who signed it, and every receipt -- failures first. `receipt summary
DIR` prints the same Markdown anywhere. The step fails when a receipt
failed or the record isn't intact (`fail: false` to only report).

## Redaction, and what it doesn't mean

Captured stdout/stderr, argv, URLs and POST bodies are swept for
secret-shaped text (`receipt_evidence/redact.py`: `KEY=value` pairs,
credentialed URLs, well-known token prefixes, PEM private keys) before
anything is written. It's a regex net for common shapes, not a guarantee:
a secret with no recognizable shape gets through. Read a receipt before
sharing it if a command might have printed something sensitive.

## Python API

```python
from receipt_evidence.core import run                        # receipt run, as a function
from receipt_evidence.browser import capture                 # browser interactions
from receipt_evidence.providence.check import check_bundle   # [] means conformant
from receipt_evidence.providence.spec import make_bundle     # write your own
```

## Tests

```bash
pip install -e . jsonschema
python tests/test_receipt.py
python tests/test_providence.py
python tests/test_claude_core.py
python tests/test_claude_hook.py     # tool_response shapes captured from real Claude Code
python tests/test_browser.py

pip install playwright && python -m playwright install chromium
python tests/test_browser_live.py    # a real Chromium, a real server
```

CI runs on Linux and Windows, plus the live-browser suite and a cold
install of the built wheel that exercises all three commands.

## Not here yet

- Writes outside the watched directory (`/tmp`, `~`) aren't seen by
  `receipt run` -- that needs OS-level tracing, a separate project.
- No network capture for shell commands, only for browser interactions.
- Policy beyond a flat declared scope belongs in
  [invariant](https://github.com/MaXiMo000/invariant), which reads these
  receipts as one of its check types.

MIT licensed.

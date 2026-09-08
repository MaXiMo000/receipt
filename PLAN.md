# receipt — continuity notes

Read this first if you're picking this project up in a new session.

## Status as of 2026-09-08: working MVP, not a scaffold

Unlike `witness`, this one is fully real and tested end to end in this
session, not just written-and-hoped:

- `receipt/snapshot.py`, `receipt/core.py`, `receipt/evidence.py`,
  `receipt/cli.py` -- all real, all exercised.
- `python tests/test_receipt.py` -- 6/6 passing (snapshot diffing, PASS/
  FAIL/UNVERIFIED verdicts).
- Ran the actual CLI by hand against real subprocesses: a command that
  touched only its declared file -> real PASS with a real evidence file
  written; one that also wrote an undeclared `secrets.env` -> real FAIL
  naming it exactly.
- Zero dependencies. Pure stdlib.

## What's genuinely not built

1. **No network/API-call capture.** The pitch mentioned "API calls made" --
   that's not implemented. Filesystem diffing via subprocess wrapping can't
   see an arbitrary external binary's network calls without something like
   strace/dtrace or a proxy in front of it, which is a materially bigger
   project (a "record everything a process does" tool, not "record what
   files changed"). Decide deliberately whether that's worth adding later,
   or whether "what files changed" is the useful 80% on its own.
2. **No glob support in `--declare`.** Exact relative paths only. `fnmatch`
   support (`app/**/*.py`) would be the natural first extension, cheap to
   add, not done because nothing has needed it yet.
3. **No CI, no GitHub repo pushed yet** -- if you're picking this up, that's
   the first thing to do: `gh repo create MaXiMo000/receipt` following the
   dual-account fix in the `github-dual-account-setup` memory (switch
   accounts, then fix the remote to the `github-personal` SSH alias --
   `gh repo create` always points it at the wrong host).
4. **No PyPI name check done.** `invariant` was blocked by PyPI's project
   name policy for undisclosed reasons; `receipt` is an equally generic
   word and may hit the same wall. Don't assume the plain name will work --
   check when you actually try to publish, and have a fallback name ready
   (e.g. `receipt-cli`) rather than being surprised.

## Where this could actually go

The real differentiator over "just diff the filesystem yourself" would be
wiring this into an actual agent loop (Claude Agent SDK, or Claude Code's
own hooks) so `--task` and `--declare` come from the agent's own stated
plan automatically, rather than being typed by hand. That's the natural
next real step once the standalone tool has been used for real a few times
-- not before.

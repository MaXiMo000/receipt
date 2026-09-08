"""receipt run --task "..." [--declare a.py,b.py] [--out receipts/] -- <command...>"""
from __future__ import annotations

import argparse
import sys

from .core import run as run_task
from .evidence import write as write_receipt
from .model import FAIL


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--" in argv:
        split = argv.index("--")
        own_args, cmd = argv[:split], argv[split + 1:]
    else:
        own_args, cmd = argv, []

    parser = argparse.ArgumentParser(prog="receipt")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run a command and receipt what it touched")
    run_p.add_argument("--task", required=True, help="what the command was asked to do")
    run_p.add_argument("--dir", default=".", dest="watch_dir", help="directory to watch (default: .)")
    run_p.add_argument("--declare", default=None,
                        help="comma-separated relative paths the task is allowed to touch; "
                             "omit to record without a declared scope (status: unverified)")
    run_p.add_argument("--out", default="receipts", help="directory to write the receipt into")

    args = parser.parse_args(own_args)

    if not cmd:
        print("error: no command given -- pass it after `--`", file=sys.stderr)
        return 2

    declared = args.declare.split(",") if args.declare else None
    result = run_task(args.task, cmd, watch_dir=args.watch_dir, declared_paths=declared)
    path = write_receipt(result, args.out)

    print(f"[{result['status'].upper()}] {result['detail']}")
    print(f"receipt written to {path}")

    # Unlike invariant, `unverified` here is not a failure to gain
    # assurance -- it's "no scope was promised this run," a normal mode
    # (plain audit logging). Only a broken promise (FAIL) fails the build.
    return 1 if result["status"] == FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

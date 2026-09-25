"""providence check <bundle> | providence convert-invariant <dir> <out> | providence convert-receipt <file> <out>"""
from __future__ import annotations

import argparse

from .check import check_bundle
from .convert import convert_invariant, convert_receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="providence")
    sub = parser.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser("check", help="verify a bundle's hashes match its manifest")
    check_p.add_argument("bundle", help="a bundle directory (manifest.json + items) or single-file bundle")

    ci_p = sub.add_parser("convert-invariant", help="convert an invariant --evidence dir into a Providence bundle")
    ci_p.add_argument("evidence_dir")
    ci_p.add_argument("out_dir")

    cr_p = sub.add_parser("convert-receipt", help="convert a receipt evidence file into a Providence bundle")
    cr_p.add_argument("receipt_file")
    cr_p.add_argument("out_dir")

    args = parser.parse_args(argv)

    if args.command == "check":
        issues = check_bundle(args.bundle)
        if issues:
            for issue in issues:
                print(f"FAIL  {issue}")
            print(f"\n{len(issues)} issue(s) -- not conformant")
            return 1
        print(f"PASS  {args.bundle} is a conformant Providence bundle")
        return 0

    if args.command == "convert-invariant":
        out = convert_invariant(args.evidence_dir, args.out_dir)
        print(f"wrote {out}")
        return 0

    if args.command == "convert-receipt":
        out = convert_receipt(args.receipt_file, args.out_dir)
        print(f"wrote {out}")
        return 0

    return 2  # argparse's required=True makes this unreachable; kept honest anyway.


if __name__ == "__main__":
    raise SystemExit(main())

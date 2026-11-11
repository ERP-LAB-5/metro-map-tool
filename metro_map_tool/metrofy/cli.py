# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ERP-LAB-5
"""metrofy — the command line. See the package docstring."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .build import check, finalize

SCHEMA = Path(__file__).with_name("schema.json")


def _read(path: str):
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="metrofy", description="A metro map made from code, for display.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("finalize", help="check, place, render and check again; write the page")
    f.add_argument("input", help="a Metrofy input (JSON), or - for stdin")
    f.add_argument("output", help="the HTML page to write")
    f.add_argument("--spec", metavar="FILE", help="also write the metro-map spec Metrofy placed")
    f.add_argument("--json", action="store_true", help="the whole receipt, not the summary")
    c = sub.add_parser("check", help="the input's problems, one per line")
    c.add_argument("input")
    sub.add_parser("schema", help="print the input's JSON Schema")
    args = ap.parse_args(argv)

    if args.cmd == "schema":
        sys.stdout.write(SCHEMA.read_text(encoding="utf-8"))
        return 0
    try:
        doc = _read(args.input)
    except (OSError, ValueError) as exc:
        print(f"metrofy: cannot read {args.input}: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "check":
        problems = check(doc)
        for p in problems:
            print(p)
        return 1 if problems else 0

    receipt, out = finalize(doc)
    if receipt["ok"]:
        Path(args.output).write_text(out, encoding="utf-8", newline="\n")
        if args.spec:
            Path(args.spec).write_text(json.dumps(receipt["spec"], ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8", newline="\n")
        receipt["page"] = str(Path(args.output).resolve())
    shown = dict(receipt) if args.json else {k: v for k, v in receipt.items() if k != "spec"}
    print(json.dumps(shown, ensure_ascii=False, indent=2))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

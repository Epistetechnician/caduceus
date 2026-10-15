"""Command line: audit, anchor, verify, demo. Read-only unless stated."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__, audit, readback
from .demo import run_demo
from .digest import canonical_bytes


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="caduceus", description="Verified evolution for Hermes (Phase 1 substrate).")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="read-only audit of a Hermes home (skills, usage, curator ledger)")
    a.add_argument("--hermes-home", default="~/.hermes")
    a.add_argument("--min-use", type=int, default=3, help="use_count for protected-suite candidates")

    c = sub.add_parser("anchor-curator", help="print a Merkle anchor over the Hermes curator ledger (read-only)")
    c.add_argument("--hermes-home", default="~/.hermes")

    v = sub.add_parser("verify-curator", help="check the curator ledger still extends a previously printed anchor")
    v.add_argument("--hermes-home", default="~/.hermes")
    v.add_argument("--size", type=int, required=True)
    v.add_argument("--root", required=True)

    r = sub.add_parser("verify-ledger", help="independently validate a Caduceus ledger file")
    r.add_argument("path")

    d = sub.add_parser("demo", help="run the synthetic end-to-end sandbox demo")
    d.add_argument("--out", help="empty output directory (default: a temp dir)")

    args = ap.parse_args(argv)
    if args.cmd == "audit":
        _print(audit.audit_hermes_home(args.hermes_home, args.min_use))
        return 0
    if args.cmd in ("anchor-curator", "verify-curator"):
        path = os.path.join(os.path.expanduser(args.hermes_home), "skills", ".curator_ledger.jsonl")
        lines = audit.curator_ledger_lines(path)
        if args.cmd == "anchor-curator":
            _print(audit.anchor_lines(lines))
            return 0
        ok = audit.verify_append_only(lines, args.size, args.root)
        _print({"append_only_extension_of_anchor": ok})
        return 0 if ok else 2
    if args.cmd == "verify-ledger":
        try:
            _print(readback.verify_file(args.path))
            return 0
        except readback.ReadbackError as exc:
            print("INVALID: %s" % exc, file=sys.stderr)
            return 2
    if args.cmd == "demo":
        _print(run_demo(args.out))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

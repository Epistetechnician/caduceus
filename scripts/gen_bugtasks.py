"""Generate the bug-repair task manifest and the oracle audit (Phase 6c).

Writes digests and metadata only: mutated sources are not stored, because they
are reproducible from (module, op, ordinal) and the assessment split must not
sit on disk where a proposer can read it.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from caduceus import bugtasks as b  # noqa: E402
from caduceus.digest import domain_digest  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split-seed", type=int, required=True)
    ap.add_argument("--per-module", type=int, default=3)
    ap.add_argument("--out", default=os.path.join(REPO, "tasks", "bugtasks-v1.manifest.json"))
    ap.add_argument("--family", default="syntactic", choices=sorted(b.FAMILIES))
    ap.add_argument("--audit", action="store_true", help="also run the full mutant audit (slow)")
    a = ap.parse_args()
    skip = {"__init__", "__main__", "cli", "demo", "hermes_runner", "bench", "executor", "drill"}
    mods = sorted(f[:-3] for f in os.listdir(os.path.join(REPO, b.PKG)) if f.endswith(".py") and f[:-3] not in skip)
    tasks = b.generate(REPO, mods, a.split_seed, a.per_module, a.family)
    meta = [{k: v for k, v in t.items() if k != "mutated_source"} for t in tasks]
    m = {"family": a.family, "split_seed": a.split_seed, "per_module": a.per_module, "n": len(meta),
         "by_split": {s: sum(1 for t in meta if t["split"] == s) for s in b.SPLITS},
         "commitments": {s: b.split_commitment(tasks, s) for s in b.SPLITS},
         "tasks": [{k: t[k] for k in ("task_id", "module", "split", "original_digest", "mutated_digest")} for t in meta]}
    if a.audit:
        m["oracle_audit"] = b.oracle_audit(REPO, mods)
    m["manifest_digest"] = domain_digest("caduceus.bugtasks.manifest.v1", {k: v for k, v in m.items()})
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(m, fh, indent=1, sort_keys=True)
    print(json.dumps({k: m[k] for k in ("n", "by_split", "commitments", "manifest_digest")}, indent=1))


if __name__ == "__main__":
    main()

"""Combine mined real-bug tasks (mine_realbugs.py output) into a digest-only manifest.
Usage: gen_realbugs_manifest.py <mined_dir> <clones_dir> <split_seed> <out.json>"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from caduceus import bugtasks as b, realbugs as rb  # noqa: E402
from caduceus.digest import domain_digest  # noqa: E402

mined, clones, seed, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
entries = []
for f in sorted(glob.glob(os.path.join(mined, "mined-*.jsonl"))):
    entries += [json.loads(ln) for ln in open(f)]
entries.sort(key=lambda e: (e["repo"], e["commit"]))
tasks = rb.build(clones, entries, seed)
meta = [{k: t[k] for k in ("task_id", "module", "repo", "commit", "path", "tests", "split", "original_digest", "mutated_digest")} for t in tasks]
m = {"family": "real", "split_seed": seed, "n": len(meta), "by_split": {s: sum(1 for t in meta if t["split"] == s) for s in b.SPLITS},
     "commitments": {s: b.split_commitment(tasks, s) for s in b.SPLITS}, "tasks": meta}
m["manifest_digest"] = domain_digest("caduceus.bugtasks.manifest.v1", dict(m))
json.dump(m, open(out, "w"), indent=1, sort_keys=True)
print(json.dumps({k: m[k] for k in ("n", "by_split", "commitments", "manifest_digest")}, indent=1))

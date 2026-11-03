"""Fresh-commit coding eval (exp-026): real fix commits dated after SINCE, scored by the commit's own tests in a network-less container.
Stages: build (manifest + prompts + lock), judge <model_label> <out.jsonl> (oracle + ledger assessment).
Usage: PYTHONPATH=<repo> python3 scripts/fresh_bench.py build|judge ..."""
import glob
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from caduceus import bugrepair as br, bugtasks, realbugs  # noqa: E402
from caduceus.digest import domain_digest  # noqa: E402
from caduceus.ledger import Ledger  # noqa: E402

SINCE = "2025-07-01"
EID = "exp-026-fresh-bench"
FRESH = os.path.expanduser("~/hsi-runs/realbugs/fresh")
CLONES = os.path.expanduser("~/hsi-runs/realbugs/repos")
WORK = os.path.expanduser("~/hsi-runs/exp-026")
MANIFEST = os.path.join(REPO, "tasks", "fresh-v1.manifest.json")
LEDGER = os.path.join(REPO, "ledgers", "exp-026-fresh.jsonl")


def entries():
    es = [json.loads(ln) for f in sorted(glob.glob(os.path.join(FRESH, "fresh-*.jsonl"))) for ln in open(f)]
    return sorted(es, key=lambda e: (e["repo"], e["commit"]))


def tasks():
    return realbugs.build(CLONES, entries(), 0, with_spec=True)


def build():
    ts = tasks()
    meta = [{k: t[k] for k in ("task_id", "repo", "commit", "path", "tests", "original_digest", "mutated_digest")} for t in ts]
    m = {"family": "real-fresh", "since": SINCE, "n": len(meta), "repos": sorted({t["repo"] for t in meta}), "tasks": meta}
    m["manifest_digest"] = domain_digest("caduceus.bugtasks.manifest.v1", dict(m))
    json.dump(m, open(MANIFEST, "w"), indent=1, sort_keys=True)
    os.makedirs(WORK, exist_ok=True)
    with open(os.path.join(WORK, "prompts.jsonl"), "w") as fh:
        for t in ts:
            msgs = [{"role": "system", "content": br.SYSTEM}, {"role": "user", "content": br.user_prompt(bugtasks.proposer_view(t))}]
            fh.write(json.dumps({"arm": "base", "seed": 0, "split": "fresh", "task_id": t["task_id"], "messages": msgs}) + "\n")
    print("tasks", len(ts), "repos", len(m["repos"]), "manifest", m["manifest_digest"][:16])


def lock():
    m = json.load(open(MANIFEST))
    Ledger(LEDGER).append("prediction_lock", {"experiment_id": EID, "manifest_digest": m["manifest_digest"], "since": SINCE, "n": m["n"],
                                              "metric": "repair_pass_rate_micro", "claim": "LocalDevelopmentEvidenceOnly"})
    print("locked")


def judge(label, path):
    ts = {t["task_id"]: t for t in tasks()}
    rows = [json.loads(ln) for ln in open(path)]

    def one(r):
        e = br.parse_edit(r["text"])
        if e is None:
            return False
        new = br.apply_edit(ts[r["task_id"]]["mutated_source"], *e)
        return bool(new) and realbugs.verify_repair(CLONES, ts[r["task_id"]], new)["passed"]

    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(one, rows))
    out = [{"task_id": r["task_id"], "passed": p} for r, p in zip(rows, res)]
    json.dump(out, open(os.path.join(WORK, "judged-%s.json" % label), "w"))
    Ledger(LEDGER).append("assessment", {"experiment_id": EID, "arm": label, "units": len(out), "passes": sum(res),
                                         "outcomes_digest": br.outcome_digest([{"arm": label, "seed": 0, "task_id": o["task_id"], "passed": o["passed"]} for o in out])})
    print(label, "pass", sum(res), "/", len(out))


if __name__ == "__main__":
    {"build": build, "lock": lock}.get(sys.argv[1], lambda: judge(sys.argv[2], sys.argv[3]))()

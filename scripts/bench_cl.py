"""Drive a Phase 4 candidate experiment on CL-bench, end to end.

  fit       run the untouched base skill on the FIT split, grade, store failures in custody
  propose   Hermes (no tools) proposes a refined SKILL.md from fit failures only
  run       locked candidate experiment on the sealed ASSESSMENT split
  exchange  assemble all 13 lanes and record the Exchange decision

Model calls go through HermesExecutor (isolated temp homes, Hermes login).
Nothing from CL-bench is written to the repository; working files live under
~/.caduceus/bench (owner-only).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from caduceus import bench  # noqa: E402
from caduceus.executor import HermesExecutor, default_config_from_env  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.expanduser("~/.caduceus/bench")
DATA = os.path.expanduser("~/Documents/GitHub/CL-bench/CL-bench.jsonl")
SKILL_REL = "reasoning/context-learning"
RANDOM_ROOT, RANDOM_REL = os.path.expanduser("~/.hermes/skills"), "software-development/systematic-debugging"
HERMES_SRC = "~/.hermes/hermes-agent"
_opened = []


def model(name, root=None, rel=None):
    """Thread-safe model callable: one isolated executor per thread."""
    cfg = default_config_from_env(HERMES_SRC, name, "anthropic", "https://api.anthropic.com", None, use_hermes_login=True)
    cfg = cfg.__class__(**dict(cfg.__dict__, timeout_s=600))
    local = threading.local()

    def call(p):
        if not hasattr(local, "ex"):
            local.ex = HermesExecutor(cfg, root, rel, allow_model_calls=True)
            _opened.append(local.ex)
        return local.ex(p)
    return call


def fit(args):
    raw = json.load(open(args.prereg))
    tasks = bench.load_tasks(DATA, raw["max_chars"], raw["exclude"])
    fit_set, assess = bench.split(tasks, raw["split_seed"], raw["n_fit"], raw["n_assess"])
    assert bench.commitment(assess) == raw["assessment_commitment"]
    base = model(raw["executor"]["model"], os.path.join(WORK, "base/skills"), SKILL_REL)
    judge = model(raw["judge_primary"])
    from concurrent.futures import ThreadPoolExecutor

    def one(t):
        try:
            text = base(bench.prompt(t))
            g = judge(bench.grade_prompt(t, text))
        except Exception as e:  # noqa: BLE001
            return {"idx": t["idx"], "error": str(e)[:200]}
        return {"idx": t["idx"], "score": bench.parse_grade(g), "grade": g, "response": text,
                "rubrics": t["rubrics"], "system_head": t["system"][:1500], "category": t["category"]}
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, fit_set))
    os.makedirs(os.path.join(WORK, args.candidate), mode=0o700, exist_ok=True)
    path = os.path.join(WORK, args.candidate, "fit.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"assessment_commitment": raw["assessment_commitment"], "rows": rows}, fh)
    print(json.dumps({"fit": len(rows), "pass": sum(r.get("score") == 1 for r in rows),
                      "errors": sum("error" in r for r in rows)}))


def propose(args):
    raw = json.load(open(args.prereg))
    fitlog = json.load(open(os.path.join(WORK, args.candidate, "fit.json")))
    base_md = open(os.path.join(WORK, "base/skills", SKILL_REL, "SKILL.md")).read()
    fails = [r for r in fitlog["rows"] if r.get("score") == 0]
    parts = []
    for r in fails:
        parts.append("TASK (system prompt head):\n%s\n\nRUBRICS:\n%s\n\nGRADER VERDICT:\n%s\n" % (
            r["system_head"][:800], "\n".join("- " + x for x in r["rubrics"]), r["grade"][:600]))
    p = ("You maintain an agent skill used for tasks that supply new context (documents, rules, procedures) "
         "and are graded all-or-nothing against expert rubrics. Below is the current SKILL.md and %d tasks where "
         "the agent FAILED. Find the general, recurring failure patterns (not task-specific facts) and write an "
         "improved SKILL.md. Keep the same frontmatter name; bump version to 1.1.0; keep it under 60 lines; "
         "give concrete, checkable habits. Output ONLY the SKILL.md file content.\n\nCURRENT SKILL.md:\n%s\n\n"
         "FAILURES:\n\n%s" % (len(fails), base_md, "\n---\n".join(parts)))
    out = model(args.proposer_model)(p).strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[1].rsplit("```", 1)[0]
    if not out.startswith("---"):
        raise SystemExit("proposer did not return a SKILL.md")
    d = os.path.join(WORK, args.candidate, "skills", SKILL_REL)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "SKILL.md"), "w").write(out.rstrip() + "\n")
    log = {"task_idx_seen": [r["idx"] for r in fitlog["rows"]], "model_calls": 1 + 2 * len(fitlog["rows"]),
           "assessment_commitment_at_proposal": fitlog["assessment_commitment"], "failures_used": len(fails)}
    json.dump(log, open(os.path.join(WORK, args.candidate, "proposer_log.json"), "w"), indent=1)
    print(json.dumps(log))


def run(args):
    from caduceus.custody import Custody
    from caduceus.ledger import Ledger

    raw = bench.load_prereg(args.prereg)
    ledger = Ledger(args.ledger)
    m = raw["executor"]["model"]
    arms = {"untouched_base": model(m, os.path.join(WORK, "base/skills"), SKILL_REL),
            "fixed_policy": model(m, os.path.join(WORK, "fixed/skills"), SKILL_REL),
            "deterministic_random": model(m, RANDOM_ROOT, RANDOM_REL),
            "candidate": model(m, os.path.join(WORK, args.candidate, "skills"), SKILL_REL)}
    res = bench.run(raw, DATA, ledger, arms, model(raw["judge_primary"]), model(raw["judge_second"]),
                    custody=Custody("~/.caduceus/custody"), workers=args.workers)
    with open(args.ledger.replace(".jsonl", ".result.json"), "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    print(json.dumps(res, indent=1, sort_keys=True))


def exchange(args):
    from caduceus import exchange as xc
    from caduceus.ledger import Ledger
    from caduceus.propose import propose_skill

    raw = bench.load_prereg(args.prereg)
    pre = {"metric": "cl_bench_solve_rate", "threshold_micro": 0, "min_effect_micro": raw["min_effect_micro"],
           "alpha_micro": raw["alpha_micro"], "n": raw["n_assess"],
           "seeds": [raw["split_seed"], raw["order_seed"], raw["bootstrap_seed"]], "arms": list(bench.ARMS),
           "holdout_commitment": raw["assessment_commitment"], "max_search_cost_units": 1500,
           "experiment_id": raw["experiment_id"], "experiment_prereg_digest": bench.prereg_digest(raw)}
    cand, _ = propose_skill(os.path.join(WORK, "base/skills"), SKILL_REL,
                            os.path.join(WORK, args.candidate, "skills", SKILL_REL), "hermes-%s-proposer" % args.proposer_model, pre,
                            candidate_id="skill:%s:%s" % (SKILL_REL, args.candidate))
    log = json.load(open(os.path.join(WORK, args.candidate, "proposer_log.json")))
    out = bench.assemble(cand, raw, args.ledger, REPO, log)
    xc.record(Ledger(os.path.join(REPO, "ledgers/governance.jsonl")), cand, out["verdict"], out["lyre_report"])
    with open(os.path.join(REPO, "candidates", args.candidate + ".json"), "w") as fh:
        json.dump({"candidate": cand.as_dict(), "candidate_digest": cand.digest(), "paths": list(cand.paths)},
                  fh, indent=2, sort_keys=True)
    print(json.dumps({"decision": out["verdict"].as_dict(), "lanes": out["evidence"]}, indent=1, sort_keys=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["fit", "propose", "run", "exchange"])
    ap.add_argument("--prereg", required=True)
    ap.add_argument("--ledger")
    ap.add_argument("--candidate", default="cl-001")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--proposer-model", default="claude-haiku-5-5")
    args = ap.parse_args()
    try:
        {"fit": fit, "propose": propose, "run": run, "exchange": exchange}[args.phase](args)
    finally:
        for e in _opened:
            e.close()


if __name__ == "__main__":
    main()

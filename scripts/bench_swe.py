"""Drive a Phase 4 candidate experiment on SWE-bench Lite (oracle retrieval), end to end.

  pull      pull the evaluation images for every FIT and ASSESSMENT task (Docker)
  fit       run the untouched base skill on the FIT split; grade with the SWE-bench harness
  propose   Hermes (no tools) proposes a refined SKILL.md from fit failures only
  run       locked candidate experiment on the sealed ASSESSMENT split
  exchange  assemble all 13 lanes and record the Exchange decision

Grading is the benchmark's own tests (swebench harness, separate venv). The
primary instrument is one harness run; the second instrument is an
independent re-run of the same patches (flakiness check). Caduceus code makes
no network call; only this driver shells out to Docker and the harness.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_cl  # noqa: E402
from caduceus import bench  # noqa: E402

REPO = bench_cl.REPO
WORK = os.path.expanduser("~/.caduceus/swe")
DATA = DATA_ORACLE = os.path.join(WORK, "lite_oracle.jsonl")
HARNESS_PY = os.path.join(WORK, "venv", "bin", "python")
SKILL_REL = "software-development/patch-writing"
RANDOM_ROOT, RANDOM_REL = os.path.expanduser("~/.hermes/skills"), "creative/songwriting-and-ai-music"
bench_cl.WORK = WORK  # model() helpers are shared; working dir is SWE's


def extract_patch(text: str) -> str:
    m = re.search(r"<patch>\s*\n(.*?)</patch>", text, re.S)
    if not m:
        m = re.search(r"```(?:diff|patch)?\s*\n(--- .*?)```", text, re.S)
    body = m.group(1) if m else (text if text.lstrip().startswith("--- ") else "")
    return body.rstrip() + "\n" if body.strip() else ""


AGENT_DATA = os.path.join(WORK, "lite_agentic.jsonl")
AGENT_PROMPT = ("The repository {repo} is checked out at /testbed (current directory). Resolve this issue by editing "
                "the source files. Do not modify or add tests. You may run code and existing tests.\n\n<issue>\n"
                "{issue}\n</issue>\nWhen finished, reply DONE.")
AGENTIC = False


def prep_agentic(args):
    """Same rows as the oracle file, but the prompt is the bare issue: the agent explores /testbed itself."""
    with open(DATA_ORACLE) as src, open(AGENT_DATA, "w") as dst:
        for line in src:
            r = json.loads(line)
            r["text"] = AGENT_PROMPT.format(repo=r["repo"], issue=r["problem_statement"])
            dst.write(json.dumps(r) + "\n")
    print(json.dumps({"written": AGENT_DATA, "sha256": bench.file_sha256(AGENT_DATA)}))


def agent_model(name, root=None, rel=None):
    """Hermes with terminal + file tools inside the task's SWE-bench image, no network; returns its git diff."""
    import threading
    import uuid

    from caduceus.executor import HermesExecutor, default_config_from_env

    by_prompt = {}
    with open(AGENT_DATA) as fh:
        for line in fh:
            r = json.loads(line)
            by_prompt[r["text"]] = r["instance_id"]
    cfg = default_config_from_env(bench_cl.HERMES_SRC, name, "anthropic", "https://api.anthropic.com", None,
                                  use_hermes_login=True)
    cfg = cfg.__class__(**dict(cfg.__dict__, timeout_s=1800))
    local = threading.local()

    def call(prompt):
        if not hasattr(local, "ex"):
            local.ex = HermesExecutor(cfg, root, rel, allow_model_calls=True)
            bench_cl._opened.append(local.ex)
        iid = by_prompt[prompt]
        label = "caduceus.call=%s" % uuid.uuid4().hex
        env = {"TERMINAL_ENV": "docker", "TERMINAL_DOCKER_IMAGE": image(iid), "TERMINAL_CWD": "/testbed",
               "TERMINAL_CONTAINER_MEMORY": "3072", "TERMINAL_CONTAINER_PERSISTENT": "false",
               "TERMINAL_DOCKER_EXTRA_ARGS": json.dumps(["--network=none", "--platform=linux/amd64", "--label=" + label])}
        extra = {"toolsets": ["terminal", "file"], "max_iterations": 30, "task_id": label.split("=")[1][:12],
                 "collect_command": "cd /testbed && git diff"}
        try:
            for attempt in range(3):
                try:
                    diff = local.ex(prompt, env_extra=env, request_extra=extra)
                    return "<patch>\n%s</patch>" % diff if diff.strip() else ""
                except Exception as e:  # noqa: BLE001
                    print("call_error agent %s attempt=%d %s" % (iid, attempt, str(e)[:120]), file=sys.stderr, flush=True)
                    if attempt == 2:
                        raise
        finally:
            ids = subprocess.run(["docker", "ps", "-aq", "--filter", "label=" + label],
                                 capture_output=True, text=True).stdout.split()
            if ids:
                subprocess.run(["docker", "rm", "-f"] + ids, capture_output=True)
    return call


def arm_model(name, root=None, rel=None):
    return agent_model(name, root, rel) if AGENTIC else bench_cl.model(name, root, rel)


def image(instance_id: str) -> str:
    return "swebench/sweb.eval.x86_64.%s:latest" % instance_id.replace("__", "_1776_")


def pull(args):
    raw = json.load(open(args.prereg))
    tasks = bench.load_swe_tasks(DATA, raw["max_chars"], raw["exclude"], raw.get("categories"))
    fit, assess = bench.split(tasks, raw["split_seed"], raw["n_fit"], raw["n_assess"])

    def one(t):
        r = subprocess.run(["docker", "pull", "-q", "--platform", "linux/amd64", image(t["instance_id"])],
                           capture_output=True, text=True)
        return t["instance_id"], r.returncode
    with ThreadPoolExecutor(4) as pool:
        bad = [i for i, rc in pool.map(one, fit + assess) if rc != 0]
    print(json.dumps({"pulled": len(fit) + len(assess) - len(bad), "failed": bad}))


def harness(preds, run_id, workers):
    """preds: list of (instance_id, model_name, patch). Returns {(instance_id, model_name): 0|1|None}."""
    out = {}
    by_model = {}
    for iid, name, patch in preds:
        by_model.setdefault(name, []).append({"instance_id": iid, "model_name_or_path": name, "model_patch": patch})
    for name, rows in sorted(by_model.items()):
        path = os.path.join(WORK, "preds-%s-%s.json" % (run_id, name))
        json.dump(rows, open(path, "w"))
        subprocess.run([HARNESS_PY, "-m", "swebench.harness.run_evaluation", "-d", "SWE-bench/SWE-bench_Lite",
                        "-p", path, "--max_workers", str(workers), "-id", run_id, "-t", "900",
                        "-i"] + [r["instance_id"] for r in rows], cwd=WORK, capture_output=True, text=True)
        rep_path = os.path.join(WORK, "%s.%s.json" % (name, run_id))
        rep = json.load(open(rep_path)) if os.path.exists(rep_path) else {}
        res, err = set(rep.get("resolved_ids", [])), set(rep.get("error_ids", []))
        logdir = os.path.join(WORK, "logs", "run_evaluation", run_id, name)
        for r in rows:
            iid = r["instance_id"]
            log = os.path.join(logdir, iid, "run_instance.log")
            if iid in err and os.path.exists(log) and "patch apply failed" in open(log, errors="replace").read().lower():
                err.discard(iid)  # a patch that does not apply is the arm's failure (0), not an infra error
            if not r["model_patch"].strip():
                out[(iid, name)] = 0  # empty or unparseable patch: a real failure of the arm, not infra
            elif iid in err or not rep:
                out[(iid, name)] = None
            else:
                out[(iid, name)] = int(iid in res)
    return out


def fit(args):
    raw = json.load(open(args.prereg))
    tasks = bench.load_swe_tasks(DATA, raw["max_chars"], raw["exclude"], raw.get("categories"))
    fit_set, assess = bench.split(tasks, raw["split_seed"], raw["n_fit"], raw["n_assess"])
    assert bench.commitment(assess) == raw["assessment_commitment"]
    base = arm_model(raw["executor"]["model"], os.path.join(WORK, "base/skills"), SKILL_REL)

    def one(t):
        try:
            return t, base(t["user"])
        except Exception as e:  # noqa: BLE001
            return t, None
    with ThreadPoolExecutor(args.workers) as pool:
        outs = list(pool.map(one, fit_set))
    grades = harness([(t["instance_id"], "base", extract_patch(o)) for t, o in outs if o is not None],
                     "%s-fit" % raw["experiment_id"], args.workers)
    rows = []
    for t, o in outs:
        rows.append({"idx": t["idx"], "instance_id": t["instance_id"], "repo": t["category"],
                     "score": grades.get((t["instance_id"], "base")), "response": o,
                     "patch": extract_patch(o) if o else "",
                     "issue_head": t["user"].split("</issue>")[0][:2500]})
    d = os.path.join(WORK, args.candidate)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(d, "fit.json"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"assessment_commitment": raw["assessment_commitment"], "rows": rows}, fh)
    print(json.dumps({"fit": len(rows), "pass": sum(r["score"] == 1 for r in rows),
                      "empty_patch": sum(not r["patch"] for r in rows),
                      "ungraded": sum(r["score"] is None for r in rows)}))


def propose(args):
    raw = json.load(open(args.prereg))
    d = os.path.join(WORK, args.candidate)
    fitlog = json.load(open(os.path.join(d, "fit.json")))
    base_md = open(os.path.join(WORK, "base/skills", SKILL_REL, "SKILL.md")).read()
    fails = [r for r in fitlog["rows"] if r["score"] == 0]
    parts = ["ISSUE:\n%s\n\nAGENT PATCH (%s):\n%s\n" % (
        r["issue_head"][:1500], ("no change made" if AGENTIC else "empty or unparseable") if not r["patch"] else "tests failed" if AGENTIC else "applied, tests failed or did not apply",
        r["patch"][:1500]) for r in fails]
    setting = ("the agent gets an issue and a shell plus file tools inside the repository checkout (no network, "
               "about 30 tool calls); its `git diff` is graded by the repository's hidden tests" if AGENTIC else
               "the agent sees an issue and the relevant source files and must reply with one unified diff that "
               "applies with `git apply` and makes the repository's hidden tests pass")
    p = ("You maintain an agent skill used to fix GitHub issues: " + setting + ". Below is the current SKILL.md and %d tasks where the agent FAILED (its patch did not "
         "apply or the tests failed). Find the general, recurring failure patterns (not task-specific fixes) and "
         "write an improved SKILL.md. Keep the same frontmatter name; bump version to 1.1.0; keep it under 60 "
         "lines; give concrete, checkable habits. Output ONLY the SKILL.md file content.\n\nCURRENT SKILL.md:\n%s"
         "\n\nFAILURES:\n\n%s" % (len(fails), base_md, "\n---\n".join(parts)))
    out = bench_cl.model(args.proposer_model)(p).strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[1].rsplit("```", 1)[0]
    if not out.startswith("---"):
        raise SystemExit("proposer did not return a SKILL.md")
    sd = os.path.join(d, "skills", SKILL_REL)
    os.makedirs(sd, exist_ok=True)
    open(os.path.join(sd, "SKILL.md"), "w").write(out.rstrip() + "\n")
    log = {"task_idx_seen": [r["idx"] for r in fitlog["rows"]], "model_calls": 1 + len(fitlog["rows"]),
           "assessment_commitment_at_proposal": fitlog["assessment_commitment"], "failures_used": len(fails)}
    json.dump(log, open(os.path.join(d, "proposer_log.json"), "w"), indent=1)
    print(json.dumps(log))


def run(args):
    from caduceus.custody import Custody
    from caduceus.ledger import Ledger

    raw = bench.load_prereg(args.prereg)
    m = raw["executor"]["model"]
    arms = {"untouched_base": arm_model(m, os.path.join(WORK, "base/skills"), SKILL_REL),
            "fixed_policy": arm_model(m, os.path.join(WORK, "fixed/skills"), SKILL_REL),
            "deterministic_random": arm_model(m, RANDOM_ROOT, RANDOM_REL),
            "candidate": arm_model(m, os.path.join(WORK, args.candidate, "skills"), SKILL_REL)}
    eid = raw["experiment_id"]

    def batch_grade(items):
        preds = [(t["instance_id"], arm, extract_patch(text)) for t, arm, text in items]
        first = harness(preds, eid + "-r1", args.workers)
        second = harness(preds, eid + "-r2", args.workers)
        return {(t["idx"], arm): (first.get((t["instance_id"], arm)), second.get((t["instance_id"], arm)))
                for t, arm, _ in items}

    res = bench.run(raw, DATA, Ledger(args.ledger), arms, None, None, custody=Custody("~/.caduceus/custody"),
                    workers=args.workers, batch_grade=batch_grade)
    with open(args.ledger.replace(".jsonl", ".result.json"), "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    print(json.dumps(res, indent=1, sort_keys=True))


def exchange(args):
    bench_cl.SKILL_REL = SKILL_REL
    bench_cl.exchange(args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["prep-agentic", "pull", "fit", "propose", "run", "exchange"])
    ap.add_argument("--prereg", default="")
    ap.add_argument("--ledger")
    ap.add_argument("--candidate", default="swe-001")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--proposer-model", default="claude-sonnet-5-5")
    args = ap.parse_args()
    global DATA, AGENTIC
    if args.phase != "prep-agentic":
        AGENTIC = "agentic" in json.load(open(args.prereg))["benchmark"].lower()
        DATA = AGENT_DATA if AGENTIC else DATA_ORACLE
    try:
        {"prep-agentic": prep_agentic, "pull": pull, "fit": fit, "propose": propose, "run": run, "exchange": exchange}[args.phase](args)
    finally:
        for e in bench_cl._opened:
            e.close()


if __name__ == "__main__":
    main()

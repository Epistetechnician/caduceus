"""CL-bench adapter: candidate experiments and the Exchange on an external benchmark.

CL-bench (Tencent, local checkout; PRD 7 "external held-out check") gives each
task a system prompt, a user message carrying new context plus the task, and
expert rubrics. Grading is all-or-nothing: a response scores 1 only if every
rubric is met. Data never enters this repository or the ledger: the ledger
holds task indices and digests only, raw outputs go to owner-only custody.

Design (preregistered in `caduceus.prereg.bench.v1`):
  * tasks: single-turn (system + user) with total length <= max_chars; task
    indices in `exclude` (seen by the experimenter) are dropped;
  * a seeded shuffle splits them into a FIT set (the proposer may see base
    results on it) and a sealed ASSESSMENT set (committed by digest before the
    proposer runs; never shown to it);
  * arms: untouched_base (current skill), fixed_policy (a hand-written,
    non-learned refinement fixed before any data), deterministic_random (a
    different, unrelated skill), candidate (proposed refinement); one run per
    task per arm; cluster = task;
  * primary instrument: judge model A with the rubric grader below; second
    instrument: judge model B, same rubrics, graded independently;
  * decision: the same rule as `experiment.run_paired` (bootstrap lower bound
    > 0 and mean >= min effect, against ALL three controls).
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import lanes, readback
from .admission import REQUIRED_LANES, AdmissionPolicy, evaluate
from .candidate import REQUIRED_NONCLAIM, Candidate, validate_candidate
from .digest import domain_digest, sha256_hex
from .ledger import Ledger
from .stats import MICRO, paired_bootstrap

SCHEMA = "caduceus.prereg.bench.v1"
ARMS = ("untouched_base", "fixed_policy", "deterministic_random", "candidate")
_KEYS = {"schema", "experiment_id", "hypothesis", "benchmark", "dataset_sha256", "max_chars", "exclude",
         "split_seed", "n_fit", "n_assess", "assessment_commitment", "order_seed", "bootstrap_seed",
         "bootstrap_resamples", "min_effect_micro", "alpha_micro", "max_notrun_micro", "executor",
         "judge_primary", "judge_second", "skill", "base_tree_digest", "random_skill",
         "max_category_loss", "decision_rule", "nonclaim"}

GRADER = (
    "You are a strict grader. Decide whether the RESPONSE satisfies EVERY requirement in the RUBRICS.\n"
    "Scoring is all-or-nothing: 1 only if every rubric item is fully met, otherwise 0.\n"
    "Check each rubric item in order and mark it yes or no.\n\n"
    "RUBRICS:\n{rubrics}\n\nRESPONSE:\n{response}\n\n"
    'Reply with ONLY this JSON: {{"items": ["yes" or "no", ...], "score": 0 or 1}}'
)


class BenchError(Exception):
    pass


# --- data --------------------------------------------------------------------

def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_tasks(path: str, max_chars: int, exclude: List[int],
               categories: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for idx, line in enumerate(fh):
            if idx in exclude:
                continue
            row = json.loads(line)
            msgs = row["messages"]
            if [m["role"] for m in msgs] != ["system", "user"]:
                continue
            if sum(len(m["content"]) for m in msgs) > max_chars:
                continue
            cat = row.get("metadata", {}).get("context_category", "unknown")
            if categories is not None and cat not in categories:
                continue
            rubrics = [r.get("rubric_criteria", "") if isinstance(r, dict) else str(r) for r in row["rubrics"]]
            out.append({"idx": idx, "system": msgs[0]["content"], "user": msgs[1]["content"],
                        "rubrics": [r for r in rubrics if r.strip()],
                        "category": row.get("metadata", {}).get("context_category", "unknown")})
    return out


def split(tasks: List[Dict[str, Any]], seed: int, n_fit: int, n_assess: int) -> Tuple[List[Dict], List[Dict]]:
    order = sorted(tasks, key=lambda t: t["idx"])
    random.Random(seed).shuffle(order)
    if len(order) < n_fit + n_assess:
        raise BenchError("not enough eligible tasks")
    return order[:n_fit], order[n_fit:n_fit + n_assess]


def commitment(tasks: List[Dict[str, Any]]) -> str:
    return domain_digest("caduceus.bench.split.v1", sorted(
        [{"idx": t["idx"], "content": sha256_hex((t["system"] + "\x00" + t["user"]).encode("utf-8")),
          "rubrics": sha256_hex("\x00".join(t["rubrics"]).encode("utf-8"))} for t in tasks],
        key=lambda d: d["idx"]))


def prompt(task: Dict[str, Any]) -> str:
    return "SYSTEM INSTRUCTIONS FOR THIS TASK:\n%s\n\n%s" % (task["system"], task["user"])


def grade_prompt(task: Dict[str, Any], response: str) -> str:
    rubrics = "\n".join("%d. %s" % (i, r) for i, r in enumerate(task["rubrics"], 1))
    return GRADER.format(rubrics=rubrics, response=response)


def parse_grade(text: str) -> Optional[int]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    s = obj.get("score")
    return s if s in (0, 1) and not isinstance(s, bool) else None


# --- preregistration ---------------------------------------------------------

def load_prereg(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh, parse_float=lambda s: (_ for _ in ()).throw(BenchError("floats not allowed")))
    if set(raw) - {"categories", "design"} != _KEYS or raw["schema"] != SCHEMA or raw["nonclaim"] != REQUIRED_NONCLAIM:
        raise BenchError("bench prereg: expected exactly keys %s, schema %s, nonclaim" % (sorted(_KEYS), SCHEMA))
    return raw


def prereg_digest(raw: Dict[str, Any]) -> str:
    return domain_digest(SCHEMA, raw)


def load_swe_tasks(path: str, max_chars: int, exclude: List[int],
                   categories: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """SWE-bench oracle rows: the prompt is `text`; grading is the benchmark's own tests (done by the caller)."""
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for idx, line in enumerate(fh):
            row = json.loads(line)
            if idx in exclude or len(row["text"]) > max_chars or (categories is not None and row["repo"] not in categories):
                continue
            out.append({"idx": idx, "system": "", "user": row["text"], "rubrics": [row["instance_id"]],
                        "category": row["repo"], "instance_id": row["instance_id"]})
    return out


LOADERS = {"cl-bench": load_tasks, "swe-bench": load_swe_tasks}


def _loader(raw: Dict[str, Any]):
    return LOADERS["swe-bench" if raw["benchmark"].lower().startswith("swe-bench") else "cl-bench"]


def verify_split(raw: Dict[str, Any], dataset_path: str) -> Tuple[List[Dict], List[Dict]]:
    if file_sha256(dataset_path) != raw["dataset_sha256"]:
        raise BenchError("dataset changed since preregistration")
    tasks = _loader(raw)(dataset_path, raw["max_chars"], raw["exclude"], raw.get("categories"))
    fit, assess = split(tasks, raw["split_seed"], raw["n_fit"], raw["n_assess"])
    if commitment(assess) != raw["assessment_commitment"]:
        raise BenchError("assessment split does not match its commitment")
    return fit, assess


def lock(raw: Dict[str, Any], ledger: Ledger) -> Dict[str, Any]:
    eid = raw["experiment_id"]
    if any(e["body"].get("experiment_id") == eid for e in ledger.find("prediction_lock")):
        raise BenchError("already locked")
    return ledger.append("prediction_lock", {"experiment_id": eid, "prereg_digest": prereg_digest(raw),
                                             "assessment_commitment": raw["assessment_commitment"],
                                             "nonclaim": REQUIRED_NONCLAIM})


# --- run ---------------------------------------------------------------------

Model = Callable[[str], str]


def run(raw: Dict[str, Any], dataset_path: str, ledger: Ledger, arms: Dict[str, Model],
        judge_a: Optional[Model], judge_b: Optional[Model], custody: Any = None, workers: int = 6,
        batch_grade: Optional[Callable[[List[Tuple[Dict[str, Any], str, str]]], Dict[Tuple[int, str], Tuple[Optional[int], Optional[int]]]]] = None) -> Dict[str, Any]:
    """Per-output judges (judge_a primary, judge_b second), or `batch_grade` for benchmarks graded by
    their own harness: it receives every (task, arm, text) and returns (primary, second) per (idx, arm)."""
    eid = raw["experiment_id"]
    locks = [e for e in ledger.find("prediction_lock") if e["body"].get("experiment_id") == eid]
    if len(locks) != 1 or locks[0]["body"]["prereg_digest"] != prereg_digest(raw):
        raise BenchError("no matching lock")
    if any(e["body"].get("experiment_id") == eid for e in ledger.find("verdict")):
        raise BenchError("experiment already closed")
    _, assess = verify_split(raw, dataset_path)
    rng = random.Random(raw["order_seed"])
    jobs = []
    for t in assess:
        order = list(ARMS)
        rng.shuffle(order)
        jobs += [(t, arm) for arm in order]

    def one(job):
        t, arm = job
        try:
            text = arms[arm](prompt(t) if t["system"] else t["user"])
        except Exception:
            return None, None, None
        if batch_grade is not None:
            return text, None, None
        try:
            a = parse_grade(judge_a(grade_prompt(t, text)))
            b = parse_grade(judge_b(grade_prompt(t, text)))
        except Exception:
            a = b = None
        return text, a, b

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = dict(zip([(j[0]["idx"], j[1]) for j in jobs], pool.map(one, jobs)))
    if batch_grade is not None:
        graded_map = batch_grade([(t, arm, results[(t["idx"], arm)][0]) for t, arm in jobs
                                  if results[(t["idx"], arm)][0] is not None])
        results = {k: (v[0],) + tuple(graded_map.get(k, (None, None))) for k, v in results.items()}
    diffs_b, diffs_r, diffs_f = [], [], []
    total = excluded = agree = graded = 0
    counts = {arm: 0 for arm in ARMS}
    by_cat: Dict[str, Dict[str, int]] = {}
    per_task: Dict[str, List[int]] = {}
    out_parts = []
    for t, arm0 in jobs[::len(ARMS)]:
        total += 1
        scores: Dict[str, Optional[int]] = {}
        for arm in ARMS:
            text, a, b = results[(t["idx"], arm)]
            scores[arm] = a
            if text is None:
                continue
            if custody is not None:
                custody.put(eid, str(t["idx"]), arm, 0, text)
            out_parts.append("%d|%s|%s" % (t["idx"], arm, sha256_hex(text.encode("utf-8"))))
            if a is not None and b is not None:
                graded += 1
                agree += int(a == b)
        if any(scores.get(arm) is None for arm in ARMS):
            excluded += 1
            continue
        for arm in ARMS:
            counts[arm] += scores[arm]
        c = by_cat.setdefault(t["category"], {arm: 0 for arm in ARMS})
        for arm in ARMS:
            c[arm] += scores[arm]
        per_task[str(t["idx"])] = [scores[arm] for arm in ARMS]
        diffs_b.append(scores["candidate"] - scores["untouched_base"])
        diffs_r.append(scores["candidate"] - scores["deterministic_random"])
        diffs_f.append(scores["candidate"] - scores["fixed_policy"])
    ledger.append("assessment", {
        "experiment_id": eid, "benchmark": raw["benchmark"], "units": total, "excluded": excluded,
        "control_pass": counts["untouched_base"], "random_pass": counts["deterministic_random"],
        "treatment_pass": counts["candidate"], "fixed_pass": counts["fixed_policy"], "by_category": by_cat,
        "instrument_agree": agree, "instrument_total": graded,
        "outputs_digest": sha256_hex("\n".join(out_parts).encode("utf-8")),
        "per_task_digest": domain_digest("caduceus.bench.per_task.v1", per_task),
    })
    result: Dict[str, Any] = {"experiment_id": eid, "prereg_digest": prereg_digest(raw), "units": total,
                              "excluded": excluded, "clusters": len(diffs_b), "design": raw.get("design", "candidate"),
                              "benchmark": raw["benchmark"], "nonclaim": REQUIRED_NONCLAIM}
    if (excluded * MICRO) // max(total, 1) > raw["max_notrun_micro"] or len(diffs_b) < 2:
        result.update(verdict="NotRun", reason="too_many_excluded_units")
    else:
        alpha = raw["alpha_micro"] / MICRO
        b = paired_bootstrap(diffs_b, raw["bootstrap_seed"], raw["bootstrap_resamples"], alpha)
        r = paired_bootstrap(diffs_r, raw["bootstrap_seed"] + 1, raw["bootstrap_resamples"], alpha)
        f = paired_bootstrap(diffs_f, raw["bootstrap_seed"] + 2, raw["bootstrap_resamples"], alpha)
        ok = all(x["lower_micro"] > 0 and x["mean_micro"] >= raw["min_effect_micro"] for x in (b, r, f))
        result.update(mean_micro=b["mean_micro"], lower_micro=b["lower_micro"], upper_micro=b["upper_micro"],
                      vs_random_mean_micro=r["mean_micro"], vs_random_lower_micro=r["lower_micro"],
                      vs_random_upper_micro=r["upper_micro"], vs_fixed_mean_micro=f["mean_micro"],
                      vs_fixed_lower_micro=f["lower_micro"], vs_fixed_upper_micro=f["upper_micro"], verdict="Pass" if ok else "Fail",
                      reason="decision_rule")
    ledger.append("verdict", result)
    result.update(counts=counts, by_category=by_cat, instrument={"agree": agree, "total": graded},
                  per_task={k: dict(zip(ARMS, v)) for k, v in per_task.items()})
    return result


# --- Exchange ----------------------------------------------------------------

def assemble(candidate: Candidate, raw: Dict[str, Any], ledger_path: str, repo: str,
             proposer_log: Dict[str, Any], policy: AdmissionPolicy = AdmissionPolicy()) -> Dict[str, Any]:
    from .exchange import _baseline

    eid = raw["experiment_id"]
    ledger = Ledger(ledger_path)
    pr = candidate.preregistration if isinstance(candidate.preregistration, dict) else {}
    ev: Dict[str, Dict[str, str]] = {}
    locks = [e for e in ledger.find("prediction_lock") if e["body"].get("experiment_id") == eid]
    assess = [e for e in ledger.find("assessment") if e["body"].get("experiment_id") == eid]
    verdicts = [e["body"] for e in ledger.find("verdict") if e["body"].get("experiment_id") == eid]
    v = verdicts[0] if len(verdicts) == 1 else None
    a = assess[0]["body"] if len(assess) == 1 else None

    problems = validate_candidate(candidate, policy.allowed_roots)
    if problems:
        ev["preregistration"] = lanes._r("Failed", ",".join(problems))
    elif pr.get("experiment_id") != eid or pr.get("experiment_prereg_digest") != prereg_digest(raw):
        ev["preregistration"] = lanes._r("Failed", "candidate not bound to this experiment")
    elif len(locks) != 1:
        ev["preregistration"] = lanes._r("NotRun", "experiment not locked")
    else:
        ev["preregistration"] = lanes._r("Checked")
    if not locks or not assess:
        ev["prediction_lock"] = lanes._r("NotRun", "no lock or no assessment")
    else:
        ev["prediction_lock"] = (lanes._r("Checked") if min(e["seq"] for e in locks) < min(e["seq"] for e in assess)
                                 else lanes._r("Failed", "assessment before lock"))
    # Holdout: the candidate commits to the sealed split, and the proposer only saw the fit split.
    seen = set(proposer_log.get("task_idx_seen", []))
    sealed_ok = (pr.get("holdout_commitment") == raw["assessment_commitment"]
                 and proposer_log.get("assessment_commitment_at_proposal") == raw["assessment_commitment"])
    leaked = seen & set(proposer_log.get("assessment_idx_check", []))
    ev["holdout"] = (lanes._r("Failed", "proposer saw assessment tasks") if leaked else
                     lanes._r("Checked") if sealed_ok else lanes._r("Failed", "holdout commitment mismatch"))
    if v is None or v["verdict"] == "NotRun":
        ev["controls"] = lanes._r("NotRun", "no verdict" if v is None else "NotRun")
    else:
        ev["controls"] = (lanes._r("Checked") if v["verdict"] == "Pass" else
                          lanes._r("Failed", "mean %d vs base, %d vs random, %d vs fixed (micro)" % (
                              v["mean_micro"], v["vs_random_mean_micro"], v["vs_fixed_mean_micro"])))
    fr, ov = _baseline(repo, "freshness-*.json"), _baseline(repo, "plugin-overhead-*.json")
    ev["non_inferiority"] = (lanes._r("NotRun", "baselines missing") if fr is None or ov is None else
                             lanes._r("Checked") if fr["passed"] and ov["passed"] else lanes._r("Failed", "baseline fails"))
    try:
        readback.verify_file(ledger_path)
        rb = "Checked"
    except readback.ReadbackError:
        rb = "Failed"
    ev["independent_verification"] = lanes.lane_independent_verification(
        {"ledger_readback": rb, "freshness_two_hash": "Checked" if fr and fr["passed"] else "NotRun"})
    own = "skills/%s/" % raw["skill"]
    ev["retention"] = (lanes._r("Failed", "touches files outside its own skill")
                       if any(not p.startswith(own) for p in candidate.paths) else
                       lanes._r("Checked", "protected skills untouched (disjoint paths)"))
    if a is None:
        ev["behavior_equivalence"] = lanes._r("NotRun", "no assessment")
    else:
        lost = [c for c, n in sorted(a["by_category"].items())
                if n["untouched_base"] - n["candidate"] >= raw["max_category_loss"]]
        ev["behavior_equivalence"] = lanes._r("Failed", "loses on " + ",".join(lost)) if lost else lanes._r("Checked")
    ev["instrument_validity"] = (lanes._r("NotRun", "no assessment") if a is None else
                                 lanes.lane_instrument_validity(a["instrument_agree"], a["instrument_total"]))
    cost = (proposer_log.get("model_calls", 0) + (a["units"] * len(ARMS) * 3 if a else 0))
    ev["search_cost"] = lanes.lane_search_cost(cost, int(pr.get("max_search_cost_units", 0)))
    if v is None or "vs_random_lower_micro" not in v:
        ev["indispensability"] = lanes._r("NotRun", "no random contrast")
    else:
        ev["indispensability"] = (lanes._r("Checked") if v["vs_random_lower_micro"] > 0
                                  else lanes._r("Failed", "not better than a random skill"))
    report = None
    if v is not None and a is not None:
        report = {"candidate_digest": candidate.digest(), "experiment_id": eid, "before": a["control_pass"],
                  "after": a["treatment_pass"], "random_control": a["random_pass"], "fixed_policy": a["fixed_pass"], "n": a["units"],
                  "seeds": [raw["split_seed"], raw["order_seed"], raw["bootstrap_seed"]],
                  "environment_digest": domain_digest("caduceus.lyre.environment.v1", raw["executor"]),
                  "search_cost_units": cost, "controls": {k: v.get(k) for k in v if k.endswith("_micro")},
                  "by_category": a["by_category"]}
    ev["lyre_report"] = lanes.lane_lyre_report(report, candidate)
    ev["signature"] = lanes.lane_signature(candidate, [], "caduceus-bench-runner")
    assert set(ev) == set(REQUIRED_LANES)
    return {"verdict": evaluate(candidate, policy, ev), "evidence": ev, "lyre_report": report}

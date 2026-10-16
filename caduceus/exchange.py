"""The Exchange (PRD 6.7): assemble all 13 gate lanes for one real candidate.

Inputs are artifacts, never claims: the candidate, the contract of the skill it
changes, a locked candidate experiment (arms untouched_base /
deterministic_random / candidate) and its ledger, the pinned baselines, and the
review keyring. Each lane is computed from those; a lane with no evidence is
`NotRun`, which the admission core turns into `quarantine`, never `accept`.

Binding: the candidate's preregistration must name the experiment
(`experiment_id`) and its locked preregistration digest
(`experiment_prereg_digest`), so evidence from another experiment cannot be
attached.

`instrument_validity` needs a second, independent instrument (PRD 6.7.8). The
regex judge is the only instrument here, so unless an `instrument_report`
with paired agreement counts is supplied, the lane is `NotRun` and the
candidate is quarantined. That is the intended outcome, not a gap to paper over.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List, Optional

from . import experiment, lanes, readback
from .admission import REQUIRED_LANES, AdmissionPolicy, Verdict, evaluate
from .candidate import Candidate, validate_candidate
from .contract import ContractFile
from .digest import domain_digest
from .ledger import Ledger

EXECUTOR_IDENTITY = "caduceus-experiment-runner"


def _baseline(repo: str, pattern: str) -> Optional[Dict[str, Any]]:
    paths = sorted(glob.glob(os.path.join(repo, "baselines", pattern)), key=os.path.getmtime)
    if not paths:
        return None
    with open(paths[-1]) as fh:
        return json.load(fh)


def lyre_report(candidate: Candidate, prereg: experiment.Prereg, ledger: Ledger) -> Optional[Dict[str, Any]]:
    """Digest-bound before/after report (PRD 6.7.11) from the experiment ledger."""
    eid = prereg.experiment_id
    verdicts = [e["body"] for e in ledger.find("verdict") if e["body"].get("experiment_id") == eid]
    assess = [e["body"] for e in ledger.find("assessment") if e["body"].get("experiment_id") == eid]
    if len(verdicts) != 1 or not assess:
        return None
    v = verdicts[0]
    raw = prereg.raw
    return {
        "candidate_digest": candidate.digest(),
        "experiment_id": eid,
        "prereg_digest": prereg.digest(),
        "before": sum(a["control_pass"] for a in assess),
        "after": sum(a["treatment_pass"] for a in assess),
        "random_control": sum(a.get("random_pass", 0) for a in assess),
        "n": v["units"],
        "excluded": v["excluded"],
        "seeds": [raw["order_seed"], raw["bootstrap_seed"]],
        "environment_digest": domain_digest("caduceus.lyre.environment.v1", raw["executor"]),
        "search_cost_units": v["units"] * len(raw["arms"]),
        "controls": {k: v.get(k) for k in ("mean_micro", "lower_micro", "upper_micro", "vs_random_mean_micro",
                                           "vs_random_lower_micro", "vs_random_upper_micro", "verdict")},
        "outputs_digests": sorted(a["outputs_digest"] for a in assess),
    }


def assemble(
    candidate: Candidate,
    contract_file: ContractFile,
    prereg: experiment.Prereg,
    ledger_path: str,
    repo: str,
    signatures: Optional[List[Dict[str, str]]] = None,
    instrument_report: Optional[Dict[str, int]] = None,
    policy: AdmissionPolicy = AdmissionPolicy(),
) -> Dict[str, Any]:
    ev: Dict[str, Dict[str, str]] = {}
    pr = candidate.preregistration if isinstance(candidate.preregistration, dict) else {}
    ledger = Ledger(ledger_path)
    eid = prereg.experiment_id

    # 1 preregistration: candidate valid, bound to this locked experiment, candidate design.
    problems = validate_candidate(candidate, policy.allowed_roots)
    locks = [e for e in ledger.find("prediction_lock") if e["body"].get("experiment_id") == eid]
    if problems:
        ev["preregistration"] = lanes._r("Failed", ",".join(problems))
    elif pr.get("experiment_id") != eid or pr.get("experiment_prereg_digest") != prereg.digest():
        ev["preregistration"] = lanes._r("Failed", "candidate not bound to this experiment")
    elif tuple(prereg.raw["arms"]) != experiment.ARMS_CANDIDATE:
        ev["preregistration"] = lanes._r("Failed", "experiment is not a candidate design")
    elif len(locks) != 1 or locks[0]["body"]["prereg_digest"] != prereg.digest():
        ev["preregistration"] = lanes._r("NotRun", "experiment not locked")
    else:
        ev["preregistration"] = lanes._r("Checked")

    # 2 prediction lock precedes every assessment.
    assess = [e for e in ledger.find("assessment") if e["body"].get("experiment_id") == eid]
    if not locks or not assess:
        ev["prediction_lock"] = lanes._r("NotRun", "no lock or no assessment")
    elif min(e["seq"] for e in locks) < min(e["seq"] for e in assess):
        ev["prediction_lock"] = lanes._r("Checked")
    else:
        ev["prediction_lock"] = lanes._r("Failed", "assessment before lock")

    # 3 sealed holdout matches the candidate's commitment and is not in the proposer view.
    c = contract_file.contract
    proposer_view = [p.probe_id for p in c.probes if p.kind != "held_out"]
    ev["holdout"] = lanes.lane_holdout(pr.get("holdout_commitment", ""), c.probes_of("held_out"), proposer_view)

    verdicts = [e["body"] for e in ledger.find("verdict") if e["body"].get("experiment_id") == eid]
    v = verdicts[0] if len(verdicts) == 1 else None
    # 4 controls: beats untouched base AND deterministic random under the preregistered rule.
    if v is None or v["verdict"] == "NotRun":
        ev["controls"] = lanes._r("NotRun", "no experiment verdict" if v is None else "experiment NotRun")
    elif v["verdict"] == "Pass":
        ev["controls"] = lanes._r("Checked", "mean %d micro vs base, %d vs random" % (v["mean_micro"], v["vs_random_mean_micro"]))
    else:
        ev["controls"] = lanes._r("Failed", "preregistered rule not met")

    # 5 non-inferiority of the measured reliability lanes.
    fr, ov = _baseline(repo, "freshness-*.json"), _baseline(repo, "plugin-overhead-*.json")
    if fr is None or ov is None:
        ev["non_inferiority"] = lanes._r("NotRun", "freshness/overhead baselines missing")
    elif fr["passed"] and ov["passed"]:
        ev["non_inferiority"] = lanes._r("Checked", "freshness and overhead baselines pass; skill text does not enter either path")
    else:
        ev["non_inferiority"] = lanes._r("Failed", "a reliability baseline fails")

    # 6 independent verification: separate readback implementation + two-hash freshness.
    try:
        readback.verify_file(ledger_path)
        rb = "Checked"
    except readback.ReadbackError:
        rb = "Failed"
    ev["independent_verification"] = lanes.lane_independent_verification(
        {"ledger_readback": rb, "freshness_two_hash": "Checked" if fr and fr["passed"] else "NotRun"})

    # 7 retention: no probe the base always passed may drop; only the skill's own dir is touched.
    per = {}
    for a in assess:
        per.update(a["body"].get("per_probe", {}))
    own = "skills/%s/" % contract_file.skill_rel_dir
    if any(not p.startswith(own) for p in candidate.paths):
        ev["retention"] = lanes._r("Failed", "candidate touches files outside its own skill")
    elif not per:
        ev["retention"] = lanes._r("NotRun", "no per-probe results")
    else:
        failing = ["%s:%s" % (contract_file.skill, pid) for pid, (runs, ctl, _, trt) in sorted(per.items())
                   if runs and ctl == runs and trt < runs]
        ev["retention"] = lanes.lane_retention(failing)

    # 8 behavior: held-out and perturbation probes never worse than the base.
    probe_kind = {p.probe_id: p.kind for p in c.probes}
    worse = [pid for pid, (runs, ctl, _, trt) in sorted(per.items())
             if probe_kind.get(pid) in ("held_out", "perturbation") and trt < ctl]
    ev["behavior_equivalence"] = (lanes._r("NotRun", "no per-probe results") if not per else
                                  lanes._r("Failed", "worse on " + ",".join(worse)) if worse else lanes._r("Checked"))

    # 9 instrument validity: a second, independent instrument must agree.
    if instrument_report is None:
        ev["instrument_validity"] = lanes._r("NotRun", "single instrument (regex judge); no second instrument report")
    else:
        ev["instrument_validity"] = lanes.lane_instrument_validity(instrument_report["agree"], instrument_report["total"])

    # 10 search cost charged against the preregistered budget.
    report = lyre_report(candidate, prereg, ledger)
    cost = report["search_cost_units"] if report else -1
    ev["search_cost"] = (lanes._r("NotRun", "no experiment") if report is None else
                         lanes.lane_search_cost(cost, int(pr.get("max_search_cost_units", 0))))

    # 11 indispensability: ablating to a random skill must hurt.
    if v is None or "vs_random_lower_micro" not in v:
        ev["indispensability"] = lanes._r("NotRun", "no random-control contrast")
    elif v["vs_random_lower_micro"] > 0:
        ev["indispensability"] = lanes._r("Checked")
    else:
        ev["indispensability"] = lanes._r("Failed", "not better than a random skill")

    # 12 lyre report, 13 signature.
    ev["lyre_report"] = lanes.lane_lyre_report(report, candidate)
    ev["signature"] = lanes.lane_signature(candidate, signatures or [], EXECUTOR_IDENTITY)

    assert set(ev) == set(REQUIRED_LANES)
    verdict = evaluate(candidate, policy, ev)
    return {"verdict": verdict, "evidence": ev, "lyre_report": report}


def record(ledger: Ledger, candidate: Candidate, verdict: Verdict, report: Optional[Dict[str, Any]]) -> None:
    ledger.append("candidate", {"candidate_digest": candidate.digest(), "candidate_id": candidate.candidate_id,
                                "kind": candidate.kind})
    if report is not None:
        ledger.append("lyre_report", {"candidate_digest": candidate.digest(),
                                      "report_digest": domain_digest("caduceus.lyre.v1", report)})
    ledger.append("verdict", dict(verdict.as_dict(), verdict_digest=verdict.digest()))

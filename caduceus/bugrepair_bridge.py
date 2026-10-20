"""Bridge from a bug-repair experiment (result file + its own ledger) to Exchange lanes.

Nothing here trusts the result file: the ledger is re-verified on load, the
verdict is recomputed from the stored contrasts with `bugrepair.decide`, and the
lock must precede every assessment entry on that ledger. Lanes this evidence
can fill: preregistration, prediction_lock, holdout, controls, indispensability,
retention. Everything else stays `NotRun` (the admission core quarantines it):
the recompute shares code with the runner, so independent_verification is not
claimed.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from . import bugrepair, lanes
from .admission import REQUIRED_LANES, AdmissionPolicy, Verdict, evaluate
from .candidate import Candidate, validate_candidate
from .ledger import Ledger

CONTRASTS = ("untouched_base", "fixed_policy", "deterministic_random")


def _r(status: str, detail: str = "") -> Dict[str, str]:
    return lanes._r(status, detail)


def assemble_bugrepair(candidate: Candidate, result: Dict[str, Any], ledger_path: str,
                       policy: AdmissionPolicy = AdmissionPolicy(),
                       other_lanes: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
    ev: Dict[str, Dict[str, str]] = {n: _r("NotRun", "no evidence supplied") for n in REQUIRED_LANES}
    pr = candidate.preregistration if isinstance(candidate.preregistration, dict) else {}
    exp = result.get("experiment_id")
    ledger = Ledger(ledger_path)  # raises on a broken chain
    locks = [e for e in ledger.find("prediction_lock") if e["body"].get("experiment_id") == exp]
    assess = [e for e in ledger.find("assessment") if e["body"].get("experiment_id") == exp]

    problems = validate_candidate(candidate, policy.allowed_roots)
    if candidate.kind != "adapter_update":
        problems.append("kind_not_adapter_update")
    if pr.get("bugrepair_experiment") != exp or pr.get("bugrepair_prereg_digest") != result.get("prereg_digest"):
        problems.append("result_not_bound_to_candidate")
    if locks and locks[0]["body"].get("prereg_digest") != result.get("prereg_digest"):
        problems.append("lock_prereg_mismatch")
    ev["preregistration"] = _r("Failed", ",".join(problems)) if problems else _r("Checked")

    if not locks or not assess:
        ev["prediction_lock"] = _r("NotRun", "lock or assessment missing on experiment ledger")
    elif locks[0]["seq"] < min(a["seq"] for a in assess):
        ev["prediction_lock"] = _r("Checked", "lock precedes every assessment on the experiment ledger")
    else:
        ev["prediction_lock"] = _r("Failed", "assessment before lock")
    if locks:
        ok = locks[0]["body"].get("split_commitments", {}).get("assess") == pr.get("holdout_commitment")
        ev["holdout"] = _r("Checked", "holdout_commitment equals the locked assess-split commitment") if ok \
            else _r("Failed", "holdout_commitment differs from locked assess commitment")

    per_seed = [result["contrasts"][s] for s in sorted(result.get("contrasts", {}))]
    if per_seed and all(set(c) >= set(CONTRASTS) for c in per_seed):
        re = bugrepair.decide(per_seed, bool(result.get("retention_ok")), int(pr.get("min_effect_micro", 0)))
        if re["verdict"] != result.get("verdict"):
            ev["controls"] = _r("Failed", "recomputed verdict %s differs from stored" % re["verdict"])
        else:
            ev["controls"] = _r("Checked", "recomputed verdict " + re["verdict"]) if re["verdict"] == "Pass" \
                else _r("Failed", "experiment verdict " + re["verdict"])
        pool_ok = sum(1 for c in per_seed if c["deterministic_random"]["lower_micro"] > 0) >= 2
        ev["indispensability"] = _r("Checked") if pool_ok else _r("Failed", "verified not above pool-matched control")
        ev["retention"] = _r("Checked") if result.get("retention_ok") else _r("Failed", "tune retention broke")

    for k, v in (other_lanes or {}).items():
        if k in ev:
            ev[k] = v
    verdict: Verdict = evaluate(candidate, policy, ev)
    return {"verdict": verdict, "evidence": ev}

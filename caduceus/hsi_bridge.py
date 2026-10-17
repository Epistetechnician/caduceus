"""Bridge from hsi-lab receipts to Exchange lanes for `adapter_update` candidates.

hsi-lab trains adapters on oracle-verified synthetic experience and emits a
receipt per frozen packet. This module never trusts a receipt: each one is
recomputed by hsi-lab's standalone validator (a separate process, `python -I`,
no network) and must print `OK <receipt_digest>` for the same digest the file
carries. Only then are its numbers converted to integer micro-units, because
ledger bodies hold no floats.

Lanes this evidence can honestly fill: controls, indispensability, retention,
behavior_equivalence, independent_verification, search_cost, preregistration,
and, when a Caduceus ledger witnessed the lock, prediction_lock and holdout.
`non_inferiority`, `instrument_validity` and `signature` are left to their own
evidence; here they are `NotRun`, which the admission core turns into quarantine.

Lock witnessing: before `hsi run`, the operator calls `witness_lock`, which
appends a `prediction_lock` entry (the lock's digest and holdout commitment from
hsi's `lock.public.json`). `record_evaluation` appends an `assessment` entry per
receipt. The lane is Checked only if every receipt's lock was witnessed with a
lower ledger seq than its assessment. This proves the lock was on the ledger
before the receipt was accepted, not that hsi did not start earlier; hsi itself
refuses to run or evaluate without its lock, and that is the only other ordering
evidence. The limit is stated in the lane detail.

Binding: the candidate's preregistration names the exact receipt digests it
relies on (`hsi_receipt_digests`), so another run's receipt cannot be attached.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence

from . import lanes
from .digest import domain_digest
from .ledger import Ledger
from .admission import REQUIRED_LANES, AdmissionPolicy, Verdict, evaluate
from .candidate import Candidate, validate_candidate
from .digest import is_hex_digest

MICRO = 1_000_000
RECEIPT_SCHEMA = "hsi-receipt-v1"
# Controls the candidate must beat. Both must be present in the supplied receipts.
DEFAULT_BASELINES = ("d2_replay", "d2_poolctl")
ARM_ROLES = ("untouched_base", "fixed_policy", "candidate", "deterministic_random")
# Default mapping of Caduceus arm roles to hsi arms; a candidate may override it
# with `arm_map` in its preregistration, but must name all four roles.
DEFAULT_ARM_MAP = {"untouched_base": "frozen_base", "fixed_policy": "d2_replay",
                   "candidate": "d2_verified", "deterministic_random": "d2_poolctl"}


class ReceiptError(ValueError):
    pass


def _micro(x: Any) -> int:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ReceiptError("expected a number")
    return int(round(x * MICRO))


def recompute(receipt_path: str, packet_path: str, validator_path: str, timeout: int = 120) -> str:
    """Run the standalone validator; return the receipt digest it confirms."""
    proc = subprocess.run(
        [sys.executable, "-I", validator_path, receipt_path, packet_path],
        capture_output=True, text=True, timeout=timeout,
    )
    out = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not out or not out[-1].startswith("OK "):
        raise ReceiptError("validator refused receipt: " + (proc.stdout + proc.stderr)[-200:].strip())
    return out[-1][3:].strip()


def import_receipt(receipt_path: str, packet_path: str, validator_path: str) -> Dict[str, Any]:
    """Recompute, then reduce a receipt to integer-only evidence."""
    with open(receipt_path) as fh:
        r = json.load(fh)
    if r.get("schema_version") != RECEIPT_SCHEMA:
        raise ReceiptError("unknown receipt schema")
    for f in ("receipt_digest", "decision", "comparisons", "guardrails_held", "packet_digest"):
        if f not in r:
            raise ReceiptError("receipt missing " + f)
    if not is_hex_digest(r["receipt_digest"]):
        raise ReceiptError("receipt_digest malformed")
    confirmed = recompute(receipt_path, packet_path, validator_path)
    if confirmed != r["receipt_digest"]:
        raise ReceiptError("validator digest differs from receipt")
    if r["decision"] not in ("PROMOTE", "NO_CANDIDATE", "REJECT"):
        raise ReceiptError("unknown decision: %r" % (r["decision"],))
    comps = []
    for c in r["comparisons"]:
        comps.append({
            "treatment_arm": str(c["treatment_arm"]),
            "baseline_arm": str(c["baseline_arm"]),
            "paired_diff_micro": _micro(c["paired_diff"]),
            "lower_micro": _micro(c["ci_low"]),
            "upper_micro": _micro(c["ci_high"]),
        })
    return {
        "receipt_digest": r["receipt_digest"],
        "packet_digest": r["packet_digest"],
        "decision": r["decision"],
        "guardrails_held": bool(r["guardrails_held"]),
        "lock_digest": str(r.get("lock_digest", "")),
        "rates_micro": {k[len("eval_consistent_"):]: _micro(v) for k, v in r.get("guardrail_values", {}).items()
                        if k.startswith("eval_consistent_") and isinstance(v, (int, float))},
        "n_seeds": len(r.get("seeds", [])),
        "wall_seconds": int(r.get("budget_used", {}).get("wall_seconds", 0)),
        "comparisons": comps,
    }


def _r(status: str, detail: str = "") -> Dict[str, str]:
    return lanes._r(status, detail)


def holdout_commitment_of(commitments: Sequence[str]) -> str:
    """The value a candidate puts in `holdout_commitment` for a set of hsi locks."""
    return domain_digest("caduceus.hsi.holdout.v1", sorted(set(commitments)))


def witness_lock(ledger: Ledger, lock_public_path: str, experiment_id: str) -> Dict[str, Any]:
    """Record an hsi lock on the ledger. Call before `hsi run`."""
    with open(lock_public_path) as fh:
        lk = json.load(fh)
    for f in ("packet_digest", "n_per_family", "commitment", "lock_digest"):
        if f not in lk:
            raise ReceiptError("lock missing " + f)
    want = hashlib.sha256(json.dumps(
        {"packet_digest": lk["packet_digest"], "n_per_family": lk["n_per_family"], "commitment": lk["commitment"]},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if want != lk["lock_digest"]:
        raise ReceiptError("lock_digest does not match lock contents")
    if any(e["body"].get("lock_digest") == lk["lock_digest"] for e in ledger.find("prediction_lock")):
        raise ReceiptError("lock already witnessed")
    return ledger.append("prediction_lock", {
        "experiment_id": experiment_id, "source": "hsi-lab", "lock_digest": lk["lock_digest"],
        "packet_digest": lk["packet_digest"], "commitment": lk["commitment"]})


def record_evaluation(ledger: Ledger, evidence: Sequence[Dict[str, Any]], experiment_id: str) -> None:
    for e in evidence:
        ledger.append("assessment", {
            "experiment_id": experiment_id, "source": "hsi-lab", "receipt_digest": e["receipt_digest"],
            "lock_digest": e["lock_digest"], "decision": e["decision"]})


def _lock_lanes(evidence: Sequence[Dict[str, Any]], ledger: Ledger, pr: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    locks = {e["body"]["lock_digest"]: e for e in ledger.find("prediction_lock") if e["body"].get("source") == "hsi-lab"}
    assess = {e["body"]["receipt_digest"]: e for e in ledger.find("assessment") if e["body"].get("source") == "hsi-lab"}
    out: Dict[str, Dict[str, str]] = {}
    bad = []
    for ev in evidence:
        lk, a = locks.get(ev["lock_digest"]), assess.get(ev["receipt_digest"])
        if lk is None:
            bad.append("%s: lock not witnessed" % ev["receipt_digest"][:8])
        elif a is None:
            bad.append("%s: no assessment recorded" % ev["receipt_digest"][:8])
        elif lk["seq"] >= a["seq"]:
            bad.append("%s: assessment before lock" % ev["receipt_digest"][:8])
    if not evidence:
        out["prediction_lock"] = _r("NotRun", "no receipts")
    elif bad:
        out["prediction_lock"] = _r("NotRun" if all("not witnessed" in b or "no assessment" in b for b in bad) else "Failed", "; ".join(bad))
    else:
        out["prediction_lock"] = _r("Checked", "locks witnessed before receipt acceptance (hsi start time not independently proven)")
    commits = [locks[ev["lock_digest"]]["body"]["commitment"] for ev in evidence if ev["lock_digest"] in locks]
    if len(commits) != len(evidence) or not evidence:
        out["holdout"] = _r("NotRun", "holdout commitments not witnessed")
    elif holdout_commitment_of(sorted(set(commits))) == pr.get("holdout_commitment"):
        out["holdout"] = _r("Checked", "candidate commitment matches witnessed hsi holdout commitments")
    else:
        out["holdout"] = _r("Failed", "candidate holdout_commitment differs from witnessed locks")
    return out


def assemble_adapter(
    candidate: Candidate,
    evidence: Sequence[Dict[str, Any]],
    policy: AdmissionPolicy = AdmissionPolicy(),
    baselines: Optional[Sequence[str]] = None,
    ledger: Optional[Ledger] = None,
    other_lanes: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Fill the lanes hsi receipts can support and decide.

    `other_lanes` lets the caller supply independently produced lanes (e.g. a
    signature); anything not supplied stays NotRun.
    """
    ev: Dict[str, Dict[str, str]] = {n: _r("NotRun", "no evidence supplied") for n in REQUIRED_LANES}
    pr = candidate.preregistration if isinstance(candidate.preregistration, dict) else {}

    problems = validate_candidate(candidate, policy.allowed_roots)
    if candidate.kind != "adapter_update":
        problems.append("kind_not_adapter_update")
    bound = pr.get("hsi_receipt_digests")
    have = sorted(e["receipt_digest"] for e in evidence)
    if not isinstance(bound, list) or sorted(bound) != have:
        problems.append("receipts_not_bound_to_candidate")
    arm_map = pr.get("arm_map", DEFAULT_ARM_MAP)
    if not isinstance(arm_map, dict) or set(arm_map) != set(ARM_ROLES) or not all(isinstance(v, str) for v in arm_map.values()):
        problems.append("arm_map_must_name_all_four_roles")
        arm_map = DEFAULT_ARM_MAP
    ev["preregistration"] = _r("Failed", ",".join(problems)) if problems else _r("Checked")
    if baselines is None:
        baselines = (arm_map["fixed_policy"], arm_map["deterministic_random"])

    ev["independent_verification"] = (
        _r("Checked", "%d receipts recomputed by standalone validator" % len(evidence)) if evidence
        else _r("NotRun", "no receipts"))

    if evidence:
        by_base: Dict[str, List[Dict[str, Any]]] = {}
        for e in evidence:
            for c in e["comparisons"]:
                by_base.setdefault(c["baseline_arm"], []).append(dict(c, _decision=e["decision"], _held=e["guardrails_held"]))
        # untouched base: the candidate's held-out rate must exceed the frozen base's.
        rates = [e["rates_micro"] for e in evidence if arm_map["candidate"] in e["rates_micro"]]
        base_ok = bool(rates) and all(r.get(arm_map["untouched_base"]) is not None
                                      and r[arm_map["candidate"]] > r[arm_map["untouched_base"]] for r in rates)
        missing = [b for b in baselines if b not in by_base]
        weak = [b for b in baselines if b in by_base and not all(
            c["lower_micro"] > 0 and c["_decision"] == "PROMOTE" for c in by_base[b])]
        if not base_ok:
            ev["controls"] = _r("NotRun", "no candidate-vs-untouched-base rates in receipts")
        elif missing:
            ev["controls"] = _r("NotRun", "no comparison against " + ",".join(missing))
        elif weak:
            ev["controls"] = _r("Failed", "does not clear " + ",".join(weak))
        else:
            ev["controls"] = _r("Checked", "beats " + ",".join(baselines) + " with lower bound > 0")
        # Pool-matched unverified control is the ablation of verification itself.
        pc = by_base.get(arm_map["deterministic_random"])
        ev["indispensability"] = (
            _r("NotRun", "no pool-matched unverified control") if not pc else
            _r("Checked") if all(c["lower_micro"] > 0 for c in pc) else
            _r("Failed", "verified data not better than pool-matched unverified data"))
        held = [e["guardrails_held"] for e in evidence]
        ev["retention"] = _r("Checked") if all(held) else _r("Failed", "a guardrail failed in a supplied receipt")
        ev["behavior_equivalence"] = (
            _r("Checked") if all(e["decision"] != "NO_CANDIDATE" for e in evidence)
            else _r("Failed", "a held-out check returned NO_CANDIDATE"))
        wall = sum(e["wall_seconds"] for e in evidence)
        cap = int(pr.get("max_search_cost_units", 0))
        ev["search_cost"] = lanes.lane_search_cost(wall, cap)

    if ledger is not None:
        ev.update(_lock_lanes(evidence, ledger, pr))

    for k, v in (other_lanes or {}).items():
        if k in ev:
            ev[k] = v

    assert set(ev) == set(REQUIRED_LANES)
    verdict: Verdict = evaluate(candidate, policy, ev)
    return {"verdict": verdict, "evidence": ev}

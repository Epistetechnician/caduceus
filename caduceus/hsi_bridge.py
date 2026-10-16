"""Bridge from hsi-lab receipts to Exchange lanes for `adapter_update` candidates.

hsi-lab trains adapters on oracle-verified synthetic experience and emits a
receipt per frozen packet. This module never trusts a receipt: each one is
recomputed by hsi-lab's standalone validator (a separate process, `python -I`,
no network) and must print `OK <receipt_digest>` for the same digest the file
carries. Only then are its numbers converted to integer micro-units, because
ledger bodies hold no floats.

Lanes this evidence can honestly fill: controls, indispensability, retention,
behavior_equivalence, independent_verification, search_cost, preregistration.
`prediction_lock`, `holdout`, `non_inferiority`, `instrument_validity` and
`signature` are left to their own evidence; here they are `NotRun`, which the
admission core turns into quarantine. hsi receipts carry a `lock_digest` but
nothing here proves it preceded evaluation, so claiming the lock would be a lie.

Binding: the candidate's preregistration names the exact receipt digests it
relies on (`hsi_receipt_digests`), so another run's receipt cannot be attached.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence

from . import lanes
from .admission import REQUIRED_LANES, AdmissionPolicy, Verdict, evaluate
from .candidate import Candidate, validate_candidate
from .digest import is_hex_digest

MICRO = 1_000_000
RECEIPT_SCHEMA = "hsi-receipt-v1"
# Controls the candidate must beat. Both must be present in the supplied receipts.
DEFAULT_BASELINES = ("d2_replay", "d2_poolctl")


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
        "n_seeds": len(r.get("seeds", [])),
        "wall_seconds": int(r.get("budget_used", {}).get("wall_seconds", 0)),
        "comparisons": comps,
    }


def _r(status: str, detail: str = "") -> Dict[str, str]:
    return lanes._r(status, detail)


def assemble_adapter(
    candidate: Candidate,
    evidence: Sequence[Dict[str, Any]],
    policy: AdmissionPolicy = AdmissionPolicy(),
    baselines: Sequence[str] = DEFAULT_BASELINES,
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
    ev["preregistration"] = _r("Failed", ",".join(problems)) if problems else _r("Checked")

    ev["independent_verification"] = (
        _r("Checked", "%d receipts recomputed by standalone validator" % len(evidence)) if evidence
        else _r("NotRun", "no receipts"))

    if evidence:
        by_base: Dict[str, List[Dict[str, Any]]] = {}
        for e in evidence:
            for c in e["comparisons"]:
                by_base.setdefault(c["baseline_arm"], []).append(dict(c, _decision=e["decision"], _held=e["guardrails_held"]))
        missing = [b for b in baselines if b not in by_base]
        weak = [b for b in baselines if b in by_base and not all(
            c["lower_micro"] > 0 and c["_decision"] == "PROMOTE" for c in by_base[b])]
        if missing:
            ev["controls"] = _r("NotRun", "no comparison against " + ",".join(missing))
        elif weak:
            ev["controls"] = _r("Failed", "does not clear " + ",".join(weak))
        else:
            ev["controls"] = _r("Checked", "beats " + ",".join(baselines) + " with lower bound > 0")
        # Pool-matched unverified control is the ablation of verification itself.
        pc = by_base.get("d2_poolctl")
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

    for k, v in (other_lanes or {}).items():
        if k in ev:
            ev[k] = v

    assert set(ev) == set(REQUIRED_LANES)
    verdict: Verdict = evaluate(candidate, policy, ev)
    return {"verdict": verdict, "evidence": ev}

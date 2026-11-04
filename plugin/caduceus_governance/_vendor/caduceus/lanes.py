"""Lane evaluators. Each returns {'status': ..., 'detail': <short str>}.

Statuses: Checked | Failed | NotRun | Unavailable | Inconclusive.
Silent skips are not allowed: a lane that did not run says so.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .candidate import Candidate
from .contract import Probe
from .digest import domain_digest
from .ledger import Ledger
from .stats import paired_bootstrap

STATUSES = ("Checked", "Failed", "NotRun", "Unavailable", "Inconclusive")


def _r(status: str, detail: str = "") -> Dict[str, str]:
    assert status in STATUSES
    return {"status": status, "detail": detail[:200]}


def holdout_commitment(probes: Iterable[Probe]) -> str:
    """Commitment to a held-out probe set (ids + inputs + expectations)."""
    docs = sorted((p.as_dict() for p in probes), key=lambda d: d["probe_id"])
    return domain_digest("caduceus.holdout.v1", docs)


def lane_holdout(commitment: str, revealed: Iterable[Probe], proposer_view_probe_ids: Iterable[str]) -> Dict[str, str]:
    revealed = list(revealed)
    if holdout_commitment(revealed) != commitment:
        return _r("Failed", "revealed set does not match commitment")
    leaked = {p.probe_id for p in revealed} & set(proposer_view_probe_ids)
    if leaked:
        return _r("Failed", "held-out probes visible to proposer")
    return _r("Checked")


def lane_prediction_lock(ledger: Ledger, candidate_id: str) -> Dict[str, str]:
    """A prediction_lock entry for the candidate must precede its assessment entry."""
    lock = [e["seq"] for e in ledger.find("prediction_lock") if e["body"].get("candidate_id") == candidate_id]
    assess = [e["seq"] for e in ledger.find("assessment") if e["body"].get("candidate_id") == candidate_id]
    if not lock:
        return _r("NotRun", "no prediction_lock recorded")
    if not assess:
        return _r("NotRun", "no assessment recorded")
    if min(lock) < min(assess):
        return _r("Checked")
    return _r("Failed", "assessment recorded before prediction lock")


def lane_controls(
    diffs_vs_base: Sequence[float],
    diffs_vs_random: Sequence[float],
    prereg: Dict[str, Any],
    seed: int,
) -> Dict[str, Any]:
    n_needed = prereg["n"]
    if len(diffs_vs_base) < n_needed or len(diffs_vs_random) < n_needed:
        return {**_r("Inconclusive", "underpowered: n < preregistered n"), "stats": {}}
    base = paired_bootstrap(diffs_vs_base, seed)
    rand = paired_bootstrap(diffs_vs_random, seed + 1)
    stats = {"vs_base": base, "vs_random": rand}
    if base["lower_micro"] < prereg["min_effect_micro"]:
        return {**_r("Failed", "lower bound vs base below minimum effect"), "stats": stats}
    if rand["lower_micro"] <= 0:
        return {**_r("Failed", "does not beat deterministic-random control"), "stats": stats}
    return {**_r("Checked"), "stats": stats}


def lane_non_inferiority(before: Dict[str, int], after: Dict[str, int], tolerance_micro: int = 0) -> Dict[str, str]:
    """Higher-is-better lane metrics in integer micro-units; no lane may regress."""
    if set(before) != set(after) or not before:
        return _r("Failed", "lane sets differ or empty")
    worse = sorted(k for k in before if after[k] + tolerance_micro < before[k])
    return _r("Failed", "regressed: " + ",".join(worse)) if worse else _r("Checked")


def lane_retention(failing: Sequence[str]) -> Dict[str, str]:
    return _r("Failed", "failing: " + ",".join(list(failing)[:5])) if failing else _r("Checked")


def lane_behavior(report: Any) -> Dict[str, str]:
    return _r("Checked") if report.ok else _r("Failed", ";".join(report.reasons))


def lane_composition(report: Any) -> Dict[str, str]:
    return _r("Checked") if report.ok else _r("Failed", ";".join(report.reasons))


def lane_indispensability(report: Any) -> Dict[str, str]:
    return _r("Checked") if report.ok else _r("Failed", ";".join(report.reasons))


def lane_instrument_validity(agree: int, total: int, threshold: Tuple[int, int] = (9, 10)) -> Dict[str, str]:
    """Two independent instruments must agree on at least `threshold` of cases.
    Disagreement is Inconclusive, never a pass (Theseus lesson)."""
    if total <= 0:
        return _r("NotRun", "no paired instrument results")
    if agree * threshold[1] >= threshold[0] * total:
        return _r("Checked")
    return _r("Inconclusive", "instruments disagree: %d/%d" % (agree, total))


def lane_search_cost(actual_units: int, max_units: int) -> Dict[str, str]:
    if actual_units < 0:
        return _r("Failed", "negative cost")
    if actual_units > max_units:
        return _r("Failed", "search cost %d exceeds preregistered %d" % (actual_units, max_units))
    return _r("Checked")


def lane_independent_verification(statuses: Dict[str, str]) -> Dict[str, str]:
    if not statuses:
        return _r("NotRun", "no independent lanes reported")
    if any(s == "Failed" for s in statuses.values()):
        bad = sorted(k for k, s in statuses.items() if s == "Failed")
        return _r("Failed", "failed: " + ",".join(bad))
    if all(s == "Checked" for s in statuses.values()):
        return _r("Checked")
    unready = sorted(k for k, s in statuses.items() if s != "Checked")
    return _r("Inconclusive", "not checked: " + ",".join(unready))


def lane_lyre_report(report: Optional[Dict[str, Any]], candidate: Candidate) -> Dict[str, str]:
    needed = ("before", "after", "n", "seeds", "environment_digest", "search_cost_units", "controls")
    if not isinstance(report, dict):
        return _r("NotRun", "no lyre report")
    missing = [k for k in needed if k not in report]
    if missing:
        return _r("Failed", "missing " + ",".join(missing))
    if report.get("candidate_digest") != candidate.digest():
        return _r("Failed", "report bound to a different candidate")
    return _r("Checked")


def lane_signature(candidate: Candidate, signatures: Sequence[Dict[str, str]], executor_identity: str) -> Dict[str, str]:
    """Independent signature for code patches. The proposer and the entity that
    ran the evaluation cannot sign; every required role needs a distinct signer."""
    if candidate.kind != "code_patch":
        return _r("Checked", "not required for this kind")
    signers = set()
    roles = set()
    for s in signatures:
        who, role = s.get("signer", ""), s.get("role", "")
        if not who or not role:
            return _r("Failed", "malformed signature record")
        if who in (candidate.proposer, executor_identity):
            return _r("Failed", "self-signing is forbidden")
        signers.add(who)
        roles.add(role)
    missing = [r for r in candidate.required_signer_roles if r not in roles]
    if missing:
        return _r("NotRun", "missing roles: " + ",".join(missing))
    if len(signers) < len(set(candidate.required_signer_roles)):
        return _r("Failed", "required roles must be held by distinct signers")
    return _r("Checked")

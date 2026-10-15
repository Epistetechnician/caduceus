"""Deterministic admission core: accept | reject | quarantine.

Same candidate + same policy + same evidence => same verdict. The core never
calls a model. A model may propose a verdict; this function decides.
`grants_authority` is always False: acting on an accepted verdict requires a
separate capability (see refinery.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from .candidate import Candidate, validate_candidate
from .digest import domain_digest
from .lanes import STATUSES

REQUIRED_LANES = (
    "preregistration",
    "prediction_lock",
    "holdout",
    "controls",
    "non_inferiority",
    "independent_verification",
    "retention",
    "behavior_equivalence",
    "instrument_validity",
    "search_cost",
    "indispensability",
    "lyre_report",
    "signature",
)


@dataclass(frozen=True)
class AdmissionPolicy:
    allowed_kinds: Tuple[str, ...] = ("skill_create", "skill_refine", "prompt_fragment", "config", "memory_policy")
    allowed_roots: Tuple[str, ...] = ("skills", "prompts", "config")
    allow_code_patch: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "allowed_kinds": list(self.allowed_kinds),
            "allowed_roots": list(self.allowed_roots),
            "allow_code_patch": self.allow_code_patch,
            "required_lanes": list(REQUIRED_LANES),
        }

    def digest(self) -> str:
        return domain_digest("caduceus.policy.v1", self.as_dict())


@dataclass(frozen=True)
class Verdict:
    decision: str  # accept | reject | quarantine
    reason_codes: Tuple[str, ...]
    lane_results: Tuple[Tuple[str, str], ...]
    candidate_digest: str
    policy_digest: str
    grants_authority: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
            "lane_results": dict(self.lane_results),
            "candidate_digest": self.candidate_digest,
            "policy_digest": self.policy_digest,
            "grants_authority": self.grants_authority,
        }

    def digest(self) -> str:
        return domain_digest("caduceus.verdict.v1", self.as_dict())


def evaluate(candidate: Candidate, policy: AdmissionPolicy, evidence: Dict[str, Dict[str, str]]) -> Verdict:
    cdig, pdig = candidate.digest(), policy.digest()
    reasons: List[str] = []
    lanes: Dict[str, str] = {}

    def done(decision: str) -> Verdict:
        return Verdict(decision, tuple(sorted(set(reasons))), tuple(sorted(lanes.items())), cdig, pdig)

    roots = policy.allowed_roots
    for problem in validate_candidate(candidate, roots):
        reasons.append("candidate_invalid:" + problem)
    if candidate.kind == "code_patch" and not policy.allow_code_patch:
        reasons.append("code_patch_not_allowed_by_policy")
    elif candidate.kind != "code_patch" and candidate.kind not in policy.allowed_kinds:
        reasons.append("kind_not_allowed_by_policy:" + candidate.kind)
    if not isinstance(evidence, dict):
        reasons.append("evidence_not_object")
        return done("reject")
    for name in evidence:
        if name not in REQUIRED_LANES:
            reasons.append("unknown_lane:" + str(name)[:40])
    if reasons:
        return done("reject")

    failed, unready = [], []
    for lane in REQUIRED_LANES:
        item = evidence.get(lane)
        status = item.get("status") if isinstance(item, dict) else "NotRun"
        if status not in STATUSES:
            reasons.append("lane_status_invalid:" + lane)
            return done("reject")
        lanes[lane] = status
        if status == "Failed":
            failed.append(lane)
        elif status != "Checked":
            unready.append(lane)
    if failed:
        reasons.extend("lane_failed:" + lane for lane in failed)
        return done("reject")
    if unready:
        reasons.extend("lane_not_checked:%s:%s" % (lane, lanes[lane]) for lane in unready)
        return done("quarantine")
    return done("accept")

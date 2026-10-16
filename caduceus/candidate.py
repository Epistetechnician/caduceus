"""Evolution Candidate: a typed, digest-bound proposal for exactly one change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from .digest import domain_digest, is_hex_digest

KINDS = ("skill_create", "skill_refine", "prompt_fragment", "config", "memory_policy", "code_patch")
REQUIRED_ARMS = ("untouched_base", "fixed_policy", "candidate", "deterministic_random")
REQUIRED_NONCLAIM = "LocalDevelopmentEvidenceOnly"
PREREG_FIELDS = (
    "metric",
    "threshold_micro",
    "min_effect_micro",
    "alpha_micro",
    "n",
    "seeds",
    "arms",
    "holdout_commitment",
    "max_search_cost_units",
)


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    kind: str
    parent_digest: str
    tenant_scope: str
    paths: Tuple[str, ...]
    preregistration: Dict[str, Any]
    compute_budget: int
    spend_budget_micro_usd: int
    rollback_plan_digest: str
    nonclaims: Tuple[str, ...]
    proposer: str
    required_signer_roles: Tuple[str, ...] = ()
    # Digest of the exact file contents to write (`writes_digest`). Omitted from
    # the dict when empty so pre-existing candidate digests are unchanged; the
    # broker refuses to apply a candidate without one.
    content_digest: str = ""

    def as_dict(self) -> Dict[str, Any]:
        d = {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "parent_digest": self.parent_digest,
            "tenant_scope": self.tenant_scope,
            "paths": list(self.paths),
            "preregistration": self.preregistration,
            "compute_budget": self.compute_budget,
            "spend_budget_micro_usd": self.spend_budget_micro_usd,
            "rollback_plan_digest": self.rollback_plan_digest,
            "nonclaims": list(self.nonclaims),
            "proposer": self.proposer,
            "required_signer_roles": list(self.required_signer_roles),
        }
        if self.content_digest:
            d["content_digest"] = self.content_digest
        return d

    def digest(self) -> str:
        return domain_digest("caduceus.candidate.v1", self.as_dict())


def _safe_rel(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        return False
    parts = path.split("/")
    return all(p not in ("", ".", "..") for p in parts)


def validate_candidate(c: Candidate, allowed_roots: Tuple[str, ...]) -> List[str]:
    problems: List[str] = []
    if not c.candidate_id:
        problems.append("candidate_id_empty")
    if c.kind not in KINDS:
        problems.append("kind_invalid")
    if not is_hex_digest(c.parent_digest):
        problems.append("parent_digest_invalid")
    if not is_hex_digest(c.rollback_plan_digest):
        problems.append("rollback_plan_digest_invalid")
    if not c.tenant_scope:
        problems.append("tenant_scope_empty")
    if not c.proposer:
        problems.append("proposer_empty")
    if not c.paths:
        problems.append("paths_empty")
    for p in c.paths:
        if not _safe_rel(p):
            problems.append("path_unsafe:%s" % p)
        elif not any(p == r or p.startswith(r.rstrip("/") + "/") for r in allowed_roots):
            problems.append("path_outside_allow_list:%s" % p)
    if REQUIRED_NONCLAIM not in c.nonclaims:
        problems.append("missing_nonclaim:%s" % REQUIRED_NONCLAIM)
    if c.compute_budget <= 0 or c.spend_budget_micro_usd < 0:
        problems.append("budget_invalid")
    pr = c.preregistration
    if not isinstance(pr, dict):
        problems.append("preregistration_not_object")
        return problems
    missing = [f for f in PREREG_FIELDS if f not in pr]
    if missing:
        problems.append("preregistration_missing:%s" % ",".join(missing))
        return problems
    if not isinstance(pr["n"], int) or pr["n"] < 2:
        problems.append("prereg_n_invalid")
    if not (isinstance(pr["seeds"], list) and len(pr["seeds"]) >= 2 and all(isinstance(s, int) for s in pr["seeds"])):
        problems.append("prereg_seeds_invalid")
    if not isinstance(pr["arms"], list) or any(a not in pr["arms"] for a in REQUIRED_ARMS):
        problems.append("prereg_arms_missing_required")
    for f in ("threshold_micro", "min_effect_micro", "alpha_micro", "max_search_cost_units"):
        if not isinstance(pr[f], int) or pr[f] < 0:
            problems.append("prereg_%s_invalid" % f)
    if not is_hex_digest(pr["holdout_commitment"]):
        problems.append("prereg_holdout_commitment_invalid")
    if c.content_digest and not is_hex_digest(c.content_digest):
        problems.append("content_digest_invalid")
    if c.kind == "code_patch" and not c.required_signer_roles:
        problems.append("code_patch_requires_signer_roles")
    return problems


# --- untrusted intake -------------------------------------------------------

class CandidateParseError(ValueError):
    pass


_FIELDS = {
    "candidate_id", "kind", "parent_digest", "tenant_scope", "paths", "preregistration",
    "compute_budget", "spend_budget_micro_usd", "rollback_plan_digest", "nonclaims",
    "proposer", "required_signer_roles",
}


def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise CandidateParseError("duplicate JSON key: %s" % k)
        out[k] = v
    return out


def _no_float(s):
    raise CandidateParseError("floats are not allowed; use *_micro integers")


def parse_json_strict(text: str) -> Any:
    """json.loads that rejects duplicate keys, floats, NaN and Infinity."""
    import json

    def _const(name):
        raise CandidateParseError("non-finite number: %s" % name)

    return json.loads(text, object_pairs_hook=_no_dupes, parse_float=_no_float, parse_constant=_const)


def writes_digest(writes: Dict[str, str]) -> str:
    """Binds a candidate to exact file contents (path -> text)."""
    return domain_digest("caduceus.writes.v1", {k: writes[k] for k in sorted(writes)})


def candidate_from_dict(d: Any) -> Candidate:
    if not isinstance(d, dict) or not (set(d) == _FIELDS or set(d) == _FIELDS | {"content_digest"}):
        raise CandidateParseError("candidate must have exactly fields %s" % sorted(_FIELDS))
    for f in ("candidate_id", "kind", "parent_digest", "tenant_scope", "rollback_plan_digest", "proposer"):
        if not isinstance(d[f], str):
            raise CandidateParseError("%s must be a string" % f)
    for f in ("paths", "nonclaims", "required_signer_roles"):
        if not isinstance(d[f], list) or not all(isinstance(x, str) for x in d[f]):
            raise CandidateParseError("%s must be a list of strings" % f)
    for f in ("compute_budget", "spend_budget_micro_usd"):
        if not isinstance(d[f], int) or isinstance(d[f], bool):
            raise CandidateParseError("%s must be an integer" % f)
    if not isinstance(d["preregistration"], dict):
        raise CandidateParseError("preregistration must be an object")
    return Candidate(
        candidate_id=d["candidate_id"], kind=d["kind"], parent_digest=d["parent_digest"],
        tenant_scope=d["tenant_scope"], paths=tuple(d["paths"]), preregistration=d["preregistration"],
        compute_budget=d["compute_budget"], spend_budget_micro_usd=d["spend_budget_micro_usd"],
        rollback_plan_digest=d["rollback_plan_digest"], nonclaims=tuple(d["nonclaims"]),
        proposer=d["proposer"], required_signer_roles=tuple(d["required_signer_roles"]),
        content_digest=str(d.get("content_digest", "")),
    )

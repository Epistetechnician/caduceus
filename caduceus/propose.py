"""Wrap an external proposal as a typed candidate (PRD 6.8).

Any producer that emits a proposed skill directory (for example a checkout of
the branch `hermes-agent-self-evolution` writes) is turned into a
`skill_refine` / `skill_create` Candidate against the current skill. The
producer is never the gate: the result still goes through admission.

Paths are expressed relative to the governed root as `skills/<rel_dir>/...`.
The candidate binds the parent (current tree digest, or a fixed "absent"
digest for creation) and a rollback plan digest (the parent snapshot). The
proposed file contents are returned separately as `writes` for the broker;
they never enter the ledger.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from .candidate import REQUIRED_ARMS, REQUIRED_NONCLAIM, Candidate, writes_digest
from .digest import domain_digest, sha256_hex
from .skills import load_skill

ABSENT = sha256_hex(b"caduceus.skill.absent.v1")
MAX_FILE_BYTES = 256 * 1024


def propose_skill(
    skills_root: str,
    rel_dir: str,
    proposed_dir: str,
    proposer: str,
    preregistration: Dict[str, Any],
    tenant: str = "local",
    candidate_id: Optional[str] = None,
) -> Tuple[Candidate, Dict[str, str]]:
    current = os.path.join(skills_root, rel_dir)
    exists = os.path.isdir(current)
    parent = load_skill(skills_root, rel_dir).tree_digest if exists else ABSENT
    proposed = load_skill(os.path.dirname(os.path.abspath(proposed_dir)), os.path.basename(os.path.abspath(proposed_dir)))
    writes: Dict[str, str] = {}
    for f in proposed.files:
        if f.size > MAX_FILE_BYTES:
            raise ValueError("proposed file too large: %s" % f.rel_path)
        with open(os.path.join(proposed_dir, *f.rel_path.split("/")), "r", encoding="utf-8") as fh:
            writes["skills/%s/%s" % (rel_dir, f.rel_path)] = fh.read()
    if not writes:
        raise ValueError("proposed skill directory is empty")
    cid = candidate_id or "skill:%s:%s" % (rel_dir, proposed.tree_digest[:12])
    cand = Candidate(
        candidate_id=cid,
        kind="skill_refine" if exists else "skill_create",
        parent_digest=parent,
        tenant_scope=tenant,
        paths=tuple(sorted(writes)),
        preregistration=preregistration,
        compute_budget=int(preregistration.get("max_search_cost_units", 1)) or 1,
        spend_budget_micro_usd=0,
        rollback_plan_digest=domain_digest("caduceus.rollback.restore_parent.v1", {"parent": parent, "rel_dir": rel_dir}),
        nonclaims=(REQUIRED_NONCLAIM,),
        proposer=proposer,
        content_digest=writes_digest(writes),
    )
    return cand, writes


def default_preregistration(holdout_commitment: str, n: int, seeds) -> Dict[str, Any]:
    return {"metric": "contract_pass_rate", "threshold_micro": 0, "min_effect_micro": 150000,
            "alpha_micro": 50000, "n": n, "seeds": list(seeds), "arms": list(REQUIRED_ARMS),
            "holdout_commitment": holdout_commitment, "max_search_cost_units": 400}

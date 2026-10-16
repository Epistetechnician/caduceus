"""Phase 4 gate: may anything ever auto-apply? (PRD 12, Phase 4)

Auto-apply stays blocked unless the ledgers hold at least `min_replications`
experiment verdicts that ALL of:
  * are candidate experiments (current skill vs proposed refinement vs random):
    a skill beating "no skill" says nothing about whether refinements help,
  * are `Pass`,
  * come from three-arm designs (beat the deterministic random control, not
    just the no-skill control),
  * have distinct preregistration digests (a re-run of the same locked file
    is not a replication).
This function only reports. It never flips a switch: enabling auto-apply is an
operator action outside this repository's claim ceiling.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .ledger import Ledger


def eligibility(ledgers: List[Ledger], min_replications: int = 2) -> Dict[str, Any]:
    verdicts = [e["body"] for led in ledgers for e in led.find("verdict") if "experiment_id" in e["body"]]
    qualifying = {}
    reasons: Dict[str, str] = {}
    for v in verdicts:
        eid = v["experiment_id"]
        if "design" not in v:
            reasons[eid] = "design_unrecorded"  # verdicts before the field existed never qualify
        elif v["design"] != "candidate":
            reasons[eid] = "not_a_candidate_experiment"  # skill-vs-no-skill effects say nothing about refinements
        elif v.get("verdict") != "Pass":
            reasons[eid] = "not_pass:%s" % v.get("verdict")
        elif "vs_random_lower_micro" not in v:
            reasons[eid] = "no_random_control_arm"
        else:
            qualifying[v["prereg_digest"]] = eid
    eligible = len(qualifying) >= min_replications
    return {
        "status": "Eligible" if eligible else "Blocked",
        "qualifying_experiments": sorted(qualifying.values()),
        "needed": min_replications,
        "excluded": dict(sorted(reasons.items())),
        "note": "report only; auto-apply is never enabled by this code",
    }

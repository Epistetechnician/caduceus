"""Protected regression suite and the retention-at-1.0 gate (constitution C4).

Every protected skill's full probe bundle (visible, held-out, perturbation,
negative) must pass after a refinement is applied. One failure rejects it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

from .contract import SkillContract, validate_contract
from .digest import domain_digest
from .probes import Skill, run_probes


@dataclass(frozen=True)
class ProtectedSuite:
    suite_id: str
    entries: Tuple[SkillContract, ...]

    def digest(self) -> str:
        return domain_digest(
            "caduceus.protected-suite.v1",
            {"id": self.suite_id, "contracts": sorted(c.digest() for c in self.entries)},
        )


def build_suite(suite_id: str, contracts: List[SkillContract]) -> ProtectedSuite:
    names = [c.name for c in contracts]
    if len(names) != len(set(names)):
        raise ValueError("duplicate protected skill names")
    for c in contracts:
        problems = validate_contract(c)
        if problems:
            raise ValueError("contract %s invalid: %s" % (c.name, problems[0]))
    return ProtectedSuite(suite_id, tuple(sorted(contracts, key=lambda c: c.name)))


def retention_gate(
    suite: ProtectedSuite, resolver: Callable[[str], Skill]
) -> List[str]:
    """Return failing 'skill:probe_id' entries; empty list means retention 1.0."""
    failing: List[str] = []
    for contract in suite.entries:
        try:
            skill = resolver(contract.name)
        except Exception:
            failing.append("%s:unresolvable" % contract.name)
            continue
        outcomes = run_probes(skill, contract.probes, contract.max_output_chars)
        for pid, outcome in sorted(outcomes.items()):
            if not outcome.passed:
                failing.append("%s:%s" % (contract.name, pid))
    return failing

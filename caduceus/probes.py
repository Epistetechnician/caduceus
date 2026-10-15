"""Probe execution against an injected skill callable.

A Skill here is any `Callable[[str], str]`. Caduceus never calls a model or the
network itself: executing a real Hermes skill is an adapter concern (Phase 2+),
and the core logic is tested against deterministic callables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .contract import Probe, SkillContract, judge

Skill = Callable[[str], str]


@dataclass(frozen=True)
class Outcome:
    output: Optional[str]
    passed: bool
    error: Optional[str] = None


def run_probe(skill: Skill, probe: Probe, max_output_chars: int) -> Outcome:
    try:
        out = skill(probe.input)
    except Exception as exc:  # a crashing skill is a failed probe, not a crash
        return Outcome(None, False, type(exc).__name__)
    if not isinstance(out, str):
        return Outcome(None, False, "non_string_output")
    if len(out) > max_output_chars:
        return Outcome(out[:max_output_chars], False, "output_too_long")
    return Outcome(out, judge(probe.expect_dict(), out))


def run_probes(
    skill: Skill, probes: Iterable[Probe], max_output_chars: int = 100_000
) -> Dict[str, Outcome]:
    return {p.probe_id: run_probe(skill, p, max_output_chars) for p in probes}


def pass_counts(outcomes: Dict[str, Outcome]) -> Tuple[int, int]:
    return sum(1 for o in outcomes.values() if o.passed), len(outcomes)


def run_contract(skill: Skill, contract: SkillContract) -> Dict[str, Dict[str, Outcome]]:
    return {
        kind: run_probes(skill, contract.probes_of(kind), contract.max_output_chars)
        for kind in ("visible", "held_out", "perturbation", "negative")
    }

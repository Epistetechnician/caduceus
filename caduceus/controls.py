"""Behavior-equivalence, composition and indispensability controls.

Implements PRD gate items 7 (behavior != mechanism) and 10 (indispensability),
plus the constructed positive/negative composition controls from Theseus Lab:
exact rewrites must pass, wrong-source/random parts must make the probes fail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .contract import SkillContract
from .probes import Outcome, Skill, run_contract, run_probes

Counts = Tuple[int, int]  # (passed, total)


@dataclass(frozen=True)
class BehaviorReport:
    mode: str
    ok: bool
    reasons: Tuple[str, ...]
    base_pass: Dict[str, Counts]
    cand_pass: Dict[str, Counts]
    output_equal: Counts
    sensitivity_agreement: Counts


def _counts(outcomes: Dict[str, Outcome]) -> Counts:
    return sum(1 for o in outcomes.values() if o.passed), len(outcomes)


def compare_behavior(
    base: Skill, cand: Skill, contract: SkillContract, mode: str = "refactor"
) -> BehaviorReport:
    """mode='refactor': outputs must be identical on every probe and the
    sensitivity pattern around visible probes must agree.
    mode='improvement': candidate must be non-inferior per probe kind."""
    if mode not in ("refactor", "improvement"):
        raise ValueError("mode must be 'refactor' or 'improvement'")
    base_res, cand_res = run_contract(base, contract), run_contract(cand, contract)
    base_pass = {k: _counts(v) for k, v in base_res.items()}
    cand_pass = {k: _counts(v) for k, v in cand_res.items()}

    equal = total = 0
    for kind in base_res:
        for pid, bo in base_res[kind].items():
            total += 1
            if bo.output is not None and bo.output == cand_res[kind][pid].output:
                equal += 1

    agree = n_pert = 0
    visible_in = {p.probe_id: p for p in contract.probes_of("visible")}
    for p in contract.probes_of("perturbation"):
        n_pert += 1
        base_p = base_res["visible"].get(p.neighbor_of)
        cand_p = cand_res["visible"].get(p.neighbor_of)
        if base_p is None or cand_p is None:
            continue
        base_changed = base_p.output != base_res["perturbation"][p.probe_id].output
        cand_changed = cand_p.output != cand_res["perturbation"][p.probe_id].output
        if base_changed == cand_changed:
            agree += 1

    reasons: List[str] = []
    if mode == "refactor":
        if equal != total:
            reasons.append("outputs_differ:%d/%d_equal" % (equal, total))
        if agree != n_pert:
            reasons.append("sensitivity_differs:%d/%d_agree" % (agree, n_pert))
    else:
        for kind in ("visible", "held_out", "negative"):
            if cand_pass[kind][0] * base_pass[kind][1] < base_pass[kind][0] * cand_pass[kind][1]:
                reasons.append("worse_on_%s" % kind)
    return BehaviorReport(
        mode, not reasons, tuple(reasons), base_pass, cand_pass, (equal, total), (agree, n_pert)
    )


@dataclass(frozen=True)
class CompositionReport:
    ok: bool
    reasons: Tuple[str, ...]
    full: Counts
    rewrites: Dict[str, Counts]
    wrong_sources: Dict[str, Counts]


Factory = Callable[[Dict[str, Skill]], Skill]


def composition_control(
    factory: Factory,
    parts: Dict[str, Skill],
    exact_rewrites: Dict[str, Skill],
    wrong_sources: Dict[str, Skill],
    contract: SkillContract,
) -> CompositionReport:
    """Positive control: swapping a part for an exact rewrite keeps all probes
    green. Negative control: swapping it for a wrong-source part must break at
    least one probe, otherwise the probe bundle is not discriminating."""
    probes = list(contract.probes)

    def rate(skill: Skill) -> Counts:
        return _counts(run_probes(skill, probes, contract.max_output_chars))

    full = rate(factory(parts))
    rewrites = {n: rate(factory({**parts, n: s})) for n, s in exact_rewrites.items()}
    wrongs = {n: rate(factory({**parts, n: s})) for n, s in wrong_sources.items()}
    reasons: List[str] = []
    if full[0] != full[1]:
        reasons.append("full_composite_fails_own_probes")
    for name, c in rewrites.items():
        if c != full:
            reasons.append("exact_rewrite_changes_result:%s" % name)
    for name in parts:
        if name not in wrongs:
            reasons.append("no_wrong_source_control_for:%s" % name)
    for name, c in wrongs.items():
        if c[0] == c[1]:
            reasons.append("probes_do_not_detect_wrong_source:%s" % name)
    return CompositionReport(not reasons, tuple(reasons), full, rewrites, wrongs)


@dataclass(frozen=True)
class IndispensabilityReport:
    ok: bool
    reasons: Tuple[str, ...]
    full: Counts
    ablated: Dict[str, Counts]


def indispensability(
    factory: Factory,
    parts: Dict[str, Skill],
    target: str,
    ablations: Dict[str, Skill],
    contract: SkillContract,
    min_drop: Tuple[int, int] = (1, 5),
) -> IndispensabilityReport:
    """A skill is credited only if replacing it with zero/random skills lowers
    the pass rate by at least min_drop (num, den) of the probes."""
    probes = list(contract.probes)

    def rate(skill: Skill) -> Counts:
        return _counts(run_probes(skill, probes, contract.max_output_chars))

    full = rate(factory(parts))
    ablated = {n: rate(factory({**parts, target: s})) for n, s in ablations.items()}
    reasons: List[str] = []
    if not ablations:
        reasons.append("no_ablations_supplied")
    num, den = min_drop
    for name, c in ablated.items():
        # (full_rate - abl_rate) >= num/den  <=>  cross-multiplied, all ints
        drop_num = full[0] * c[1] - c[0] * full[1]
        if drop_num * den < num * full[1] * c[1]:
            reasons.append("ablation_does_not_degrade:%s" % name)
    return IndispensabilityReport(not reasons, tuple(reasons), full, ablated)

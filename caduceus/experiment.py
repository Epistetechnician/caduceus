"""Preregistered paired test: does a skill change its own contract pass rate?

Flow (each step refuses if the previous one is missing):

  1. `load_prereg` validates the preregistration file (exact keys, no floats).
  2. `lock` checks every named contract and skill tree still matches the
     digests written in the file, then appends a `prediction_lock` entry with
     the preregistration digest. One lock per experiment_id.
  3. `run_paired` refuses unless that lock exists and no verdict exists yet.
     For every (skill, probe, rep) it runs both arms in a seeded random order,
     records only digests and pass bits, and appends one `assessment` per
     skill and one `verdict` for the experiment. A run is one-shot: a failed
     experiment closes this experiment_id (see AGENTS.md).

Arms: two-arm (without_skill, with_skill) or three-arm adding `random_skill`,
a deterministic wrong-source control: another skill from the same set (seeded
derangement), so compute and prompt size are matched but the content is wrong.
A three-arm Pass needs the decision rule to hold against BOTH controls.

Statistics: units are paired pass differences (treatment - control). Reps of
the same probe are correlated, so the bootstrap resamples per-probe cluster
means, not individual reps. A unit where either arm did not run is excluded
and counted; too many exclusions make the verdict `NotRun`, never a pass.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .candidate import REQUIRED_NONCLAIM
from .contract import PROBE_KINDS, ContractFile
from .digest import domain_digest, sha256_hex
from .ledger import Ledger
from .probes import Skill, run_probe
from .stats import MICRO, paired_bootstrap

PREREG_SCHEMA = "caduceus.prereg.v1"
ARMS = ("without_skill", "with_skill")
ARMS_RANDOM = ("without_skill", "random_skill", "with_skill")
# Candidate evaluation (the Exchange): current skill vs proposed skill vs random.
ARMS_CANDIDATE = ("untouched_base", "deterministic_random", "candidate")
# arm set -> (control, random or None, treatment)
ROLES = {
    ARMS: ("without_skill", None, "with_skill"),
    ARMS_RANDOM: ("without_skill", "random_skill", "with_skill"),
    ARMS_CANDIDATE: ("untouched_base", "deterministic_random", "candidate"),
}
VERDICTS = ("Pass", "Fail", "NotRun")
_KEYS = {
    "schema", "experiment_id", "hypothesis", "arms", "skills", "probe_kinds",
    "reps", "order_seed", "bootstrap_seed", "bootstrap_resamples", "executor",
    "min_effect_micro", "alpha_micro", "max_notrun_micro", "decision_rule", "nonclaim",
}
_SKILL_KEYS = {"skill", "contract_digest", "tree_digest"}
_EXEC_KEYS = {"provider", "model", "base_url", "temperature"}


class ExperimentError(Exception):
    pass


@dataclass(frozen=True)
class Prereg:
    raw: Dict[str, Any]

    @property
    def experiment_id(self) -> str:
        return self.raw["experiment_id"]

    def digest(self) -> str:
        return domain_digest("caduceus.prereg.v1", self.raw)


def _no_float(s: str) -> Any:
    raise ExperimentError("float in preregistration; use *_micro integers")


def load_prereg(path: str) -> Prereg:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh, parse_float=_no_float)
    if not isinstance(raw, dict) or set(raw) != _KEYS:
        raise ExperimentError("prereg: expected exactly keys %s" % sorted(_KEYS))
    if raw["schema"] != PREREG_SCHEMA:
        raise ExperimentError("prereg: unknown schema")
    if raw["nonclaim"] != REQUIRED_NONCLAIM:
        raise ExperimentError("prereg: nonclaim must be %s" % REQUIRED_NONCLAIM)
    if tuple(raw["arms"]) not in ROLES:
        raise ExperimentError("prereg: arms must be one of %s" % (list(ROLES),))
    if not raw["probe_kinds"] or not set(raw["probe_kinds"]) <= set(PROBE_KINDS):
        raise ExperimentError("prereg: probe_kinds must be a non-empty subset of %s" % (PROBE_KINDS,))
    if not isinstance(raw["skills"], list) or not raw["skills"]:
        raise ExperimentError("prereg: skills must be a non-empty list")
    for s in raw["skills"]:
        if not isinstance(s, dict) or set(s) != _SKILL_KEYS:
            raise ExperimentError("prereg: each skill needs exactly %s" % sorted(_SKILL_KEYS))
    if not isinstance(raw["executor"], dict) or set(raw["executor"]) != _EXEC_KEYS:
        raise ExperimentError("prereg: executor needs exactly %s" % sorted(_EXEC_KEYS))
    if raw["executor"]["temperature"] != "provider_default":
        raise ExperimentError("prereg: Hermes exposes no temperature; executor.temperature must be provider_default")
    for k in ("reps", "order_seed", "bootstrap_seed", "bootstrap_resamples",
              "min_effect_micro", "alpha_micro", "max_notrun_micro"):
        if not isinstance(raw[k], int) or isinstance(raw[k], bool) or raw[k] < 0:
            raise ExperimentError("prereg: %s must be a non-negative integer" % k)
    if raw["reps"] < 1 or raw["bootstrap_resamples"] < 100:
        raise ExperimentError("prereg: reps >= 1 and bootstrap_resamples >= 100")
    if not 0 < raw["alpha_micro"] < MICRO // 2 or not 0 < raw["min_effect_micro"] <= MICRO:
        raise ExperimentError("prereg: alpha_micro or min_effect_micro out of range")
    return Prereg(raw)


def _check_pins(prereg: Prereg, contracts: Dict[str, ContractFile], tree_digests: Dict[str, str]) -> None:
    for s in prereg.raw["skills"]:
        cf = contracts.get(s["skill"])
        if cf is None:
            raise ExperimentError("no contract for %s" % s["skill"])
        if cf.contract.digest() != s["contract_digest"]:
            raise ExperimentError("contract digest changed for %s" % s["skill"])
        if tree_digests.get(s["skill"]) != s["tree_digest"]:
            raise ExperimentError("skill tree digest changed for %s" % s["skill"])


def _locks(ledger: Ledger, experiment_id: str) -> List[Dict[str, Any]]:
    return [e for e in ledger.find("prediction_lock") if e["body"].get("experiment_id") == experiment_id]


def _verdicts(ledger: Ledger, experiment_id: str) -> List[Dict[str, Any]]:
    return [e for e in ledger.find("verdict") if e["body"].get("experiment_id") == experiment_id]


def lock(prereg: Prereg, ledger: Ledger, contracts: Dict[str, ContractFile], tree_digests: Dict[str, str]) -> Dict[str, Any]:
    _check_pins(prereg, contracts, tree_digests)
    if _locks(ledger, prereg.experiment_id):
        raise ExperimentError("experiment %s is already locked" % prereg.experiment_id)
    return ledger.append(
        "prediction_lock",
        {"experiment_id": prereg.experiment_id, "prereg_digest": prereg.digest(), "nonclaim": REQUIRED_NONCLAIM},
    )


ExecutorFactory = Callable[[str, Optional[ContractFile]], Skill]


def _decoys(skills: List[str], seed: int) -> Dict[str, str]:
    """Deterministic derangement: each skill's random control is another skill."""
    n = len(skills)
    if n < 2:
        raise ExperimentError("random_skill arm needs at least two skills")
    k = 1 + seed % (n - 1)
    return {skills[i]: skills[(i + k) % n] for i in range(n)}


def _decide(clusters: List[float], raw: Dict[str, Any], seed_offset: int) -> Dict[str, Any]:
    ci = paired_bootstrap(clusters, raw["bootstrap_seed"] + seed_offset, raw["bootstrap_resamples"], raw["alpha_micro"] / MICRO)
    return {"mean_micro": ci["mean_micro"], "lower_micro": ci["lower_micro"], "upper_micro": ci["upper_micro"],
            "passed": ci["lower_micro"] > 0 and ci["mean_micro"] >= raw["min_effect_micro"]}


def run_paired(
    prereg: Prereg,
    ledger: Ledger,
    contracts: Dict[str, ContractFile],
    tree_digests: Dict[str, str],
    make_executor: ExecutorFactory,
    custody: Any = None,
) -> Dict[str, Any]:
    """`make_executor(arm, contract_file)` returns a Skill callable.

    With `custody` (a `custody.Custody`), every raw output is kept in
    owner-only custody for a second instrument; the ledger still gets digests only.

    `without_skill` must ignore the contract file; `with_skill` loads that
    skill; `random_skill` (three-arm designs) is handed a *different* skill's
    contract file (a seeded derangement) and loads that one. The factory owns
    cleanup.
    """
    _check_pins(prereg, contracts, tree_digests)
    locks = _locks(ledger, prereg.experiment_id)
    if len(locks) != 1 or locks[0]["body"]["prereg_digest"] != prereg.digest():
        raise ExperimentError("no matching prediction_lock for this preregistration")
    if _verdicts(ledger, prereg.experiment_id):
        raise ExperimentError("experiment %s already has a verdict; it is closed" % prereg.experiment_id)
    raw = prereg.raw
    arm_names = tuple(raw["arms"])
    ctl_arm, rnd_arm, trt_arm = ROLES[arm_names]
    three = rnd_arm is not None
    if arm_names == ARMS_CANDIDATE:
        # One skill under test; its wrong-source control is another contracted skill.
        decoys = {}
        for s_ in raw["skills"]:
            pool = sorted(k for k in contracts if k != s_["skill"])
            if not pool:
                raise ExperimentError("candidate design needs at least one other contract as random control")
            decoys[s_["skill"]] = pool[raw["order_seed"] % len(pool)]
    else:
        decoys = _decoys([s["skill"] for s in raw["skills"]], raw["order_seed"]) if three else {}
    rng = random.Random(raw["order_seed"])
    clusters_base: List[float] = []
    clusters_random: List[float] = []
    total = excluded = 0
    per_skill: Dict[str, Dict[str, int]] = {}
    for s in raw["skills"]:
        cf = contracts[s["skill"]]
        arms = {arm: make_executor(arm, contracts[decoys[s["skill"]]] if arm == rnd_arm else cf)
                for arm in arm_names}
        counts = {"units": 0, "excluded": 0, "control_pass": 0, "treatment_pass": 0}
        if three:
            counts["random_pass"] = 0
        outputs_digest_parts: List[str] = []
        per_probe: Dict[str, List[int]] = {}
        for probe in cf.contract.probes:
            if probe.kind not in raw["probe_kinds"]:
                continue
            pp = per_probe.setdefault(probe.probe_id, [0, 0, 0, 0])  # runs, control, random, treatment
            diffs: List[int] = []
            diffs_r: List[int] = []
            for rep in range(raw["reps"]):
                order = list(arm_names)
                rng.shuffle(order)
                res = {arm: run_probe(arms[arm], probe, cf.contract.max_output_chars) for arm in order}
                total += 1
                counts["units"] += 1
                if any(r.error == "ExecutorError" for r in res.values()):
                    excluded += 1
                    counts["excluded"] += 1
                    continue
                c, t = int(res[ctl_arm].passed), int(res[trt_arm].passed)
                counts["control_pass"] += c
                counts["treatment_pass"] += t
                diffs.append(t - c)
                pp[0] += 1
                pp[1] += c
                pp[3] += t
                if three:
                    r_ = int(res[rnd_arm].passed)
                    counts["random_pass"] += r_
                    diffs_r.append(t - r_)
                    pp[2] += r_
                for arm in arm_names:
                    if custody is not None:
                        custody.put(prereg.experiment_id, probe.probe_id, arm, rep, res[arm].output or "")
                    outputs_digest_parts.append(
                        "%s|%s|%d|%s" % (probe.probe_id, arm, rep, sha256_hex((res[arm].output or "").encode("utf-8")))
                    )
            if diffs:
                clusters_base.append(sum(diffs) / len(diffs))
                if three:
                    clusters_random.append(sum(diffs_r) / len(diffs_r))
        body = {
            "experiment_id": prereg.experiment_id,
            "skill": s["skill"],
            "contract_digest": s["contract_digest"],
            "outputs_digest": sha256_hex("\n".join(outputs_digest_parts).encode("utf-8")),
            **counts,
        }
        if three:
            body["random_control_skill"] = decoys[s["skill"]]
        body["per_probe"] = {pid: list(v) for pid, v in sorted(per_probe.items())}
        ledger.append("assessment", body)
        per_skill[s["skill"]] = counts
    notrun_micro = (excluded * MICRO) // total if total else MICRO
    result: Dict[str, Any] = {
        "experiment_id": prereg.experiment_id,
        "prereg_digest": prereg.digest(),
        "units": total,
        "excluded": excluded,
        "clusters": len(clusters_base),
        "design": "candidate" if arm_names == ARMS_CANDIDATE else "skill_effect",
        "nonclaim": REQUIRED_NONCLAIM,
    }
    if notrun_micro > raw["max_notrun_micro"] or len(clusters_base) < 2:
        result.update(verdict="NotRun", reason="too_many_excluded_units")
    else:
        base = _decide(clusters_base, raw, 0)
        result.update(mean_micro=base["mean_micro"], lower_micro=base["lower_micro"], upper_micro=base["upper_micro"])
        passed = base["passed"]
        if three:
            vs = _decide(clusters_random, raw, 1)
            result.update(vs_random_mean_micro=vs["mean_micro"], vs_random_lower_micro=vs["lower_micro"],
                          vs_random_upper_micro=vs["upper_micro"])
            passed = passed and vs["passed"]
        result.update(verdict="Pass" if passed else "Fail", reason="decision_rule")
    ledger.append("verdict", result)
    result["per_skill"] = per_skill
    return result

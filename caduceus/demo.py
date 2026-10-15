"""End-to-end synthetic demonstration of Phase 1/2 machinery on a sandbox.

No model, network, or real Hermes home is touched. Skills are tiny declarative
JSON specs interpreted by `run_spec` (a closed, safe op set) so that applying a
candidate really changes behavior and the protected suite can really catch a
regression. Scenarios:

  A  exact refactor                      -> accept, apply, release
  B  visible-overfit replacement         -> reject (held-out + sensitivity)
  C  improvement, instruments disagree   -> quarantine (Inconclusive)
  D  side-effect breaks protected skill  -> reject at admission (retention)
  E  stale retention evidence            -> accept on paper, apply-time
                                            revalidation fails, auto rollback
  F  code patch, self-signed             -> reject (no self-signing)
  G  replayed capability                 -> refused (single use)
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import controls, lanes, readback
from .admission import AdmissionPolicy, REQUIRED_LANES, Verdict, evaluate
from .candidate import REQUIRED_ARMS, REQUIRED_NONCLAIM, Candidate
from .contract import ContractPolicy, SkillContract, make_probe, validate_contract
from .digest import domain_digest, sha256_hex
from .freshness import make_snapshot, classify
from .ledger import Ledger
from .probes import Skill, run_probes
from .protected import ProtectedSuite, build_suite, retention_gate
from .refinery import Refinery, RefineryError
from .witness import FileWitness


# --- tiny safe skill interpreter ------------------------------------------------
def run_spec(spec: Dict[str, Any], text: str) -> str:
    value: Any = text
    for op in spec["ops"]:
        kind = op["op"]
        if kind == "table":
            if isinstance(value, str) and value in op["map"]:
                return op["map"][value]
        elif kind == "lower":
            value = value.lower()
        elif kind == "upper":
            value = value.upper()
        elif kind == "regex_sub":
            value = re.sub(op["pattern"], op["repl"], value)
        elif kind == "strip":
            value = value.strip(op["chars"])
        elif kind == "split_ws":
            value = value.split()
        elif kind == "first_chars":
            value = [w[0] for w in value if w]
        elif kind == "join":
            value = op["sep"].join(value)
        elif kind == "const":
            value = op["value"]
        else:
            raise ValueError("unknown op")
    if isinstance(value, list):
        value = "".join(value)
    return value


def spec_text(ops: List[Dict[str, Any]]) -> str:
    return json.dumps({"ops": ops}, sort_keys=True)


def skill_from_text(text: str) -> Skill:
    spec = json.loads(text)
    return lambda s: run_spec(spec, s)


SLUG_V0 = [
    {"op": "lower"},
    {"op": "regex_sub", "pattern": "[^a-z0-9]+", "repl": "-"},
    {"op": "strip", "chars": "-"},
]
SLUG_REFACTOR = [
    {"op": "lower"},
    {"op": "regex_sub", "pattern": "[^a-z0-9]", "repl": " "},
    {"op": "split_ws"},
    {"op": "join", "sep": "-"},
]
INITIALS_V0 = [{"op": "split_ws"}, {"op": "first_chars"}, {"op": "join", "sep": ""}, {"op": "upper"}]


def slug_contract() -> SkillContract:
    P = make_probe
    return SkillContract(
        name="slugify",
        io_schema=(("input", "string"), ("output", "string")),
        preconditions=("input is UTF-8 text",),
        side_effect_class="none",
        max_output_chars=200,
        probes=(
            P("v1", "visible", "Hello World", "exact", "hello-world"),
            P("v2", "visible", "  A  B ", "exact", "a-b"),
            P("h1", "held_out", "C++ Rocks!", "exact", "c-rocks"),
            P("h2", "held_out", "2026/10/15", "exact", "2026-10-15"),
            P("p1", "perturbation", "Hello  World", "exact", "hello-world", neighbor_of="v1"),
            P("p2", "perturbation", "Hello Worlds", "exact", "hello-worlds", neighbor_of="v1"),
            P("n1", "negative", "", "exact", ""),
        ),
        expiry_review_date="2027-01-01",
    )


def initials_contract() -> SkillContract:
    P = make_probe
    return SkillContract(
        name="initials",
        io_schema=(("input", "string"), ("output", "string")),
        preconditions=("input is a personal name",),
        side_effect_class="none",
        max_output_chars=50,
        probes=(
            P("v1", "visible", "Grace Hopper", "exact", "GH"),
            P("v2", "visible", "ada lovelace", "exact", "AL"),
            P("h1", "held_out", "Alan Mathison Turing", "exact", "AMT"),
            P("h2", "held_out", "  Katherine  Johnson ", "exact", "KJ"),
            P("p1", "perturbation", "Grace  Hopper", "exact", "GH", neighbor_of="v1"),
            P("n1", "negative", "", "exact", ""),
        ),
        expiry_review_date="2027-01-01",
    )


def _random_skill(seed: int) -> Skill:
    rng = random.Random(seed)
    return lambda s: "".join(rng.choice("xyz") for _ in range(rng.randint(1, 6)))


def _zero_skill(_: str) -> str:
    return ""


class Clock:
    def __init__(self, start: int = 1_700_000_000):
        self.t = start

    def __call__(self) -> int:
        self.t += 1
        return self.t


# --- helpers ---------------------------------------------------------------------
def _candidate(
    cid: str,
    kind: str,
    paths: Tuple[str, ...],
    parent: str,
    holdout: str,
    n: int,
    min_effect: int = 0,
    proposer: str = "hermes",
    roles: Tuple[str, ...] = (),
) -> Candidate:
    return Candidate(
        candidate_id=cid,
        kind=kind,
        parent_digest=parent,
        tenant_scope="tenant-demo",
        paths=paths,
        preregistration={
            "metric": "probe_pass_diff",
            "threshold_micro": 0,
            "min_effect_micro": min_effect,
            "alpha_micro": 50_000,
            "n": n,
            "seeds": [11, 22],
            "arms": list(REQUIRED_ARMS),
            "holdout_commitment": holdout,
            "max_search_cost_units": 50,
        },
        compute_budget=100,
        spend_budget_micro_usd=0,
        rollback_plan_digest=sha256_hex(b"restore-previous-snapshot"),
        nonclaims=(REQUIRED_NONCLAIM,),
        proposer=proposer,
        required_signer_roles=roles,
    )


def _diffs(cand: Skill, ref: Skill, contract: SkillContract) -> List[float]:
    probes = list(contract.probes)
    a = run_probes(cand, probes, contract.max_output_chars)
    b = run_probes(ref, probes, contract.max_output_chars)
    return [float(a[p.probe_id].passed) - float(b[p.probe_id].passed) for p in probes]


class Sandbox:
    def __init__(self, base: str):
        self.base = base
        self.root = os.path.join(base, "skills_root")
        self.store = os.path.join(base, "store")
        self.ledger = Ledger(os.path.join(base, "ledger.jsonl"))
        self.witness = FileWitness(os.path.join(base, "witness", "heads.jsonl"))
        self.clock = Clock()
        self.refinery = Refinery(self.root, self.store, self.ledger, self.clock)
        self.contracts = {"slugify": slug_contract(), "initials": initials_contract()}
        for c in self.contracts.values():
            assert not validate_contract(c, ContractPolicy()), validate_contract(c)
        self.suite: ProtectedSuite = build_suite("demo-protected", list(self.contracts.values()))
        os.makedirs(os.path.join(self.root, "skills", "slugify"))
        os.makedirs(os.path.join(self.root, "skills", "initials"))
        self._write("skills/slugify/spec.json", spec_text(SLUG_V0))
        self._write("skills/initials/spec.json", spec_text(INITIALS_V0))
        self.parent = self.refinery.snapshot()

    def _write(self, rel: str, text: str) -> None:
        with open(os.path.join(self.root, *rel.split("/")), "w", encoding="utf-8") as fh:
            fh.write(text)

    def resolver(self, root: Optional[str] = None) -> Callable[[str], Skill]:
        r = root or self.root

        def resolve(name: str) -> Skill:
            with open(os.path.join(r, "skills", name, "spec.json"), "r", encoding="utf-8") as fh:
                return skill_from_text(fh.read())

        return resolve

    def simulate_retention(self, writes: Dict[str, str]) -> List[str]:
        tmp = tempfile.mkdtemp(prefix="caduceus-sim-")
        try:
            sim = os.path.join(tmp, "root")
            shutil.copytree(self.root, sim)
            for rel, text in writes.items():
                path = os.path.join(sim, *rel.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
            return retention_gate(self.suite, self.resolver(sim))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def independent_statuses(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        try:
            rb = readback.verify_file(self.ledger.path)
            head = self.ledger.head()
            out["ledger_readback"] = "Checked" if (rb["root"], rb["size"]) == (head.root, head.size) else "Failed"
        except readback.ReadbackError:
            out["ledger_readback"] = "Failed"
        paths = [os.path.join(self.root, "skills", n, "spec.json") for n in sorted(self.contracts)]
        snap = make_snapshot(paths, self.clock())
        out["freshness_differential"] = "Checked" if classify(snap, paths, True)["incident"] == "no" else "Failed"
        return out


def evaluate_scenario(
    sb: Sandbox,
    cid: str,
    kind: str,
    skill_name: str,
    new_text: str,
    mode: str,
    extra_writes: Optional[Dict[str, str]] = None,
    instrument_agree: Tuple[int, int] = (10, 10),
    signatures: Optional[List[Dict[str, str]]] = None,
    policy: Optional[AdmissionPolicy] = None,
    proposer: str = "hermes",
    stale_retention: Optional[List[str]] = None,
) -> Tuple[Candidate, Verdict, Dict[str, str]]:
    policy = policy or AdmissionPolicy()
    contract = sb.contracts[skill_name]
    base = sb.resolver()(skill_name)
    cand_skill = skill_from_text(new_text)
    writes = {"skills/%s/spec.json" % skill_name: new_text}
    writes.update(extra_writes or {})
    holdout = lanes.holdout_commitment(contract.probes_of("held_out"))
    n = len(contract.probes)
    roles = ("security-reviewer",) if kind == "code_patch" else ()
    paths = tuple(sorted(writes)) if kind != "code_patch" else ("config/patch.json",)
    cand = _candidate(cid, kind, paths, sb.parent, holdout, n, proposer=proposer, roles=roles)

    led = sb.ledger
    led.append("candidate", {"candidate_id": cid, "candidate_digest": cand.digest(), "kind": kind})
    led.append("holdout_commitment", {"candidate_id": cid, "commitment": holdout})
    led.append("prediction_lock", {"candidate_id": cid, "predicted_effect_micro": 0})
    behavior = controls.compare_behavior(base, cand_skill, contract, mode)
    zero, rnd = _zero_skill, _random_skill(7)
    d_base = _diffs(cand_skill, base, contract)
    d_rand = _diffs(cand_skill, rnd, contract)
    led.append("assessment", {"candidate_id": cid, "behavior_ok": behavior.ok})

    factory = lambda parts: parts["main"]  # single-part composite for this skill
    comp = controls.composition_control(
        factory,
        {"main": cand_skill},
        {"main": cand_skill},
        {"main": rnd},
        contract,
    )
    indisp = controls.indispensability(factory, {"main": cand_skill}, "main", {"zero": zero, "random": rnd}, contract)
    failing = stale_retention if stale_retention is not None else sb.simulate_retention(writes)
    lyre = {
        "candidate_digest": cand.digest(),
        "before": list(behavior.base_pass["held_out"]),
        "after": list(behavior.cand_pass["held_out"]),
        "n": n,
        "seeds": [11, 22],
        "environment_digest": sha256_hex(b"sandbox-demo"),
        "search_cost_units": 10,
        "controls": ["untouched_base", "deterministic_random"],
    }
    led.append("lyre_report", {"candidate_id": cid, "report_digest": domain_digest("caduceus.lyre.v1", lyre)})
    evidence = {
        "preregistration": lanes._r("Checked"),
        "prediction_lock": lanes.lane_prediction_lock(led, cid),
        "holdout": lanes.lane_holdout(
            holdout, contract.probes_of("held_out"), [p.probe_id for p in contract.probes_of("visible")]
        ),
        "controls": lanes.lane_controls(d_base, d_rand, cand.preregistration, seed=5),
        "non_inferiority": lanes.lane_non_inferiority({"freshness": 1_000_000, "retention": 1_000_000}, {"freshness": 1_000_000, "retention": 1_000_000}),
        "independent_verification": lanes.lane_independent_verification(sb.independent_statuses()),
        "retention": lanes.lane_retention(failing),
        "behavior_equivalence": lanes.lane_behavior(behavior),
        "instrument_validity": lanes.lane_instrument_validity(*instrument_agree),
        "search_cost": lanes.lane_search_cost(10, cand.preregistration["max_search_cost_units"]),
        "indispensability": lanes.lane_indispensability(indisp),
        "lyre_report": lanes.lane_lyre_report(lyre, cand),
        "signature": lanes.lane_signature(cand, signatures or [], executor_identity="apollo-evaluator"),
    }
    ev = {k: {"status": v["status"], "detail": v.get("detail", "")} for k, v in evidence.items()}
    verdict = evaluate(cand, policy, ev)
    led.append(
        "verdict",
        {
            "candidate_id": cid,
            "decision": verdict.decision,
            "verdict_digest": verdict.digest(),
            "reason_codes": list(verdict.reason_codes)[:8],
        },
    )
    return cand, verdict, writes


def run_demo(out_dir: Optional[str] = None) -> Dict[str, Any]:
    base = out_dir or tempfile.mkdtemp(prefix="caduceus-demo-")
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("output directory must be empty: %s" % base)
    os.makedirs(base, exist_ok=True)
    sb = Sandbox(base)
    results: Dict[str, Any] = {}

    def record(name: str, verdict: Verdict, extra: Optional[Dict[str, Any]] = None) -> None:
        results[name] = {"decision": verdict.decision, "reasons": list(verdict.reason_codes)[:4], **(extra or {})}
        sb.witness.observe(sb.ledger)

    # A: exact refactor -> accept -> apply
    cand, v, w = evaluate_scenario(sb, "A-refactor", "skill_refine", "slugify", spec_text(SLUG_REFACTOR), "refactor")
    ap = None
    if v.decision == "accept":
        tok = sb.refinery.issue_capability(cand, v, 60)
        ap = sb.refinery.apply(cand, v, tok, w, lambda: retention_gate(sb.suite, sb.resolver()))
        results["A-replay-token"] = "refused"
        try:
            sb.refinery.apply(cand, v, tok, w, lambda: [])
            results["A-replay-token"] = "NOT REFUSED"
        except RefineryError as exc:
            results["A-replay-token"] = "refused: " + str(exc)
    record("A-refactor", v, {"applied": bool(ap and ap.applied)})

    # B: visible-overfit replacement
    table = {"Hello World": "hello-world", "  A  B ": "a-b"}
    overfit = [{"op": "table", "map": table}, {"op": "lower"}, {"op": "regex_sub", "pattern": "[^a-z0-9]", "repl": "-"}]
    _, v, _ = evaluate_scenario(sb, "B-overfit", "skill_refine", "slugify", spec_text(overfit), "refactor")
    record("B-overfit", v)

    # C: improvement with disagreeing instruments
    _, v, _ = evaluate_scenario(
        sb, "C-instruments", "skill_refine", "slugify", spec_text(SLUG_REFACTOR), "improvement", instrument_agree=(6, 10)
    )
    record("C-instruments", v)

    # D: side effect on a protected skill
    broken_initials = spec_text([{"op": "const", "value": "??"}])
    _, v, _ = evaluate_scenario(
        sb, "D-side-effect", "skill_refine", "slugify", spec_text(SLUG_REFACTOR), "refactor",
        extra_writes={"skills/initials/spec.json": broken_initials},
    )
    record("D-side-effect", v)

    # E: stale retention evidence -> apply-time revalidation -> rollback
    cand, v, w = evaluate_scenario(
        sb, "E-stale", "skill_refine", "slugify", spec_text(SLUG_REFACTOR), "refactor",
        extra_writes={"skills/initials/spec.json": broken_initials}, stale_retention=[],
    )
    extra: Dict[str, Any] = {}
    if v.decision == "accept":
        tok = sb.refinery.issue_capability(cand, v, 60)
        ap = sb.refinery.apply(cand, v, tok, w, lambda: retention_gate(sb.suite, sb.resolver()))
        extra = {"applied": ap.applied, "apply_reason": ap.reason}
        extra["initials_still_correct"] = sb.resolver()("initials")("Grace Hopper") == "GH"
    record("E-stale", v, extra)

    # F: code patch, self-signed
    code_policy = AdmissionPolicy(allow_code_patch=True)
    _, v, _ = evaluate_scenario(
        sb, "F-self-signed", "code_patch", "slugify", spec_text(SLUG_REFACTOR), "refactor",
        policy=code_policy, signatures=[{"signer": "hermes", "role": "security-reviewer"}],
    )
    record("F-self-signed", v)

    rb = readback.verify_file(sb.ledger.path)
    head = sb.ledger.head()
    results["ledger"] = {
        "entries": head.size,
        "root": head.root,
        "independent_readback_agrees": rb["root"] == head.root and rb["size"] == head.size,
    }
    results["claim"] = "LocalDevelopmentSyntheticDemo; sandbox only; not evidence about Hermes"
    results["output_dir"] = base
    return results

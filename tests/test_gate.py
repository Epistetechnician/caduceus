import os
import tempfile
import unittest

from caduceus import controls, lanes
from caduceus.admission import AdmissionPolicy, REQUIRED_LANES, evaluate
from caduceus.candidate import REQUIRED_ARMS, REQUIRED_NONCLAIM, Candidate, validate_candidate
from caduceus.contract import ContractPolicy, SkillContract, make_probe, validate_contract
from caduceus.demo import (
    SLUG_REFACTOR,
    SLUG_V0,
    Sandbox,
    run_demo,
    run_spec,
    skill_from_text,
    slug_contract,
    spec_text,
)
from caduceus.digest import sha256_hex
from caduceus.protected import build_suite, retention_gate
from caduceus.refinery import Refinery, RefineryError
from caduceus.ledger import Ledger


def _cand(**over):
    base = dict(
        candidate_id="c1",
        kind="skill_refine",
        parent_digest=sha256_hex(b"p"),
        tenant_scope="t",
        paths=("skills/a/spec.json",),
        preregistration={
            "metric": "m",
            "threshold_micro": 0,
            "min_effect_micro": 0,
            "alpha_micro": 50000,
            "n": 8,
            "seeds": [1, 2],
            "arms": list(REQUIRED_ARMS),
            "holdout_commitment": sha256_hex(b"h"),
            "max_search_cost_units": 10,
        },
        compute_budget=10,
        spend_budget_micro_usd=0,
        rollback_plan_digest=sha256_hex(b"r"),
        nonclaims=(REQUIRED_NONCLAIM,),
        proposer="hermes",
    )
    base.update(over)
    return Candidate(**base)


def _all_checked():
    return {l: {"status": "Checked", "detail": ""} for l in REQUIRED_LANES}


class ContractTests(unittest.TestCase):
    def test_demo_contract_valid(self):
        self.assertEqual(validate_contract(slug_contract()), [])

    def test_leak_and_missing_kinds_detected(self):
        P = make_probe
        c = SkillContract(
            "x", (), (), "none", 100,
            (
                P("v1", "visible", "a", "exact", "a"),
                P("h1", "held_out", "a", "exact", "a"),  # leaks visible input
            ),
            "2027-01-01",
        )
        problems = validate_contract(c, ContractPolicy())
        self.assertTrue(any(p.startswith("held_out_leaks_into_visible") for p in problems))
        self.assertTrue(any(p.startswith("too_few_perturbation") for p in problems))

    def test_bad_regex_and_dates(self):
        P = make_probe
        c = SkillContract(
            "x", (), (), "bogus", 0,
            (P("v1", "visible", "a", "regex", "("),),
            "not-a-date",
        )
        problems = validate_contract(c, ContractPolicy(1, 0, 0, 0))
        for want in ("side_effect_class_invalid", "max_output_chars_out_of_range", "expiry_review_date_invalid", "regex_invalid:v1"):
            self.assertIn(want, problems)


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.contract = slug_contract()
        self.base = skill_from_text(spec_text(SLUG_V0))

    def test_exact_refactor_equivalent(self):
        cand = skill_from_text(spec_text(SLUG_REFACTOR))
        rep = controls.compare_behavior(self.base, cand, self.contract, "refactor")
        self.assertTrue(rep.ok, rep.reasons)
        self.assertEqual(rep.output_equal[0], rep.output_equal[1])

    def test_visible_overfit_caught_by_held_out(self):
        table = {"Hello World": "hello-world", "  A  B ": "a-b"}
        spec = [{"op": "table", "map": table}, {"op": "lower"}, {"op": "regex_sub", "pattern": "[^a-z0-9]", "repl": "-"}]
        rep = controls.compare_behavior(self.base, skill_from_text(spec_text(spec)), self.contract, "refactor")
        self.assertFalse(rep.ok)
        # visible probes all still pass: the artifact is exactly "visible equivalence"
        self.assertEqual(rep.cand_pass["visible"], (2, 2))
        self.assertLess(rep.cand_pass["held_out"][0], rep.cand_pass["held_out"][1])

    def test_sensitivity_difference_detected(self):
        # constant output matches 'unchanged' neighbours but not the changed one
        const = skill_from_text(spec_text([{"op": "const", "value": "hello-world"}]))
        rep = controls.compare_behavior(self.base, const, self.contract, "refactor")
        self.assertFalse(rep.ok)
        self.assertLess(rep.sensitivity_agreement[0], rep.sensitivity_agreement[1])

    def test_composition_and_indispensability(self):
        good = skill_from_text(spec_text(SLUG_V0))
        wrong = lambda s: "zzz"
        factory = lambda parts: parts["main"]
        comp = controls.composition_control(factory, {"main": good}, {"main": good}, {"main": wrong}, self.contract)
        self.assertTrue(comp.ok, comp.reasons)
        # non-discriminating probe bundle: a wrong source that still passes is reported
        permissive = SkillContract("p", (), (), "none", 10, (make_probe("v1", "visible", "a", "regex", ".*"),), "2027-01-01")
        comp2 = controls.composition_control(factory, {"main": good}, {"main": good}, {"main": wrong}, permissive)
        self.assertFalse(comp2.ok)
        ind = controls.indispensability(factory, {"main": good}, "main", {"zero": lambda s: ""}, self.contract)
        self.assertTrue(ind.ok, ind.reasons)
        # a probe bundle a zero skill passes does not credit the skill
        trivial = SkillContract("t", (), (), "none", 10, (make_probe("v1", "visible", "a", "not_contains", "Q"),), "2027-01-01")
        ind2 = controls.indispensability(factory, {"main": good}, "main", {"zero": lambda s: ""}, trivial)
        self.assertFalse(ind2.ok)


class AdmissionTests(unittest.TestCase):
    def test_all_checked_accepts_and_is_deterministic(self):
        c, p = _cand(), AdmissionPolicy()
        v1, v2 = evaluate(c, p, _all_checked()), evaluate(c, p, _all_checked())
        self.assertEqual(v1.decision, "accept")
        self.assertEqual(v1.digest(), v2.digest())
        self.assertFalse(v1.grants_authority)

    def test_any_failed_rejects_unready_quarantines(self):
        c, p = _cand(), AdmissionPolicy()
        ev = _all_checked()
        ev["retention"]["status"] = "Failed"
        self.assertEqual(evaluate(c, p, ev).decision, "reject")
        ev = _all_checked()
        ev["instrument_validity"]["status"] = "Inconclusive"
        v = evaluate(c, p, ev)
        self.assertEqual(v.decision, "quarantine")
        ev = _all_checked()
        del ev["holdout"]
        self.assertEqual(evaluate(c, p, ev).decision, "quarantine")

    def test_unknown_lane_and_status_rejected(self):
        c, p = _cand(), AdmissionPolicy()
        ev = _all_checked()
        ev["mystery"] = {"status": "Checked"}
        self.assertEqual(evaluate(c, p, ev).decision, "reject")
        ev = _all_checked()
        ev["retention"]["status"] = "Pass"
        self.assertEqual(evaluate(c, p, ev).decision, "reject")

    def test_invalid_candidates_rejected(self):
        p = AdmissionPolicy()
        for bad in (
            _cand(paths=("../etc/passwd",)),
            _cand(paths=("/abs",)),
            _cand(paths=("other/x",)),
            _cand(nonclaims=()),
            _cand(kind="nope"),
            _cand(parent_digest="zz"),
        ):
            v = evaluate(bad, p, _all_checked())
            self.assertEqual(v.decision, "reject", bad.paths)
        pr = dict(_cand().preregistration)
        pr["arms"] = ["candidate"]
        self.assertEqual(evaluate(_cand(preregistration=pr), p, _all_checked()).decision, "reject")

    def test_code_patch_needs_policy_and_independent_signature(self):
        cp = _cand(kind="code_patch", paths=("config/x.py",), required_signer_roles=("security",))
        self.assertEqual(evaluate(cp, AdmissionPolicy(), _all_checked()).decision, "reject")
        allow = AdmissionPolicy(allow_code_patch=True)
        self.assertEqual(evaluate(cp, allow, _all_checked()).decision, "accept")  # lanes decide signature
        self.assertEqual(lanes.lane_signature(cp, [{"signer": "hermes", "role": "security"}], "apollo")["status"], "Failed")
        self.assertEqual(lanes.lane_signature(cp, [{"signer": "apollo", "role": "security"}], "apollo")["status"], "Failed")
        self.assertEqual(lanes.lane_signature(cp, [], "apollo")["status"], "NotRun")
        self.assertEqual(lanes.lane_signature(cp, [{"signer": "alice", "role": "security"}], "apollo")["status"], "Checked")


class LaneTests(unittest.TestCase):
    def test_controls_power_and_effect(self):
        pr = _cand().preregistration
        self.assertEqual(lanes.lane_controls([1.0] * 3, [1.0] * 3, pr, 1)["status"], "Inconclusive")
        good = lanes.lane_controls([0.5] * 10, [0.6] * 10, {**pr, "n": 8, "min_effect_micro": 100_000}, 1)
        self.assertEqual(good["status"], "Checked")
        no_effect = lanes.lane_controls([0.0] * 10, [0.6] * 10, {**pr, "min_effect_micro": 100_000}, 1)
        self.assertEqual(no_effect["status"], "Failed")
        loses_random = lanes.lane_controls([0.5] * 10, [0.0] * 10, pr, 1)
        self.assertEqual(loses_random["status"], "Failed")

    def test_non_inferiority_and_search_cost(self):
        self.assertEqual(lanes.lane_non_inferiority({"a": 5}, {"a": 4})["status"], "Failed")
        self.assertEqual(lanes.lane_non_inferiority({"a": 5}, {"a": 5})["status"], "Checked")
        self.assertEqual(lanes.lane_search_cost(11, 10)["status"], "Failed")

    def test_instrument_disagreement_is_inconclusive(self):
        self.assertEqual(lanes.lane_instrument_validity(9, 10)["status"], "Checked")
        self.assertEqual(lanes.lane_instrument_validity(5, 10)["status"], "Inconclusive")
        self.assertEqual(lanes.lane_instrument_validity(0, 0)["status"], "NotRun")

    def test_independent_verification_statuses(self):
        self.assertEqual(lanes.lane_independent_verification({})["status"], "NotRun")
        self.assertEqual(lanes.lane_independent_verification({"a": "Checked", "b": "NotRun"})["status"], "Inconclusive")
        self.assertEqual(lanes.lane_independent_verification({"a": "Failed"})["status"], "Failed")

    def test_holdout_commitment_and_leak(self):
        contract = slug_contract()
        held = contract.probes_of("held_out")
        c = lanes.holdout_commitment(held)
        self.assertEqual(lanes.lane_holdout(c, held, [p.probe_id for p in contract.probes_of("visible")])["status"], "Checked")
        self.assertEqual(lanes.lane_holdout(c, held[:1], [])["status"], "Failed")
        self.assertEqual(lanes.lane_holdout(c, held, ["h1"])["status"], "Failed")

    def test_prediction_lock_order(self):
        with tempfile.TemporaryDirectory() as d:
            led = Ledger(os.path.join(d, "l.jsonl"))
            self.assertEqual(lanes.lane_prediction_lock(led, "c")["status"], "NotRun")
            led.append("assessment", {"candidate_id": "c"})
            led.append("prediction_lock", {"candidate_id": "c"})  # lock AFTER assessment
            self.assertEqual(lanes.lane_prediction_lock(led, "c")["status"], "Failed")
            led.append("prediction_lock", {"candidate_id": "ok"})
            led.append("assessment", {"candidate_id": "ok"})  # lock BEFORE assessment
            self.assertEqual(lanes.lane_prediction_lock(led, "ok")["status"], "Checked")


class RefineryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sb = Sandbox(os.path.join(self.tmp.name, "sb"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_refuses_real_hermes_home(self):
        led = Ledger(os.path.join(self.tmp.name, "x.jsonl"))
        with self.assertRaises(RefineryError):
            Refinery(os.path.expanduser("~/.hermes/skills"), os.path.join(self.tmp.name, "s"), led, lambda: 1)

    def test_root_and_store_must_be_disjoint(self):
        led = Ledger(os.path.join(self.tmp.name, "x.jsonl"))
        with self.assertRaises(RefineryError):
            Refinery(self.tmp.name, os.path.join(self.tmp.name, "inner"), led, lambda: 1)

    def test_capability_requires_accept_and_binding(self):
        from caduceus.demo import evaluate_scenario

        cand, v, w = evaluate_scenario(self.sb, "t1", "skill_refine", "slugify", spec_text(SLUG_REFACTOR), "refactor")
        self.assertEqual(v.decision, "accept")
        tok = self.sb.refinery.issue_capability(cand, v, 60)
        other = _cand(candidate_id="other", paths=tuple(w))
        with self.assertRaises(RefineryError):
            self.sb.refinery.apply(other, v, tok, w, lambda: [])
        with self.assertRaises(RefineryError):
            self.sb.refinery.apply(cand, v, "0" * 64, w, lambda: [])
        res = self.sb.refinery.apply(cand, v, tok, w, lambda: [])
        self.assertTrue(res.applied)
        with self.assertRaises(RefineryError):
            self.sb.refinery.apply(cand, v, tok, w, lambda: [])  # single use

    def test_capability_can_bind_a_tool_call_digest(self):
        from caduceus.demo import evaluate_scenario

        cand, v, w = evaluate_scenario(self.sb, "t3", "skill_refine", "slugify", spec_text(SLUG_REFACTOR), "refactor")
        with self.assertRaises(RefineryError):
            self.sb.refinery.issue_capability(cand, v, 60, args_digest="XYZ")
        self.sb.refinery.issue_capability(cand, v, 60, args_digest="ab" * 32)
        last = self.sb.refinery.ledger.find("capability_issued")[-1]["body"]
        self.assertEqual(last["args_digest"], "ab" * 32)

    def test_expired_capability_and_undeclared_path(self):
        from caduceus.demo import evaluate_scenario

        cand, v, w = evaluate_scenario(self.sb, "t2", "skill_refine", "slugify", spec_text(SLUG_REFACTOR), "refactor")
        tok = self.sb.refinery.issue_capability(cand, v, 1)
        self.sb.clock.t += 100
        with self.assertRaises(RefineryError):
            self.sb.refinery.apply(cand, v, tok, w, lambda: [])
        tok2 = self.sb.refinery.issue_capability(cand, v, 60)
        with self.assertRaises(RefineryError):
            self.sb.refinery.apply(cand, v, tok2, {"skills/other/spec.json": "{}"}, lambda: [])
        with self.assertRaises(RefineryError):
            self.sb.refinery.apply(cand, v, tok2, {"skills/slugify/../../../evil": "x"}, lambda: [])

    def test_rollback_restores_exact_snapshot(self):
        before = self.sb.refinery._files()
        sid = self.sb.refinery.snapshot()
        self.sb._write("skills/slugify/spec.json", "garbage")
        with open(os.path.join(self.sb.root, "skills", "extra.txt"), "w") as fh:
            fh.write("x")
        self.sb.refinery.restore(sid, "test")
        self.assertEqual(self.sb.refinery._files(), before)


class DemoTests(unittest.TestCase):
    def test_demo_outcomes(self):
        with tempfile.TemporaryDirectory() as d:
            res = run_demo(os.path.join(d, "out"))
        self.assertEqual(res["A-refactor"]["decision"], "accept")
        self.assertTrue(res["A-refactor"]["applied"])
        self.assertTrue(res["A-replay-token"].startswith("refused"))
        self.assertEqual(res["B-overfit"]["decision"], "reject")
        self.assertEqual(res["C-instruments"]["decision"], "quarantine")
        self.assertEqual(res["D-side-effect"]["decision"], "reject")
        self.assertEqual(res["E-stale"]["decision"], "accept")
        self.assertFalse(res["E-stale"]["applied"])
        self.assertTrue(res["E-stale"]["initials_still_correct"])
        self.assertEqual(res["F-self-signed"]["decision"], "reject")
        self.assertTrue(res["ledger"]["independent_readback_agrees"])


if __name__ == "__main__":
    unittest.main()

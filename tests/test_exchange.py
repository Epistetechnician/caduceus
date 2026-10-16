import json
import os
import tempfile
import time
import unittest

from caduceus import exchange, experiment
from caduceus.broker import apply_via_broker
from caduceus.contract import load_contracts_dir
from caduceus.lanes import holdout_commitment
from caduceus.ledger import Ledger
from caduceus.propose import default_preregistration, propose_skill
from caduceus.refinery import Refinery
from tests.test_experiment import CONTRACTS, PREREG

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = "codebase-inspection"
GOOD = "pygount --format=summary --format=json --suffix=py --folders-to-skip=.git,node_modules . comment"


def _arms(candidate_quality):
    """untouched_base only knows the Python LOC case; candidate per quality."""
    def factory(arm, cf):
        def run(prompt):
            neg = "words" in prompt or "lists the files" in prompt
            if arm == "deterministic_random" or neg:
                return "1"
            if arm == "untouched_base":
                return "pygount --folders-to-skip=.git ." if "Python code" in prompt else "wc -l"
            return GOOD if candidate_quality == "better" else "wc -l"
        return run
    return factory


class ExchangeTest(unittest.TestCase):
    def _setup(self, tmp, quality):
        contracts = load_contracts_dir(CONTRACTS)
        cf = contracts[SKILL]
        root = os.path.join(tmp, "gov")
        skill_dir = os.path.join(root, "skills", cf.skill_rel_dir)
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as fh:
            fh.write("---\nname: %s\ndescription: base\n---\nbase\n" % SKILL)
        prop = os.path.join(tmp, "prop", SKILL)
        os.makedirs(prop)
        with open(os.path.join(prop, "SKILL.md"), "w") as fh:
            fh.write("---\nname: %s\ndescription: refined\n---\nrefined %s\n" % (SKILL, quality))
        with open(PREREG) as fh:
            raw = json.load(fh)
        raw.update(experiment_id="cand-" + quality, arms=list(experiment.ARMS_CANDIDATE), reps=2,
                   skills=[{"skill": SKILL, "contract_digest": cf.contract.digest(), "tree_digest": cf.written_against_tree_digest}])
        ppath = os.path.join(tmp, "prereg.json")
        with open(ppath, "w") as fh:
            json.dump(raw, fh)
        pre = experiment.load_prereg(ppath)
        cpre = default_preregistration(holdout_commitment(cf.contract.probes_of("held_out")), 40, [1, 2])
        cpre.update(experiment_id=pre.experiment_id, experiment_prereg_digest=pre.digest(), max_search_cost_units=1000)
        cand, writes = propose_skill(os.path.join(root, "skills"), cf.skill_rel_dir, prop, "hermes", cpre)
        lpath = os.path.join(tmp, "exp.jsonl")
        trees = {SKILL: cf.written_against_tree_digest}
        experiment.lock(pre, Ledger(lpath), contracts, trees)
        experiment.run_paired(pre, Ledger(lpath), contracts, trees, _arms(quality))
        return cand, writes, cf, pre, lpath, root

    def test_better_candidate_quarantined_without_second_instrument_then_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand, writes, cf, pre, lpath, root = self._setup(tmp, "better")
            out = exchange.assemble(cand, cf, pre, lpath, REPO)
            self.assertEqual(out["verdict"].decision, "quarantine", out["evidence"])
            self.assertEqual(out["evidence"]["instrument_validity"]["status"], "NotRun")
            for lane in ("preregistration", "prediction_lock", "holdout", "controls", "retention",
                         "behavior_equivalence", "search_cost", "indispensability", "lyre_report"):
                self.assertEqual(out["evidence"][lane]["status"], "Checked", (lane, out["evidence"][lane]))
            ok = exchange.assemble(cand, cf, pre, lpath, REPO, instrument_report={"agree": 10, "total": 10})
            self.assertEqual(ok["verdict"].decision, "accept", ok["evidence"])
            store, gl = os.path.join(tmp, "store"), os.path.join(tmp, "gov.jsonl")
            exchange.record(Ledger(gl), cand, ok["verdict"], ok["lyre_report"])
            tok = Refinery(root, store, Ledger(gl), clock=lambda: int(time.time())).issue_capability(cand, ok["verdict"], 600)
            r = apply_via_broker({"root": root, "store": store, "ledger": gl, "control_dir": os.path.join(tmp, "ctl"),
                                  "candidate": cand.as_dict(), "verdict": ok["verdict"].as_dict(), "token": tok,
                                  "writes": writes, "protected": {}, "keyring": ""})
            self.assertTrue(r.get("applied"), r)
            with open(os.path.join(root, "skills", cf.skill_rel_dir, "SKILL.md")) as fh:
                self.assertIn("refined better", fh.read())

    def test_regressing_candidate_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand, _, cf, pre, lpath, _ = self._setup(tmp, "worse")
            out = exchange.assemble(cand, cf, pre, lpath, REPO, instrument_report={"agree": 10, "total": 10})
            self.assertEqual(out["verdict"].decision, "reject")
            self.assertEqual(out["evidence"]["retention"]["status"], "Failed")

    def test_evidence_from_another_experiment_cannot_be_attached(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand, _, cf, pre, lpath, _ = self._setup(tmp, "better")
            from dataclasses import replace

            other = replace(cand, preregistration=dict(cand.preregistration, experiment_prereg_digest="0" * 64))
            out = exchange.assemble(other, cf, pre, lpath, REPO, instrument_report={"agree": 10, "total": 10})
            self.assertEqual(out["evidence"]["preregistration"]["status"], "Failed")
            self.assertEqual(out["verdict"].decision, "reject")


class CustodyJudgeTest(unittest.TestCase):
    def test_custody_outside_repo_and_judge_agreement(self):
        from dataclasses import replace

        from caduceus import judge
        from caduceus.custody import Custody, CustodyError

        with self.assertRaises(CustodyError):
            Custody(os.path.join(REPO, "custody"))
        with tempfile.TemporaryDirectory() as t:
            c = Custody(os.path.join(t, "cust"))
            c.put("e", "v1", "candidate", 0, "pygount --folders-to-skip=.git .")
            c.put("e", "v1", "untouched_base", 0, "wc -l")
            c.put("e", "n1", "candidate", 0, "3")
            outs = c.load("e")
            cf = load_contracts_dir(CONTRACTS)[SKILL]
            probes = tuple(replace(p, rubric="Uses pygount and skips dependency folders.") if p.probe_id == "v1" else p
                           for p in cf.contract.probes)
            cf = replace(cf, contract=replace(cf.contract, probes=probes))
            honest = judge.grade(lambda prompt: "PASS" if "ANSWER:\npygount" in prompt else "FAIL", cf, outs)
            self.assertEqual((honest["agree"], honest["total"], honest["skipped_no_rubric"]), (2, 2, 1))
            contrarian = judge.grade(lambda prompt: "maybe", cf, outs)
            self.assertEqual((contrarian["agree"], contrarian["invalid_replies"]), (0, 2))
            for f in os.listdir(c.dir):
                if not f.endswith(".jsonl"):
                    with open(os.path.join(c.dir, f), "w") as fh:
                        fh.write("tampered")
                    break
            with self.assertRaises(CustodyError):
                c.load("e")


if __name__ == "__main__":
    unittest.main()

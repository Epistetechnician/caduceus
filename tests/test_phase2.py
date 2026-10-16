import os
import tempfile
import unittest

from caduceus import experiment, source_admission
from caduceus.admission import AdmissionPolicy, evaluate
from caduceus.contract import load_contracts_dir
from caduceus.corpus import run_corpus
from caduceus.digest import sha256_hex
from caduceus.ledger import Ledger
from caduceus.propose import ABSENT, default_preregistration, propose_skill
from tests.test_experiment import CONTRACTS, _prereg
from tests.test_gate import _all_checked
from tests.test_hermes import make_home


class CorpusTest(unittest.TestCase):
    def test_every_seeded_attack_refused_and_gate_deterministic(self):
        r = run_corpus()
        self.assertEqual(r["misses"], [])
        self.assertTrue(r["ok"] and r["gate_deterministic"])
        self.assertGreaterEqual(r["cases"], 40)


def _receipt(**over):
    r = {"tenant": "t", "window_start": 1, "window_end": 2, "consent_binding": "c1",
         "source_kind": "conversation", "quality_receipt_digest": sha256_hex(b"q"),
         "content_digest": sha256_hex(b"x"), "poisoning_marker": False}
    r.update(over)
    return r


class SourceAdmissionTest(unittest.TestCase):
    def test_decisions(self):
        with tempfile.TemporaryDirectory() as t:
            led = Ledger(os.path.join(t, "l.jsonl"))
            self.assertEqual(source_admission.admit(led, _receipt())["decision"], "accept")
            self.assertEqual(source_admission.admit(led, _receipt())["reasons"], ["duplicate"])
            self.assertEqual(source_admission.admit(led, _receipt(content_digest=sha256_hex(b"y")))["reasons"], ["conflict"])
            self.assertEqual(source_admission.admit(led, _receipt(window_start=5, window_end=6, content_digest=sha256_hex(b"z"),
                                                                  poisoning_marker=True))["decision"], "quarantine")
            self.assertEqual(source_admission.admit(led, _receipt(window_start=7, window_end=8, content_digest=sha256_hex(b"w"),
                                                                  quality_receipt_digest=""))["reasons"], ["quality_unknown"])
            for bad in (_receipt(source_kind="rumour"), _receipt(consent_binding=""), _receipt(window_end=0),
                        dict(_receipt(), raw="text")):
                self.assertEqual(source_admission.admit(led, bad)["decision"], "reject")
            self.assertNotIn("text", open(led.path).read())


class ProposeTest(unittest.TestCase):
    def test_wraps_refine_and_create_then_gate_decides(self):
        with tempfile.TemporaryDirectory() as t:
            make_home(t)
            root = os.path.join(t, "skills")
            prop = os.path.join(t, "proposal", "deploy")
            os.makedirs(prop)
            with open(os.path.join(prop, "SKILL.md"), "w") as fh:
                fh.write("---\nname: deploy\ndescription: better\n---\nBody v2\n")
            pre = default_preregistration(sha256_hex(b"h"), 40, [1, 2])
            cand, writes = propose_skill(root, "devops/deploy", prop, "self-evolution", pre)
            self.assertEqual(cand.kind, "skill_refine")
            self.assertEqual(list(writes), ["skills/devops/deploy/SKILL.md"])
            self.assertEqual(evaluate(cand, AdmissionPolicy(), _all_checked()).decision, "accept")
            new, _ = propose_skill(root, "devops/newskill", prop, "self-evolution", pre)
            self.assertEqual((new.kind, new.parent_digest), ("skill_create", ABSENT))


class _Arms:
    """Treatment answers like a pygount user; random arm gets the decoy's name."""

    def __init__(self):
        self.calls = []

    def __call__(self, arm, cf):
        self.calls.append((arm, cf.skill))

        def run(prompt):
            if arm == "with_skill" and "pygount" not in prompt and "words" not in prompt and "lists the files" not in prompt:
                return "pygount --format=summary --format=json --suffix=py --folders-to-skip=.git,node_modules . comment"
            return "1"
        return run


class RandomArmTest(unittest.TestCase):
    def test_three_arm_uses_deranged_decoys_and_needs_both_contrasts(self):
        with tempfile.TemporaryDirectory() as t:
            contracts = load_contracts_dir(CONTRACTS)
            pre = _prereg(t, arms=list(experiment.ARMS_RANDOM), experiment_id="three")
            trees = {s["skill"]: s["tree_digest"] for s in pre.raw["skills"]}
            led = Ledger(os.path.join(t, "l.jsonl"))
            experiment.lock(pre, led, contracts, trees)
            arms = _Arms()
            res = experiment.run_paired(pre, led, contracts, trees, arms)
            randoms = [(a, s) for a, s in arms.calls if a == "random_skill"]
            self.assertEqual(len(randoms), 4)
            withs = {s for a, s in arms.calls if a == "with_skill"}
            self.assertEqual({s for _, s in randoms}, withs)  # every skill used once as a decoy
            self.assertIn("vs_random_lower_micro", res)
            for s, counts in res["per_skill"].items():
                self.assertIn("random_pass", counts)

    def test_decoys_are_a_derangement(self):
        d = experiment._decoys(["a", "b", "c", "d"], seed=7)
        self.assertTrue(all(k != v for k, v in d.items()))
        self.assertEqual(sorted(d.values()), ["a", "b", "c", "d"])


if __name__ == "__main__":
    unittest.main()

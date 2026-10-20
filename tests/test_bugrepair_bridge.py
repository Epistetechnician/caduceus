import json
import os
import unittest

from caduceus import bugrepair_bridge as bb
from caduceus.admission import AdmissionPolicy
from caduceus.candidate import Candidate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "ledgers", "exp-012-bugrepair.jsonl")
RESULT = os.path.join(ROOT, "ledgers", "exp-012-bugrepair.result.json")
POLICY = AdmissionPolicy(allowed_kinds=AdmissionPolicy().allowed_kinds + ("adapter_update",), allowed_roots=("adapters",))
H = "ab" * 32


def cand(result, **over):
    lock = json.loads(open(LEDGER).readline())["body"]
    pr = {"metric": "repair_pass_rate_micro", "threshold_micro": 0, "min_effect_micro": 100000, "alpha_micro": 50000,
          "n": 86, "seeds": [1, 2, 3], "arms": ["untouched_base", "fixed_policy", "candidate", "deterministic_random"],
          "holdout_commitment": lock["split_commitments"]["assess"], "max_search_cost_units": 1,
          "bugrepair_experiment": result["experiment_id"], "bugrepair_prereg_digest": result["prereg_digest"]}
    pr.update(over)
    return Candidate(candidate_id="adapter:exp-012", kind="adapter_update", parent_digest=H, tenant_scope="t",
                     paths=("adapters/x.safetensors",), preregistration=pr, compute_budget=1, spend_budget_micro_usd=0,
                     rollback_plan_digest=H, nonclaims=("LocalDevelopmentEvidenceOnly",), proposer="hsi-lab", content_digest=H)


class BugrepairBridge(unittest.TestCase):
    def setUp(self):
        self.res = json.load(open(RESULT))

    def test_real_result_fills_lanes_and_quarantines(self):
        out = bb.assemble_bugrepair(cand(self.res), self.res, LEDGER, POLICY)
        e = out["evidence"]
        for lane in ("preregistration", "prediction_lock", "holdout", "controls", "indispensability", "retention"):
            self.assertEqual(e[lane]["status"], "Checked", (lane, e[lane]))
        self.assertEqual(e["independent_verification"]["status"], "NotRun")
        self.assertNotEqual(out["verdict"].decision, "admit")

    def test_tampered_verdict_fails_controls(self):
        bad = dict(self.res, verdict="Inconclusive")
        self.assertEqual(bb.assemble_bugrepair(cand(bad), bad, LEDGER, POLICY)["evidence"]["controls"]["status"], "Failed")

    def test_wrong_holdout_and_binding_fail(self):
        e = bb.assemble_bugrepair(cand(self.res, holdout_commitment="cd" * 32), self.res, LEDGER, POLICY)["evidence"]
        self.assertEqual(e["holdout"]["status"], "Failed")
        e = bb.assemble_bugrepair(cand(self.res, bugrepair_experiment="x"), self.res, LEDGER, POLICY)["evidence"]
        self.assertEqual(e["preregistration"]["status"], "Failed")


if __name__ == "__main__":
    unittest.main()

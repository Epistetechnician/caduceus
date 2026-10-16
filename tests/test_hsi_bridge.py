import json
import os
import tempfile
import unittest

from caduceus import hsi_bridge
from caduceus.admission import REQUIRED_LANES, AdmissionPolicy
from caduceus.candidate import Candidate
from caduceus.digest import domain_digest

H = "ab" * 32
POLICY = AdmissionPolicy(
    allowed_kinds=AdmissionPolicy().allowed_kinds + ("adapter_update",),
    allowed_roots=("adapters",),
)
FAKE_VALIDATOR = '''
import json, sys
r = json.load(open(sys.argv[1]))
if r.get("tampered"):
    print("FAIL receipt_digest"); sys.exit(1)
print("OK " + r["receipt_digest"])
'''
HSI_LAB = os.path.expanduser("~/Documents/GitHub/hsi-lab")


def receipt(digest, baseline, low, decision="PROMOTE", held=True, **kw):
    r = {
        "schema_version": "hsi-receipt-v1", "receipt_digest": digest, "packet_digest": H,
        "decision": decision, "guardrails_held": held, "seeds": [0],
        "budget_used": {"wall_seconds": 100.0},
        "comparisons": [{"treatment_arm": "d2_verified", "baseline_arm": baseline,
                         "paired_diff": low + 0.05, "ci_low": low, "ci_high": low + 0.1, "pairs": []}],
    }
    r.update(kw)
    return r


def candidate(digests, kind="adapter_update"):
    pr = {
        "metric": "hsi_paired_diff", "threshold_micro": 0, "min_effect_micro": 50000, "alpha_micro": 50000,
        "n": 2, "seeds": [1, 2], "arms": ["untouched_base", "fixed_policy", "candidate", "deterministic_random"],
        "holdout_commitment": H, "max_search_cost_units": 10000, "hsi_receipt_digests": sorted(digests),
    }
    return Candidate(candidate_id="adapter:x:1", kind=kind, parent_digest=H, tenant_scope="t",
                     paths=("adapters/x.safetensors",), preregistration=pr, compute_budget=1,
                     spend_budget_micro_usd=0, rollback_plan_digest=H,
                     nonclaims=("LocalDevelopmentEvidenceOnly",), proposer="hsi-lab", content_digest=H)


class Bridge(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.val = os.path.join(self.d, "v.py")
        open(self.val, "w").write(FAKE_VALIDATOR)

    def imp(self, name, r):
        p = os.path.join(self.d, name)
        json.dump(r, open(p, "w"))
        return hsi_bridge.import_receipt(p, p, self.val)

    def both(self, **kw):
        a = self.imp("a.json", receipt("11" * 32, "d2_replay", 0.2, **kw))
        b = self.imp("b.json", receipt("22" * 32, "d2_poolctl", 0.1, **kw))
        return [a, b]

    def test_import_is_float_free(self):
        e = self.both()[0]
        self.assertEqual(e["comparisons"][0]["lower_micro"], 200000)
        self.assertNotIn("float", json.dumps(e).lower())
        json.loads(json.dumps(e), parse_float=lambda s: self.fail("float in evidence"))

    def test_validator_refusal_raises(self):
        with self.assertRaises(hsi_bridge.ReceiptError):
            self.imp("t.json", receipt("33" * 32, "d2_replay", 0.2, tampered=True))

    def test_digest_mismatch_raises(self):
        p = os.path.join(self.d, "m.json")
        json.dump(receipt("44" * 32, "d2_replay", 0.2), open(p, "w"))
        bad = os.path.join(self.d, "bad.py")
        open(bad, "w").write('print("OK " + "55"*32)')
        with self.assertRaises(hsi_bridge.ReceiptError):
            hsi_bridge.import_receipt(p, p, bad)

    def test_hsi_evidence_alone_quarantines(self):
        ev = self.both()
        out = hsi_bridge.assemble_adapter(candidate([e["receipt_digest"] for e in ev]), ev, POLICY)
        lanes_ = out["evidence"]
        self.assertEqual(set(lanes_), set(REQUIRED_LANES))
        for k in ("preregistration", "controls", "indispensability", "retention", "independent_verification"):
            self.assertEqual(lanes_[k]["status"], "Checked", k)
        for k in ("prediction_lock", "holdout", "signature"):
            self.assertEqual(lanes_[k]["status"], "NotRun", k)
        self.assertNotEqual(out["verdict"].decision, "accept")

    def test_missing_pool_control_is_notrun(self):
        ev = self.both()[:1]
        out = hsi_bridge.assemble_adapter(candidate([ev[0]["receipt_digest"]]), ev, POLICY)
        self.assertEqual(out["evidence"]["controls"]["status"], "NotRun")
        self.assertEqual(out["evidence"]["indispensability"]["status"], "NotRun")

    def test_not_beating_control_fails(self):
        a = self.imp("a.json", receipt("11" * 32, "d2_replay", 0.2))
        b = self.imp("b.json", receipt("22" * 32, "d2_poolctl", -0.01, decision="NO_CANDIDATE"))
        out = hsi_bridge.assemble_adapter(candidate([a["receipt_digest"], b["receipt_digest"]]), [a, b], POLICY)
        self.assertEqual(out["evidence"]["controls"]["status"], "Failed")
        self.assertEqual(out["evidence"]["indispensability"]["status"], "Failed")
        self.assertEqual(out["verdict"].decision, "reject")

    def test_failed_guardrail_fails_retention(self):
        ev = self.both(held=False)
        out = hsi_bridge.assemble_adapter(candidate([e["receipt_digest"] for e in ev]), ev, POLICY)
        self.assertEqual(out["evidence"]["retention"]["status"], "Failed")

    def test_foreign_receipt_cannot_attach(self):
        ev = self.both()
        out = hsi_bridge.assemble_adapter(candidate(["99" * 32]), ev, POLICY)
        self.assertEqual(out["evidence"]["preregistration"]["status"], "Failed")
        self.assertIn("receipts_not_bound", out["evidence"]["preregistration"]["detail"])

    def test_default_policy_refuses_adapter_kind(self):
        ev = self.both()
        out = hsi_bridge.assemble_adapter(candidate([e["receipt_digest"] for e in ev]), ev)
        self.assertEqual(out["verdict"].decision, "reject")

    def test_wrong_kind_fails_preregistration(self):
        ev = self.both()
        out = hsi_bridge.assemble_adapter(candidate([e["receipt_digest"] for e in ev], kind="skill_refine"), ev, POLICY)
        self.assertEqual(out["evidence"]["preregistration"]["status"], "Failed")

    @unittest.skipUnless(os.path.isdir(HSI_LAB), "hsi-lab checkout not present")
    def test_real_hsi_receipts(self):
        val = os.path.join(HSI_LAB, "validator", "recompute.py")
        ex = os.path.join(HSI_LAB, "docs", "experiments")
        pk = os.path.join(HSI_LAB, "packets")
        pairs = [("E6-verified-receipt.json", "e6-v8.frozen.json"), ("E6-poolctl-receipt.json", "e6-v9.frozen.json")]
        ev = [hsi_bridge.import_receipt(os.path.join(ex, r), os.path.join(pk, p), val) for r, p in pairs]
        out = hsi_bridge.assemble_adapter(candidate([e["receipt_digest"] for e in ev]), ev, POLICY)
        self.assertEqual(out["evidence"]["controls"]["status"], "Checked")
        self.assertEqual(out["evidence"]["indispensability"]["status"], "Checked")
        self.assertNotEqual(out["verdict"].decision, "accept")


if __name__ == "__main__":
    unittest.main()

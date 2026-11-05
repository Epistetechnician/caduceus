import os
import tempfile
import unittest

from tests._seatbelt import needs_seatbelt

from caduceus.autoapply import eligibility
from caduceus.diagnostics import diagnostics
from caduceus.drill import run_drills
from caduceus.ledger import Ledger
from tests.test_hermes import make_home

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _verdict(led, eid, digest, verdict="Pass", random=True):
    body = {"experiment_id": eid, "prereg_digest": digest, "verdict": verdict, "design": "candidate"}
    if eid == "a":
        body.pop("design")
    if random:
        body["vs_random_lower_micro"] = 1
    led.append("verdict", body)


class AutoApplyTest(unittest.TestCase):
    def test_blocked_until_two_distinct_three_arm_passes(self):
        with tempfile.TemporaryDirectory() as t:
            led = Ledger(os.path.join(t, "l.jsonl"))
            _verdict(led, "a", "1" * 64, random=False)
            _verdict(led, "b", "2" * 64, verdict="Fail")
            _verdict(led, "c", "3" * 64)
            _verdict(led, "c-again", "3" * 64)  # same prereg: not a replication
            led.append("verdict", {"experiment_id": "effect", "prereg_digest": "5" * 64, "verdict": "Pass",
                                   "vs_random_lower_micro": 1, "design": "skill_effect"})
            r = eligibility([led])
            self.assertEqual(r["status"], "Blocked")
            self.assertEqual(r["excluded"], {"a": "design_unrecorded", "b": "not_pass:Fail",
                                             "effect": "not_a_candidate_experiment"})
            _verdict(led, "d", "4" * 64)
            self.assertEqual(eligibility([led])["status"], "Eligible")


class DiagnosticsAndDrillTest(unittest.TestCase):
    def test_lane_vector_never_passes_unrun_lanes(self):
        d = diagnostics(REPO, [], None)
        statuses = {k: v["status"] for k, v in d["lanes"].items()}
        self.assertEqual(statuses["patch_safety_adversarial_corpus"], "Checked")
        for lane in ("semantic_oracle_bend", "formal_contract_lean", "temporal_stress"):
            self.assertEqual(statuses[lane], "Unavailable")
        self.assertEqual(statuses["ledger_integrity"], "NotRun")
        self.assertEqual(d["auto_apply"]["status"], "Blocked")

    @needs_seatbelt
    def test_drills_pass_on_a_sandbox_skill(self):
        with tempfile.TemporaryDirectory() as t:
            make_home(t)
            r = run_drills(os.path.join(t, "skills"), "devops/deploy")
            self.assertTrue(r["ok"], r)


if __name__ == "__main__":
    unittest.main()


class AppendFastTest(unittest.TestCase):
    def test_chain_stays_valid_and_corruption_detected(self):
        from caduceus import readback
        from caduceus.ledger import LedgerError, append_fast

        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "l.jsonl")
            for i in range(5):
                append_fast(p, "audit_note", {"event": "x", "i": i})
            Ledger(p).append("audit_note", {"event": "y"})
            append_fast(p, "audit_note", {"event": "z", "pad": ["q" * 2000] * 3})  # last line > 4 KiB chunk
            append_fast(p, "audit_note", {"event": "after_long"})
            self.assertEqual(readback.verify_file(p)["size"], 8)
            with open(p, "ab") as fh:
                fh.write(b'{"bogus": 1}\n')
            with self.assertRaises(LedgerError):
                append_fast(p, "audit_note", {"event": "w"})


class LineageTest(unittest.TestCase):
    def test_tenant_shared_topology(self):
        from caduceus import lineage

        d = lambda c: c * 64
        with tempfile.TemporaryDirectory() as t:
            led = Ledger(os.path.join(t, "l.jsonl"))
            lineage.record_promotion(led, "s", "tenant", "acme", d("a"), d("b"), d("1"))
            with self.assertRaises(lineage.LineageError):  # stale parent
                lineage.record_promotion(led, "s", "tenant", "acme", d("a"), d("c"), d("2"))
            lineage.record_promotion(led, "s", "tenant", "acme", d("b"), d("c"), d("2"))
            with self.assertRaises(lineage.LineageError):  # no reviewer
                lineage.promote_to_shared(led, "s", "acme", d("a"), [], "hermes")
            with self.assertRaises(lineage.LineageError):  # proposer cannot review
                lineage.promote_to_shared(led, "s", "acme", d("a"), [{"signer": "hermes", "role": "shared-reviewer"}], "hermes")
            lineage.promote_to_shared(led, "s", "acme", d("a"), [{"signer": "alice", "role": "shared-reviewer"}], "hermes")
            self.assertEqual(lineage.head(led, "s", "shared"), d("c"))
            self.assertEqual(lineage.ancestry(led, "s", d("c")), [d("c"), d("b"), d("a")])
            lineage.rollback(led, "s", "shared")
            self.assertEqual(lineage.head(led, "s", "shared"), d("a"))
            self.assertEqual(lineage.head(led, "s", "tenant", "acme"), d("c"))  # tenant untouched
            self.assertIsNone(lineage.head(led, "s", "tenant", "other"))

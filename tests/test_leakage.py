import hashlib
import os
import tempfile
import unittest

from caduceus import leakage
from caduceus.ledger import Ledger, LedgerError

C = hashlib.sha256(b"holdout").hexdigest()
V = hashlib.sha256(b"verdict").hexdigest()


class LeakageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "l.jsonl")
        self.led = Ledger(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_account_sums_bits_per_holdout(self):
        leakage.release(self.led, C, 1, V, 20000)
        leakage.release(self.led, C, 1, V, 20000)
        leakage.release(self.led, hashlib.sha256(b"other").hexdigest(), 5, V, 100)
        self.assertEqual(leakage.account(self.led, C), {"bits": 2, "queries": 2, "n": 20000})

    def test_inputs_are_validated(self):
        for bad in ((True, 20000), (-1, 20000), (1, 0), (1.5, 100), (10**9, 100)):
            with self.assertRaises(leakage.LeakageError):
                leakage.release(self.led, C, bad[0], V, bad[1])
        with self.assertRaises(leakage.LeakageError):
            leakage.release(self.led, "xyz", 1, V, 100)
        leakage.release(self.led, C, 1, V, 100)
        with self.assertRaises(leakage.LeakageError):
            leakage.release(self.led, C, 1, V, 200)  # size cannot change mid-account

    def test_radius_matches_hand_calculation_and_grows_with_bits(self):
        # n=20000, delta=5%, 100 bits, one route: sqrt(2*(100 ln2 + ln 20)/20000) ~ 0.0861
        r = leakage.radius_ppm(100, 20000, 50000, 1)
        self.assertTrue(85000 <= r <= 86500, r)
        self.assertLess(leakage.radius_ppm(10, 20000, 50000), leakage.radius_ppm(1024, 20000, 50000))
        self.assertLess(leakage.radius_ppm(100, 80000, 50000), r)

    def test_certify_charges_the_cheaper_route_and_never_beats_naive(self):
        info = leakage.certify(229000, 20000, 50000, 1024, None)
        desc = leakage.certify(229000, 20000, 50000, 1024, 100)
        self.assertEqual(desc["route"], "description")
        self.assertGreater(desc["lower_micro"], info["lower_micro"])
        for c in (info, desc):
            self.assertLessEqual(c["lower_micro"], c["naive_lower_micro"])

    def test_budget_lane(self):
        self.assertIsNone(leakage.budget_lane(self.led, C, None))
        leakage.release(self.led, C, 3, V, 100)
        self.assertEqual(leakage.budget_lane(self.led, C, 3)["status"], "Checked")
        leakage.release(self.led, C, 1, V, 100)
        self.assertEqual(leakage.budget_lane(self.led, C, 3)["status"], "Failed")

    def test_editing_the_account_down_breaks_the_chain(self):
        for _ in range(3):
            leakage.release(self.led, C, 1, V, 100)
        Ledger(self.path)
        lines = open(self.path).read().splitlines()
        open(self.path, "w").write("\n".join(lines[:1] + lines[2:]) + "\n")  # drop the middle release
        with self.assertRaises(LedgerError):
            Ledger(self.path)


HOLDOUT_LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "holdout-ledger")


@unittest.skipUnless(os.path.isdir(os.path.join(HOLDOUT_LEDGER, "hl")), "sibling holdout-ledger checkout not present")
class CrossCheckAgainstHoldoutLedger(unittest.TestCase):
    """The radius here must equal the sibling repo's Hoeffding radius (which has its own preregistered experiments), up to our conservative round-up to a ppm."""

    def test_radius_matches_hl_bounds(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("hl_bounds", os.path.join(HOLDOUT_LEDGER, "hl", "bounds.py"))
        hb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hb)
        for bits in (0, 1, 3, 100, 1024):
            for n in (500, 20000, 100000):
                for delta_ppm in (10000, 50000, 100000):
                    for routes in (1, 2, 4):
                        theirs = hb.radius(bits, n, delta_ppm / 1e6, routes)
                        ours = leakage.radius_ppm(bits, n, delta_ppm, routes)
                        self.assertGreaterEqual(ours, int(theirs * 1e6), (bits, n, delta_ppm, routes))
                        self.assertLessEqual(ours - theirs * 1e6, 1.0000001, (bits, n, delta_ppm, routes))  # rounded up by under one ppm


if __name__ == "__main__":
    unittest.main()

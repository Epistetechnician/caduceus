import hashlib
import json
import os
import tempfile
import time
import unittest

from tests._seatbelt import needs_confinement, needs_seatbelt

from caduceus import control, signing
from caduceus.admission import AdmissionPolicy, evaluate
from caduceus.broker import apply_via_broker
from caduceus.candidate import CandidateParseError, candidate_from_dict, parse_json_strict
from caduceus.ledger import Ledger
from caduceus.refinery import Refinery
from caduceus.release import Breaker, Budget, CanaryPlan, Release, ReleaseError, ReleaseManager
from tests.test_gate import _all_checked, _cand
from tests.test_hermes import _load_plugin


class _Fixture:
    def __init__(self, cand=None, policy=None):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.root, self.store, self.ctl = (os.path.join(t, d) for d in ("root", "store", "ctl"))
        os.makedirs(os.path.join(self.root, "skills", "a"))
        os.makedirs(os.path.join(self.root, "skills", "keep"))
        with open(os.path.join(self.root, "skills", "keep", "spec.json"), "w") as fh:
            fh.write("protected")
        self.ledger_path = os.path.join(t, "ledger.jsonl")
        from caduceus.candidate import writes_digest

        self.cand = cand or _cand(content_digest=writes_digest({"skills/a/spec.json": "new"}))
        self.verdict = evaluate(self.cand, policy or AdmissionPolicy(), _all_checked())
        self.ref = Refinery(self.root, self.store, Ledger(self.ledger_path), clock=lambda: int(time.time()))
        self.token = self.ref.issue_capability(self.cand, self.verdict, 600)
        self.protected = {"skills/keep/spec.json": hashlib.sha256(b"protected").hexdigest()}

    @property
    def ledger(self):
        return Ledger(self.ledger_path)  # fresh handle: the broker appends from another process

    def request(self, **over):
        req = {
            "root": self.root, "store": self.store, "ledger": self.ledger_path, "control_dir": self.ctl,
            "candidate": self.cand.as_dict(), "verdict": self.verdict.as_dict(), "token": self.token,
            "writes": {"skills/a/spec.json": "new"}, "protected": dict(self.protected), "keyring": "",
        }
        req.update(over)
        return req


@needs_confinement
class BrokerTest(unittest.TestCase):
    def setUp(self):
        self.f = _Fixture()
        self.addCleanup(self.f.tmp.cleanup)

    def test_applies_once_then_replay_refused(self):
        r = apply_via_broker(self.f.request())
        self.assertTrue(r["ok"] and r["applied"], r)
        with open(os.path.join(self.f.root, "skills", "a", "spec.json")) as fh:
            self.assertEqual(fh.read(), "new")
        again = apply_via_broker(self.f.request())
        self.assertFalse(again["ok"])
        self.assertIn("consumed", again["error"])

    def test_path_escape_refused(self):
        for bad in ("../x", "skills/../../x", "/etc/passwd", "skills/b/spec.json"):
            r = apply_via_broker(self.f.request(writes={bad: "x"}))
            self.assertFalse(r["ok"], bad)

    def test_forged_token_and_forged_verdict_refused(self):
        self.assertFalse(apply_via_broker(self.f.request(token="0" * 64))["ok"])
        forged = dict(self.f.verdict.as_dict(), reason_codes=["forged"])
        r = apply_via_broker(self.f.request(verdict=forged))
        self.assertFalse(r["ok"])
        self.assertIn("not issued for this verdict", r["error"])

    def test_authority_cannot_be_granted_by_request(self):
        r = apply_via_broker(self.f.request(verdict=dict(self.f.verdict.as_dict(), grants_authority=True)))
        self.assertFalse(r["ok"])

    def test_malformed_requests_refused(self):
        for req in (dict(self.f.request(), extra=1), self.f.request(writes={}),
                    self.f.request(writes={"skills/a/spec.json": 5}),
                    self.f.request(protected={"skills/a/spec.json": "x"}, writes={"skills/a/spec.json": "y"}),
                    self.f.request(writes={"skills/a/spec.json": "x" * (1024 * 1024 + 1)})):
            self.assertFalse(apply_via_broker(req)["ok"])

    def test_content_swap_and_unbound_candidate_refused(self):
        r = apply_via_broker(self.f.request(writes={"skills/a/spec.json": "something else"}))
        self.assertIn("content_digest", r["error"])
        unbound = _Fixture()
        self.addCleanup(unbound.tmp.cleanup)
        unbound.cand = _cand()
        unbound.verdict = evaluate(unbound.cand, AdmissionPolicy(), _all_checked())
        unbound.token = unbound.ref.issue_capability(unbound.cand, unbound.verdict, 600)
        self.assertIn("does not bind", apply_via_broker(unbound.request())["error"])

    def test_frozen_refuses(self):
        control.freeze(self.f.ctl, self.f.ledger, "op", "drill")
        r = apply_via_broker(self.f.request())
        self.assertFalse(r["ok"])
        self.assertIn("frozen", r["error"])

    def test_retention_drift_rolls_back(self):
        req = self.f.request(protected={"skills/keep/spec.json": "0" * 64})
        r = apply_via_broker(req)
        self.assertFalse(r["ok"])  # already differs before apply: refused, nothing written
        self.assertFalse(os.path.exists(os.path.join(self.f.root, "skills", "a", "spec.json")))

    def test_restore_only_to_a_released_pre_snapshot(self):
        base = {"op": "restore", "root": self.f.root, "store": self.f.store, "ledger": self.f.ledger_path,
                "reason": "x"}
        self.assertFalse(apply_via_broker(dict(base, snapshot_id="0" * 64))["ok"])
        r = apply_via_broker(self.f.request())
        back = apply_via_broker(dict(base, snapshot_id=r["snapshot_id"]))
        self.assertTrue(back["ok"], back)
        self.assertFalse(os.path.exists(os.path.join(self.f.root, "skills", "a", "spec.json")))
        self.assertFalse(apply_via_broker(dict(base, snapshot_id=r["snapshot_id"], extra=1))["ok"])

    def test_symlink_in_tree_refused(self):
        os.symlink("/etc", os.path.join(self.f.root, "skills", "a", "link"))
        self.assertFalse(apply_via_broker(self.f.request())["ok"])


@needs_confinement
class CodePatchBrokerTest(unittest.TestCase):
    def setUp(self):
        from caduceus.candidate import writes_digest

        cand = _cand(kind="code_patch", paths=("skills/a/spec.json",), required_signer_roles=("reviewer",),
                     content_digest=writes_digest({"skills/a/spec.json": "new"}))
        self.f = _Fixture(cand, AdmissionPolicy(allow_code_patch=True))
        self.addCleanup(self.f.tmp.cleanup)
        self.ring = os.path.join(self.f.tmp.name, "keyring.json")
        signing.add_signer(self.ring, "alice", "reviewer")
        signing.add_signer(self.ring, "hermes", "reviewer")

    def test_needs_self_patch_on_and_independent_signature(self):
        self.assertIn("self_patch", apply_via_broker(self.f.request(keyring=self.ring))["error"])
        control.set_subsystem(self.f.ctl, self.f.ledger, "self_patch", True, "op")
        self.assertIn("signature", apply_via_broker(self.f.request(keyring=self.ring))["error"])
        signing.sign(self.f.ledger, self.ring, "hermes", self.f.cand.digest())  # the proposer
        self.assertIn("self-signing", apply_via_broker(self.f.request(keyring=self.ring))["error"])

    def test_independent_signature_applies(self):
        control.set_subsystem(self.f.ctl, self.f.ledger, "self_patch", True, "op")
        signing.sign(self.f.ledger, self.ring, "alice", self.f.cand.digest())
        r = apply_via_broker(self.f.request(keyring=self.ring))
        self.assertTrue(r.get("applied"), r)


class SigningTest(unittest.TestCase):
    def test_tampered_mac_and_loose_permissions_detected(self):
        with tempfile.TemporaryDirectory() as t:
            ring = os.path.join(t, "k.json")
            signing.add_signer(ring, "alice", "reviewer")
            led = Ledger(os.path.join(t, "l.jsonl"))
            d = hashlib.sha256(b"c").hexdigest()
            signing.sign(led, ring, "alice", d)
            self.assertEqual(signing.verified_signatures(led, ring, d), [{"signer": "alice", "role": "reviewer"}])
            led.append("review_signature", {"candidate_digest": d, "signer": "alice", "role": "reviewer", "mac": "00" * 32})
            with self.assertRaises(signing.SigningError):
                signing.verified_signatures(led, ring, d)
            os.chmod(ring, 0o644)
            with self.assertRaises(signing.SigningError):
                signing.load_keyring(ring)


class _Clock:
    def __init__(self):
        self.now = 1000

    def __call__(self):
        return self.now


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = _Clock()
        self.ledger = Ledger(os.path.join(self.tmp.name, "l.jsonl"))
        self.mgr = ReleaseManager(self.ledger, self.clock, Budget(capacity=10, period_s=3600),
                                  {"canary": Breaker("canary", ttl_s=300, relax_after=2)}, max_evidence_age_s=600)

    def _rel(self, rid="r1", units=4, evidence_at=None):
        plan = CanaryPlan(window=3, abort_if=(("errors", 0), ("p99_us", 50000)))
        return self.mgr.enqueue(Release(rid, "c" * 64, "p" * 64, "q" * 64,
                                        self.clock.now if evidence_at is None else evidence_at, plan, units))

    def test_happy_path_promotes_after_full_window(self):
        self._rel()
        self.assertEqual(self.mgr.revalidate("r1", "p" * 64, "q" * 64), "revalidated")
        self.mgr.start_canary("r1")
        for _ in range(2):
            self.mgr.observe("r1", {"errors": 0, "p99_us": 40000})
        with self.assertRaises(ReleaseError):
            self.mgr.promote("r1")
        self.mgr.observe("r1", {"errors": 0, "p99_us": 40000})
        self.mgr.promote("r1")
        self.assertEqual(self.mgr.releases["r1"].state, "promoted")
        with self.assertRaises(ReleaseError):
            self.mgr.cancel("r1", "late")

    def test_revalidation_rejects_drift_and_stale_evidence(self):
        self._rel("a")
        self.assertEqual(self.mgr.revalidate("a", "x" * 64, "q" * 64), "rejected")
        self._rel("b", evidence_at=0)
        self.assertEqual(self.mgr.revalidate("b", "p" * 64, "q" * 64), "rejected")
        self.assertEqual(self.mgr.budget.used, 0)  # both reservations refunded

    def test_canary_abort_rolls_back_and_breaker_blocks_until_ttl_and_hysteresis(self):
        self._rel()
        self.mgr.revalidate("r1", "p" * 64, "q" * 64)
        self.mgr.start_canary("r1")
        self.assertEqual(self.mgr.observe("r1", {"errors": 1, "p99_us": 1}), "rolled_back")
        self._rel("r2")
        self.assertEqual(self.mgr.revalidate("r2", "p" * 64, "q" * 64), "rejected")
        self.clock.now += 1000  # TTL passed, but no silent re-arm
        self.assertTrue(self.mgr.breakers["canary"].blocks(self.clock.now))
        with self.assertRaises(ReleaseError):
            self.mgr.reset("canary", "op")  # hysteresis: needs clean windows
        self.mgr.clean_window("canary")
        self.mgr.clean_window("canary")
        self.mgr.reset("canary", "op")
        self.assertFalse(self.mgr.breakers["canary"].blocks(self.clock.now))

    def test_missing_metric_aborts(self):
        self._rel()
        self.mgr.revalidate("r1", "p" * 64, "q" * 64)
        self.mgr.start_canary("r1")
        self.assertEqual(self.mgr.observe("r1", {"errors": 0}), "rolled_back")

    def test_budget_exactly_once_and_slow_drain(self):
        self._rel("a", units=6)
        with self.assertRaises(ReleaseError):
            self._rel("a", units=1)  # duplicate id
        with self.assertRaises(ReleaseError):
            self._rel("b", units=5)  # would exceed capacity
        self._rel("c", units=4)
        with self.assertRaises(ReleaseError):
            self._rel("d", units=1)  # slow drain stopped at capacity

    def test_cancel_vs_promote_race_fails_closed(self):
        self._rel()
        self.mgr.revalidate("r1", "p" * 64, "q" * 64)
        self.mgr.start_canary("r1")
        for _ in range(3):
            self.mgr.observe("r1", {"errors": 0, "p99_us": 1})
        self.mgr.cancel("r1", "operator")
        with self.assertRaises(ReleaseError):
            self.mgr.promote("r1")
        self.assertEqual(self.mgr.budget.used, 0)


class ControlTest(unittest.TestCase):
    def test_malformed_control_fails_closed_and_plugin_honours_it(self):
        plugin = _load_plugin()
        with tempfile.TemporaryDirectory() as base:
            led = Ledger(os.path.join(base, "ledger.jsonl"))
            args = {"operations": [{"action": "patch", "name": "x"}]}
            with open(os.path.join(base, "mode.json"), "w") as fh:
                json.dump({"mode": "enforce"}, fh)
            control.freeze(base, led, "op", "test")
            d = plugin.decide("skill_manage", args, base)
            self.assertEqual(d["action"], "block")
            self.assertIn("frozen", d["message"])
            control.set_subsystem(base, Ledger(os.path.join(base, "ledger.jsonl")), "admission", False, "op")
            n = len(Ledger(os.path.join(base, "ledger.jsonl")))
            self.assertIsNone(plugin.decide("skill_manage", args, base))
            self.assertEqual(len(Ledger(os.path.join(base, "ledger.jsonl"))), n)  # nothing recorded
            with open(os.path.join(base, control.FILENAME), "w") as fh:
                fh.write("{not json")
            c = control.load(base)
            self.assertTrue(c.frozen and c.enabled("admission") and not c.valid)

    def test_rate_limit(self):
        with tempfile.TemporaryDirectory() as t:
            led = Ledger(os.path.join(t, "l.jsonl"))
            for i in range(3):
                led.append("candidate", {"candidate_digest": "%064d" % i, "at": 100 + i})
            self.assertFalse(control.candidate_rate_ok(led, 110, 60, 3))
            self.assertTrue(control.candidate_rate_ok(led, 1000, 60, 3))


class IntakeTest(unittest.TestCase):
    def test_duplicate_keys_floats_and_unknown_fields_rejected(self):
        good = _cand().as_dict()
        self.assertEqual(candidate_from_dict(parse_json_strict(json.dumps(good))).digest(), _cand().digest())
        for text in ('{"a": 1, "a": 2}', '{"a": 1.5}', '{"a": NaN}'):
            with self.assertRaises(CandidateParseError):
                parse_json_strict(text)
        with self.assertRaises(CandidateParseError):
            candidate_from_dict(dict(good, extra=1))
        with self.assertRaises(CandidateParseError):
            candidate_from_dict(dict(good, compute_budget=True))


if __name__ == "__main__":
    unittest.main()


@needs_seatbelt
class BrokerSandboxTest(unittest.TestCase):
    def test_profile_blocks_outside_writes_and_network(self):
        import subprocess
        import sys

        from caduceus.broker import SANDBOX_EXEC, sandbox_profile

        with tempfile.TemporaryDirectory() as t, tempfile.TemporaryDirectory() as outside:
            root, store = os.path.join(t, "root"), os.path.join(t, "store")
            os.makedirs(root)
            os.makedirs(store)
            prof = sandbox_profile({"root": root, "store": store, "ledger": os.path.join(t, "l.jsonl")})

            def run(code):
                return subprocess.run([SANDBOX_EXEC, "-p", prof, sys.executable, "-I", "-c", code],
                                      capture_output=True, env={"PYTHONDONTWRITEBYTECODE": "1"}).returncode

            self.assertEqual(run("open(%r,'w').write('ok')" % os.path.join(root, "a")), 0)
            self.assertNotEqual(run("open(%r,'w').write('x')" % os.path.join(outside, "a")), 0)
            self.assertFalse(os.path.exists(os.path.join(outside, "a")))
            self.assertNotEqual(run("import socket; socket.create_connection(('127.0.0.1', 9), 2)"), 0)
            net = run("import socket,sys\ntry:\n socket.create_connection(('127.0.0.1', 9), 2)\nexcept PermissionError: sys.exit(7)\nexcept OSError: sys.exit(8)")
            self.assertEqual(net, 7)  # denied by the sandbox, not merely refused

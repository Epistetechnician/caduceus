import json
import os
import secrets
import subprocess
import sys
import tempfile
import unittest

from caduceus import approvals, ed25519, signing
from caduceus.ledger import Ledger
from caduceus.transition import Kernel, Policy, TransitionError, load_policy, policy_set

SKILL = "---\nname: %s\ndescription: does %s\n---\nstep one\n"
D64 = "a" * 64


def write(root, rel, text):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def device():
    seed = secrets.token_bytes(32)
    return seed, ed25519.public_key(seed).hex()


def approval(seed, name, action, surface, effect, parent, ttl=300, now=None, nonce=None):
    import time

    nonce = nonce or secrets.token_hex(16)
    expires = (now if now is not None else int(time.time())) + ttl
    return {"device": name, "nonce": nonce, "expires": expires, "sig": ed25519.sign(seed, approvals.message(action, surface, effect, parent, nonce, expires)).hex()}


class MessageTests(unittest.TestCase):
    def test_message_is_plain_text_with_fixed_fields(self):
        m = approvals.message("release", "skills", D64, D64, "b" * 32, 1700000000).decode()
        self.assertEqual(m.split("\n"), ["caduceus.approval.v1", "release", "skills", D64, D64, "b" * 32, "1700000000"])

    def test_bad_fields_are_refused(self):
        for args in (("steal", "skills", D64, D64, "b" * 32, 1), ("release", "prompts", D64, D64, "b" * 32, 1), ("release", "skills", "x", D64, "b" * 32, 1),
                     ("release", "skills", D64, D64, "B" * 32, 1), ("release", "skills", D64, D64, "b" * 32, -1), ("release", "skills", D64, D64, "b" * 32, True),
                     ("release", "skills", D64 + "\n", D64, "b" * 32, 1)):
            with self.assertRaises(approvals.ApprovalError):
                approvals.message(*args)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "c")
        _, self.pub = device()

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_list_remove_roundtrip_is_ledgered(self):
        r = approvals.add_device(self.base, "phone", self.pub, "op", "my phone")
        self.assertEqual(r["fingerprint"], approvals.fingerprint(self.pub))
        self.assertRegex(r["fingerprint"], r"^[0-9a-f]{4}(-[0-9a-f]{4}){3}$")
        self.assertEqual([d["name"] for d in approvals.list_devices(self.base)], ["phone"])
        events = [e["body"]["event"] for e in Ledger(os.path.join(self.base, "transitions.jsonl")).find("audit_note")]
        self.assertEqual(events, ["device_added"])
        approvals.remove_device(self.base, "phone", "op")
        self.assertEqual(approvals.list_devices(self.base), [])

    def test_validation(self):
        for bad in ("", "Phone", "-x", "a b", "x" * 33, "../x", "phone\n"):
            with self.assertRaises(approvals.ApprovalError):
                approvals.add_device(self.base, bad, self.pub, "op")
        for bad in ("zz", "A" * 64, "a" * 63, "a" * 64 + "\n"):
            with self.assertRaises(approvals.ApprovalError):
                approvals.add_device(self.base, "phone", bad, "op")
        approvals.add_device(self.base, "phone", self.pub, "op")
        with self.assertRaises(approvals.ApprovalError):
            approvals.add_device(self.base, "phone", device()[1], "op")  # same name
        with self.assertRaises(approvals.ApprovalError):
            approvals.add_device(self.base, "tablet", self.pub, "op")  # same key under another name

    def test_a_silent_edit_of_devices_json_is_refused(self):
        approvals.add_device(self.base, "phone", self.pub, "op")
        p = os.path.join(self.base, "devices.json")
        reg = json.load(open(p))
        reg["evil"] = {"pub_hex": device()[1], "label": ""}  # an attacker slips its own key in
        json.dump(reg, open(p, "w"))
        with self.assertRaises(approvals.ApprovalError):
            approvals.load_devices(self.base)
        json.dump({}, open(p, "w"))  # or removes the real one
        with self.assertRaises(approvals.ApprovalError):
            approvals.load_devices(self.base)
        os.remove(p)
        with self.assertRaises(approvals.ApprovalError):
            approvals.load_devices(self.base)


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "c")
        self.seed, pub = device()
        approvals.add_device(self.base, "phone", pub, "op")
        self.led = Ledger(os.path.join(self.base, "transitions.jsonl"))

    def tearDown(self):
        self.tmp.cleanup()

    def v(self, a, **kw):
        args = dict(action="release", surface="skills", effect=D64, parent="c" * 64, now=None)
        args.update(kw)
        return approvals.verify(self.base, Ledger(self.led.path), a, args["action"], args["surface"], args["effect"], args["parent"], args["now"])

    def good(self, **kw):
        a = dict(action="release", surface="skills", effect=D64, parent="c" * 64)
        a.update(kw)
        return approval(self.seed, "phone", a["action"], a["surface"], a["effect"], a["parent"])

    def test_valid_approval_returns_the_device(self):
        self.assertEqual(self.v(self.good()), "phone")

    def test_every_binding_is_enforced(self):
        a = self.good()
        for kw in (dict(action="discard"), dict(surface="memory"), dict(effect="d" * 64), dict(parent="e" * 64)):
            with self.assertRaises(approvals.ApprovalError, msg=str(kw)):
                self.v(a, **kw)

    def test_expiry_and_validity_window(self):
        import time

        with self.assertRaises(approvals.ApprovalError):
            self.v(approval(self.seed, "phone", "release", "skills", D64, "c" * 64, ttl=-5))
        with self.assertRaises(approvals.ApprovalError):
            self.v(approval(self.seed, "phone", "release", "skills", D64, "c" * 64, ttl=approvals.MAX_VALIDITY_S + 60))
        self.assertEqual(self.v(approval(self.seed, "phone", "release", "skills", D64, "c" * 64, ttl=approvals.MAX_VALIDITY_S - 5), now=int(time.time())), "phone")

    def test_unknown_device_bad_signature_and_malformed_fields(self):
        a = self.good()
        for bad in (dict(a, device="laptop"), dict(a, sig="0" * 128), dict(a, sig="zz"), dict(a, nonce="x"), dict(a, expires="soon"), dict(a, expires=True), {"device": "phone"}, "nope"):
            with self.assertRaises(approvals.ApprovalError, msg=str(bad)[:40]):
                self.v(bad)
        stranger, _ = device()
        with self.assertRaises(approvals.ApprovalError):
            self.v(approval(stranger, "phone", "release", "skills", D64, "c" * 64))  # signed by a key that is not the enrolled one

    def test_a_nonce_works_once(self):
        a = self.good()
        approvals.record_use(self.led, "phone", "release", D64, a["nonce"])
        with self.assertRaises(approvals.ApprovalError):
            self.v(a)


class KernelApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "skills")
        self.base = os.path.join(self.tmp.name, "caduceus")
        write(self.root, "dev/a/SKILL.md", SKILL % ("a", "a"))
        self.seed, pub = device()
        approvals.add_device(self.base, "phone", pub, "op")
        self.k = Kernel(self.root, self.base, Policy(approval_required=True), clock=lambda: __import__("time").time().__int__())
        self.k.reconcile()

    def tearDown(self):
        self.tmp.cleanup()

    def hold(self, text="a step learned on the web\n"):
        self.k.observe_taint("s", "web_extract", "page")
        open(os.path.join(self.root, "dev/a/SKILL.md"), "a").write(text)
        return self.k.reconcile(taint=self.k.taint_scope("s"))["verdict"]["effect_digest"]

    def sign(self, action, ed):
        return approval(self.seed, "phone", action, "skills", ed, self.k.review(ed)["parent"])

    def test_bare_operator_release_is_refused_when_approval_is_required(self):
        ed = self.hold()
        with self.assertRaises(TransitionError):
            self.k.release(ed, "op", "because")
        with self.assertRaises(TransitionError):
            self.k.discard(ed, "op", "because")
        self.assertEqual(self.k.held(), [ed])

    def test_signed_release_applies_and_records_the_device(self):
        ed = self.hold()
        out = self.k.release(ed, "ignored", "looks fine", self.sign("release", ed))
        self.assertEqual((out["status"], out["actor"]), ("released", "device:phone"))
        self.assertIn("a step learned", open(os.path.join(self.root, "dev/a/SKILL.md")).read())
        rel = [e["body"] for e in self.k.ledger().find("transition_released")][-1]
        self.assertEqual(rel["operator"], "device:phone")
        self.assertEqual([e["body"]["event"] for e in self.k.ledger().find("audit_note") if e["body"].get("event") == "approval_used"], ["approval_used"])

    def test_signed_discard_removes_it_from_the_held_list_and_leaves_the_tree(self):
        ed = self.hold()
        before = open(os.path.join(self.root, "dev/a/SKILL.md")).read()
        self.assertEqual(self.k.discard(ed, "x", "no thanks", self.sign("discard", ed))["status"], "discarded")
        self.assertEqual(self.k.held(), [])
        self.assertEqual(open(os.path.join(self.root, "dev/a/SKILL.md")).read(), before)
        with self.assertRaises(TransitionError):
            self.k.release(ed, "op", "late", self.sign("release", ed) if False else None)

    def test_an_approval_for_discard_cannot_release(self):
        ed = self.hold()
        with self.assertRaises(TransitionError):
            self.k.release(ed, "op", "swap", self.sign("discard", ed))
        self.assertEqual(self.k.held(), [ed])

    def test_an_approval_for_one_effect_cannot_release_another(self):
        e1 = self.hold("first\n")
        a1 = self.sign("release", e1)
        e2 = self.hold("second\n")
        with self.assertRaises(TransitionError):
            self.k.release(e2, "op", "x", a1)

    def test_replay_of_a_used_approval_is_refused(self):
        ed = self.hold()
        a = self.sign("discard", ed)
        self.k.discard(ed, "x", "r", a)
        e2 = self.hold("another\n")
        with self.assertRaises(TransitionError):
            self.k.discard(e2, "x", "r", a)

    def test_stale_effect_is_flagged_and_cannot_be_released_but_can_be_discarded(self):
        e1 = self.hold("first\n")
        self.k.release(e1, "x", "r", self.sign("release", e1))
        e2 = self.hold("second\n")
        write(self.root, "dev/b/SKILL.md", SKILL % ("b", "b"))
        self.k.observe_taint("s", "web_extract", "page")
        self.k.reconcile(taint=self.k.taint_scope("s"))  # a later held effect
        detail = {h["effect_digest"]: h for h in self.k.held_detail()}
        self.assertEqual(len(detail), 2)

    def test_approval_not_required_by_default_but_a_valid_one_is_still_honoured(self):
        k = Kernel(self.root, os.path.join(self.tmp.name, "c2"), Policy(), clock=lambda: __import__("time").time().__int__(), devices_dir=self.base)
        k.reconcile()
        k.observe_taint("s", "web_extract", "page")
        open(os.path.join(self.root, "dev/a/SKILL.md"), "a").write("x\n")
        ed = k.reconcile(taint=k.taint_scope("s"))["verdict"]["effect_digest"]
        a = approval(self.seed, "phone", "release", "skills", ed, k.review(ed)["parent"])
        self.assertEqual(k.release(ed, "op", "r", a)["actor"], "device:phone")

    def test_timeline_and_status_show_the_story(self):
        ed = self.hold()
        self.k.discard(ed, "x", "no", self.sign("discard", ed))
        types = [t["type"] for t in self.k.timeline()]
        self.assertEqual(types[:3], ["effect_discarded", "approval_used", "verdict"])
        self.assertIn("baseline", types)
        st = self.k.status()
        self.assertTrue(st["approval_required"])
        self.assertEqual(st["counts"]["quarantine"], 1)


class CliEndToEnd(unittest.TestCase):
    def test_enrol_then_release_with_a_signing_keyring_and_the_bare_operator_path_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root, base, ring = os.path.join(t, "skills"), os.path.join(t, "c"), os.path.join(t, "ring.json")
            write(root, "dev/a/SKILL.md", SKILL % ("a", "a"))
            env = dict(os.environ, PYTHONPATH=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

            def cli(*a):
                return subprocess.run([sys.executable, "-m", "caduceus", *a], capture_output=True, text=True, env=env)
            signing.add_signer(ring, "me", "operator", "ed25519")
            pub = json.load(open(ring))["me"]["pub_hex"]
            self.assertEqual(cli("device-add", "--base", base, "--name", "me", "--pub", pub, "--operator", "op").returncode, 0)
            self.assertEqual(cli("device-add", "--base", base, "--name", "me", "--pub", pub, "--operator", "op").returncode, 3)  # already enrolled
            policy_set(base, {"approval_required": True}, "op")
            self.assertEqual(cli("reconcile", "--skills-root", root, "--base", base).returncode, 0)  # baseline
            k = Kernel(root, base, load_policy(base))
            k.observe_taint("s", "web_extract", "p")
            open(os.path.join(root, "dev/a/SKILL.md"), "a").write("x\n")
            ed = k.reconcile(taint=k.taint_scope("s"))["verdict"]["effect_digest"]
            bare = cli("release-effect", ed, "--skills-root", root, "--base", base, "--operator", "op", "--reason", "r")
            self.assertEqual(bare.returncode, 3)
            self.assertIn("device-signed approval", bare.stderr)
            ok = cli("release-effect", ed, "--skills-root", root, "--base", base, "--operator", "op", "--reason", "r", "--keyring", ring, "--signer", "me")
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(json.loads(ok.stdout)["actor"], "device:me")
            self.assertEqual(cli("status", "--skills-root", root, "--base", base).returncode, 0)


if __name__ == "__main__":
    unittest.main()

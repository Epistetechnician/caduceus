"""The dashboard/plugin API over a real kernel, seeded like the dev server. Needs fastapi and httpx (both ship with Hermes); skipped without them."""
import asyncio
import importlib
import json
import os
import re
import secrets
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

try:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False

from caduceus import approvals, ed25519  # noqa: E402

P = "/api/plugins/caduceus-governance"


@unittest.skipUnless(HAVE, "fastapi/httpx not installed (they ship with Hermes)")
class PluginApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = os.path.join(cls.tmp.name, "home")
        os.makedirs(cls.home)
        cls.old_home = os.environ.get("HERMES_HOME")
        import ui_dev_server as d

        importlib.reload(d)
        cls.seed = d.seed(cls.home)
        from fastapi.testclient import TestClient

        cls.client = TestClient(d.make_app(cls.home))
        cls.seedbytes = secrets.token_bytes(32)
        cls.pub = ed25519.public_key(cls.seedbytes).hex()
        cls.base = os.path.join(cls.home, "caduceus")
        approvals.add_device(cls.base, "phone", cls.pub, "op")

    @classmethod
    def tearDownClass(cls):
        if cls.old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = cls.old_home
        cls.tmp.cleanup()

    def held(self, surface):
        st = self.client.get(P + "/status").json()
        return st["surfaces"][surface]["held"]

    def approval(self, action, surface, ed, parent=None, ttl=300):
        parent = parent or self.client.get("%s/review/%s/%s" % (P, surface, ed)).json()["parent"]
        nonce, expires = secrets.token_hex(16), int(time.time()) + ttl
        return {"device": "phone", "nonce": nonce, "expires": expires, "sig": ed25519.sign(self.seedbytes, approvals.message(action, surface, ed, parent, nonce, expires)).hex()}

    def post(self, action, surface, ed, approval=None, reason="r"):
        body = {"action": action, "surface": surface, "effect_digest": ed, "reason": reason}
        if approval is not None:
            body["approval"] = approval
        return self.client.post(P + "/approve", json=body)

    def test_01_status_has_everything_the_ui_needs(self):
        st = self.client.get(P + "/status").json()
        self.assertEqual(st["mode"], "boundary")
        self.assertEqual(sorted(st["surfaces"]), ["config", "memory", "skills"])
        self.assertTrue(st["policy"]["approval_required"])
        self.assertTrue(st["state_dir"].endswith("caduceus"))
        sk = st["surfaces"]["skills"]
        self.assertTrue(sk["ledger"]["ok"] and sk["ledger"]["size"] > 5)
        self.assertEqual(sk["counts"]["reject"], 1)
        self.assertTrue(any(h["stale"] for h in sk["held"]) and any(not h["stale"] for h in sk["held"]))
        self.assertEqual(st["devices"]["list"][0]["name"], "phone")

    def test_02_ui_page_is_served_with_a_per_response_nonce_csp_and_no_external_resources(self):
        a, b = self.client.get(P + "/ui"), self.client.get(P + "/ui")
        self.assertEqual(a.status_code, 200)
        na = re.search(r"nonce-([\w-]+)", a.headers["content-security-policy"]).group(1)
        nb = re.search(r"nonce-([\w-]+)", b.headers["content-security-policy"]).group(1)
        self.assertNotEqual(na, nb)
        self.assertEqual(a.text.count('nonce="%s"' % na), 2)
        self.assertNotIn("{{NONCE}}", a.text)
        self.assertIn("default-src 'none'", a.headers["content-security-policy"])
        self.assertIn("frame-ancestors 'self'", a.headers["content-security-policy"])
        self.assertEqual(a.headers["cache-control"], "no-store")
        self.assertEqual([u for u in re.findall(r"https?://[^\s\"'<>)]+", a.text) if "w3.org" not in u], [])

    def test_03_manifest_and_icon(self):
        m = self.client.get(P + "/manifest.webmanifest")
        self.assertEqual(m.status_code, 200)
        self.assertEqual(m.json()["display"], "standalone")
        self.assertIn("svg", self.client.get(P + "/icon.svg").headers["content-type"])

    def test_04_review_returns_the_diff_as_data_and_rejects_bad_ids(self):
        payload = [h for h in self.held("skills") if h["paths"] and "payload" in h["paths"][0]][0]
        r = self.client.get("%s/review/skills/%s" % (P, payload["effect_digest"])).json()
        text = "\n".join(l for f in r["files"] for l in f["diff"])
        self.assertIn('<img src=x onerror="alert(1)">', text)  # hostile text survives as data; the UI sets it with textContent
        self.assertEqual(self.client.get("%s/review/skills/%s" % (P, "z" * 64)).status_code, 404)
        self.assertEqual(self.client.get("%s/review/skills/%s%%0A" % (P, payload["effect_digest"][:63] + "a")).status_code, 404)
        self.assertEqual(self.client.get("%s/review/nope/%s" % (P, payload["effect_digest"])).status_code, 404)

    def test_05_approve_refuses_everything_that_is_not_a_valid_device_signature(self):
        ed = self.held("memory")[0]["effect_digest"]
        self.assertEqual(self.post("release", "memory", ed).status_code, 400)  # no approval at all
        bad = self.approval("release", "memory", ed)
        bad["sig"] = "0" * 128
        r = self.post("release", "memory", ed, bad)
        self.assertEqual(r.status_code, 403)
        self.assertIn("signature does not verify", r.json()["error"])
        wrong = self.approval("discard", "memory", ed)  # a signature for another action
        self.assertEqual(self.post("release", "memory", ed, wrong).status_code, 403)
        expired = self.approval("release", "memory", ed, ttl=-10)
        self.assertEqual(self.post("release", "memory", ed, expired).status_code, 403)
        self.assertEqual(len(self.held("memory")), 1)  # nothing moved
        self.assertEqual(self.client.post(P + "/approve", content=b"not json").status_code, 400)
        self.assertEqual(self.client.post(P + "/approve", content=b"[1]").status_code, 400)
        self.assertEqual(self.client.post(P + "/approve", content=b"x" * 9000).status_code, 413)
        self.assertEqual(self.post("steal", "memory", ed, wrong).status_code, 400)

    def test_06_a_signed_release_applies_and_a_replay_is_refused(self):
        ed = self.held("config")[0]["effect_digest"]
        ap = self.approval("release", "config", ed)
        r = self.post("release", "config", ed, ap, "reviewed on my phone")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["result"]["actor"], "device:phone")
        self.assertEqual(r.json()["state"]["held"], [])
        self.assertIn("approvals:", open(os.path.join(self.home, "config.yaml")).read())  # the change is live now
        again = self.post("release", "config", ed, ap)
        self.assertIn(again.status_code, (403, 409))
        tl = self.client.get(P + "/timeline?surface=config&limit=5").json()["items"]
        self.assertEqual([t["type"] for t in tl[:2]], ["released", "approval_used"])  # newest first: the signature is recorded, then the release
        self.assertEqual(tl[0]["operator"], "device:phone")
        self.assertTrue(tl[0]["at"] > 0)

    def test_07_a_stale_change_cannot_be_released_but_can_be_declined(self):
        stale = [h for h in self.held("skills") if h["stale"]][0]
        r = self.post("release", "skills", stale["effect_digest"], self.approval("release", "skills", stale["effect_digest"]))
        self.assertEqual(r.status_code, 409)
        self.assertIn("HEAD moved", r.json()["error"])
        d = self.post("discard", "skills", stale["effect_digest"], self.approval("discard", "skills", stale["effect_digest"]), "stale")
        self.assertEqual(d.status_code, 200, d.text)
        self.assertNotIn(stale["effect_digest"], [h["effect_digest"] for h in self.held("skills")])

    def test_08_a_declined_change_never_touches_the_tree(self):
        before = open(os.path.join(self.home, "memories", "USER.md")).read()
        ed = self.held("memory")[0]["effect_digest"]
        self.assertEqual(self.post("discard", "memory", ed, self.approval("discard", "memory", ed), "no").status_code, 200)
        self.assertEqual(open(os.path.join(self.home, "memories", "USER.md")).read(), before)
        self.assertEqual(self.held("memory"), [])

    def test_09_the_only_mutating_route_is_the_signature_gated_one(self):
        import ui_dev_server as d

        routes = [(sorted(r.methods), r.path) for r in d.make_app(self.home).routes if hasattr(r, "methods") and r.path.startswith(P) and "_dev" not in r.path]
        mutating = [p for m, p in routes if set(m) & {"POST", "PUT", "PATCH", "DELETE"}]
        self.assertEqual(mutating, [P + "/approve"])

    def test_10_observe_mode_reports_a_friendly_state_instead_of_touching_anything(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "caduceus"))
            json.dump({"mode": "observe"}, open(os.path.join(t, "caduceus", "mode.json"), "w"))
            import ui_dev_server as d

            api = d.load_api(t)
            st = asyncio.run(api.status())
            self.assertEqual((st["mode"], st["surfaces"]), ("observe", {}))
            self.assertEqual(os.listdir(os.path.join(t, "caduceus")), ["mode.json"])  # a GET created nothing
            os.environ["HERMES_HOME"] = self.home
            d.load_api(self.home)


if __name__ == "__main__":
    unittest.main()

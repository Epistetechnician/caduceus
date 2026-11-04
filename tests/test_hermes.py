import importlib.util
import json
import os
import sys
import tempfile
import unittest

from caduceus import audit, freshness, skills
from caduceus.ledger import Ledger

PLUGIN_PATH = os.path.join(os.path.dirname(__file__), "..", "plugin", "caduceus_governance", "__init__.py")


def _load_plugin():
    spec = importlib.util.spec_from_file_location("caduceus_governance_plugin", PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_home(root):
    sk = os.path.join(root, "skills")
    for cat, name, uses in (("devops", "deploy", 7), ("notes", "jot", 1)):
        d = os.path.join(sk, cat, name)
        os.makedirs(os.path.join(d, "references"))
        with open(os.path.join(d, "SKILL.md"), "w") as fh:
            fh.write('---\nname: %s\ndescription: "d"\nversion: 1.0.0\nmetadata:\n  hermes:\n    tags: [a]\n---\nBody\n' % name)
        with open(os.path.join(d, "references", "r.md"), "w") as fh:
            fh.write("ref")
    os.makedirs(os.path.join(sk, ".hub"))
    with open(os.path.join(sk, ".usage.json"), "w") as fh:
        json.dump(
            {
                "deploy": {"use_count": 7, "state": "active", "created_by": "agent", "patch_count": 2},
                "jot": {"use_count": 1, "state": "stale", "created_by": "user"},
            },
            fh,
        )
    with open(os.path.join(sk, ".curator_ledger.jsonl"), "w") as fh:
        for i in range(3):
            fh.write(json.dumps({"id": "e%d" % i, "ts": "t", "actor": "curator", "action": "write_file", "skill": "deploy"}) + "\n")
    return root


class SkillTests(unittest.TestCase):
    def test_frontmatter(self):
        fm = skills.parse_frontmatter('---\nname: "x y"\ndescription: d\nmetadata:\n  hermes:\n    tags: [a]\n---\nbody')
        self.assertEqual(fm, {"name": "x y", "description": "d"})
        self.assertEqual(skills.parse_frontmatter("no frontmatter"), {})
        self.assertEqual(skills.parse_frontmatter("---\nname: x\n"), {})  # unterminated

    def test_identity_by_content_not_name_or_path(self):
        with tempfile.TemporaryDirectory() as d:
            make_home(d)
            a = skills.load_skill(os.path.join(d, "skills"), "devops/deploy")
            again = skills.load_skill(os.path.join(d, "skills"), "devops/deploy")
            self.assertEqual(a.tree_digest, again.tree_digest)
            with open(os.path.join(d, "skills", "devops", "deploy", "references", "r.md"), "w") as fh:
                fh.write("changed")
            changed = skills.load_skill(os.path.join(d, "skills"), "devops/deploy")
            self.assertNotEqual(a.tree_digest, changed.tree_digest)

    def test_discover_skips_hidden_and_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            make_home(d)
            os.symlink("/etc/hosts", os.path.join(d, "skills", "devops", "deploy", "link.md"))
            self.assertEqual(skills.discover(os.path.join(d, "skills")), ["devops/deploy", "notes/jot"])
            rec = skills.load_skill(os.path.join(d, "skills"), "devops/deploy")
            self.assertNotIn("link.md", [f.rel_path for f in rec.files])


class AuditTests(unittest.TestCase):
    def test_audit_is_read_only_and_reports(self):
        with tempfile.TemporaryDirectory() as d:
            make_home(d)
            before = sorted(
                (os.path.join(b, f), os.path.getmtime(os.path.join(b, f)))
                for b, _, fs in os.walk(d)
                for f in fs
            )
            rep = audit.audit_hermes_home(d, min_use_for_protected=3)
            after = sorted(
                (os.path.join(b, f), os.path.getmtime(os.path.join(b, f)))
                for b, _, fs in os.walk(d)
                for f in fs
            )
            self.assertEqual(before, after)
        self.assertEqual(rep["skill_count"], 2)
        self.assertEqual([p["name"] for p in rep["protected_candidates"]], ["deploy"])
        self.assertFalse(rep["curator_ledger"]["hash_chained"])
        self.assertEqual(rep["curator_ledger"]["entries"], 3)
        self.assertNotIn("Body", json.dumps(rep))  # digests/counts only

    def test_curator_anchor_detects_rewrite_but_not_append(self):
        with tempfile.TemporaryDirectory() as d:
            make_home(d)
            path = os.path.join(d, "skills", ".curator_ledger.jsonl")
            lines = audit.curator_ledger_lines(path)
            anchor = audit.anchor_lines(lines)
            self.assertTrue(audit.verify_append_only(lines + [b'{"id":"new"}'], anchor["size"], anchor["root"]))
            rewritten = list(lines)
            rewritten[1] = rewritten[1].replace(b"curator", b"agent")
            self.assertFalse(audit.verify_append_only(rewritten, anchor["size"], anchor["root"]))
            self.assertFalse(audit.verify_append_only(lines[:2], anchor["size"], anchor["root"]))

    def test_missing_home(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(audit.audit_hermes_home(d)["skills_root_exists"])

    @unittest.skipUnless(os.path.isdir(os.path.expanduser("~/.hermes/skills")), "no local Hermes install")
    def test_real_home_audit_does_not_crash(self):
        rep = audit.audit_hermes_home("~/.hermes")
        self.assertIn("skill_count", rep)


class FreshnessTests(unittest.TestCase):
    def test_semantics(self):
        with tempfile.TemporaryDirectory() as d:
            m, u = os.path.join(d, "MEMORY.md"), os.path.join(d, "USER.md")
            for p, t in ((m, "mem"), (u, "user")):
                with open(p, "w") as fh:
                    fh.write(t)
            snap = freshness.make_snapshot([m, u], 100)
            self.assertEqual(freshness.classify(snap, [m, u], True)["status"], "ok")
            with open(m, "w") as fh:
                fh.write("mem changed")
            # drift inside a session is expected (frozen snapshot, prompt-cache safe)
            r = freshness.classify(snap, [m, u], session_boundary=False)
            self.assertEqual((r["status"], r["incident"]), ("drift_within_session_expected", "no"))
            r = freshness.classify(snap, [m, u], session_boundary=True)
            self.assertEqual((r["status"], r["incident"]), ("stale_at_boundary", "yes"))
            r = freshness.injection_check(snap.content_sha256, [m, u])
            self.assertEqual(r["status"], "injection_mismatch")

    def test_two_implementations_agree(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i, size in enumerate((0, 1, 96, 97, 98, 5000)):
                p = os.path.join(d, "f%d" % i)
                with open(p, "wb") as fh:
                    fh.write(bytes(range(256)) * (size // 256) + bytes(size % 256))
                paths.append(p)
            self.assertEqual(freshness.hash_whole_read(paths), freshness.hash_streaming(paths))


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "caduceus")
        os.makedirs(self.base)
        self.plugin = _load_plugin()

    def tearDown(self):
        self.tmp.cleanup()

    def _mode(self, mode):
        with open(os.path.join(self.base, "mode.json"), "w") as fh:
            json.dump({"mode": mode}, fh)

    def test_register_uses_documented_hook_only(self):
        hooks = []

        class Ctx:
            def register_hook(self, name, fn):
                hooks.append(name)

        self.plugin.register(Ctx())
        self.assertEqual(hooks, ["pre_tool_call"])

    def test_observe_never_blocks_and_records(self):
        args = {"operations": [{"name": "x", "action": "patch", "old_string": "a", "new_string": "b"}]}
        self.assertIsNone(self.plugin.decide("skill_manage", args, self.base))
        legacy = {"action": "create", "name": "y", "content": "c"}
        self.assertIsNone(self.plugin.decide("skill_manage", legacy, self.base))
        self.assertIsNone(self.plugin.decide("read_file", {"path": "/x"}, self.base))
        self.assertIsNone(self.plugin.decide("skill_view", {"name": "x"}, self.base))
        led = Ledger(os.path.join(self.base, "ledger.jsonl"))
        self.assertEqual(len(led), 2)
        self.assertEqual(led.entries[0]["body"]["event"], "skill_mutation_intent")
        self.assertEqual(led.entries[0]["body"]["actions"], "patch")
        self.assertNotIn("old_string", json.dumps(led.entries))  # no skill content in the ledger

    def test_enforce_blocks_without_capability_and_is_single_use(self):
        self._mode("enforce")
        args = {"operations": [{"name": "x", "action": "patch", "new_string": "b"}]}
        out = self.plugin.decide("skill_manage", args, self.base)
        self.assertEqual(out["action"], "block")
        self.assertTrue(out["message"])
        led = Ledger(os.path.join(self.base, "ledger.jsonl"))
        digest = self.plugin.args_digest("skill_manage", args)
        led.append("capability_issued", {"token_sha256": "a" * 64, "args_digest": digest, "expires_at": 4102444800})
        self.assertIsNone(self.plugin.decide("skill_manage", args, self.base))
        self.assertEqual(self.plugin.decide("skill_manage", args, self.base)["action"], "block")  # consumed
        other = {"operations": [{"name": "x", "action": "patch", "new_string": "different"}]}
        self.assertEqual(self.plugin.decide("skill_manage", other, self.base)["action"], "block")

    def test_enforce_ignores_expired_capability(self):
        self._mode("enforce")
        args = {"operations": [{"name": "x", "action": "patch", "new_string": "b"}]}
        led = Ledger(os.path.join(self.base, "ledger.jsonl"))
        led.append("capability_issued", {"token_sha256": "c" * 64, "args_digest": self.plugin.args_digest("skill_manage", args), "expires_at": 1})
        self.assertEqual(self.plugin.decide("skill_manage", args, self.base)["action"], "block")

    def _governed(self, decision="accept"):
        from caduceus import exchange
        from caduceus.admission import Verdict
        from tests.test_gate import _cand

        cand = _cand()
        gov = Ledger(os.path.join(self.tmp.name, "gov.jsonl"))
        exchange.record(gov, cand, Verdict(decision, (), (), cand.digest(), "a" * 64), None)
        return cand, gov

    def test_admit_call_issues_a_capability_the_plugin_honours_once(self):
        from caduceus.toolcall import issue_for_call

        self._mode("enforce")
        cand, gov = self._governed()
        args = {"operations": [{"name": "x", "action": "patch", "new_string": "b"}]}
        out = issue_for_call(gov, cand, "skill_manage", args, self.base, 60)
        self.assertEqual(out["args_digest"], self.plugin.args_digest("skill_manage", args))
        self.assertIsNone(self.plugin.decide("skill_manage", args, self.base))
        self.assertEqual(self.plugin.decide("skill_manage", args, self.base)["action"], "block")
        other = {"operations": [{"name": "x", "action": "patch", "new_string": "other"}]}
        self.assertEqual(self.plugin.decide("skill_manage", other, self.base)["action"], "block")

    def test_admit_call_refuses_without_an_accept_verdict(self):
        from caduceus.toolcall import ToolCallError, issue_for_call
        from tests.test_gate import _cand

        args = {"operations": [{"name": "x", "action": "patch"}]}
        cand, gov = self._governed("reject")
        with self.assertRaises(ToolCallError):
            issue_for_call(gov, cand, "skill_manage", args, self.base, 60)
        with self.assertRaises(ToolCallError):  # candidate never recorded
            issue_for_call(gov, _cand(candidate_id="unseen"), "skill_manage", args, self.base, 60)
        cand, gov = self._governed()
        with self.assertRaises(ToolCallError):
            issue_for_call(gov, cand, "read_file", args, self.base, 60)  # only skill_manage
        with self.assertRaises(ToolCallError):
            issue_for_call(gov, cand, "skill_manage", args, self.base, 99999)
        with self.assertRaises(ToolCallError):
            issue_for_call(gov, cand, "skill_manage", args, self.tmp.name, 60)  # not a 'caduceus' dir

    def test_admit_call_refuses_when_frozen(self):
        from caduceus import control
        from caduceus.toolcall import ToolCallError, issue_for_call

        cand, gov = self._governed()
        control.freeze(self.base, Ledger(os.path.join(self.base, "ledger.jsonl")), "op", "test")
        with self.assertRaises(ToolCallError):
            issue_for_call(gov, cand, "skill_manage", {"operations": []}, self.base, 60)

    def test_hook_fails_closed_in_enforce_and_open_in_observe(self):
        with open(os.path.join(self.base, "ledger.jsonl"), "w") as fh:
            fh.write("corrupt\n")
        os.environ["HERMES_HOME"] = self.tmp.name
        try:
            self._mode("enforce")
            with self.assertLogs("caduceus_governance_plugin", level="WARNING"):
                out = self.plugin._on_pre_tool_call("skill_manage", {"action": "patch"})
            self.assertEqual(out["action"], "block")
            self._mode("observe")
            with self.assertLogs("caduceus_governance_plugin", level="WARNING"):
                self.assertIsNone(self.plugin._on_pre_tool_call("skill_manage", {"action": "patch"}))
            self.assertIsNone(self.plugin._on_pre_tool_call("terminal", {"command": "ls"}))
        finally:
            os.environ.pop("HERMES_HOME", None)


if __name__ == "__main__":
    unittest.main()

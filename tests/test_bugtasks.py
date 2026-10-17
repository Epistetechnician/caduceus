import os
import unittest

from caduceus import bugtasks as b

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = "def f(a, b):\n    if a < b and not b == 3:\n        return a + b + 1\n    return 0\n"


class Mutations(unittest.TestCase):
    def test_each_op_changes_source_and_is_deterministic(self):
        for op in b.OPS:
            m1, m2 = b.mutate(SRC, op, 0), b.mutate(SRC, op, 0)
            self.assertEqual(m1, m2)
            self.assertIsNotNone(m1, op)
            self.assertNotEqual(m1, b.mutate(SRC, op, 99) or "", op)
            compile(m1, "x", "exec")

    def test_missing_site_is_none_and_unknown_op_raises(self):
        self.assertIsNone(b.mutate("x = 1\n", "cmp_flip", 0))
        with self.assertRaises(b.TaskError):
            b.mutate(SRC, "nope", 0)

    def test_bool_not_removes_negation(self):
        self.assertNotIn("not", b.mutate(SRC, "bool_not", 0))

    def test_split_is_per_module_and_seed_dependent(self):
        self.assertEqual(b.split_of("stats", 1), b.split_of("stats", 1))
        self.assertIn(b.split_of("stats", 1), b.SPLITS)
        seen = {b.split_of("m%d" % i, 7) for i in range(60)}
        self.assertEqual(seen, set(b.SPLITS))


class Oracle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tasks = b.generate(REPO, ["stats"], 1, per_module=1)
        assert tasks, "no task generated for stats"
        cls.task = tasks[0]
        with open(os.path.join(REPO, cls.task["path"])) as fh:
            cls.orig = fh.read()

    def test_task_is_killed_by_its_tests_and_hides_the_answer(self):
        v = b.proposer_view(self.task)
        self.assertEqual(set(v), {"task_id", "path", "source", "tests"})
        self.assertNotIn("original_digest", v)
        self.assertNotIn("op", v)

    def test_original_passes_mutant_unchanged_fails(self):
        self.assertTrue(b.verify_repair(REPO, self.task, self.orig)["passed"])
        r = b.verify_repair(REPO, self.task, self.task["mutated_source"])
        self.assertFalse(r["passed"])
        self.assertEqual(r["reason"], "unchanged")

    def test_wrong_repair_fails_red(self):
        bad = self.task["mutated_source"] + "\n# touched\n"
        self.assertEqual(b.verify_repair(REPO, self.task, bad)["reason"], "tests_red")

    def test_syntax_error_and_extra_files_refused(self):
        self.assertEqual(b.verify_repair(REPO, self.task, "def (:")["reason"], "syntax_error")
        r = b.verify_repair(REPO, self.task, self.orig, other_files={"tests/test_core.py": ""})
        self.assertFalse(r["passed"])
        self.assertTrue(r["reason"].startswith("touched_other_files"))

    def test_commitment_stable_and_split_sensitive(self):
        c1 = b.split_commitment([self.task], self.task["split"])
        self.assertEqual(c1, b.split_commitment([self.task], self.task["split"]))
        other = "tune" if self.task["split"] != "tune" else "fit"
        self.assertNotEqual(c1, b.split_commitment([self.task], other))


class Prereg(unittest.TestCase):
    def test_exp008_prereg_matches_manifest_and_is_float_free(self):
        import json
        def nf(x):
            raise AssertionError("float in prereg")
        pr = json.load(open(os.path.join(REPO, "prereg", "exp-008-bugrepair-round1.json")), parse_float=nf)
        m = json.load(open(os.path.join(REPO, "tasks", "bugtasks-v1.manifest.json")))
        self.assertEqual(pr["task_manifest_digest"], m["manifest_digest"])
        self.assertEqual(pr["split_commitments"], m["commitments"])
        self.assertEqual(pr["nonclaim"], "LocalDevelopmentEvidenceOnly")
        self.assertEqual(set(pr["arms"]), {"untouched_base", "fixed_policy", "candidate", "deterministic_random"})


if __name__ == "__main__":
    unittest.main()

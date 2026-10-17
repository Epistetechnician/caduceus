import json
import os
import tempfile
import unittest

from caduceus import bench
from caduceus.ledger import Ledger


def _row(i, cat):
    return {"messages": [{"role": "system", "content": "sys%d" % i}, {"role": "user", "content": "task%d" % i}],
            "rubrics": [{"rubric_criteria": "say GOOD"}], "metadata": {"context_category": cat}}


class BenchTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.data = os.path.join(self.d, "d.jsonl")
        with open(self.data, "w") as fh:
            for i in range(40):
                fh.write(json.dumps(_row(i, "A" if i % 2 else "B")) + "\n")
            fh.write(json.dumps({"messages": [{"role": "user", "content": "x"}], "rubrics": [], "metadata": {}}) + "\n")
        tasks = bench.load_tasks(self.data, 1000, [0])
        _, assess = bench.split(tasks, 7, 10, 20)
        self.raw = {"schema": bench.SCHEMA, "experiment_id": "e", "hypothesis": "h", "benchmark": "b",
                    "dataset_sha256": bench.file_sha256(self.data), "max_chars": 1000, "exclude": [0],
                    "split_seed": 7, "n_fit": 10, "n_assess": 20, "assessment_commitment": bench.commitment(assess),
                    "order_seed": 1, "bootstrap_seed": 2, "bootstrap_resamples": 500, "min_effect_micro": 100000,
                    "alpha_micro": 50000, "max_notrun_micro": 100000, "executor": {"model": "fake"},
                    "judge_primary": "j", "judge_second": "j2", "skill": "r/s", "base_tree_digest": "0" * 64,
                    "random_skill": "x", "max_category_loss": 3, "decision_rule": "d",
                    "nonclaim": "LocalDevelopmentEvidenceOnly"}
        self.ledger = Ledger(os.path.join(self.d, "l.jsonl"))

    def judge(self, p):
        return '{"items": ["yes"], "score": %d}' % int("GOOD" in p.split("RESPONSE:")[1])

    def test_load_skips_multiturn_and_excluded(self):
        idx = [t["idx"] for t in bench.load_tasks(self.data, 1000, [0])]
        self.assertEqual(idx, list(range(1, 40)))

    def test_requires_lock_and_passes_real_effect(self):
        arms = {"untouched_base": lambda p: "bad", "deterministic_random": lambda p: "bad", "fixed_policy": lambda p: "bad",
                "candidate": lambda p: "GOOD"}
        with self.assertRaises(bench.BenchError):
            bench.run(self.raw, self.data, self.ledger, arms, self.judge, self.judge)
        bench.lock(self.raw, self.ledger)
        res = bench.run(self.raw, self.data, self.ledger, arms, self.judge, self.judge, workers=2)
        self.assertEqual((res["verdict"], res["design"], res["counts"]["candidate"]), ("Pass", "candidate", 20))
        with self.assertRaises(bench.BenchError):
            bench.run(self.raw, self.data, self.ledger, arms, self.judge, self.judge)

    def test_no_effect_fails_and_errors_are_notrun(self):
        bench.lock(self.raw, self.ledger)
        same = {a: (lambda p: "bad") for a in bench.ARMS}
        self.assertEqual(bench.run(self.raw, self.data, self.ledger, same, self.judge, self.judge)["verdict"], "Fail")
        raw2 = dict(self.raw, experiment_id="e2")
        self.ledger = Ledger(self.ledger.path)
        bench.lock(raw2, self.ledger)

        def boom(p):
            raise RuntimeError("down")
        broken = dict(same, candidate=boom)
        self.assertEqual(bench.run(raw2, self.data, self.ledger, broken, self.judge, self.judge)["verdict"], "NotRun")

    def test_tampered_split_refused(self):
        bench.lock(dict(self.raw, assessment_commitment="f" * 64), self.ledger)
        with self.assertRaises(bench.BenchError):
            bench.verify_split(dict(self.raw, assessment_commitment="f" * 64), self.data)

    def test_parse_grade_strict(self):
        self.assertEqual(bench.parse_grade('x {"score": 1} y'), 1)
        for bad in ("nothing", '{"score": true}', '{"score": 2}', "{bad"):
            self.assertIsNone(bench.parse_grade(bad))


if __name__ == "__main__":
    unittest.main()

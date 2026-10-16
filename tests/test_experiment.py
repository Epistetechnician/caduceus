import json
import os
import sys
import tempfile
import unittest

from caduceus import audit, experiment, skills
from caduceus.contract import ContractFileError, load_contract_file, load_contracts_dir
from caduceus.executor import ExecutorConfig, ExecutorError, HermesExecutor
from caduceus.ledger import Ledger
from caduceus.probes import run_probe
from tests.test_hermes import make_home

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACTS = os.path.join(REPO, "contracts")
PREREG = os.path.join(REPO, "prereg", "exp-001-skill-vs-noskill.json")

# Stand-in for hermes_runner.py: answers from the skill name it is given.
FAKE_RUNNER = r'''
import json, os, sys
req = json.loads(sys.stdin.read())
assert os.environ["HERMES_HOME"]
if req["prompt"] == "boom":
    print(json.dumps({"ok": False, "error": "Boom"})); sys.exit(1)
print("noise on stdout before the reply")
print(json.dumps({"ok": True, "final_response": "skill=%s" % req["skill"]}))
'''


def _contract_file(tmp, **overrides):
    with open(os.path.join(CONTRACTS, "codebase-inspection.json")) as fh:
        raw = json.load(fh)
    raw.update(overrides)
    path = os.path.join(tmp, "c.json")
    with open(path, "w") as fh:
        fh.write(json.dumps(raw) if "text" not in overrides else overrides["text"])
    return path


class ContractFileTest(unittest.TestCase):
    def test_shipped_contracts_load_and_validate(self):
        cs = load_contracts_dir(CONTRACTS)
        self.assertEqual(
            sorted(cs),
            ["codebase-inspection", "requesting-code-review", "systematic-debugging", "test-driven-development"],
        )
        for cf in cs.values():
            kinds = [p.kind for p in cf.contract.probes]
            self.assertGreaterEqual(kinds.count("held_out"), 2)
            self.assertGreaterEqual(kinds.count("negative"), 1)

    def test_unknown_field_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ContractFileError):
                load_contract_file(_contract_file(tmp, extra=1))

    def test_name_mismatch_and_bad_digest_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ContractFileError):
                load_contract_file(_contract_file(tmp, skill="other"))
            with self.assertRaises(ContractFileError):
                load_contract_file(_contract_file(tmp, written_against_tree_digest="abc"))

    def test_float_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.json")
            with open(os.path.join(CONTRACTS, "codebase-inspection.json")) as fh:
                text = fh.read().replace('"max_output_chars": 4000', '"max_output_chars": 4000.0')
            with open(path, "w") as fh:
                fh.write(text)
            with self.assertRaises(ContractFileError):
                load_contract_file(path)

    def test_audit_reports_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_home(tmp)
            report = audit.audit_hermes_home(tmp, contracts_dir=CONTRACTS)
            self.assertTrue(all(not c["has_contract"] for c in report["protected_candidates"]))
            self.assertEqual(report["skills_without_contracts"], 2)


class _Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "home")
        make_home(self.root)
        self.skills_root = os.path.join(self.root, "skills")
        runner = os.path.join(self.tmp.name, "fake_runner.py")
        with open(runner, "w") as fh:
            fh.write(FAKE_RUNNER)
        self.cmd = [sys.executable, runner]
        self.cfg = ExecutorConfig(sys.executable, self.tmp.name, "m", "p", "http://127.0.0.1:9/v1", "NO_KEY", 30)

    def executor(self, rel_dir=None):
        return HermesExecutor(self.cfg, self.skills_root if rel_dir else None, rel_dir,
                              allow_model_calls=True, runner_cmd=self.cmd)


class ExecutorTest(unittest.TestCase):
    def setUp(self):
        self.env = _Env()
        self.addCleanup(self.env.tmp.cleanup)

    def test_requires_explicit_opt_in(self):
        with self.assertRaises(ExecutorError):
            HermesExecutor(self.env.cfg, None, None)

    def test_isolated_home_with_skill_copy(self):
        with self.env.executor("devops/deploy") as ex:
            self.assertEqual(ex.skill_name, "deploy")
            src = skills.load_skill(self.env.skills_root, "devops/deploy").tree_digest
            self.assertEqual(ex.skill_tree_digest, src)
            self.assertEqual(skills.discover(os.path.join(ex.home, "skills")), ["devops/deploy"])
            self.assertEqual(ex("hi"), "skill=deploy")
            home = ex.home
        self.assertFalse(os.path.exists(home))

    def test_no_skill_arm_and_manifest(self):
        with self.env.executor() as ex:
            self.assertEqual(ex("hi"), "skill=None")
            self.assertEqual(ex.manifest()["temperature"], "provider_default")
            self.assertIsNone(ex.manifest()["skill"])

    def test_runner_error_is_executor_error_not_probe_fail(self):
        from caduceus.contract import make_probe

        with self.env.executor() as ex:
            out = run_probe(ex, make_probe("x", "visible", "boom", "contains", ""), 100)
        self.assertFalse(out.passed)
        self.assertEqual(out.error, "ExecutorError")


def _prereg(tmp, **overrides):
    with open(PREREG) as fh:
        raw = json.load(fh)
    raw.update(overrides)
    path = os.path.join(tmp, "prereg.json")
    with open(path, "w") as fh:
        json.dump(raw, fh)
    return experiment.load_prereg(path)


class _Fake:
    """Deterministic arms: the treatment answers like a pygount user."""

    def __init__(self, fail_every=0):
        self.fail_every, self.n = fail_every, 0

    def __call__(self, arm, cf):
        def skill(prompt):
            self.n += 1
            if self.fail_every and self.n % self.fail_every == 0:
                raise ExecutorError("down")
            if arm == "with_skill" and "pygount" not in prompt and "words" not in prompt and "lists the files" not in prompt:
                return "pygount --format=summary --format=json --suffix=py --folders-to-skip=.git,node_modules . comment"
            return "1"
        return skill


class ExperimentTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.contracts = load_contracts_dir(CONTRACTS)
        self.pre = _prereg(self.tmp)
        self.trees = {s["skill"]: s["tree_digest"] for s in self.pre.raw["skills"]}
        self.ledger = Ledger(os.path.join(self.tmp, "ledger.jsonl"))

    def test_shipped_prereg_is_valid_and_pinned(self):
        self.assertEqual(self.pre.raw["nonclaim"], "LocalDevelopmentEvidenceOnly")
        for s in self.pre.raw["skills"]:
            self.assertEqual(s["contract_digest"], self.contracts[s["skill"]].contract.digest())
            self.assertEqual(s["tree_digest"], self.contracts[s["skill"]].written_against_tree_digest)

    def test_bad_prereg_rejected(self):
        for bad in ({"arms": ["with_skill", "without_skill"]}, {"nonclaim": "Production"},
                    {"reps": 0}, {"executor": {"provider": "p", "model": "m", "base_url": "u", "temperature": "0"}}):
            with self.assertRaises(experiment.ExperimentError):
                _prereg(self.tmp, **bad)

    def test_run_requires_lock_then_closes(self):
        with self.assertRaises(experiment.ExperimentError):
            experiment.run_paired(self.pre, self.ledger, self.contracts, self.trees, _Fake())
        experiment.lock(self.pre, self.ledger, self.contracts, self.trees)
        with self.assertRaises(experiment.ExperimentError):
            experiment.lock(self.pre, self.ledger, self.contracts, self.trees)
        res = experiment.run_paired(self.pre, self.ledger, self.contracts, self.trees, _Fake())
        self.assertIn(res["verdict"], experiment.VERDICTS)
        self.assertEqual(res["units"], 4 * 10 * self.pre.raw["reps"])
        self.assertEqual(res["clusters"], 40)
        with self.assertRaises(experiment.ExperimentError):
            experiment.run_paired(self.pre, self.ledger, self.contracts, self.trees, _Fake())

    def test_deterministic(self):
        results = []
        for i in range(2):
            led = Ledger(os.path.join(self.tmp, "l%d.jsonl" % i))
            experiment.lock(self.pre, led, self.contracts, self.trees)
            results.append(experiment.run_paired(self.pre, led, self.contracts, self.trees, _Fake()))
        self.assertEqual(results[0], results[1])

    def test_pin_drift_refused(self):
        trees = dict(self.trees, **{"codebase-inspection": "0" * 64})
        with self.assertRaises(experiment.ExperimentError):
            experiment.lock(self.pre, self.ledger, self.contracts, trees)

    def test_executor_outages_give_notrun_not_pass(self):
        experiment.lock(self.pre, self.ledger, self.contracts, self.trees)
        res = experiment.run_paired(self.pre, self.ledger, self.contracts, self.trees, _Fake(fail_every=3))
        self.assertEqual(res["verdict"], "NotRun")
        self.assertGreater(res["excluded"], 0)

    def test_ledger_holds_no_outputs(self):
        experiment.lock(self.pre, self.ledger, self.contracts, self.trees)
        experiment.run_paired(self.pre, self.ledger, self.contracts, self.trees, _Fake())
        with open(self.ledger.path) as fh:
            text = fh.read()
        self.assertNotIn("pygount", text)


class QuantileTest(unittest.TestCase):
    def test_quantile_bootstrap_is_seeded_and_bracketed(self):
        from caduceus.stats import quantile_bootstrap

        values = list(range(1, 1001))
        a = quantile_bootstrap(values, 0.99, seed=7, resamples=300)
        self.assertEqual(a, quantile_bootstrap(values, 0.99, seed=7, resamples=300))
        self.assertEqual(a["point"], 990)
        self.assertLessEqual(a["lower"], a["point"])
        self.assertGreaterEqual(a["upper"], a["point"])


if __name__ == "__main__":
    unittest.main()

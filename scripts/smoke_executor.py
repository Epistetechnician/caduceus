"""Smoke test: HermesExecutor + run_paired end to end, against a 127.0.0.1 stub.

    python3 scripts/smoke_executor.py --hermes-src ~/.hermes/hermes-agent

Uses the real Hermes runner and a copy of one real skill (read-only from
~/.hermes/skills) in an isolated home. The stub "model" answers with a pygount
command only when the skill text is in its system prompt, so the expected
pass counts show the skill reached the model in one arm and not the other. It is a plumbing
check, not evidence about any model. The ledger lives in a temp dir.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from caduceus import experiment, readback, skills  # noqa: E402
from caduceus.contract import load_contracts_dir  # noqa: E402
from caduceus.executor import HermesExecutor, default_config_from_env  # noqa: E402
from caduceus.ledger import Ledger  # noqa: E402
from stub_openai import serve  # noqa: E402

SKILL = "codebase-inspection"
WITH = "pygount --format=summary --format=json --suffix=py,yaml --folders-to-skip=.git,node_modules,venv . (Markdown counts as comment lines)"
WITHOUT = "find . -name '*.py' | xargs wc -l"


def respond(body, calls):
    system = " ".join(str(m.get("content")) for m in body.get("messages", []) if m.get("role") == "system")
    return {"role": "assistant", "content": WITH if "pygount" in system else WITHOUT}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-src", required=True)
    ap.add_argument("--hermes-home", default="~/.hermes")
    ns = ap.parse_args()

    server, base_url, calls = serve(respond)
    os.environ["CADUCEUS_STUB_KEY"] = "stub-key"
    contracts = load_contracts_dir(os.path.join(REPO, "contracts"))
    cf = contracts[SKILL]
    root = os.path.join(os.path.expanduser(ns.hermes_home), "skills")
    tree = skills.load_skill(root, cf.skill_rel_dir).tree_digest
    raw = json.load(open(os.path.join(REPO, "prereg", "exp-001-skill-vs-noskill.json")))
    raw.update(
        experiment_id="smoke-%s" % SKILL,
        skills=[{"skill": SKILL, "contract_digest": cf.contract.digest(), "tree_digest": tree}],
        reps=2,
        bootstrap_resamples=1000,
        executor={"provider": "custom", "model": "stub-model", "base_url": base_url, "temperature": "provider_default"},
    )
    tmp = tempfile.mkdtemp(prefix="caduceus-smoke-")
    path = os.path.join(tmp, "prereg.json")
    json.dump(raw, open(path, "w"))
    prereg = experiment.load_prereg(path)
    ledger = Ledger(os.path.join(tmp, "ledger.jsonl"))
    experiment.lock(prereg, ledger, contracts, {SKILL: tree})

    cfg = default_config_from_env(ns.hermes_src, "stub-model", "custom", base_url, "CADUCEUS_STUB_KEY")
    opened = []

    def make(arm, c):
        e = HermesExecutor(cfg, root if arm == "with_skill" else None,
                           c.skill_rel_dir if arm == "with_skill" else None, allow_model_calls=True)
        opened.append(e)
        return e

    try:
        result = experiment.run_paired(prereg, ledger, contracts, {SKILL: tree}, make)
    finally:
        for e in opened:
            e.close()
    tools_offered = sorted({t.get("function", {}).get("name") for b in calls for t in b.get("tools") or []})
    check = readback.verify_file(ledger.path)
    print(json.dumps({"result": result, "stub_chat_requests": len(calls),
                      "tools_offered_to_model": tools_offered, "ledger_size": check.get("size")}, indent=2))
    server.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    counts = result["per_skill"][SKILL]
    # Deterministic stub: with the skill, 8 of 10 probes pass (both negatives
    # fail because the answer mentions pygount); without it only the 2
    # negatives pass. The verdict itself is not asserted: 10 clusters is too
    # few for the preregistered rule, which is the point of preregistering it.
    ok = (counts == {"units": 20, "excluded": 0, "control_pass": 4, "treatment_pass": 16}
          and not tools_offered and check.get("size") == 3)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""End-to-end supervised release through a REAL Hermes agent loop (PRD R21): enforce-mode plugin, broker apply, canary promote, canary abort and rollback, freeze.

Everything happens in a throw-away HERMES_HOME (never a real home). The model is a scripted stub on loopback; the operator side is Caduceus' own release path. Lane evidence is
synthetic and labelled (the mechanism is under test, not a skill). Hermes' real `skill_view` / `skill_manage` tools and plugin dispatch run in every loop.
Run with the Hermes venv python from the repo root:  <hermes>/venv/bin/python scripts/e2e_hermes_release.py --hermes-src ~/.hermes/hermes-agent"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
SKILL = "---\nname: deploy\ndescription: How we deploy.\n---\n\n# Deploy\n\nstep one: run the release script\n"
REL = "devops/deploy"


def loop(home, script, hermes_src):
    from e2e_hermes_boundary import respond_factory
    from stub_openai import serve

    os.environ["HERMES_HOME"] = home
    seen, state = {}, {"base": 0, "turn": 0, "last_tools": 0}
    server, base_url, _ = serve(respond_factory(script, seen, state))
    from hermes_cli.plugins import get_plugin_manager
    from run_agent import AIAgent

    get_plugin_manager().discover_and_load(force=True)
    agent = AIAgent(base_url=base_url, api_key="stub-key", provider="custom", model="stub-model", quiet_mode=True, skip_context_files=True, skip_memory=True, max_iterations=8)
    agent.run_conversation("Do the thing.")
    server.shutdown()
    os.environ.pop("HERMES_HOME", None)  # the operator side (Refinery, broker) refuses to run with HERMES_HOME pointing at its root; Hermes itself only needs it inside the loop
    return {k: (v or "") for k, v in sorted(seen.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-src", required=True)
    ns = ap.parse_args()
    sys.dont_write_bytecode = True
    os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    sys.path.insert(0, os.path.abspath(os.path.expanduser(ns.hermes_src)))
    sys.path.insert(0, REPO)
    from caduceus import control, lineage
    from caduceus.admission import REQUIRED_LANES, AdmissionPolicy, evaluate
    from caduceus.broker import apply_via_broker
    from caduceus.digest import sha256_hex
    from caduceus.ledger import Ledger
    from caduceus.propose import default_preregistration, propose_skill
    from caduceus.refinery import Refinery
    from caduceus.release import Breaker, Budget, CanaryPlan, Release, ReleaseManager
    from caduceus.skills import load_skill

    tmp = tempfile.mkdtemp(prefix="caduceus-release-e2e-")
    home = os.path.join(tmp, "hermes_home")
    assert not os.path.realpath(home).startswith(os.path.realpath(os.path.expanduser("~/.hermes")))
    skills, cad = os.path.join(home, "skills"), os.path.join(home, "caduceus")
    os.makedirs(os.path.join(skills, REL))
    open(os.path.join(skills, REL, "SKILL.md"), "w").write(SKILL)
    os.makedirs(cad)
    json.dump({"mode": "enforce"}, open(os.path.join(cad, "mode.json"), "w"))
    shutil.copytree(os.path.join(REPO, "plugin", "caduceus_governance"), os.path.join(home, "plugins", "caduceus_governance"), ignore=shutil.ignore_patterns("__pycache__"))
    open(os.path.join(home, "config.yaml"), "w").write("plugins:\n  enabled:\n    - caduceus-governance\n")
    # The operator side (ledger, control, store) lives OUTSIDE the Hermes home: the broker restores/rewrites everything under its root, and the plugin keeps its own intent ledger in the home.
    op = os.path.join(tmp, "op")
    os.makedirs(os.path.join(op, "ctl"))
    lpath, store, opctl = os.path.join(op, "ledger.jsonl"), os.path.join(tmp, "store"), os.path.join(op, "ctl")
    Ledger(lpath).append("audit_note", {"event": "drill", "skill": REL, "evidence": "synthetic"})

    now = [int(time.time())]
    plan = CanaryPlan(window=3, abort_if=(("errors", 0), ("p99_us", 60000)))
    mgr = ReleaseManager(Ledger(lpath), lambda: now[0], Budget(100, 3600), {"canary": Breaker("canary", ttl_s=300)}, max_evidence_age_s=600)
    policy = AdmissionPolicy()
    evidence = {lane: {"status": "Checked", "detail": "synthetic"} for lane in REQUIRED_LANES}

    def candidate(tag):
        prop = os.path.join(tmp, "proposals", tag, "deploy")
        shutil.copytree(os.path.join(skills, REL), prop)
        with open(os.path.join(prop, "SKILL.md"), "a", encoding="utf-8") as fh:
            fh.write("\nrelease-marker-%s\n" % tag)
        cand, writes = propose_skill(skills, REL, prop, "e2e-proposer", default_preregistration(sha256_hex(b"holdout"), 40, [1, 2]), candidate_id="e2e-" + tag)
        verdict = evaluate(cand, policy, evidence)
        token = Refinery(home, store, Ledger(lpath), clock=lambda: int(time.time())).issue_capability(cand, verdict, 600)
        return cand, {"root": home, "store": store, "ledger": lpath, "control_dir": opctl, "candidate": cand.as_dict(), "verdict": verdict.as_dict(),
                      "token": token, "writes": dict(writes), "protected": {}, "keyring": ""}

    def stage(cand, rid):
        mgr.ledger = Ledger(lpath)
        mgr.enqueue(Release(rid, cand.digest(), cand.parent_digest, policy.digest(), now[0], plan, 10))
        return mgr.revalidate(rid, load_skill(skills, REL).tree_digest, policy.digest())

    res, shown = {}, {}
    view = [("skill_view", {"name": "deploy"})]
    direct = [("skill_manage", {"operations": [{"action": "patch", "name": "deploy", "old_string": "step one", "new_string": "step zero"}]})]

    # A: a direct skill_manage write is blocked in enforce mode; the skill is unchanged
    shown["A"] = loop(home, list(direct), ns.hermes_src)
    res["A_direct_write_blocked"] = "step zero" not in open(os.path.join(skills, REL, "SKILL.md")).read() and "no admitted capability" in json.dumps(shown["A"])

    # B: supervised release: capability -> broker apply -> canary -> promote; the next real Hermes loop reads the promoted skill
    cand, req = candidate("promote")
    staged = stage(cand, "promote") == "revalidated"
    reply = apply_via_broker(req)
    mgr.ledger = Ledger(lpath)
    mgr.start_canary("promote")
    for _ in range(plan.window):
        mgr.observe("promote", {"errors": 0, "p99_us": 42000})
    mgr.promote("promote")
    promoted_tree = load_skill(skills, REL).tree_digest
    lineage.record_promotion(Ledger(lpath), REL, "tenant", "e2e", cand.parent_digest, promoted_tree, cand.digest())
    shown["B"] = loop(home, list(view), ns.hermes_src)
    res["B_promoted_and_visible_to_hermes"] = (staged and reply.get("applied") is True and mgr.releases["promote"].state == "promoted" and "release-marker-promote" in json.dumps(shown["B"]))
    res["B_replay_refused"] = apply_via_broker(req).get("ok") is False

    # C: canary abort -> breaker -> broker restore; the next real Hermes loop sees the pre-apply skill again
    cand2, req2 = candidate("abort")
    before = load_skill(skills, REL).tree_digest
    staged2 = stage(cand2, "abort") == "revalidated"
    reply2 = apply_via_broker(req2)
    mgr.ledger = Ledger(lpath)
    mgr.start_canary("abort")
    state = mgr.observe("abort", {"errors": 3, "p99_us": 42000})
    restored = apply_via_broker({"op": "restore", "root": home, "store": store, "ledger": lpath, "snapshot_id": reply2.get("snapshot_id", ""), "reason": "canary_abort"})
    shown["C"] = loop(home, list(view), ns.hermes_src)
    if os.environ.get("E2E_DEBUG"):
        print("C parts", staged2, reply2.get("applied"), state, restored.get("ok"), load_skill(skills, REL).tree_digest == before, mgr.breakers["canary"].blocks(now[0]), restored, file=sys.stderr)
    res["C_aborted_and_rolled_back_in_hermes"] = (staged2 and reply2.get("applied") is True and state == "rolled_back" and restored.get("ok") is True
                                                  and load_skill(skills, REL).tree_digest == before and "release-marker-abort" not in json.dumps(shown["C"])
                                                  and "release-marker-promote" in json.dumps(shown["C"]) and mgr.breakers["canary"].blocks(now[0]))

    # D: operator freeze: the broker refuses a fresh valid capability and Hermes' enforce hook blocks every skill write
    control.freeze(opctl, Ledger(lpath), "e2e", "freeze drill")  # the broker's side
    control.freeze(cad, Ledger(os.path.join(cad, "ledger.jsonl")), "e2e", "freeze drill")  # the plugin's side (enforce hook)
    _, req3 = candidate("frozen")
    reply3 = apply_via_broker(req3)
    shown["D"] = loop(home, list(direct), ns.hermes_src)
    res["D_frozen_refuses_broker_and_hermes"] = reply3.get("ok") is False and "frozen" in reply3.get("error", "") and "frozen" in json.dumps(shown["D"])

    from caduceus.readback import verify_file

    head = verify_file(lpath)
    out = {"results": res, "ledger_entries": head.get("size"), "evidence": "synthetic lanes; real Hermes loop, plugin dispatch and tools; scripted model",
           "model_saw": {k: {i: v[:140] for i, v in d.items()} for k, d in shown.items()}}
    print(json.dumps(out, indent=1))
    shutil.rmtree(tmp, ignore_errors=True)
    ok = all(res.values())
    print("E2E-RELEASE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

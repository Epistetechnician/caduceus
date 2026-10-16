"""Supervised-apply drills (PRD 12, Phase 3 exit: "drills pass").

Runs the whole Phase 3 path on a sandbox copy of one real skill:

  promote   propose -> admit -> capability -> queue -> revalidate -> broker
            apply -> canary (clean window) -> promote -> lineage manifest
  rollback  same, but the canary trips an abort condition -> breaker opens ->
            broker restores the pre-apply snapshot -> tree digest == parent
  frozen    operator freeze -> broker refuses a fresh, valid capability
  replay    the capability from `promote` is presented again -> refused

Lane evidence in a drill is synthetic and labelled so in the ledger
(`audit_note` event `drill`); drills test the mechanism, not a skill. The
real Hermes home is only read (to copy the skill).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Dict

from . import control
from .admission import REQUIRED_LANES, AdmissionPolicy, evaluate
from .broker import apply_via_broker
from .digest import sha256_hex
from .ledger import Ledger
from .propose import default_preregistration, propose_skill
from .refinery import Refinery
from .release import Breaker, Budget, CanaryPlan, Release, ReleaseManager
from .skills import load_skill

PLAN = CanaryPlan(window=3, abort_if=(("errors", 0), ("p99_us", 60000)))


def run_drills(skills_root: str, rel_dir: str) -> Dict[str, Any]:
    tmp = tempfile.mkdtemp(prefix="caduceus-drill-")
    try:
        return _run(tmp, skills_root, rel_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: str, skills_root: str, rel_dir: str) -> Dict[str, Any]:
    root, store, ctl = (os.path.join(tmp, d) for d in ("root", "store", "ctl"))
    shutil.copytree(os.path.join(skills_root, rel_dir), os.path.join(root, "skills", rel_dir),
                    ignore=shutil.ignore_patterns(".*"))
    lpath = os.path.join(tmp, "ledger.jsonl")
    Ledger(lpath).append("audit_note", {"event": "drill", "skill": rel_dir, "evidence": "synthetic"})
    sandbox_skills = os.path.join(root, "skills")
    parent = load_skill(sandbox_skills, rel_dir).tree_digest
    now = [int(time.time())]
    mgr = ReleaseManager(Ledger(lpath), lambda: now[0], Budget(100, 3600),
                         {"canary": Breaker("canary", ttl_s=300)}, max_evidence_age_s=600)
    policy = AdmissionPolicy()
    evidence = {lane: {"status": "Checked", "detail": "drill"} for lane in REQUIRED_LANES}

    def candidate(tag: str):
        prop = os.path.join(tmp, "proposals", tag, os.path.basename(rel_dir))
        shutil.copytree(os.path.join(sandbox_skills, rel_dir), prop)
        with open(os.path.join(prop, "SKILL.md"), "a", encoding="utf-8") as fh:
            fh.write("\n<!-- caduceus drill %s -->\n" % tag)
        cand, writes = propose_skill(sandbox_skills, rel_dir, prop, "drill-proposer",
                                     default_preregistration(sha256_hex(b"drill-holdout"), 40, [1, 2]),
                                     candidate_id="drill-" + tag)
        verdict = evaluate(cand, policy, evidence)
        token = Refinery(root, store, Ledger(lpath), clock=lambda: int(time.time())).issue_capability(cand, verdict, 600)
        req = {"root": root, "store": store, "ledger": lpath, "control_dir": ctl,
               "candidate": cand.as_dict(), "verdict": verdict.as_dict(), "token": token,
               "writes": dict(writes),
               "protected": {}, "keyring": ""}
        return cand, verdict, req

    def stage(cand, rid):
        mgr.ledger = Ledger(lpath)
        mgr.enqueue(Release(rid, cand.digest(), cand.parent_digest, policy.digest(), now[0], PLAN, 10))
        return mgr.revalidate(rid, load_skill(sandbox_skills, rel_dir).tree_digest, policy.digest())

    results: Dict[str, Any] = {}

    # promote drill
    cand, _, req = candidate("promote")
    ok = stage(cand, "promote") == "revalidated"
    reply = apply_via_broker(req)
    ok = ok and reply.get("applied") is True
    mgr.ledger = Ledger(lpath)
    mgr.start_canary("promote")
    for _ in range(PLAN.window):
        mgr.observe("promote", {"errors": 0, "p99_us": 42000})
    mgr.promote("promote")
    promoted_tree = load_skill(sandbox_skills, rel_dir).tree_digest
    from . import lineage

    lineage.record_promotion(Ledger(lpath), rel_dir, "tenant", "drill", parent, promoted_tree, cand.digest())
    results["promote"] = (ok and mgr.releases["promote"].state == "promoted" and promoted_tree != parent
                          and lineage.head(Ledger(lpath), rel_dir, "tenant", "drill") == promoted_tree)
    results["replay"] = apply_via_broker(req).get("ok") is False

    # rollback drill
    cand2, _, req2 = candidate("rollback")
    before = load_skill(sandbox_skills, rel_dir).tree_digest
    staged = stage(cand2, "rollback") == "revalidated"
    reply2 = apply_via_broker(req2)
    mgr.ledger = Ledger(lpath)
    mgr.start_canary("rollback")
    state = mgr.observe("rollback", {"errors": 3, "p99_us": 42000})
    restored = apply_via_broker({"op": "restore", "root": root, "store": store, "ledger": lpath,
                                 "snapshot_id": reply2.get("snapshot_id", ""), "reason": "canary_abort"})
    after = load_skill(sandbox_skills, rel_dir).tree_digest
    results["rollback"] = (staged and reply2.get("applied") is True and state == "rolled_back"
                           and restored.get("ok") is True and after == before
                           and mgr.breakers["canary"].blocks(now[0]))

    # frozen drill
    control.freeze(ctl, Ledger(lpath), "drill", "frozen drill")
    _, _, req3 = candidate("frozen")
    reply3 = apply_via_broker(req3)
    results["frozen"] = reply3.get("ok") is False and "frozen" in reply3.get("error", "")

    from .readback import verify_file

    head = verify_file(lpath)
    return {"drills": results, "ok": all(results.values()), "ledger_entries": head.get("size"),
            "skill": rel_dir, "evidence": "synthetic (mechanism drill, not a skill result)"}

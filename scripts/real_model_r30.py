"""r30: Caduceus vs Hermes core skills.write_approval, counting items a human must review. Spends model calls through the Hermes login.
See prereg/r30-vs-core-write-approval.json.
Run: <hermes>/venv/bin/python scripts/real_model_r30.py --hermes-src ~/.hermes/hermes-agent [--n 10] [--out ledgers/r30.result.json]"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("r29", os.path.join(REPO, "scripts", "real_model_r29.py"))
r29 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r29)
CELLS = [  # (cell, prompt, mode, marker)
    ("core_A_benign", r29.P_A, "core", None),
    ("core_B_poisoned", r29.P_B, "core", r29.MARK_B),
    ("core_C_user_instructed", r29.P_C, "core", r29.MARK_C),
    ("cad_A_benign_no_web", r29.P_A, "caduceus", None),
]


def run_one(cell, rt, provider, model):
    from hermes_cli.plugins import get_plugin_manager
    from run_agent import AIAgent

    name, prompt, mode, marker = cell
    tmp = tempfile.mkdtemp(prefix="caduceus-r30-")
    home = os.path.join(tmp, "hermes_home")
    os.makedirs(os.path.join(home, "skills"))
    real_home = os.path.realpath(os.path.expanduser("~/.hermes"))
    assert not os.path.realpath(home).startswith(real_home)
    os.environ["HERMES_HOME"] = home
    base = os.path.join(home, "caduceus")
    if mode == "caduceus":
        shutil.copytree(os.path.join(REPO, "plugin", "caduceus_governance"), os.path.join(home, "plugins", "caduceus_governance"), ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(base)
        json.dump({"mode": "boundary"}, open(os.path.join(base, "mode.json"), "w"))
        open(os.path.join(home, "config.yaml"), "w").write("plugins:\n  enabled:\n    - caduceus-governance\n")
    else:
        open(os.path.join(home, "config.yaml"), "w").write("plugins:\n  enabled: []\nskills:\n  write_approval: true\n")
    get_plugin_manager().discover_and_load(force=True)
    held = 0
    if mode == "caduceus":
        from caduceus.transition import Kernel, Policy, load_policy
        Kernel(os.path.join(home, "skills"), base, load_policy(base) or Policy()).reconcile()
    agent = r29.make_agent(AIAgent, rt, provider, model, enabled_toolsets=["skills"], session_id="r30-" + uuid.uuid4().hex[:8])
    reply = agent.run_conversation(prompt)
    files, text = r29.skills_text(home)
    if mode == "caduceus":
        from caduceus.transition import Kernel, Policy, load_policy
        held = len(Kernel(os.path.join(home, "skills"), base, load_policy(base) or Policy()).held())
    else:
        pend = os.path.join(home, "pending", "skills")
        held = len([f for f in os.listdir(pend) if f.endswith(".json")]) if os.path.isdir(pend) else 0
    out = {"cell": name, "mode": mode, "human_items": held, "live_skill": any(f.endswith("SKILL.md") for f in files),
           "marker_live": (marker in text.lower()) if marker else None, "reply": str((reply or {}).get("final_response", ""))[:120]}
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-src", required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--model", default="anthropic:claude-haiku-4-5-20251001")
    ap.add_argument("--out")
    ns = ap.parse_args()
    sys.dont_write_bytecode = True
    os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    sys.path.insert(0, os.path.abspath(os.path.expanduser(ns.hermes_src)))
    sys.path.insert(0, REPO)
    import anthropic  # noqa: F401
    import openai  # noqa: F401
    real_home = os.path.realpath(os.path.expanduser("~/.hermes"))
    from hermes_cli.runtime_provider import resolve_runtime_provider

    provider, model = ns.model.split(":", 1)
    os.environ["HERMES_HOME"] = real_home
    rt = resolve_runtime_provider(requested=provider, target_model=model)
    runs = []
    for cell in CELLS:
        for i in range(ns.n):
            try:
                r = run_one(cell, rt, provider, model)
            except Exception as exc:  # noqa: BLE001 - a failed sample is data, never a pass
                r = {"cell": cell[0], "error": type(exc).__name__ + ": " + str(exc)[:120]}
            runs.append(r)
            print(r["cell"], i, {k: r.get(k) for k in ("human_items", "live_skill", "marker_live", "error")}, flush=True)
            os.environ["HERMES_HOME"] = real_home
    summ = {}
    for r in runs:
        s = summ.setdefault(r["cell"], {"n": 0, "errors": 0, "runs_with_items": 0, "live_skill": 0, "marker_live": 0, "total_items": 0})
        s["n"] += 1
        if "error" in r:
            s["errors"] += 1
            continue
        s["runs_with_items"] += r["human_items"] > 0
        s["total_items"] += r["human_items"]
        s["live_skill"] += bool(r["live_skill"])
        s["marker_live"] += bool(r["marker_live"])
    res = {"model": ns.model, "n": ns.n, "prereg_sha256": hashlib.sha256(open(os.path.join(REPO, "prereg", "r30-vs-core-write-approval.json"), "rb").read()).hexdigest(), "summary": summ, "runs": runs}
    print(json.dumps(summ, indent=1, sort_keys=True))
    if ns.out:
        json.dump(res, open(ns.out, "w"), indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

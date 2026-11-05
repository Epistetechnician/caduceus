"""Probe (PRD open question 1): which session_id do the hooks see when Hermes' background-review fork writes a skill, compared with the parent agent's?

A throw-away home, a recorder plugin that logs the ids of every hook the fork triggers, the real `agent.background_review._run_review_in_thread`, a stub model on loopback.
Run with the Hermes venv python from the repo root:  <hermes>/venv/bin/python scripts/probe_fork_session.py --hermes-src ~/.hermes/hermes-agent"""
import argparse
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
RECORDER = '''
import json, os
LOG = os.path.join(os.environ["HERMES_HOME"], "hook_ids.jsonl")
def _rec(hook):
    def f(tool_name="", session_id=None, task_id=None, action=None, skill_name=None, **kw):
        with open(LOG, "a") as fh:
            fh.write(json.dumps({"hook": hook, "tool": tool_name or skill_name, "session_id": session_id, "task_id": task_id}) + "\\n")
        return None
    return f
def register(ctx):
    for h in ("pre_tool_call", "post_tool_call", "transform_tool_result", "on_skill_lifecycle"):
        ctx.register_hook(h, _rec(h))
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-src", required=True)
    ns = ap.parse_args()
    sys.dont_write_bytecode = True
    os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    tmp = tempfile.mkdtemp(prefix="caduceus-forkprobe-")
    home = os.path.join(tmp, "hermes_home")
    os.makedirs(os.path.join(home, "skills"))
    assert not os.path.realpath(home).startswith(os.path.realpath(os.path.expanduser("~/.hermes")))
    os.environ["HERMES_HOME"] = home
    rec = os.path.join(home, "plugins", "idrecorder")
    os.makedirs(rec)
    open(os.path.join(rec, "plugin.yaml"), "w").write("name: idrecorder\nversion: 0.0.1\ndescription: records hook ids\nprovides_hooks:\n  - pre_tool_call\n  - post_tool_call\n  - transform_tool_result\n  - on_skill_lifecycle\n")
    open(os.path.join(rec, "__init__.py"), "w").write(RECORDER)
    open(os.path.join(home, "config.yaml"), "w").write("plugins:\n  enabled:\n    - idrecorder\n")
    sys.path.insert(0, os.path.abspath(os.path.expanduser(ns.hermes_src)))
    sys.path.insert(0, REPO)
    from probe_background_review import _respond
    from stub_openai import serve

    server, base_url, calls = serve(_respond)
    from hermes_cli.plugins import get_plugin_manager
    from run_agent import AIAgent
    from agent import background_review as br

    get_plugin_manager().discover_and_load(force=True)
    agent = AIAgent(base_url=base_url, api_key="stub-key", provider="custom", model="stub-model", quiet_mode=True, skip_context_files=True, skip_memory=True)
    parent = getattr(agent, "session_id", None)
    br._run_review_in_thread(agent, [{"role": "user", "content": "Remember how to run the probe."}, {"role": "assistant", "content": "Done."}],
                             "Review the conversation above and save a skill if useful.")
    log = os.path.join(home, "hook_ids.jsonl")
    rows = [json.loads(x) for x in open(log)] if os.path.exists(log) else []
    sm = [r for r in rows if r["tool"] in ("skill_manage",) or r["hook"] == "on_skill_lifecycle"]
    out = {"parent_session_id": parent, "rows": sm, "fork_session_ids": sorted({str(r["session_id"]) for r in sm}),
           "fork_shares_parent_session": all(r["session_id"] == parent for r in sm) if sm else None,
           "any_empty": any(not r["session_id"] for r in sm)}
    print(json.dumps(out, indent=1))
    server.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if sm else 1


if __name__ == "__main__":
    sys.exit(main())

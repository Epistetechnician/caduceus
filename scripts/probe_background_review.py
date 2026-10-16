"""Probe: does Hermes' background-review fork route skill_manage through pre_tool_call?

Run with the Hermes venv interpreter, from this repository root:

    ~/.hermes/hermes-agent/venv/bin/python scripts/probe_background_review.py \
        --hermes-src ~/.hermes/hermes-agent

What it does (no model call, no network beyond 127.0.0.1):
  1. Creates a throw-away HERMES_HOME in a temp dir and refuses to run if
     HERMES_HOME would resolve to a real home.
  2. Installs plugin/caduceus_governance there in observe mode.
  3. Serves a canned OpenAI-compatible chat-completions stub on 127.0.0.1
     that answers the first request with one `skill_manage` create call and
     every later request with plain text.
  4. Builds a parent AIAgent pointed at the stub and calls the real
     `agent.background_review._run_review_in_thread` (the fork entry point).
  5. Checks the Caduceus ledger in the throw-away home for an entry whose
     args_digest matches the stub's call. In observe mode the entry is
     `skill_mutation_intent` and the skill must be written; in enforce mode
     (no capability issued) it is `skill_mutation_blocked` and the skill must
     not exist.

Hermes source is imported read-only (bytecode writes disabled). Exit code 0
means the fork's skill_manage call reached the plugin hook and the
plugin's directive was honoured.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_NAME = "caduceus-probe-skill"
SKILL_BODY = (
    "---\nname: %s\ndescription: Throw-away skill written by the Caduceus probe.\n---\n\n"
    "# Probe\n\nCreated by the background-review fork under test.\n" % SKILL_NAME
)
STUB_ARGS = {
    "operations": [
        {"action": "create", "name": SKILL_NAME, "category": "probe", "content": SKILL_BODY}
    ]
}


def _respond(body, calls):
    tools = [t.get("function", {}).get("name") for t in body.get("tools") or []]
    if len(calls) == 1 and "skill_manage" in tools:
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_probe_1", "type": "function",
            "function": {"name": "skill_manage", "arguments": json.dumps(STUB_ARGS)},
        }]}
    return {"role": "assistant", "content": "Nothing else to save."}


def _install(home: str, mode: str) -> None:
    import shutil

    dst = os.path.join(home, "plugins", "caduceus_governance")
    shutil.copytree(os.path.join(REPO, "plugin", "caduceus_governance"), dst,
                    ignore=shutil.ignore_patterns("__pycache__"))
    os.makedirs(os.path.join(home, "caduceus"))
    with open(os.path.join(home, "caduceus", "mode.json"), "w") as fh:
        json.dump({"mode": mode}, fh)
    with open(os.path.join(home, "config.yaml"), "w") as fh:
        fh.write("plugins:\n  enabled:\n    - caduceus_governance\n    - caduceus-governance\n")
    os.makedirs(os.path.join(home, "skills"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-src", required=True)
    ap.add_argument("--mode", choices=("observe", "enforce"), default="observe")
    ap.add_argument("--keep", action="store_true", help="print and keep the temp home")
    ns = ap.parse_args()

    sys.dont_write_bytecode = True
    tmp = tempfile.mkdtemp(prefix="caduceus-probe-")
    home = os.path.join(tmp, "hermes_home")
    os.makedirs(home)
    real = os.path.realpath(os.path.expanduser("~/.hermes"))
    if os.path.realpath(home).startswith(real):
        print("refusing: temp home resolves inside a real Hermes home", file=sys.stderr)
        return 2
    os.environ["HERMES_HOME"] = home
    for var in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "HERMES_PROFILE"):
        os.environ.pop(var, None)
    _install(home, ns.mode)

    from stub_openai import serve

    server, base_url, calls = serve(_respond)

    sys.path.insert(0, os.path.abspath(os.path.expanduser(ns.hermes_src)))
    sys.path.insert(0, REPO)
    from hermes_cli.plugins import get_plugin_manager
    from run_agent import AIAgent
    from agent import background_review as br

    pm = get_plugin_manager()
    pm.discover_and_load(force=True)
    loaded = sorted(getattr(pm, "_plugins", {}) or {})
    print("plugins loaded:", loaded)

    agent = AIAgent(
        base_url=base_url, api_key="stub-key", provider="custom", model="stub-model",
        quiet_mode=True, skip_context_files=True, skip_memory=True,
    )
    snapshot = [
        {"role": "user", "content": "Remember how to run the probe."},
        {"role": "assistant", "content": "Done."},
    ]
    br._run_review_in_thread(agent, snapshot, "Review the conversation above and save a skill if useful.")

    from plugin.caduceus_governance import args_digest
    from caduceus.ledger import Ledger

    want = args_digest("skill_manage", STUB_ARGS)
    ledger_path = os.path.join(home, "caduceus", "ledger.jsonl")
    event = "skill_mutation_intent" if ns.mode == "observe" else "skill_mutation_blocked"
    intents = []
    if os.path.exists(ledger_path):
        intents = [e["body"] for e in Ledger(ledger_path).find("audit_note")
                   if e["body"].get("event") == event]
    skill_written = any(SKILL_NAME in d for d, _, _ in os.walk(os.path.join(home, "skills")))
    hit = any(b.get("args_digest") == want for b in intents)
    ok = hit and (skill_written if ns.mode == "observe" else not skill_written)
    report = {
        "mode": ns.mode,
        "stub_chat_requests": len(calls),
        "ledger_entries_for_event": len(intents),
        "entry_matches_fork_call": hit,
        "fork_executed_skill_manage": skill_written,
        "hermes_home": home if ns.keep else "(deleted)",
    }
    print(json.dumps(report, indent=2))
    server.shutdown()
    if not ns.keep:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

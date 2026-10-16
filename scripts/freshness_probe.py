"""Differential freshness check against real Hermes memory injection (PRD 6.2, amendment 2).

    ~/.hermes/hermes-agent/venv/bin/python scripts/freshness_probe.py \
        --hermes-src ~/.hermes/hermes-agent --out baselines/

Frozen corpus of seeded cases in a throw-away HERMES_HOME, stub model on
127.0.0.1 (no model call). For each case a unique marker is written into
MEMORY.md or USER.md, a session runs a turn, the file is edited, the same
session runs another turn, then a new session runs. The stub captures the
system prompt each turn. Caduceus' checker (`freshness.classify`, two
independent hash implementations) classifies each point:

  * turn 2 of the first session must still show the old marker
    (`drift_within_session_expected`: Hermes' prompt-cache rule, not an incident);
  * the new session must show the new marker and not the old one; anything
    else is `stale_at_boundary`, an incident.

Negative control: a seeded stale injector (the new session is handed the
previous session's system prompt) must be flagged `stale_at_boundary`.
Pass = zero silent stale injections and the negative control caught.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.dont_write_bytecode = True

from caduceus import freshness  # noqa: E402
from stub_openai import serve  # noqa: E402

CASES = (("MEMORY.md", "edit"), ("MEMORY.md", "append"), ("USER.md", "edit"), ("USER.md", "remove"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-src", required=True)
    ap.add_argument("--out", default=os.path.join(REPO, "baselines"))
    ns = ap.parse_args()
    src = os.path.abspath(os.path.expanduser(ns.hermes_src))
    tmp = tempfile.mkdtemp(prefix="caduceus-fresh-")
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, "skills"))
    with open(os.path.join(home, "config.yaml"), "w") as fh:
        fh.write("plugins:\n  enabled: []\n")
    os.environ["HERMES_HOME"] = home
    os.chdir(tmp)

    prompts = []

    def respond(body, calls):
        prompts.append(" ".join(str(m.get("content")) for m in body.get("messages", []) if m.get("role") == "system"))
        return {"role": "assistant", "content": "ok"}

    server, base_url, _ = serve(respond)
    sys.path.insert(0, src)
    from run_agent import AIAgent
    from tools.memory_tool import get_memory_dir

    mem_dir = str(get_memory_dir())
    os.makedirs(mem_dir, exist_ok=True)

    def agent(sid):
        return AIAgent(base_url=base_url, api_key="stub", provider="custom", model="stub-model",
                       quiet_mode=True, skip_context_files=True, skip_background_review=True, session_id=sid)

    def turn(a, history):
        before = len(prompts)
        a.run_conversation("hello", conversation_history=list(history))
        return prompts[before]

    results = []
    for i, (fname, change) in enumerate(CASES):
        for f in ("MEMORY.md", "USER.md"):
            with open(os.path.join(mem_dir, f), "w") as fh:
                fh.write("baseline note %s %d" % (f.split(".")[0].lower(), i))
        path = os.path.join(mem_dir, fname)
        old, new = "caduceusold%dx" % i, "caduceusnew%dx" % i
        with open(path, "w") as fh:
            fh.write("keep this note\n§\n" + old + " fact")
        paths = [os.path.join(mem_dir, "MEMORY.md"), os.path.join(mem_dir, "USER.md")]
        snap = freshness.make_snapshot(paths, now=0)
        a = agent("fresh-%d-a" % i)
        p1 = turn(a, [])
        with open(path, "w") as fh:
            if change == "edit":
                fh.write("keep this note\n§\n" + new + " fact")
            elif change == "append":
                fh.write("keep this note\n§\n" + old + " fact\n§\n" + new + " fact")
            else:
                fh.write("keep this note\n§\n" + new + " fact removed old")
        p2 = turn(a, [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "ok"}])
        within = freshness.classify(snap, paths, session_boundary=False)
        b = agent("fresh-%d-b" % i)
        p3 = turn(b, [])
        snap_b = freshness.make_snapshot(paths, now=1)
        # What session B injected, judged by markers: new present; old present only if the edit kept it.
        expect_old = change == "append"
        injected_current = new in p3 and ((old in p3) == expect_old)
        boundary = freshness.classify(snap_b if injected_current else snap, paths, session_boundary=True)
        stale_control = freshness.classify(snap, paths, session_boundary=True)  # seeded stale injector
        results.append({
            "case": "%s:%s" % (fname, change),
            "turn1_has_old": old in p1,
            "turn2_frozen": old in p2 and new not in p2,
            "within_session": within["status"],
            "new_session_current": injected_current,
            "boundary": boundary["status"],
            "negative_control": stale_control["status"],
        })
    server.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

    silent_stale = [r["case"] for r in results if not r["new_session_current"] or r["boundary"] != "ok"]
    controls_caught = all(r["negative_control"] == "stale_at_boundary" for r in results)
    frozen_ok = all(r["turn1_has_old"] and r["turn2_frozen"] and r["within_session"] == "drift_within_session_expected"
                    for r in results)
    commit = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out = {
        "schema": "caduceus.baseline.freshness.v1",
        "hermes_commit": commit,
        "cases": results,
        "silent_stale_injections": silent_stale,
        "negative_controls_caught": controls_caught,
        "within_session_frozen_as_designed": frozen_ok,
        "passed": not silent_stale and controls_caught and frozen_ok,
        "nonclaim": "LocalDevelopmentEvidenceOnly",
    }
    os.makedirs(ns.out, exist_ok=True)
    path = os.path.join(ns.out, "freshness-%s.json" % commit[:10])
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({k: out[k] for k in ("passed", "silent_stale_injections", "negative_controls_caught",
                                          "within_session_frozen_as_designed")}, indent=2))
    print(json.dumps(results, indent=1))
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

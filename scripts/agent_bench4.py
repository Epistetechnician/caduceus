"""exp-028 admission-gate A/B on the exp-027 hidden-test tasks (VM, same sandbox as exp-027).
The agent must add a regression test. A deterministic gate (no model) admits the patch iff
  G1 a new test file exists, G2 it FAILS on the parent source, G3 it PASSES on the patched tree,
  G4 the repo's existing suite does not regress (NotRun if the parent suite is not green within the limit).
On rejection the agent is resumed with the gate's verdict (<= RETRIES times). Hidden tests score every attempt:
attempt 1 = control (no gate feedback), final attempt = treatment. Paired within the same trajectory.
Usage: agent_bench4.py run <label> <model> <max_turns> <workers> | report <label>"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_bench2 as b2  # noqa: E402
from caduceus import realbugs  # noqa: E402

OUT, WORKROOT, RETRIES = b2.OUT, b2.WORKROOT, 2
PROMPT = b2.AGENT_PROMPT.replace("Stop when you are confident the issue is resolved.",
    "Also add ONE new regression test file (name it test_issue_<topic>.py, in the repository's test directory) that fails on the original code and passes with your fix. "
    "Your patch is admitted only if that test fails before and passes after your fix and the existing tests do not regress. Stop when you are confident the issue is resolved.")


def pytest(image, tree, args, timeout=180):
    cmd = ["docker", "run", "--rm", "--network", "none", "--memory", "2g", "--cpus", "1", "-v", tree + ":/w", "-w", "/w", "-e", "PYTHONPATH=/w:/w/src",
           "-e", "PYTHONDONTWRITEBYTECODE=1", image, "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        return None, "timeout"


def pyset(root):
    return {p: open(os.path.join(root, p), "rb").read() for p in glob.glob("**/*.py", root_dir=root, recursive=True)}


def gate(image, work, parent_tree, before, hidden):
    new = sorted(p for p in pyset(work) if p not in before and os.path.basename(p).startswith("test") and p not in hidden)
    if not new:
        return {"admit": False, "failed": "G1", "msg": "No new regression test file was added. Add test_issue_<topic>.py."}
    tmp = tempfile.mkdtemp(prefix="gate-")
    try:
        shutil.copytree(parent_tree, tmp, dirs_exist_ok=True)
        for p in new:
            os.makedirs(os.path.dirname(os.path.join(tmp, p)), exist_ok=True)
            shutil.copy(os.path.join(work, p), os.path.join(tmp, p))
        rc, out = pytest(image, tmp, new)
        if rc != 1:
            return {"admit": False, "failed": "G2", "new": new, "msg": "Your new test must FAIL (assertion failure, not an error) on the ORIGINAL code; it did not (exit %s). Output tail:\n%s" % (rc, out[-700:])}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rc, out = pytest(image, work, new)
    if rc != 0:
        return {"admit": False, "failed": "G3", "new": new, "msg": "Your new test must PASS with your fix; it did not (exit %s). Output tail:\n%s" % (rc, out[-700:])}
    bf = parent_tree + ".base"
    if os.path.exists(bf):
        brc = int(open(bf).read())
    else:
        brc, _ = pytest(image, parent_tree, ["-x"])
        brc = -1 if brc is None else brc
        open(bf, "w").write(str(brc))
    if brc != 0:
        return {"admit": True, "g4": "NotRun", "new": new, "msg": "admitted (existing suite not green on parent; regression check not run)"}
    rc, out = pytest(image, work, ["-x", *[f"--ignore={p}" for p in new]], 180)
    if rc != 0:
        return {"admit": False, "failed": "G4", "new": new, "msg": "The existing tests regress with your change (exit %s). Output tail:\n%s" % (rc, out[-700:])}
    return {"admit": True, "g4": "Pass", "new": new, "msg": "admitted"}


def score(image, work, clone, t):
    fix = tempfile.mkdtemp(prefix="agfix-")
    snap = tempfile.mkdtemp(prefix="agsnap-")
    try:
        b2.extract(clone, t["commit"], fix)
        shutil.copytree(work, snap, dirs_exist_ok=True)
        for tf in t["tests"]:
            os.makedirs(os.path.dirname(os.path.join(snap, tf)), exist_ok=True)
            shutil.copy(os.path.join(fix, tf), os.path.join(snap, tf))
        return b2.run_pytest(image, snap, t["tests"])
    finally:
        shutil.rmtree(fix, ignore_errors=True)
        shutil.rmtree(snap, ignore_errors=True)


def run(label, model, max_turns, workers):
    st = json.load(open(os.path.join(OUT, "statements.json")))
    ts = [t for t in b2.load_tasks() if st.get(t["task_id"])]
    os.makedirs(os.path.join(OUT, label), exist_ok=True)

    def one(t):
        rp = os.path.join(OUT, label, t["task_id"] + ".json")
        if os.path.exists(rp):
            return json.load(open(rp))
        clone, image = os.path.join(b2.CLONES, t["repo"]), realbugs.image_for(t["repo"])
        parent = b2.git(clone, "rev-parse", t["commit"] + "^").strip()
        work, ptree = os.path.join(WORKROOT, label + "-" + t["task_id"]), os.path.join(WORKROOT, label + "-" + t["task_id"] + "-parent")
        for d in (work, ptree):
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d)
        t0, att, sid, cost, turns, err = time.time(), [], None, 0.0, 0, None
        try:
            b2.extract(clone, parent, work)
            b2.extract(clone, parent, ptree)
            open(os.path.join(work, ".rbimage"), "w").write(image)
            with open(os.path.join(work, "runtests.sh"), "w") as fh:
                fh.write('#!/bin/sh\nexec sudo /usr/local/bin/rb-run "$@"\n')
            os.chmod(os.path.join(work, "runtests.sh"), 0o755)
            before = pyset(work)
            prompt, extra = PROMPT.format(repo=t["repo"], statement=st[t["task_id"]]), []
            for k in range(RETRIES + 1):
                subprocess.run(["sudo", "chown", "-R", "agent:agent", work], check=True)
                try:
                    p = b2.claude(prompt, model, max_turns, work, ["--output-format", "stream-json", "--verbose", "--allowedTools", "Bash,Read,Edit,Write,Glob,Grep", *extra])
                    lines = [ln for ln in p.stdout.splitlines() if ln.strip().startswith("{")]
                    with open(os.path.join(OUT, label, "%s.a%d.stream.jsonl" % (t["task_id"], k + 1)), "w") as fh:
                        fh.write("\n".join(lines))
                    meta = json.loads(lines[-1]) if lines else {}
                    sid = meta.get("session_id") or sid
                    cost += meta.get("total_cost_usd") or 0
                    turns += meta.get("num_turns") or 0
                except subprocess.TimeoutExpired:
                    err = "agent_timeout"
                subprocess.run(["sudo", "chown", "-R", "%d:%d" % (os.getuid(), os.getgid()), work], check=True)
                for f in (".rbimage", "runtests.sh"):
                    if os.path.exists(os.path.join(work, f)):
                        os.remove(os.path.join(work, f))
                g = gate(image, work, ptree, before, set(t["tests"]))
                att.append({"attempt": k + 1, "hidden_pass": score(image, work, clone, t), "gate": {x: g[x] for x in g if x != "msg"}})
                if g["admit"] or err or not sid or k == RETRIES:
                    break
                open(os.path.join(work, ".rbimage"), "w").write(image)
                with open(os.path.join(work, "runtests.sh"), "w") as fh:
                    fh.write('#!/bin/sh\nexec sudo /usr/local/bin/rb-run "$@"\n')
                os.chmod(os.path.join(work, "runtests.sh"), 0o755)
                prompt, extra = "GATE REJECTED your patch (%s): %s\nFix this and finish." % (g["failed"], g["msg"]), ["--resume", sid]
        finally:
            subprocess.run(["sudo", "rm", "-rf", work, ptree, ptree + ".base"])
        r = {"task_id": t["task_id"], "attempts": att, "cost_usd": cost, "turns": turns, "error": err, "seconds": int(time.time() - t0)}
        json.dump(r, open(rp, "w"))
        print(t["task_id"], [(a["hidden_pass"], a["gate"].get("admit")) for a in att], round(cost, 2), flush=True)
        return r
    with ThreadPoolExecutor(workers) as ex:
        rs = list(ex.map(one, ts))
    print("DONE", label, "first", sum(r["attempts"][0]["hidden_pass"] for r in rs if r["attempts"]), "final", sum(r["attempts"][-1]["hidden_pass"] for r in rs if r["attempts"]), "/", len(rs))


if __name__ == "__main__":
    a = sys.argv
    run(a[2], a[3], int(a[4]), int(a[5]))

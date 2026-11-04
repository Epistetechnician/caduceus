"""exp-029 independent-tester admission gate on the exp-027 hidden-test tasks (VM, same sandbox as exp-027/028).
A separate tester session sees ONLY the issue statement and the parent tree (never the fixer's tree or patch) and writes a pytest file.
Oracle validity: the tester's tests must fail (exit 1) on the parent source; the tester gets one resume to repair an invalid test.
Gate on the fixer's patch: G3 the tester's tests pass on the patched tree, G4 the existing suite does not regress (NotRun if the parent suite is not green).
Rejection resumes the fixer with the pytest output tail only (never the tester's source). Hidden tests score every attempt.
Usage: agent_bench5.py run <label> <model> <max_turns> <workers>"""
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_bench2 as b2  # noqa: E402
import agent_bench4 as b4  # noqa: E402
from caduceus import realbugs  # noqa: E402

OUT, WORKROOT, RETRIES = b2.OUT, b2.WORKROOT, 2
TOOLS = ["--output-format", "stream-json", "--verbose", "--allowedTools", "Bash,Read,Edit,Write,Glob,Grep"]
TESTER_PROMPT = """You are an independent QA engineer for the Python library {repo}. The repository (current directory) is the CURRENT, unfixed code.
Write ONE new pytest file named test_qa_<topic>.py in the repository's test directory that checks the expected behaviour described in the issue below.
Rules: derive the expected behaviour only from the issue; do not edit library source; the test must FAIL on the current code because the issue's behaviour is missing or wrong
(an assertion failure, not an import/syntax error), and must pass once the issue is correctly resolved. Cover each concrete example in the issue. Use ./runtests.sh [pytest args] to run tests
(sandboxed, no internet). Stop when the file is written and fails for the right reason.

ISSUE:
{statement}"""


def prep(work, image):
    open(os.path.join(work, ".rbimage"), "w").write(image)
    with open(os.path.join(work, "runtests.sh"), "w") as fh:
        fh.write('#!/bin/sh\nexec sudo /usr/local/bin/rb-run "$@"\n')
    os.chmod(os.path.join(work, "runtests.sh"), 0o755)


def unprep(work):
    for f in (".rbimage", "runtests.sh"):
        if os.path.exists(os.path.join(work, f)):
            os.remove(os.path.join(work, f))


def session(prompt, model, turns, work, extra, label, tag):
    subprocess.run(["sudo", "chown", "-R", "agent:agent", work], check=True)
    meta, err = {}, None
    try:
        p = b2.claude(prompt, model, turns, work, TOOLS + extra)
        lines = [ln for ln in p.stdout.splitlines() if ln.strip().startswith("{")]
        with open(os.path.join(OUT, label, tag + ".stream.jsonl"), "w") as fh:
            fh.write("\n".join(lines))
        meta = json.loads(lines[-1]) if lines else {}
    except subprocess.TimeoutExpired:
        err = "timeout"
    subprocess.run(["sudo", "chown", "-R", "%d:%d" % (os.getuid(), os.getgid()), work], check=True)
    return meta, err


def run(label, model, max_turns, workers):
    st = json.load(open(os.path.join(OUT, "statements.json")))
    ts = [t for t in b2.load_tasks() if st.get(t["task_id"])]
    os.makedirs(os.path.join(OUT, label), exist_ok=True)

    def one(t):
        tid = t["task_id"]
        rp = os.path.join(OUT, label, tid + ".json")
        if os.path.exists(rp):
            return json.load(open(rp))
        clone, image = os.path.join(b2.CLONES, t["repo"]), realbugs.image_for(t["repo"])
        parent = b2.git(clone, "rev-parse", t["commit"] + "^").strip()
        base = os.path.join(WORKROOT, label + "-" + tid)
        work, qa, ptree = base, base + "-qa", base + "-parent"
        for d in (work, qa, ptree):
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d)
        t0, att, cost, turns, err, tester = time.time(), [], 0.0, 0, None, {}
        try:
            for d in (work, qa, ptree):
                b2.extract(clone, parent, d)
            before = b4.pyset(work)
            # --- independent tester (own tree, statement only)
            prep(qa, image)
            prompt, extra, sid = TESTER_PROMPT.format(repo=t["repo"], statement=st[tid]), [], None
            qa_new, valid = [], False
            for k in range(2):
                meta, e = session(prompt, model, 20, qa, extra, label, tid + ".tester%d" % (k + 1))
                sid = meta.get("session_id") or sid
                cost += meta.get("total_cost_usd") or 0
                unprep(qa)
                qa_new = sorted(p for p in b4.pyset(qa) if p not in before and os.path.basename(p).startswith("test"))
                rc, out = (None, "no test file") if not qa_new else b4.pytest(image, qa, qa_new)
                valid = rc == 1
                if valid or not sid or e:
                    break
                prep(qa, image)
                prompt, extra = "Your tests are not a valid oracle: on the CURRENT code they exited %s instead of failing with assertion failures. Output tail:\n%s\nFix the test file." % (rc, out[-600:]), ["--resume", sid]
            tester = {"valid": valid, "files": qa_new}
            qtests = {p: open(os.path.join(qa, p), "rb").read() for p in qa_new} if valid else {}
            # --- fixer loop
            prep(work, image)
            prompt, extra, sid = b2.AGENT_PROMPT.format(repo=t["repo"], statement=st[tid]), [], None
            for k in range(RETRIES + 1):
                meta, e = session(prompt, model, max_turns, work, extra, label, "%s.fix%d" % (tid, k + 1))
                err = err or e
                sid = meta.get("session_id") or sid
                cost += meta.get("total_cost_usd") or 0
                turns += meta.get("num_turns") or 0
                unprep(work)
                hidden = b4.score(image, work, clone, t)
                gate = {"admit": True, "g": "NoValidTest"}
                if valid:
                    import tempfile
                    tmp = tempfile.mkdtemp(prefix="qa-")
                    try:
                        shutil.copytree(work, tmp, dirs_exist_ok=True)
                        for p, data in qtests.items():
                            os.makedirs(os.path.dirname(os.path.join(tmp, p)), exist_ok=True)
                            open(os.path.join(tmp, p), "wb").write(data)
                        rc, out = b4.pytest(image, tmp, list(qtests))
                        if rc != 0:
                            gate = {"admit": False, "failed": "G3", "msg": "Independent tests (not shown) still fail on your patch (exit %s). Output tail:\n%s" % (rc, out[-900:])}
                        else:
                            bf = ptree + ".base"
                            if os.path.exists(bf):
                                brc = int(open(bf).read())
                            else:
                                brc, _ = b4.pytest(image, ptree, ["-x"])
                                brc = -1 if brc is None else brc
                                open(bf, "w").write(str(brc))
                            if brc != 0:
                                gate = {"admit": True, "g": "Pass", "g4": "NotRun"}
                            else:
                                rrc, rout = b4.pytest(image, tmp, ["-x", *[f"--ignore={p}" for p in qtests]])
                                gate = {"admit": True, "g": "Pass", "g4": "Pass"} if rrc == 0 else {"admit": False, "failed": "G4", "msg": "Existing tests regress (exit %s). Output tail:\n%s" % (rrc, rout[-900:])}
                    finally:
                        shutil.rmtree(tmp, ignore_errors=True)
                att.append({"attempt": k + 1, "hidden_pass": hidden, "gate": {x: gate[x] for x in gate if x != "msg"}})
                if gate["admit"] or e or not sid or k == RETRIES:
                    break
                prep(work, image)
                prompt, extra = "GATE REJECTED your patch (%s): %s\nFix this and finish." % (gate["failed"], gate["msg"]), ["--resume", sid]
        finally:
            subprocess.run(["sudo", "rm", "-rf", work, qa, ptree, ptree + ".base"])
        r = {"task_id": tid, "tester": tester, "attempts": att, "cost_usd": cost, "turns": turns, "error": err, "seconds": int(time.time() - t0)}
        json.dump(r, open(rp, "w"))
        print(tid, tester.get("valid"), [(a["hidden_pass"], a["gate"].get("admit")) for a in att], round(cost, 2), flush=True)
        return r
    with ThreadPoolExecutor(workers) as ex:
        rs = list(ex.map(one, ts))
    print("DONE", label, "first", sum(r["attempts"][0]["hidden_pass"] for r in rs if r["attempts"]), "final", sum(r["attempts"][-1]["hidden_pass"] for r in rs if r["attempts"]), "/", len(rs))


if __name__ == "__main__":
    a = sys.argv
    run(a[2], a[3], int(a[4]), int(a[5]))

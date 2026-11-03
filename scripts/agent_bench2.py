"""exp-027 hidden-test agent benchmark.
select      (local)  harder subset of the fresh-commit tasks -> tasks/fresh-v2.manifest.json + selected.jsonl
statements  (VM)     Haiku writes an issue statement per task from commit message + test diff (no source diff, no code)
run         (VM)     agent <model> works from the statement only; hidden tests (fix commit) score it
Usage: agent_bench2.py select | statements <model> | run <label> <model> <max_turns> <workers>"""
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from caduceus import realbugs  # noqa: E402
from caduceus.digest import domain_digest  # noqa: E402

FRESH = os.path.expanduser(os.environ.get("FRESH_DIR", "~/hsi-runs/realbugs/fresh"))
CLONES = os.path.expanduser(os.environ.get("CLONES", "~/hsi-runs/realbugs/repos"))
OUT = os.path.expanduser(os.environ.get("OUT", "~/agent-out2"))
WORKROOT = "/srv/agentwork"
PROXY = "http://127.0.0.1:3128"
MIN_HUNKS, MIN_LINES, PER_REPO = 2, 10, 8
STMT_PROMPT = """You write the text of an issue for a software project's bug tracker.
Below are a library's commit message and the test changes added with the fix. Write a concise issue (at most 200 words) that a maintainer could act on:
describe the current wrong or missing behaviour and the expected behaviour precisely, including every exact public name, signature, argument, return value and
example input/output that the tests depend on. Do not describe how to implement the fix, do not include source code of the fix or test code beyond short usage
examples. Output only the issue text.

{spec}"""
AGENT_PROMPT = """Resolve the following issue in this repository (Python library: {repo}). The repository is in the current directory.
Hidden tests, which you cannot see, will check your fix. Use ./runtests.sh [pytest args] to run the repository's existing tests (sandboxed, no internet);
you may also write your own quick checks. Edit library source; do not weaken or delete existing tests. Stop when you are confident the issue is resolved.

ISSUE:
{statement}"""


def git(clone, *a, binary=False):
    p = subprocess.run(["git", "-C", clone, *a], capture_output=True)
    return p.stdout if binary else p.stdout.decode("utf-8", "replace")


def extract(clone, rev, dest):
    tarfile.open(fileobj=io.BytesIO(git(clone, "archive", rev, binary=True))).extractall(dest)


def select():
    es = sorted((json.loads(ln) for f in glob.glob(os.path.join(FRESH, "fresh-*.jsonl")) for ln in open(f)), key=lambda e: (e["repo"], e["commit"]))
    keep = []
    for e in es:
        d = git(os.path.join(CLONES, e["repo"]), "diff", "-U0", e["commit"] + "^", e["commit"], "--", e["path"])
        hunks = len(re.findall(r"^@@", d, re.M))
        lines = sum(1 for ln in d.splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))
        if hunks >= MIN_HUNKS and lines >= MIN_LINES:
            keep.append(e)
    by = {}
    for e in sorted(keep, key=lambda e: domain_digest("caduceus.exp027.cap.v1", [e["repo"], e["commit"]])):
        by.setdefault(e["repo"], []).append(e)
    sel = sorted((e for v in by.values() for e in v[:PER_REPO]), key=lambda e: (e["repo"], e["commit"]))
    tasks = realbugs.build(CLONES, sel, 0, with_spec=False)
    meta = [{k: t[k] for k in ("task_id", "repo", "commit", "path", "tests", "original_digest", "mutated_digest")} for t in tasks]
    m = {"family": "real-fresh-hard", "filter": {"min_hunks": MIN_HUNKS, "min_changed_lines": MIN_LINES, "per_repo_cap": PER_REPO}, "n": len(meta),
         "repos": sorted({t["repo"] for t in meta}), "tasks": meta}
    m["manifest_digest"] = domain_digest("caduceus.bugtasks.manifest.v1", dict(m))
    json.dump(m, open(os.path.join(REPO, "tasks", "fresh-v2.manifest.json"), "w"), indent=1, sort_keys=True)
    with open(os.path.join(REPO, "tasks", "fresh-v2.selected.jsonl"), "w") as fh:
        for e in sel:
            fh.write(json.dumps(e) + "\n")
    print("selected", len(sel), "repos", len(m["repos"]), "manifest", m["manifest_digest"][:16])


def load_tasks(spec=False):
    es = [json.loads(ln) for ln in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tasks", "fresh-v2.selected.jsonl"))]
    return realbugs.build(CLONES, es, 0, with_spec=spec)


def claude(prompt, model, max_turns, cwd, extra=()):
    cmd = ["sudo", "-u", "agent", "-H", "env", "HTTPS_PROXY=" + PROXY, "HTTP_PROXY=" + PROXY, "NO_PROXY=", "claude", "-p", prompt, "--model", model,
           "--max-turns", str(max_turns), *extra]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1500)


def statements(model):
    ts = load_tasks(spec=True)
    out = os.path.join(OUT, "statements.json")
    st = json.load(open(out)) if os.path.exists(out) else {}
    os.makedirs(OUT, exist_ok=True)

    def one(t):
        if t["task_id"] in st:
            return
        p = claude(STMT_PROMPT.format(spec=t["spec"]), model, 1, "/tmp", ["--output-format", "json", "--allowedTools", ""])
        try:
            st[t["task_id"]] = json.loads(p.stdout)["result"].strip()
        except Exception:
            st[t["task_id"]] = ""
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(one, ts))
    json.dump(st, open(out, "w"), indent=1, sort_keys=True)
    print("statements", len(st), "empty", sum(1 for v in st.values() if not v), "digest", domain_digest("caduceus.exp027.statements.v1", sorted(st.items()))[:16])


def run_pytest(image, tree, tests):
    cmd = ["docker", "run", "--rm", "--network", "none", "--memory", "2g", "--cpus", "1", "-v", tree + ":/w", "-w", "/w", "-e", "PYTHONPATH=/w:/w/src",
           "-e", "PYTHONDONTWRITEBYTECODE=1", image, "python", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--no-header", *tests]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=180).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def run(label, model, max_turns, workers):
    st = json.load(open(os.path.join(OUT, "statements.json")))
    ts = [t for t in load_tasks() if st.get(t["task_id"])]
    os.makedirs(os.path.join(OUT, label), exist_ok=True)

    def one(t):
        rp = os.path.join(OUT, label, t["task_id"] + ".json")
        if os.path.exists(rp):
            return json.load(open(rp))
        clone, image = os.path.join(CLONES, t["repo"]), realbugs.image_for(t["repo"])
        parent = git(clone, "rev-parse", t["commit"] + "^").strip()
        work = os.path.join(WORKROOT, label + "-" + t["task_id"])
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work)
        t0 = time.time()
        meta, err = {}, None
        try:
            extract(clone, parent, work)
            open(os.path.join(work, ".rbimage"), "w").write(image)
            with open(os.path.join(work, "runtests.sh"), "w") as fh:
                fh.write('#!/bin/sh\nexec sudo /usr/local/bin/rb-run "$@"\n')
            os.chmod(os.path.join(work, "runtests.sh"), 0o755)
            subprocess.run(["sudo", "chown", "-R", "agent:agent", work], check=True)
            before = {p: open(os.path.join(work, p), "rb").read() for p in glob.glob("**/*.py", root_dir=work, recursive=True)}
            try:
                p = claude(AGENT_PROMPT.format(repo=t["repo"], statement=st[t["task_id"]]), model, max_turns, work,
                           ["--output-format", "stream-json", "--verbose", "--allowedTools", "Bash,Read,Edit,Write,Glob,Grep"])
                lines = [ln for ln in p.stdout.splitlines() if ln.strip().startswith("{")]
                with open(os.path.join(OUT, label, t["task_id"] + ".stream.jsonl"), "w") as fh:
                    fh.write("\n".join(lines))
                meta = json.loads(lines[-1]) if lines else {}
            except subprocess.TimeoutExpired:
                err = "agent_timeout"
            subprocess.run(["sudo", "chown", "-R", "%d:%d" % (os.getuid(), os.getgid()), work], check=True)
            for f in (".rbimage", "runtests.sh"):
                if os.path.exists(os.path.join(work, f)):
                    os.remove(os.path.join(work, f))
            fix = tempfile.mkdtemp(prefix="agfix-")
            extract(clone, t["commit"], fix)
            for tf in t["tests"]:  # hidden tests overlay
                os.makedirs(os.path.dirname(os.path.join(work, tf)), exist_ok=True)
                shutil.copy(os.path.join(fix, tf), os.path.join(work, tf))
            shutil.rmtree(fix, ignore_errors=True)
            changed = sorted(q for q in glob.glob("**/*.py", root_dir=work, recursive=True) if open(os.path.join(work, q), "rb").read() != before.get(q) and q not in t["tests"])
            passed = run_pytest(image, work, t["tests"])
        finally:
            subprocess.run(["sudo", "rm", "-rf", work])
        r = {"task_id": t["task_id"], "passed": passed, "changed_files": changed[:10], "cost_usd": meta.get("total_cost_usd"), "turns": meta.get("num_turns"),
             "is_error": meta.get("is_error", False), "error": err, "seconds": int(time.time() - t0)}
        json.dump(r, open(rp, "w"))
        print(t["task_id"], passed, r["turns"], r["cost_usd"], flush=True)
        return r
    with ThreadPoolExecutor(workers) as ex:
        rs = list(ex.map(one, ts))
    print("DONE", label, sum(r["passed"] for r in rs), "/", len(rs), "cost", sum(r["cost_usd"] or 0 for r in rs))


if __name__ == "__main__":
    a = sys.argv
    {"select": lambda: select(), "statements": lambda: statements(a[2]), "run": lambda: run(a[2], a[3], int(a[4]), int(a[5]))}[a[1]]()

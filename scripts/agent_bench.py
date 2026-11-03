"""Run a coding agent (Claude Code CLI) on the exp-026 fresh-commit tasks, on a VM with docker. One task = one fix commit.
The agent gets the parent-commit tree plus the fix commit's test files, may edit non-test files and run tests via ./runtests.sh
(docker, no network). Scoring restores the fix commit's test files and runs them in a clean container.
Usage: agent_bench.py <label> <model> <max_turns> <workers> [limit]   (env: FRESH_DIR, CLONES, OUT)"""
import glob
import io
import json
import os
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

FRESH = os.path.expanduser(os.environ.get("FRESH_DIR", "~/fresh"))
CLONES = os.path.expanduser(os.environ.get("CLONES", "~/repos"))
OUT = os.path.expanduser(os.environ.get("OUT", "~/agent-out"))
label, model, max_turns, workers = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
limit = int(sys.argv[5]) if len(sys.argv) > 5 else None

RUNTESTS = """#!/bin/sh
exec docker run --rm --network none --memory 2g --cpus 1 -v "$PWD":/w -w /w -e PYTHONPATH=/w:/w/src -e PYTHONDONTWRITEBYTECODE=1 {image} python -m pytest -q -x -p no:cacheprovider --no-header {tests} "$@"
"""
PROMPT = """You are fixing a bug or missing behaviour in this Python repository (library: {repo}).
{spec}

The test files above are already updated in the working tree. Make the library code (not the tests) satisfy them.
Run the tests with ./runtests.sh (it runs in a sandboxed container). Do not edit test files; edit only library source.
Stop when the tests pass. Be concise."""


def git(clone, *a, binary=False):
    p = subprocess.run(["git", "-C", clone, *a], capture_output=True)
    return p.stdout if binary else p.stdout.decode("utf-8", "replace")


def run_pytest(image, tree, tests):
    cmd = ["docker", "run", "--rm", "--network", "none", "--memory", "2g", "--cpus", "1", "-v", tree + ":/w", "-w", "/w",
           "-e", "PYTHONPATH=/w:/w/src", "-e", "PYTHONDONTWRITEBYTECODE=1", image, "python", "-m", "pytest", "-q", "-x",
           "-p", "no:cacheprovider", "--no-header", *tests]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=180).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def extract(clone, rev, dest):
    tarfile.open(fileobj=io.BytesIO(git(clone, "archive", rev, binary=True))).extractall(dest)


def one(task):
    tid = task["task_id"]
    res_path = os.path.join(OUT, label, tid + ".json")
    if os.path.exists(res_path):
        return json.load(open(res_path))
    clone = os.path.join(CLONES, task["repo"])
    image = realbugs.image_for(task["repo"])
    parent = git(clone, "rev-parse", task["commit"] + "^").strip()
    work = tempfile.mkdtemp(prefix="ag-")
    t0 = time.time()
    try:
        extract(clone, parent, work)
        fix = tempfile.mkdtemp(prefix="agfix-")
        extract(clone, task["commit"], fix)
        for t in task["tests"]:  # fix-commit tests overlay the parent tree
            os.makedirs(os.path.dirname(os.path.join(work, t)), exist_ok=True)
            shutil.copy(os.path.join(fix, t), os.path.join(work, t))
        with open(os.path.join(work, "runtests.sh"), "w") as fh:
            fh.write(RUNTESTS.format(image=image, tests=" ".join(task["tests"])))
        os.chmod(os.path.join(work, "runtests.sh"), 0o755)
        before = {p: open(os.path.join(work, p), "rb").read() for p in glob.glob("**/*.py", root_dir=work, recursive=True)}
        cmd = ["claude", "-p", PROMPT.format(repo=task["repo"], spec=task["spec"]), "--model", model, "--max-turns", str(max_turns),
               "--allowedTools", "Bash,Read,Edit,Write,Glob,Grep", "--output-format", "json"]
        try:
            p = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=1200)
            meta = json.loads(p.stdout) if p.stdout.strip().startswith("{") else {"error": (p.stdout + p.stderr)[-300:]}
        except subprocess.TimeoutExpired:
            meta = {"error": "agent_timeout"}
        os.remove(os.path.join(work, "runtests.sh"))
        for t in task["tests"]:  # restore the oracle's tests exactly
            shutil.copy(os.path.join(fix, t), os.path.join(work, t))
        changed = sorted(p for p in glob.glob("**/*.py", root_dir=work, recursive=True)
                         if open(os.path.join(work, p), "rb").read() != before.get(p) and p not in task["tests"])
        passed = run_pytest(image, work, task["tests"])
        shutil.rmtree(fix, ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    r = {"task_id": tid, "passed": passed, "changed_files": changed[:10], "touched_other_files": [c for c in changed if c != task["path"]][:10],
         "cost_usd": meta.get("total_cost_usd"), "turns": meta.get("num_turns"), "is_error": meta.get("is_error", False),
         "error": meta.get("error"), "seconds": int(time.time() - t0)}
    os.makedirs(os.path.dirname(res_path), exist_ok=True)
    json.dump(r, open(res_path, "w"))
    print(tid, passed, r["turns"], r["cost_usd"], flush=True)
    return r


if __name__ == "__main__":
    es = sorted((json.loads(ln) for f in glob.glob(os.path.join(FRESH, "fresh-*.jsonl")) for ln in open(f)), key=lambda e: (e["repo"], e["commit"]))
    tasks = realbugs.build(CLONES, es, 0, with_spec=True)[:limit]
    with ThreadPoolExecutor(workers) as ex:
        rs = list(ex.map(one, tasks))
    print("DONE", label, sum(r["passed"] for r in rs), "/", len(rs), "cost", sum(r["cost_usd"] or 0 for r in rs))

"""Mine real bug-fix commits from outside repositories (stdlib only; tests run in Docker, no network).

A candidate is a non-merge commit that changes exactly one non-test .py source file (<= MAX_CHARS)
and at least one test file. It becomes a task only if, in a sandbox, the commit's changed test files
are GREEN on the fix tree and RED when only that source file is reverted to its parent version.
Usage: mine_realbugs.py <repos_dir> <repo_name> <image> <out.jsonl> [max_commits]
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor

MAX_CHARS = 12000
TIMEOUT = 120
TEST_RE = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$")


def git(repo, *a, binary=False):
    p = subprocess.run(["git", "-C", repo, *a], capture_output=True)
    return p.stdout if binary else p.stdout.decode("utf-8", "replace")


def candidates(repo, limit):
    out = []
    since = ["--since=" + os.environ["SINCE"]] if os.environ.get("SINCE") else []
    for c in git(repo, "log", "--no-merges", "--format=%H", *since, "-n", str(limit)).split():
        names = git(repo, "diff-tree", "--no-commit-id", "--name-status", "-r", "-m", "--first-parent", c).splitlines()
        src, tests = [], []
        for ln in names:
            st, _, path = ln.partition("\t")
            if not path.endswith(".py"):
                continue
            if TEST_RE.search(path):
                if st in ("M", "A"):
                    tests.append(path)
            elif st == "M" and "/docs/" not in path and not path.startswith(("docs/", "setup.py", "conftest.py", "tasks.py")):
                src.append(path)
        if len(src) == 1 and tests:
            out.append((c, src[0], sorted(tests)))
    return out


def run_pytest(image, tree, tests):
    cmd = ["docker", "run", "--rm", "--network", "none", "--memory", "2g", "--cpus", "1", "-v", tree + ":/w", "-w", "/w",
           "-e", "PYTHONPATH=/w:/w/src", "-e", "PYTHONDONTWRITEBYTECODE=1", image,
           "python", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--no-header", *tests]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill"], capture_output=True)
        return None
    return p.returncode == 0


def check(job):
    repo, name, image, (c, src, tests) = job
    parent = git(repo, "rev-parse", c + "^").strip()
    if not parent:
        return None
    fixed = git(repo, "show", "%s:%s" % (c, src))
    old = git(repo, "show", "%s:%s" % (parent, src))
    if not old or old == fixed or len(old) > MAX_CHARS or len(fixed) > MAX_CHARS:
        return None
    tmp = tempfile.mkdtemp(prefix="rb-")
    try:
        raw = git(repo, "archive", c, binary=True)
        tarfile.open(fileobj=io.BytesIO(raw)).extractall(tmp)
        if run_pytest(image, tmp, tests) is not True:
            return None
        with open(os.path.join(tmp, src), "w") as fh:
            fh.write(old)
        if run_pytest(image, tmp, tests) is not False:
            return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"repo": name, "commit": c, "parent": parent, "path": src, "tests": tests,
            "subject": git(repo, "log", "-1", "--format=%s", c).strip()[:100]}


def main():
    root, name, image, out = sys.argv[1:5]
    limit = int(sys.argv[5]) if len(sys.argv) > 5 else 600
    repo = os.path.join(root, name)
    cands = candidates(repo, limit)
    print(name, "candidates", len(cands), flush=True)
    done = set()
    if os.path.exists(out):
        done = {json.loads(l)["commit"] for l in open(out)}
    jobs = [(repo, name, image, c) for c in cands if c[0] not in done]
    with ThreadPoolExecutor(int(os.environ.get("MINE_THREADS", "3"))) as ex, open(out, "a") as fh:
        for r in ex.map(check, jobs):
            if r:
                fh.write(json.dumps(r) + "\n")
                fh.flush()
                print("task", r["commit"][:8], r["path"], r["subject"], flush=True)
    print(name, "DONE", flush=True)


if __name__ == "__main__":
    main()

"""Real bug-fix tasks mined from outside repositories (Phase 6c, real-bug family).

A task is a historical fix commit that changed exactly one source file and its tests.
Buggy source = the file at the commit's parent; the oracle applies a proposed repair to the
commit's tree (fix tree, tests included) and runs the commit's changed test files in a network-less
Docker container. Pass iff they go green. Splits are by repository (a repo is wholly in one split).

The manifest holds metadata and digests only (commit, path, tests, source digests); sources are
read from local git clones at run time. Stdlib only; needs `git` and `docker`.
"""

from __future__ import annotations

import ast
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any, Dict, List, Sequence

from .digest import domain_digest

TIMEOUT_S = 120


def split_of_repo(repo: str, split_seed: int) -> str:
    r = int(domain_digest("caduceus.realbugs.split.v1", {"repo": repo, "seed": split_seed})[:8], 16) % 100
    return "fit" if r < 55 else "tune" if r < 70 else "assess"


def image_for(repo: str) -> str:
    return "rb-" + repo.lower() if repo == "PySnooper" else "rb-" + repo


def _git(clone: str, *a: str) -> str:
    return subprocess.run(["git", "-C", clone, *a], capture_output=True).stdout.decode("utf-8", "replace")


def build(clones_dir: str, entries: Sequence[Dict[str, Any]], split_seed: int) -> List[Dict[str, Any]]:
    """Tasks from manifest-style entries (repo, commit, path, tests). Sources come from the clones."""
    out = []
    for e in entries:
        clone = os.path.join(clones_dir, e["repo"])
        parent = _git(clone, "rev-parse", e["commit"] + "^").strip()
        old = _git(clone, "show", "%s:%s" % (parent, e["path"]))
        fixed = _git(clone, "show", "%s:%s" % (e["commit"], e["path"]))
        t = {"module": e["repo"], "repo": e["repo"], "commit": e["commit"], "path": e["path"], "tests": list(e["tests"]),
             "split": split_of_repo(e["repo"], split_seed),
             "original_digest": domain_digest("caduceus.bugtasks.src.v1", fixed),
             "mutated_digest": domain_digest("caduceus.bugtasks.src.v1", old)}
        t["task_id"] = domain_digest("caduceus.realbugs.task.v1", t)[:16]
        t["mutated_source"] = old
        out.append(t)
    return out


def verify_repair(clones_dir: str, task: Dict[str, Any], repaired_source: str) -> Dict[str, Any]:
    try:
        ast.parse(repaired_source)
    except SyntaxError:
        return {"task_id": task["task_id"], "passed": False, "reason": "syntax_error"}
    dg = domain_digest("caduceus.bugtasks.src.v1", repaired_source)
    if dg == task["mutated_digest"]:
        return {"task_id": task["task_id"], "passed": False, "reason": "unchanged"}
    clone = os.path.join(clones_dir, task["repo"])
    raw = subprocess.run(["git", "-C", clone, "archive", task["commit"]], capture_output=True).stdout
    tmp = tempfile.mkdtemp(prefix="rbv-")
    try:
        tarfile.open(fileobj=io.BytesIO(raw)).extractall(tmp)
        with open(os.path.join(tmp, task["path"]), "w") as fh:
            fh.write(repaired_source)
        cmd = ["docker", "run", "--rm", "--network", "none", "--memory", "2g", "--cpus", "1", "-v", tmp + ":/w", "-w", "/w",
               "-e", "PYTHONPATH=/w:/w/src", "-e", "PYTHONDONTWRITEBYTECODE=1", image_for(task["repo"]),
               "python", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--no-header", *task["tests"]]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
            ok, why = p.returncode == 0, "tests_green" if p.returncode == 0 else "tests_red"
        except subprocess.TimeoutExpired:
            ok, why = False, "timeout"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"task_id": task["task_id"], "passed": ok, "reason": why, "repaired_digest": dg}

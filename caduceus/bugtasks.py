"""Executable-oracle repair tasks built from this repository (Phase 6c).

A task is: a repository module with one injected, deterministic mutation that
the repository's own tests detect. A proposed repair passes only if, applied to
a fresh copy of the mutated tree, the tests that cover that module go green and
nothing but the target file changed. The oracle is the test run; no model
labels anything.

Everything is seeded and stdlib-only. Tasks bind digests of the original and
mutated source, so a verifier result is reproducible and a repair cannot be
credited to a different task. Splits are by module (a module is wholly in one
split), assigned by a digest of the module name and a split seed, so fit data
never shares a module with the sealed assessment set.

Limits, stated up front: mutations are syntactic (comparison, arithmetic,
boolean, constant, boundary); the oracle can only see what the tests see, so a
repair that passes the tests but is wrong elsewhere passes. `oracle_audit`
measures the first limit by reporting how many mutants the suite kills.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .digest import domain_digest

PKG = "caduceus"
SPLITS = ("fit", "tune", "assess")
OPS = ("cmp_flip", "arith_swap", "bool_not", "const_off_by_one", "and_or_swap")
_CMP = {ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.Gt: ast.LtE, ast.LtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
_ARITH = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv}


class TaskError(ValueError):
    pass


def _sites(tree: ast.AST) -> List[Tuple[str, int, ast.AST]]:
    """(op, ordinal, node) for every mutable site, in source order."""
    out: List[Tuple[str, int, ast.AST]] = []
    counters: Dict[str, int] = {}

    def add(op: str, node: ast.AST) -> None:
        out.append((op, counters.get(op, 0), node))
        counters[op] = counters.get(op, 0) + 1

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP:
            add("cmp_flip", node)
        elif isinstance(node, ast.BinOp) and type(node.op) in _ARITH:
            add("arith_swap", node)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            add("bool_not", node)
        elif isinstance(node, ast.Constant) and type(node.value) is int and not isinstance(node.value, bool):
            add("const_off_by_one", node)
        elif isinstance(node, ast.BoolOp):
            add("and_or_swap", node)
    return out


def _apply(tree: ast.AST, op: str, node: ast.AST) -> None:
    if op == "cmp_flip":
        node.ops[0] = _CMP[type(node.ops[0])]()  # type: ignore[attr-defined]
    elif op == "arith_swap":
        node.op = _ARITH[type(node.op)]()  # type: ignore[attr-defined]
    elif op == "bool_not":
        pass  # handled by the caller (needs the parent node)
    elif op == "const_off_by_one":
        node.value = node.value + 1  # type: ignore[attr-defined]
    elif op == "and_or_swap":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()  # type: ignore[attr-defined]


def mutate(source: str, op: str, ordinal: int) -> Optional[str]:
    """Return the mutated source, or None if the site does not exist."""
    if op not in OPS:
        raise TaskError("unknown op %r" % op)
    tree = ast.parse(source)
    sites = [(o, i, n) for (o, i, n) in _sites(tree) if o == op and i == ordinal]
    if not sites:
        return None
    node = sites[0][2]
    if op == "bool_not":
        # replace `not x` with `x` by rewriting the parent's field
        for parent in ast.walk(tree):
            for field, value in ast.iter_fields(parent):
                if value is node:
                    setattr(parent, field, node.operand)  # type: ignore[attr-defined]
                elif isinstance(value, list):
                    for k, item in enumerate(value):
                        if item is node:
                            value[k] = node.operand  # type: ignore[attr-defined]
    else:
        _apply(tree, op, node)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) + "\n"


def split_of(module: str, split_seed: int) -> str:
    d = domain_digest("caduceus.bugtasks.split.v1", {"module": module, "seed": split_seed})
    r = int(d[:8], 16) % 100
    return "fit" if r < 50 else "tune" if r < 70 else "assess"


def covering_tests(repo: str, module: str) -> List[str]:
    """Test modules that reference the target module by name, sorted."""
    pat = re.compile(r"\b(?:from\s+caduceus\s+import\s+[^\n]*\b%s\b|caduceus\.%s\b|from\s+\.%s\b)" % ((re.escape(module),) * 3))
    found = []
    tdir = os.path.join(repo, "tests")
    for fn in sorted(os.listdir(tdir)):
        if fn.startswith("test_") and fn.endswith(".py") and fn != "test_bugtasks.py":
            with open(os.path.join(tdir, fn)) as fh:
                if pat.search(fh.read()):
                    found.append("tests.%s" % fn[:-3])
    return found


def _copy_tree(repo: str, dst: str) -> str:
    root = os.path.join(dst, "repo")
    shutil.copytree(repo, root, ignore=shutil.ignore_patterns(".git", "__pycache__", "ledgers", "*.pyc", "node_modules"))
    return root


TIMEOUT_S = 60


def run_tests(root: str, tests: Sequence[str], timeout: int = TIMEOUT_S) -> Tuple[bool, str]:
    """(green, tail). A timeout is reported as red with tail "TIMEOUT"."""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "HOME": root}
    try:
        proc = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", *tests],
                              cwd=root, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-400:]


def make_task(repo: str, module: str, op: str, ordinal: int, split_seed: int) -> Optional[Dict[str, Any]]:
    """Build one task if the mutation applies, changes behaviour, and the covering tests detect it."""
    rel = "%s/%s.py" % (PKG, module)
    with open(os.path.join(repo, rel)) as fh:
        orig = fh.read()
    mutated = mutate(orig, op, ordinal)
    canon = ast.unparse(ast.parse(orig)) + "\n"
    if mutated is None or mutated == canon:
        return None
    tests = covering_tests(repo, module)
    if not tests:
        return None
    tmp = tempfile.mkdtemp()
    try:
        root = _copy_tree(repo, tmp)
        with open(os.path.join(root, rel), "w") as fh:
            fh.write(canon)
        ok, _ = run_tests(root, tests)
        if not ok:
            return None  # baseline (normalised) source must itself pass
        with open(os.path.join(root, rel), "w") as fh:
            fh.write(mutated)
        ok, tail = run_tests(root, tests)
        if ok or tail == "TIMEOUT":
            return None  # survived (oracle cannot see it) or hangs (unbounded oracle cost): not a task
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    task = {
        "module": module, "path": rel, "op": op, "ordinal": ordinal, "tests": tests,
        "split": split_of(module, split_seed),
        "original_digest": domain_digest("caduceus.bugtasks.src.v1", canon),
        "mutated_digest": domain_digest("caduceus.bugtasks.src.v1", mutated),
    }
    task["task_id"] = domain_digest("caduceus.bugtasks.task.v1", task)[:16]
    task["mutated_source"] = mutated
    return task


def generate(repo: str, modules: Sequence[str], split_seed: int, per_module: int = 3) -> List[Dict[str, Any]]:
    """Deterministic task list: for each module, the first `per_module` killed mutants in (op, ordinal) order."""
    tasks: List[Dict[str, Any]] = []
    for module in sorted(modules):
        with open(os.path.join(repo, PKG, module + ".py")) as fh:
            tree = ast.parse(fh.read())
        got = 0
        for op, ordinal, _ in sorted(_sites(tree), key=lambda s: (OPS.index(s[0]), s[1])):
            if got >= per_module:
                break
            t = make_task(repo, module, op, ordinal, split_seed)
            if t:
                tasks.append(t)
                got += 1
    return tasks


def verify_repair(repo: str, task: Dict[str, Any], repaired_source: str, other_files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The oracle. Pass iff only the target file changes and its covering tests go green.

    `other_files` is any additional path the proposer tried to write; any entry
    makes the repair invalid (tests and other modules are not repairable).
    """
    if other_files:
        return {"task_id": task["task_id"], "passed": False, "reason": "touched_other_files:" + ",".join(sorted(other_files))}
    try:
        ast.parse(repaired_source)
    except SyntaxError:
        return {"task_id": task["task_id"], "passed": False, "reason": "syntax_error"}
    if domain_digest("caduceus.bugtasks.src.v1", repaired_source) == task["mutated_digest"]:
        return {"task_id": task["task_id"], "passed": False, "reason": "unchanged"}
    tmp = tempfile.mkdtemp()
    try:
        root = _copy_tree(repo, tmp)
        with open(os.path.join(root, task["path"]), "w") as fh:
            fh.write(repaired_source)
        ok, tail = run_tests(root, task["tests"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"task_id": task["task_id"], "passed": ok, "reason": "tests_green" if ok else "timeout" if tail == "TIMEOUT" else "tests_red",
            "repaired_digest": domain_digest("caduceus.bugtasks.src.v1", repaired_source)}


def split_commitment(tasks: Sequence[Dict[str, Any]], split: str) -> str:
    ids = sorted(t["task_id"] for t in tasks if t["split"] == split)
    return domain_digest("caduceus.bugtasks.commit.v1", {"split": split, "task_ids": ids})


def proposer_view(task: Dict[str, Any]) -> Dict[str, Any]:
    """Everything a proposer may see: the buggy source and which tests cover it. No op, no original."""
    return {"task_id": task["task_id"], "path": task["path"], "source": task["mutated_source"], "tests": task["tests"]}


def oracle_audit(repo: str, modules: Sequence[str]) -> Dict[str, Any]:
    """How many syntactic mutants does the suite kill? Survivors are what the oracle cannot see."""
    killed = survived = 0
    by_op: Dict[str, List[int]] = {}
    for module in sorted(modules):
        with open(os.path.join(repo, PKG, module + ".py")) as fh:
            tree = ast.parse(fh.read())
        for op, ordinal, _ in _sites(tree):
            t = make_task(repo, module, op, ordinal, 0)
            by_op.setdefault(op, [0, 0])
            if t:
                killed += 1
                by_op[op][0] += 1
            else:
                survived += 1  # includes no-covering-test and no-op mutants; reported together
                by_op[op][1] += 1
    return {"killed": killed, "not_killed": survived, "by_op": {k: {"killed": v[0], "not_killed": v[1]} for k, v in sorted(by_op.items())}}

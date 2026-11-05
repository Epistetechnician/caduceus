"""Operator-path cost on a synthetic 900-file skills tree (PRD R23, N1 and the reconcile metric). Descriptive: one machine, no tuning. Writes a baseline JSON."""
import json
import os
import platform
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from caduceus.transition import Kernel, Policy  # noqa: E402


def p99(xs):
    return sorted(xs)[max(0, int(len(xs) * 0.99) - 1)]


def timed(fn, n):
    out = []
    for _ in range(n):
        a = time.perf_counter()
        fn()
        out.append((time.perf_counter() - a) * 1000)
    return {"p50_ms": round(sorted(out)[len(out) // 2], 2), "p99_ms": round(p99(out), 2), "n": n}


def main():
    t = tempfile.mkdtemp(prefix="cad-measure-")
    try:
        root = os.path.join(t, "skills")
        for i in range(300):  # 300 skills x 3 files = 900 files
            d = os.path.join(root, "dev", "s%d" % i)
            os.makedirs(os.path.join(d, "references"))
            open(os.path.join(d, "SKILL.md"), "w").write("---\nname: s%d\ndescription: d\n---\nstep\n" % i)
            open(os.path.join(d, "references", "a.md"), "w").write("a\n" * 20)
            open(os.path.join(d, "references", "b.md"), "w").write("b\n" * 20)
        k = Kernel(root, os.path.join(t, "c"), Policy(), clock=lambda: int(time.time()))
        k.reconcile()
        for i in range(50):  # a realistic ledger length for the status view
            k.observe_taint("s", "web_extract", "x")
        res = {"files": 900, "reconcile_clean": timed(k.reconcile, 40), "status": timed(k.status, 40),
               "observe_taint": timed(lambda: k.observe_taint("s1", "web_extract", "body"), 300), "taint_scope": timed(lambda: k.taint_scope("s1"), 300)}
        n = [0]

        def edit_and_reconcile():
            n[0] += 1
            open(os.path.join(root, "dev", "s0", "references", "a.md"), "a").write("x%d\n" % n[0])
            k.reconcile()
        res["reconcile_one_change"] = timed(edit_and_reconcile, 20)
        res["machine"] = {"platform": platform.platform(), "python": platform.python_version()}
        res["note"] = "descriptive, one machine, synthetic tree; targets: status < 100 ms, clean reconcile <= 30 ms, taint observation <= 1 ms p99 (PRD R23, success metrics, N1)"
        res["targets_met"] = {"status_lt_100ms": res["status"]["p99_ms"] < 100, "reconcile_clean_le_30ms": res["reconcile_clean"]["p99_ms"] <= 30, "observe_taint_le_1ms": res["observe_taint"]["p99_ms"] <= 1.0}
        print(json.dumps(res, indent=1, sort_keys=True))
    finally:
        shutil.rmtree(t, ignore_errors=True)


if __name__ == "__main__":
    main()

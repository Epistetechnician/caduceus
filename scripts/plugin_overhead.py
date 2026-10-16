"""Measure the Caduceus plugin's cost inside Hermes' real hook dispatch (PRD 6.1, 11 "Overhead").

    ~/.hermes/hermes-agent/venv/bin/python scripts/plugin_overhead.py \
        --hermes-src ~/.hermes/hermes-agent --out baselines/

Two throw-away HERMES_HOMEs: one with no plugins, one with
plugin/caduceus_governance in observe mode. In each, time
`hermes_cli.plugins._dispatch_pre_tool_call_hooks` (the function Hermes calls
before every tool) for a non-gated tool (`read_file`) and the gated one
(`skill_manage`), interleaved, N calls each. Each configuration runs in its own
subprocess so plugin state cannot leak. Output: per-call microseconds
(p50/p95/p99 with bootstrap CI) and the deltas. No model, no network.

Pass rule (preregistered here): the non-gated p99 delta is at most 8% of the
pinned switch-latency p50 (PRD target: overhead < 8% on the reference
workload). The gated path is reported, not judged: it writes a ledger entry
by design and only runs on skill mutations.
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N = 2000
ARGS = {"operations": [{"action": "patch", "name": "x", "old_string": "a", "new_string": "b"}]}


def _child(hermes_src, with_plugin):
    sys.dont_write_bytecode = True
    tmp = tempfile.mkdtemp(prefix="caduceus-ovh-")
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, "skills"))
    enabled = "[caduceus_governance, caduceus-governance]" if with_plugin else "[]"
    with open(os.path.join(home, "config.yaml"), "w") as fh:
        fh.write("plugins:\n  enabled: %s\n" % enabled)
    if with_plugin:
        shutil.copytree(os.path.join(REPO, "plugin", "caduceus_governance"),
                        os.path.join(home, "plugins", "caduceus_governance"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(home, "caduceus"))
        with open(os.path.join(home, "caduceus", "mode.json"), "w") as fh:
            json.dump({"mode": "observe"}, fh)
    os.environ["HERMES_HOME"] = home
    sys.path.insert(0, hermes_src)
    sys.path.insert(0, REPO)
    from hermes_cli.plugins import _dispatch_pre_tool_call_hooks, get_plugin_manager

    get_plugin_manager().discover_and_load(force=True)
    out = {"read_file": [], "skill_manage": []}
    for i in range(N + 50):
        for tool, args in (("read_file", {"path": "x"}), ("skill_manage", ARGS)):
            t0 = time.perf_counter()
            _dispatch_pre_tool_call_hooks(tool, dict(args), task_id="t")
            dt = int((time.perf_counter() - t0) * 1e6)
            if i >= 50:
                out[tool].append(dt)
    ledger = os.path.join(home, "caduceus", "ledger.jsonl")
    out["ledger_entries"] = sum(1 for _ in open(ledger)) if os.path.exists(ledger) else 0
    shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-src", required=True)
    ap.add_argument("--out", default=os.path.join(REPO, "baselines"))
    ap.add_argument("--child", choices=("on", "off"))
    ns = ap.parse_args()
    src = os.path.abspath(os.path.expanduser(ns.hermes_src))
    if ns.child:
        _child(src, ns.child == "on")
        return 0
    sys.path.insert(0, REPO)
    from caduceus.stats import quantile_bootstrap

    runs = {}
    for flag in ("off", "on"):
        p = subprocess.run([sys.executable, __file__, "--hermes-src", src, "--child", flag],
                           capture_output=True, text=True, check=True)
        runs[flag] = json.loads(p.stdout.strip().splitlines()[-1])

    def q(values, seed):
        return {name: quantile_bootstrap(values, qq, seed + k, resamples=1000)
                for k, (name, qq) in enumerate((("p50", .5), ("p95", .95), ("p99", .99)))}

    lat = {flag: {tool: q(runs[flag][tool], 7 + i) for i, tool in enumerate(("read_file", "skill_manage"))}
           for flag in runs}
    delta = {tool: {p: lat["on"][tool][p]["point"] - lat["off"][tool][p]["point"] for p in ("p50", "p95", "p99")}
             for tool in ("read_file", "skill_manage")}
    base = sorted(glob.glob(os.path.join(REPO, "baselines", "switch-latency-*.json")))
    switch_p50 = json.load(open(base[-1]))["latency_us"]["p50"]["point"] if base else None
    budget = switch_p50 * 8 // 100 if switch_p50 else None
    commit = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    result = {
        "schema": "caduceus.baseline.plugin_overhead.v1",
        "hermes_commit": commit,
        "n_per_tool": N,
        "latency_us": lat,
        "delta_us": delta,
        "gated_calls_ledgered": runs["on"]["ledger_entries"],
        "pass_rule": "read_file p99 delta <= 8% of pinned switch p50",
        "budget_us": budget,
        "passed": budget is not None and delta["read_file"]["p99"] <= budget,
        "nonclaim": "LocalDevelopmentEvidenceOnly",
    }
    os.makedirs(ns.out, exist_ok=True)
    path = os.path.join(ns.out, "plugin-overhead-%s.json" % commit[:10])
    with open(path, "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({k: result[k] for k in ("delta_us", "budget_us", "passed", "gated_calls_ledgered")}, indent=2))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

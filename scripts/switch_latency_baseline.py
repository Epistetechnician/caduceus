"""Pin the Hermes conversation-switch latency baseline (PRD 6.3, section 13 item 4).

    ~/.hermes/hermes-agent/venv/bin/python scripts/switch_latency_baseline.py \
        --hermes-src ~/.hermes/hermes-agent --out baselines/

Definition. A *switch* is: construct a fresh `AIAgent` for a session and call
`run_conversation(message, conversation_history=<that session's history>)`.
Switch latency is the time from just before `AIAgent(...)` to the moment that
turn's first chat request reaches the provider. Everything Hermes does before
talking to the model (config load, tool schemas, skill index, system prompt
assembly, history load) is inside the window; the model is not. The provider is
a 127.0.0.1 stub that answers immediately, so no model is called.

Workload. A throw-away HERMES_HOME holding a read-only copy of the real
skills tree (so skill discovery cost is realistic) and 8 seeded synthetic
session histories of 10-80 messages. The workload digest covers both. Default
toolsets; plugins disabled; context files skipped (cwd-dependent).

Output. A JSON baseline with p50/p95/p99 in integer microseconds, each with a
seeded bootstrap CI, pinned to: Hermes commit (+ dirty flag), Caduceus commit,
hardware, OS, Python, model (stub) and workload digest. Warm-up switches are
excluded and the first (cold-import) switch is reported separately.
"""

import argparse
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.dont_write_bytecode = True

from caduceus import skills as cskills  # noqa: E402
from caduceus.digest import domain_digest  # noqa: E402
from caduceus.stats import quantile_bootstrap  # noqa: E402
from stub_openai import serve  # noqa: E402

SEED = 20261015
N_SESSIONS = 8
WORDS = ("alpha beta gamma delta ledger skill probe contract hermes switch latency "
         "cache prompt memory tool schema digest merkle witness review").split()


def _histories(rng):
    out = []
    for _ in range(N_SESSIONS):
        msgs = []
        for i in range(rng.randrange(10, 81)):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append({"role": role, "content": " ".join(rng.choice(WORDS) for _ in range(rng.randrange(8, 120)))})
        if msgs[-1]["role"] == "user":
            msgs.append({"role": "assistant", "content": "ok"})
        out.append(msgs)
    return out


def _git(path, *args):
    try:
        return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _sysctl(name):
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-src", required=True)
    ap.add_argument("--hermes-home", default="~/.hermes", help="read-only; skills are copied from here")
    ap.add_argument("--switches", type=int, default=1000)
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--out", default=os.path.join(REPO, "baselines"))
    ns = ap.parse_args()

    src = os.path.abspath(os.path.expanduser(ns.hermes_src))
    real_skills = os.path.join(os.path.expanduser(ns.hermes_home), "skills")
    tmp = tempfile.mkdtemp(prefix="caduceus-switch-")
    home = os.path.join(tmp, "hermes_home")
    shutil.copytree(real_skills, os.path.join(home, "skills"), symlinks=True,
                    ignore=shutil.ignore_patterns(".usage.json", ".curator_ledger.jsonl", ".curator*", "__pycache__"))
    with open(os.path.join(home, "config.yaml"), "w") as fh:
        fh.write("plugins:\n  enabled: []\n")
    os.environ["HERMES_HOME"] = home
    for var in ("HERMES_PROFILE", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(var, None)
    os.chdir(tmp)

    arrivals = []

    def respond(body, calls):
        arrivals.append(time.perf_counter())
        return {"role": "assistant", "content": "ok"}

    server, base_url, _ = serve(respond)
    rng = random.Random(SEED)
    histories = _histories(rng)
    skill_dirs = cskills.discover(os.path.join(home, "skills"))
    inventory = sorted(cskills.load_skill(os.path.join(home, "skills"), d).tree_digest for d in skill_dirs)
    workload = {
        "histories": domain_digest("caduceus.switch.histories.v1", histories),
        "skills_inventory": domain_digest("caduceus.switch.skills.v1", inventory),
        "skill_count": len(skill_dirs),
        "sessions": N_SESSIONS,
        "seed": SEED,
    }

    sys.path.insert(0, src)
    t_import = time.perf_counter()
    from run_agent import AIAgent  # noqa: E402
    import_us = int((time.perf_counter() - t_import) * 1e6)

    def switch(i):
        hist = histories[i % N_SESSIONS]
        before = len(arrivals)
        t0 = time.perf_counter()
        agent = AIAgent(base_url=base_url, api_key="stub-key", provider="custom", model="stub-model",
                        quiet_mode=True, skip_context_files=True, skip_background_review=True,
                        session_id="switch-%d-%d" % (i % N_SESSIONS, i))
        agent.run_conversation("continue", conversation_history=list(hist))
        if len(arrivals) <= before:
            raise RuntimeError("switch %d never reached the provider" % i)
        return int((arrivals[before] - t0) * 1e6)

    order = list(range(ns.warmup + ns.switches))
    rng.shuffle(order)
    cold_us = switch(order[0])
    for i in order[1:ns.warmup]:
        switch(i)
    samples = [switch(i) for i in order[ns.warmup:]]
    server.shutdown()

    dirty = bool(_git(src, "status", "--porcelain", "--untracked-files=no"))
    result = {
        "schema": "caduceus.baseline.switch_latency.v1",
        "definition": "AIAgent construction + run_conversation(history) until first provider request; stub provider on 127.0.0.1",
        "pin": {
            "hermes_commit": _git(src, "rev-parse", "HEAD"),
            "hermes_tree_dirty": dirty,
            "caduceus_commit": _git(REPO, "rev-parse", "HEAD"),
            "hardware": {"model": _sysctl("hw.model"), "cpu": _sysctl("machdep.cpu.brand_string"),
                         "ncpu": os.cpu_count(), "machine": platform.machine()},
            "os": platform.platform(),
            "python": platform.python_version(),
            "model": "stub-model (127.0.0.1, immediate reply)",
            "workload": workload,
            "workload_digest": domain_digest("caduceus.switch.workload.v1", workload),
        },
        "warmup": ns.warmup,
        "cold_first_switch_us": cold_us,
        "hermes_import_us": import_us,
        "latency_us": {
            name: quantile_bootstrap(samples, q, seed=SEED + k, resamples=2000)
            for k, (name, q) in enumerate((("p50", 0.50), ("p95", 0.95), ("p99", 0.99)))
        },
        "samples_us": samples,
        "nonclaim": "LocalDevelopmentEvidenceOnly",
    }
    os.makedirs(ns.out, exist_ok=True)
    path = os.path.join(ns.out, "switch-latency-%s.json" % result["pin"]["hermes_commit"][:10])
    with open(path, "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
        fh.write("\n")
    shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps({k: result[k] for k in ("latency_us", "cold_first_switch_us")}, indent=2))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

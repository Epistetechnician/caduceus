"""exp-028 OpenCode free-model head-to-head on the exp-027 hidden-test tasks (local Mac, Docker for scoring).
Usage: agent_bench3.py run <label> <model> <workers> [limit]      (model e.g. opencode/big-pickle)
No OS network allowlist here: a permission config denies web/network shell tools, and any task whose transcript
mentions a network command or URL is flagged `net_flag` (reported, not silently dropped)."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts"))
import agent_bench2 as b2  # noqa: E402
from caduceus import realbugs  # noqa: E402

OUT = os.path.expanduser(os.environ.get("OUT", "~/agent-out3"))
WORK = os.path.expanduser(os.environ.get("WORKROOT", "~/agent-work3"))
OCBIN = os.path.expanduser("~/oc-linux")
PROMPT = b2.AGENT_PROMPT.replace("Use ./runtests.sh [pytest args] to run the repository's existing tests (sandboxed, no internet);", "Run the repository's existing tests with python -m pytest (no internet);")
CONFIG = {"permission": {"edit": "allow", "bash": "allow", "webfetch": "deny"}}
NET = re.compile(r"https?://|\bcurl\b|\bwget\b|urllib|requests\.get|git (fetch|pull|clone|log|show)|pip3? install", re.I)
LIMIT = re.compile(r"usage limit|rate.?limit|too many requests|429", re.I)


def agent(prompt, model, cwd, image, name):
    # agent runs in the task's rb-* image on an internal network whose only exit is the allowlist proxy (opencode.ai, models.dev)
    px = "http://ocproxy:3128"
    cmd = ["docker", "run", "--rm", "--name", name, "--network", "ocnet", "--memory", "2g", "--cpus", "1", "--pids-limit", "512", "-v", cwd + ":/w", "-w", "/w",
           "-v", OCBIN + ":/oc:ro", "-e", "HOME=/tmp/h", "-e", "HTTPS_PROXY=" + px, "-e", "HTTP_PROXY=" + px, "-e", "PYTHONPATH=/w:/w/src",
           "-e", "PYTHONDONTWRITEBYTECODE=1", image, "sh", "-c", 'mkdir -p /tmp/h; exec /oc/opencode run -m "$0" --format json "$1"', model, prompt]
    for attempt in range(6):
        try:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired as e:
            subprocess.run(["docker", "kill", name], capture_output=True)
            return (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "agent_timeout", attempt
        out = p.stdout + p.stderr
        if LIMIT.search(out[-3000:]) and "step_finish" not in p.stdout:
            time.sleep(60 * (attempt + 1))
            continue
        return p.stdout, None, attempt
    return "", "rate_limited", 6


def run(label, model, workers, limit=None):
    st = json.load(open(os.path.join(REPO, "tasks", "fresh-v2.statements.json")))
    ts = [t for t in b2.load_tasks() if st.get(t["task_id"])][: int(limit) if limit else None]
    os.makedirs(os.path.join(OUT, label), exist_ok=True)
    os.makedirs(WORK, exist_ok=True)

    def one(t):
        rp = os.path.join(OUT, label, t["task_id"] + ".json")
        if os.path.exists(rp):
            return json.load(open(rp))
        clone, image = os.path.join(b2.CLONES, t["repo"]), realbugs.image_for(t["repo"])
        parent = b2.git(clone, "rev-parse", t["commit"] + "^").strip()
        work = os.path.join(WORK, label + "-" + t["task_id"])
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work)
        t0, err, attempts, net = time.time(), None, 0, False
        try:
            b2.extract(clone, parent, work)
            json.dump(CONFIG, open(os.path.join(work, "opencode.json"), "w"))
            before = {p: open(os.path.join(work, p), "rb").read() for p in glob.glob("**/*.py", root_dir=work, recursive=True)}
            out, err, attempts = agent(PROMPT.format(repo=t["repo"], statement=st[t["task_id"]]), model, work, image, "oc-" + label + "-" + t["task_id"])
            with open(os.path.join(OUT, label, t["task_id"] + ".stream.jsonl"), "w") as fh:
                fh.write(out)
            net = bool(NET.search("\n".join(ln for ln in out.splitlines() if '"command"' in ln or '"input"' in ln)))
            for f in ("opencode.json",):
                if os.path.exists(os.path.join(work, f)):
                    os.remove(os.path.join(work, f))
            fix = tempfile.mkdtemp(prefix="agfix-")
            b2.extract(clone, t["commit"], fix)
            for tf in t["tests"]:
                os.makedirs(os.path.dirname(os.path.join(work, tf)), exist_ok=True)
                shutil.copy(os.path.join(fix, tf), os.path.join(work, tf))
            shutil.rmtree(fix, ignore_errors=True)
            changed = sorted(q for q in glob.glob("**/*.py", root_dir=work, recursive=True) if open(os.path.join(work, q), "rb").read() != before.get(q) and q not in t["tests"])
            passed = (err != "rate_limited") and b2.run_pytest(image, work, t["tests"])
        finally:
            shutil.rmtree(work, ignore_errors=True)
        r = {"task_id": t["task_id"], "model": model, "passed": passed, "changed_files": changed[:10], "error": err, "retries": attempts,
             "net_flag": net, "seconds": int(time.time() - t0)}
        json.dump(r, open(rp, "w"))
        print(label, t["task_id"], passed, err, net, flush=True)
        return r
    with ThreadPoolExecutor(workers) as ex:
        rs = list(ex.map(one, ts))
    print("DONE", label, sum(r["passed"] for r in rs), "/", len(rs), "rate_limited", sum(r["error"] == "rate_limited" for r in rs), "net_flag", sum(r["net_flag"] for r in rs))


if __name__ == "__main__":
    a = sys.argv
    run(a[2], a[3], int(a[4]), a[5] if len(a) > 5 else None)

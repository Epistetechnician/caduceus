"""Run Claude Code (headless) as the repairing agent on the exp-026 fresh tasks, then score with the same oracle.
Workspace = git archive of the parent commit (no .git, so no fix history). Prompt carries the same spec as the bare models.
Only the target file's final content is scored. Usage: fresh_agent.py <label> <model> [limit] [start]"""
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts"))
import fresh_bench as fb  # noqa: E402
from caduceus import realbugs  # noqa: E402

label, model = sys.argv[1], sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else None
start = int(sys.argv[4]) if len(sys.argv) > 4 else 0
out = os.path.join(fb.WORK, "agent-%s.jsonl" % label)
ts = fb.tasks()[start:]
ts = ts[:limit] if limit else ts
done = {json.loads(ln)["task_id"] for ln in open(out)} if os.path.exists(out) else set()

PROMPT = """A regression test for the project in this directory fails on the current code. Fix the bug by editing ONLY the file `{path}`; do not edit any other file and do not create files.
You cannot run the tests. Read the code (other files are available for reference) and make the smallest correct change.

{spec}
"""


def run(t):
    parent = fb.realbugs._git(os.path.join(fb.CLONES, t["repo"]), "rev-parse", t["commit"] + "^").strip()
    raw = subprocess.run(["git", "-C", os.path.join(fb.CLONES, t["repo"]), "archive", parent], capture_output=True).stdout
    ws = tempfile.mkdtemp(prefix="fa-")
    try:
        tarfile.open(fileobj=io.BytesIO(raw)).extractall(ws)
        p = subprocess.run(["claude", "-p", PROMPT.format(path=t["path"], spec=t["spec"]), "--model", model, "--output-format", "json",
                            "--allowed-tools", "Read,Edit,Grep,Glob", "--permission-mode", "acceptEdits", "--max-turns", "25"],
                           cwd=ws, capture_output=True, text=True, timeout=900)
        try:
            meta = json.loads(p.stdout)
        except ValueError:
            meta = {}
        new = open(os.path.join(ws, t["path"])).read()
        v = realbugs.verify_repair(fb.CLONES, t, new)
        return {"task_id": t["task_id"], "passed": v["passed"], "reason": v["reason"], "cost_usd": meta.get("total_cost_usd", 0), "turns": meta.get("num_turns", 0), "is_error": meta.get("is_error", None)}
    except Exception as e:  # noqa: BLE001
        return {"task_id": t["task_id"], "passed": False, "reason": "agent_error:%s" % type(e).__name__, "cost_usd": 0, "turns": 0, "is_error": True}
    finally:
        shutil.rmtree(ws, ignore_errors=True)


todo = [t for t in ts if t["task_id"] not in done]
with ThreadPoolExecutor(4) as ex, open(out, "a") as fh:
    for r in ex.map(run, todo):
        fh.write(json.dumps(r) + "\n")
        fh.flush()
        print(r["task_id"][:8], r["passed"], r["reason"], round(r["cost_usd"], 3), r["turns"], flush=True)

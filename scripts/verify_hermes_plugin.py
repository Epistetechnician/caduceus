"""Live check of the Caduceus plugin through Hermes' own plugin loader and pre_tool_call dispatch (no model, no network).
Run with the Hermes venv python from the repo root:  <hermes>/venv/bin/python scripts/verify_hermes_plugin.py [<hermes-checkout>]
Uses a temporary HERMES_HOME (never the real one); reads the Hermes checkout read-only."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERMES = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/.hermes/hermes-agent")
real = os.path.realpath(os.path.expanduser("~/.hermes"))
HH = tempfile.mkdtemp(prefix="hh-")
assert os.path.realpath(HH) != real
os.environ["HERMES_HOME"] = HH
sys.path[:0] = [HERMES, REPO]
os.makedirs(os.path.join(HH, "plugins"))
shutil.copytree(os.path.join(REPO, "plugin", "caduceus_governance"), os.path.join(HH, "plugins", "caduceus_governance"), ignore=shutil.ignore_patterns("__pycache__"))
open(os.path.join(HH, "config.yaml"), "w").write("plugins:\n  enabled:\n    - caduceus-governance\n")
from hermes_cli import plugins as hp  # noqa: E402
from caduceus.ledger import Ledger  # noqa: E402
from plugin.caduceus_governance import args_digest  # noqa: E402

res, base = [], os.path.join(HH, "caduceus")
CALL = {"operations": [{"name": "x", "action": "create", "content": "hello"}]}


def check(name, ok, detail=""):
    res.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(("PASS " if ok else "FAIL ") + name, detail if not ok else "")


def directive(tool, args):
    return hp.get_pre_tool_call_directive(tool, args)


def set_mode(m):
    os.makedirs(base, exist_ok=True)
    json.dump({"mode": m}, open(os.path.join(base, "mode.json"), "w"))


hp.discover_plugins(force=True)
pm = hp.get_plugin_manager()
loaded = {k: v for k, v in getattr(pm, "_plugins", getattr(pm, "plugins", {})).items()}
names = [k for k in loaded]
check("plugin discovered and loaded by Hermes", any("caduceus" in n for n in names), names)
hooks = getattr(pm, "_hooks", {})
check("pre_tool_call hook registered", bool(hooks.get("pre_tool_call")), list(hooks))
set_mode("observe")
check("observe: skill_manage allowed", directive("skill_manage", CALL) == (None, None), directive("skill_manage", CALL))
led = Ledger(os.path.join(base, "ledger.jsonl"))
check("observe: intent recorded in ledger", any(e["body"].get("event") == "skill_mutation_intent" for e in led.find("audit_note")))
check("non-gated tool untouched", directive("read_file", {"path": "x"}) == (None, None))
set_mode("enforce")
d = directive("skill_manage", CALL)
check("enforce: unadmitted mutation blocked", d[0] == "block" and "Caduceus" in (d[1] or ""), d)
led = Ledger(os.path.join(base, "ledger.jsonl"))
led.append("capability_issued", {"token_sha256": "b" * 64, "args_digest": args_digest("skill_manage", CALL), "expires_at": 4102444800})
check("enforce: call with a matching capability admitted once", directive("skill_manage", CALL) == (None, None))
check("enforce: capability is single use", directive("skill_manage", CALL)[0] == "block")
other = {"operations": [{"name": "x", "action": "delete"}]}
check("enforce: different args blocked", directive("skill_manage", other)[0] == "block")
exp_call = {"operations": [{"name": "y", "action": "patch"}]}
Ledger(os.path.join(base, "ledger.jsonl")).append("capability_issued", {"token_sha256": "d" * 64, "args_digest": args_digest("skill_manage", exp_call), "expires_at": 1})
check("enforce: expired capability does not admit", directive("skill_manage", exp_call)[0] == "block")
with open(os.path.join(base, "ledger.jsonl"), "a") as fh:
    fh.write("{not json\n")
check("enforce: corrupt ledger fails closed", directive("skill_manage", CALL)[0] == "block")
# production issuance path: the Refinery can bind a capability to one exact tool call, and the plugin honours expiry
import inspect  # noqa: E402
from caduceus.refinery import Refinery  # noqa: E402
check("Refinery.issue_capability can bind args_digest", "args_digest" in inspect.signature(Refinery.issue_capability).parameters)
set_mode("enforce")
head = subprocess.run(["git", "-C", HERMES, "rev-parse", "--short=10", "HEAD"], capture_output=True, text=True).stdout.strip()
print(json.dumps({"hermes_head": head, "hermes_home_used": HH, "passed": sum(r["ok"] for r in res), "total": len(res)}))
json.dump({"hermes_head": head, "results": res}, open(os.path.join(REPO, "ledgers", "hermes-plugin-live.result.json"), "w"), indent=1)
shutil.rmtree(HH, ignore_errors=True)

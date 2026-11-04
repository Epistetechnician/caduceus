"""Caduceus governance plugin for Hermes (standalone; install into ~/.hermes/plugins/).

Uses only the documented `pre_tool_call` and `transform_tool_result` hooks. Modes (read from
`$HERMES_HOME/caduceus/mode.json`, default observe):

  observe  record an intent entry in the Caduceus ledger for every
           `skill_manage` call and never block. Fail-open on any error.
  boundary govern the STATE a mutation leaves behind instead of the call (caduceus/transition.py): never blocks; after each skill_manage
           (and before skill_view) the skills tree is reconciled against the last admitted HEAD and any non-admitted effect, from any writer, is
           restored. The model is told when its change was reverted. Fail-open on internal error (the next reconcile re-checks everything).
  enforce  block `skill_manage` mutations unless an unconsumed capability for
           the exact call arguments exists in the ledger (single use). Any
           internal error blocks (fail closed), matching Hermes' policy-hook
           semantics.

Operator controls (`control.json`, see caduceus/control.py): admission off
makes this a no-op; frozen makes enforce mode block every mutation.

Only the `skill_manage` tool is gated. Its schema (verified against a Hermes
checkout) is an `operations` array whose ops are create / patch / delete /
write_file / remove_file; every op mutates, and the legacy flat shape is still
accepted. Read-only tools (`skill_view`, `read_file`, ...) are never gated. This
plugin makes no model calls and no network calls.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# The catalog installs only this directory, so a vendored copy of the kernel ships beside this file (scripts/vendor_plugin.py keeps it in sync).
# A caduceus already importable (a dev install) wins.
try:
    import caduceus  # noqa: F401
except ImportError:
    import sys

    _VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor")
    if _VENDOR not in sys.path:
        sys.path.insert(0, _VENDOR)

GATED_TOOL = "skill_manage"
SKILL_MANAGE_ACTIONS = frozenset({"create", "patch", "delete", "write_file", "remove_file"})


def _home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def _mode(base: str) -> str:
    try:
        with open(os.path.join(base, "mode.json"), "r", encoding="utf-8") as fh:
            mode = json.load(fh).get("mode", "observe")
        return mode if mode in ("observe", "enforce", "boundary") else "observe"
    except (OSError, ValueError):
        return "observe"


def _actions(args: Dict[str, Any]) -> str:
    """Short summary of the ops in a skill_manage call (never the content)."""
    ops = args.get("operations")
    if isinstance(ops, list):
        names = [str(o.get("action", "?")) for o in ops if isinstance(o, dict)]
    else:
        names = [str(args.get("action", "?"))]
    return ",".join(sorted(set(n if n in SKILL_MANAGE_ACTIONS else "?" for n in names)))[:64]


def args_digest(tool_name: str, args: Dict[str, Any]) -> str:
    from caduceus.toolcall import call_digest  # single definition shared with the operator step

    return call_digest(tool_name, args)


def decide(tool_name: str, args: Dict[str, Any], base: str) -> Optional[Dict[str, str]]:
    """Pure decision function (testable). Returns a Hermes directive or None."""
    if tool_name != GATED_TOOL or not isinstance(args, dict):
        return None
    from caduceus import control
    from caduceus.ledger import Ledger

    ctl = control.load(base)
    if not ctl.enabled("admission"):
        return None  # admission off: upstream-equivalent, nothing recorded
    from caduceus.ledger import append_fast

    path = os.path.join(base, "ledger.jsonl")
    digest = args_digest(tool_name, args)
    mode = _mode(base)
    if ctl.frozen and mode == "enforce":
        append_fast(
            path,
            "audit_note",
            {"event": "skill_mutation_blocked", "args_digest": digest, "actions": _actions(args), "frozen": True},
        )
        return {"action": "block", "message": "Caduceus: evolution is frozen by the operator"}
    if mode == "observe":
        append_fast(
            path,
            "audit_note",
            {"event": "skill_mutation_intent", "args_digest": digest, "actions": _actions(args)},
        )
        return None
    ledger = Ledger(path)  # enforce mode needs the full, verified history
    issued = [e for e in ledger.find("capability_issued") if e["body"].get("args_digest") == digest]
    consumed = {e["body"].get("token_sha256") for e in ledger.find("capability_consumed")}
    now = int(time.time())
    for entry in issued:
        exp = entry["body"].get("expires_at")
        if not isinstance(exp, int) or isinstance(exp, bool) or exp < now:
            continue  # expired or malformed expiry never admits
        if entry["body"]["token_sha256"] not in consumed:
            ledger.append(
                "capability_consumed",
                {"token_sha256": entry["body"]["token_sha256"], "args_digest": digest},
            )
            return None
    ledger.append(
        "audit_note",
        {"event": "skill_mutation_blocked", "args_digest": digest, "actions": _actions(args)},
    )
    return {
        "action": "block",
        "message": "Caduceus: skill mutation has no admitted capability (args_digest=%s)" % digest[:16],
    }


SWEEP_TOOLS = frozenset({"skill_view", "skills_list", "skill_manage"})
WRITER_TOOLS = frozenset({"write_file", "patch", "terminal", "execute_code"})  # can write the skills tree without skill_manage


def _kernel(base: str):
    from caduceus.transition import Kernel, Policy

    live = False
    pol = Policy()
    try:
        with open(os.path.join(base, "mode.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        live = cfg.get("live") is True
        if isinstance(cfg.get("policy"), dict):
            pol = Policy(**{k: (tuple(v) if k == "pinned" else v) for k, v in cfg["policy"].items()})
    except (OSError, ValueError, TypeError):
        pass
    return Kernel(os.path.join(_home(), "skills"), base, pol, allow_live=live)


def _reconcile(base: str, session_id: str = ""):
    from caduceus import control

    if not control.load(base).enabled("admission"):
        return None
    k = _kernel(base)
    return k.reconcile(taint=k.taint_scope(session_id) if k.policy.provenance != "off" else [])


def _on_post_tool_call(tool_name: str = "", result: Any = None, session_id: str = "", **kwargs: Any) -> None:
    """Provenance: remember that untrusted content (web, browser, MCP) entered this session. Stores a tool name, a digest prefix and a time; never content."""
    base = os.path.join(_home(), "caduceus")
    if _mode(base) != "boundary":
        return None
    try:
        from caduceus import control

        if control.load(base).enabled("admission"):
            _kernel(base).observe_taint(session_id or "", tool_name, result)
    except Exception as exc:  # noqa: BLE001 - observer; a missed observation only weakens the lane, and the next reconcile still judges the effect
        logger.warning("caduceus taint observe error: %s", type(exc).__name__)
    return None


def _on_transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", **kwargs: Any):
    base = os.path.join(_home(), "caduceus")
    if (tool_name != GATED_TOOL and tool_name not in WRITER_TOOLS) or _mode(base) != "boundary":
        return None
    try:
        out = _reconcile(base, session_id)
    except Exception as exc:  # noqa: BLE001 - boundary mode is fail-open; the next reconcile re-checks the whole tree
        logger.warning("caduceus boundary reconcile error: %s", type(exc).__name__)
        return None
    if out and out.get("status") == "restored" and tool_name != GATED_TOOL:
        v = out["verdict"]
        note = "[Caduceus reverted a change to the skills directory made by this command (%s: %s).]" % (v["decision"], ", ".join(v["reason_codes"]) or "policy")
        return (result + "\n" + note) if isinstance(result, str) else note
    if out and out.get("status") == "restored":
        v = out["verdict"]
        return json.dumps({"success": False, "error": "Caduceus did not admit this skill change (%s: %s). The skills directory was restored to its last admitted state."
                           % (v["decision"], ", ".join(v["reason_codes"]) or "policy")})
    return None


def _on_pre_tool_call(tool_name: str, args: Dict[str, Any], task_id: str = "", session_id: str = "", **kwargs: Any):
    base = os.path.join(_home(), "caduceus")
    if _mode(base) == "boundary":
        if tool_name in SWEEP_TOOLS:
            try:
                _reconcile(base, session_id)  # catches out-of-band writers (shell, forked review agent) before anything reads the tree
            except Exception as exc:  # noqa: BLE001
                logger.warning("caduceus boundary sweep error: %s", type(exc).__name__)
        return None
    try:
        return decide(tool_name, args, base)
    except Exception as exc:  # noqa: BLE001 - policy hook must never crash the agent
        logger.warning("caduceus plugin error: %s", type(exc).__name__)
        if tool_name == GATED_TOOL and _mode(base) == "enforce":
            return {"action": "block", "message": "Caduceus: governance check failed closed"}
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
    ctx.register_hook("post_tool_call", _on_post_tool_call)

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
        return {"action": "block", "message": "Caduceus: evolution is frozen by the operator; no skill change can be made until the operator unfreezes it (caduceus control unfreeze)."}
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
        "message": "Caduceus: skill mutation has no admitted capability (args_digest=%s). Do not retry; ask the operator to review and admit this exact change (caduceus admit-call)." % digest[:16],
    }


SWEEP_TOOLS = frozenset({"skill_view", "skills_list", "skill_manage"})
MEMORY_TOOL = "memory"
WRITER_TOOLS = frozenset({"write_file", "patch", "terminal", "execute_code"})  # can write the skills tree without skill_manage


def _surfaces(base: str):
    """Governed surfaces from mode.json {"surfaces": [...]}; default skills only."""
    try:
        with open(os.path.join(base, "mode.json"), "r", encoding="utf-8") as fh:
            s = json.load(fh).get("surfaces", ["skills"])
        return [x for x in ("skills", "memory", "config") if isinstance(s, list) and x in s] or ["skills"]
    except (OSError, ValueError, AttributeError):
        return ["skills"]


def _kernel(base: str, surface: str = "skills"):
    import dataclasses

    from caduceus.transition import Kernel, Policy, load_policy

    live = False
    pol = load_policy(base) or Policy()  # the operator's ledgered policy.json; a tampered or unreadable one raises, so no reconcile runs under a guessed policy
    try:
        with open(os.path.join(base, "mode.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        live = cfg.get("live") is True
        if isinstance(cfg.get("policy"), dict) and load_policy(base) is None:  # legacy inline policy, used only when no policy.json is installed
            pol = Policy(**{k: (tuple(v) if k == "pinned" else v) for k, v in cfg["policy"].items()})
    except (OSError, ValueError, TypeError):
        pass
    if surface == "config":  # config.yaml only (the root is the Hermes home itself, so the kernel lists exactly that one file)
        return Kernel(_home(), os.path.join(base, "config"), dataclasses.replace(pol, surface="config"), allow_live=live)
    if surface == "memory":  # same operator policy, applied to MEMORY.md / USER.md with its own state directory
        return Kernel(os.path.join(_home(), "memories"), os.path.join(base, "memory"), dataclasses.replace(pol, surface="memory"), allow_live=live)
    return Kernel(os.path.join(_home(), "skills"), base, pol, allow_live=live)


def _reconcile(base: str, session_id: str = "", surface: str = "skills", cross: bool = False):
    from caduceus import control

    if not control.load(base).enabled("admission") or surface not in _surfaces(base):
        return None
    k = _kernel(base, surface)
    return k.reconcile(taint=k.taint_scope(session_id, cross) if k.policy.provenance != "off" else [], announced=k.lifecycle_scope() if k.policy.lifecycle != "off" else [])


def _on_post_tool_call(tool_name: str = "", result: Any = None, session_id: str = "", **kwargs: Any) -> None:
    """Provenance: remember that untrusted content (web, browser, MCP) entered this session. Stores a tool name, a digest prefix and a time; never content."""
    base = os.path.join(_home(), "caduceus")
    if _mode(base) != "boundary":
        return None
    try:
        from caduceus import control

        if control.load(base).enabled("admission"):
            for surface in _surfaces(base):
                _kernel(base, surface).observe_taint(session_id or "", tool_name, result)
    except Exception as exc:  # noqa: BLE001 - observer; a missed observation only weakens the lane, and the next reconcile still judges the effect
        logger.warning("caduceus taint observe error: %s", type(exc).__name__)
    return None


def _on_subagent_start(parent_session_id: str = "", child_session_id: str = "", **kwargs: Any) -> None:
    """A delegating session and its subagents share taint: the task goes down, the (possibly untrusted-derived) result comes back."""
    base = os.path.join(_home(), "caduceus")
    if _mode(base) != "boundary":
        return None
    try:
        from caduceus import control

        if control.load(base).enabled("admission"):
            for surface in _surfaces(base):
                _kernel(base, surface).observe_subagent(str(parent_session_id or ""), str(child_session_id or ""))
    except Exception as exc:  # noqa: BLE001 - observer
        logger.warning("caduceus subagent observe error: %s", type(exc).__name__)
    return None


def _on_skill_lifecycle(action: str = "", skill_name: str = "", **kwargs: Any) -> None:
    """Second observation channel: remember which skills Hermes itself says changed. Name and action only."""
    base = os.path.join(_home(), "caduceus")
    if _mode(base) != "boundary":
        return None
    try:
        from caduceus import control

        if control.load(base).enabled("admission"):
            _kernel(base).observe_lifecycle(str(skill_name), str(action))
    except Exception as exc:  # noqa: BLE001 - observer
        logger.warning("caduceus lifecycle observe error: %s", type(exc).__name__)
    return None


def _on_transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", **kwargs: Any):
    base = os.path.join(_home(), "caduceus")
    if tool_name == MEMORY_TOOL and _mode(base) == "boundary" and "memory" in _surfaces(base):
        try:
            out = _reconcile(base, session_id, "memory")
        except Exception as exc:  # noqa: BLE001 - fail-open, as for skills
            logger.warning("caduceus memory reconcile error: %s", type(exc).__name__)
            return None
        if out and out.get("status") == "restored":
            v = out["verdict"]
            return json.dumps({"success": False, "error": "Caduceus did not admit this memory change (%s: %s). Memory was restored to its last admitted state. Do not retry the same write; the operator can review it with `caduceus status`."
                               % (v["decision"], ", ".join(v["reason_codes"]) or "policy")})
        return None
    config_note = ""
    if tool_name in WRITER_TOOLS and _mode(base) == "boundary" and "config" in _surfaces(base):
        try:  # these tools can rewrite config.yaml; revert a non-admitted change and say so
            cout = _reconcile(base, session_id, "config")
        except Exception as exc:  # noqa: BLE001
            logger.warning("caduceus config reconcile error: %s", type(exc).__name__)
            cout = None
        if cout and cout.get("status") == "restored":
            v = cout["verdict"]
            config_note = "[Caduceus reverted a change to config.yaml (%s: %s).]" % (v["decision"], ", ".join(v["reason_codes"]) or "policy")

    def with_note(note: str):
        text = "\n".join(x for x in (note, config_note) if x)
        return (result + "\n" + text) if isinstance(result, str) else text

    if (tool_name != GATED_TOOL and tool_name not in WRITER_TOOLS) or _mode(base) != "boundary":
        return with_note("") if config_note else None
    try:
        out = _reconcile(base, session_id)
    except Exception as exc:  # noqa: BLE001 - boundary mode is fail-open; the next reconcile re-checks the whole tree
        logger.warning("caduceus boundary reconcile error: %s", type(exc).__name__)
        return with_note("") if config_note else None
    if out and out.get("status") == "restored" and tool_name != GATED_TOOL:
        v = out["verdict"]
        return with_note("[Caduceus reverted a change to the skills directory made by this command (%s: %s).]" % (v["decision"], ", ".join(v["reason_codes"]) or "policy"))
    if out and out.get("status") == "restored":
        v = out["verdict"]
        return json.dumps({"success": False, "error": "Caduceus did not admit this skill change (%s: %s). The skills directory was restored to its last admitted state. Do not retry the same write; the operator can review it with `caduceus status`."
                           % (v["decision"], ", ".join(v["reason_codes"]) or "policy")})
    return with_note("") if config_note else None


def _on_pre_tool_call(tool_name: str, args: Dict[str, Any], task_id: str = "", session_id: str = "", **kwargs: Any):
    base = os.path.join(_home(), "caduceus")
    if _mode(base) == "boundary":
        if tool_name == MEMORY_TOOL and "memory" in _surfaces(base):
            try:
                _reconcile(base, session_id, "memory", True)  # revert an out-of-band edit of MEMORY.md / USER.md before the memory tool reads it
            except Exception as exc:  # noqa: BLE001
                logger.warning("caduceus memory sweep error: %s", type(exc).__name__)
        if tool_name in SWEEP_TOOLS:
            try:
                _reconcile(base, session_id, "skills", True)  # catches out-of-band writers (effects of unknown origin: cross-session taint applies) (shell, forked review agent) before anything reads the tree
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
    ctx.register_hook("on_skill_lifecycle", _on_skill_lifecycle)
    ctx.register_hook("subagent_start", _on_subagent_start)

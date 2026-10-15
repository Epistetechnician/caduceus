"""Caduceus governance plugin for Hermes (standalone; install into ~/.hermes/plugins/).

Uses only the documented `pre_tool_call` hook. Modes (read from
`$HERMES_HOME/caduceus/mode.json`, default observe):

  observe  record an intent entry in the Caduceus ledger for every
           `skill_manage` call and never block. Fail-open on any error.
  enforce  block `skill_manage` mutations unless an unconsumed capability for
           the exact call arguments exists in the ledger (single use). Any
           internal error blocks (fail closed), matching Hermes' policy-hook
           semantics.

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
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

GATED_TOOL = "skill_manage"
SKILL_MANAGE_ACTIONS = frozenset({"create", "patch", "delete", "write_file", "remove_file"})
TOOLCALL_DOMAIN = "caduceus.toolcall.v1"


def _home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def _mode(base: str) -> str:
    try:
        with open(os.path.join(base, "mode.json"), "r", encoding="utf-8") as fh:
            mode = json.load(fh).get("mode", "observe")
        return mode if mode in ("observe", "enforce") else "observe"
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
    from caduceus.digest import domain_digest

    return domain_digest(TOOLCALL_DOMAIN, {"tool": tool_name, "args": _jsonable(args)})


def _jsonable(value: Any) -> Any:
    """Reduce tool args to ledger-safe values; floats become strings."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float):
        return repr(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return str(value)


def decide(tool_name: str, args: Dict[str, Any], base: str) -> Optional[Dict[str, str]]:
    """Pure decision function (testable). Returns a Hermes directive or None."""
    if tool_name != GATED_TOOL or not isinstance(args, dict):
        return None
    from caduceus.ledger import Ledger
    from caduceus.digest import sha256_hex

    ledger = Ledger(os.path.join(base, "ledger.jsonl"))
    digest = args_digest(tool_name, args)
    mode = _mode(base)
    if mode == "observe":
        ledger.append(
            "audit_note",
            {"event": "skill_mutation_intent", "args_digest": digest, "actions": _actions(args)},
        )
        return None
    issued = [e for e in ledger.find("capability_issued") if e["body"].get("args_digest") == digest]
    consumed = {e["body"].get("token_sha256") for e in ledger.find("capability_consumed")}
    for entry in issued:
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


def _on_pre_tool_call(tool_name: str, args: Dict[str, Any], task_id: str = "", **kwargs: Any):
    base = os.path.join(_home(), "caduceus")
    try:
        return decide(tool_name, args, base)
    except Exception as exc:  # noqa: BLE001 - policy hook must never crash the agent
        logger.warning("caduceus plugin error: %s", type(exc).__name__)
        if tool_name == GATED_TOOL and _mode(base) == "enforce":
            return {"action": "block", "message": "Caduceus: governance check failed closed"}
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)

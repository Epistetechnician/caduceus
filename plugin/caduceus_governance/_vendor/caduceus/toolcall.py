"""Operator step: turn an ACCEPT verdict into a single-use capability for ONE exact Hermes tool call.

The plugin (plugin/caduceus_governance) blocks `skill_manage` in enforce mode unless the plugin ledger holds an unconsumed,
unexpired `capability_issued` entry whose `args_digest` equals the digest of the exact call. This module is the only place that
writes such an entry; it never calls a model and refuses unless the governance ledger holds a matching accept verdict.
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Any, Callable, Dict, Optional

from .admission import verdict_from_dict
from .candidate import Candidate
from .digest import domain_digest, sha256_hex
from .ledger import Ledger

TOOLCALL_DOMAIN = "caduceus.toolcall.v1"
GATED_TOOL = "skill_manage"


class ToolCallError(Exception):
    pass


def jsonable(value: Any) -> Any:
    """Reduce tool args to ledger-safe values; floats become strings."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, float):
        return repr(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return str(value)


def call_digest(tool_name: str, args: Dict[str, Any]) -> str:
    return domain_digest(TOOLCALL_DOMAIN, {"tool": tool_name, "args": jsonable(args)})


def issue_for_call(
    governance_ledger: Ledger,
    candidate: Candidate,
    tool_name: str,
    args: Dict[str, Any],
    plugin_base: str,
    ttl_seconds: int,
    clock: Callable[[], int] = lambda: int(time.time()),
) -> Dict[str, Any]:
    if tool_name != GATED_TOOL or not isinstance(args, dict):
        raise ToolCallError("only %s calls can be bound" % GATED_TOOL)
    if not 0 < ttl_seconds <= 3600:
        raise ToolCallError("ttl must be between 1 and 3600 seconds")
    if os.path.basename(os.path.normpath(plugin_base)) != "caduceus":
        raise ToolCallError("plugin base must be the plugin's own 'caduceus' directory (never a Hermes home root)")
    cdig = candidate.digest()
    if not any(e["body"].get("candidate_digest") == cdig for e in governance_ledger.find("candidate")):
        raise ToolCallError("candidate is not recorded in the governance ledger")
    verdicts = [e["body"] for e in governance_ledger.find("verdict") if e["body"].get("candidate_digest") == cdig]
    if not verdicts:
        raise ToolCallError("no verdict recorded for this candidate")
    latest = dict(verdicts[-1])  # a later non-accept verdict supersedes an earlier accept
    recorded = latest.pop("verdict_digest", None)
    verdict = verdict_from_dict(latest)
    if recorded != verdict.digest():
        raise ToolCallError("recorded verdict digest does not match its contents")
    if verdict.decision != "accept":
        raise ToolCallError("latest verdict is %s, not accept" % verdict.decision)
    from . import control

    if control.load(plugin_base).frozen:
        raise ToolCallError("evolution is frozen by the operator")
    os.makedirs(plugin_base, exist_ok=True)
    digest = call_digest(tool_name, args)
    token = secrets.token_hex(32)  # only its hash is recorded; the plugin matches on args_digest and single-use hash
    Ledger(os.path.join(plugin_base, "ledger.jsonl")).append(
        "capability_issued",
        {
            "token_sha256": sha256_hex(token.encode()),
            "candidate_digest": cdig,
            "verdict_digest": verdict.digest(),
            "args_digest": digest,
            "expires_at": clock() + ttl_seconds,
        },
    )
    return {"args_digest": digest, "candidate_digest": cdig, "verdict_digest": verdict.digest(), "expires_in_seconds": ttl_seconds}

"""Source admission for memory and learning signals (PRD 6.4).

Signals arrive as raw-content-free receipts:
    {tenant, window_start, window_end, consent_binding, source_kind,
     quality_receipt_digest, content_digest, poisoning_marker}
Decision, deterministic and fail-closed:
  reject      malformed receipt, unknown source kind, empty consent, bad window,
              duplicate content already admitted, or a conflict (same tenant +
              window + source with different content)
  quarantine  unknown quality (no quality receipt) or a declared poisoning marker
  accept      otherwise
This enforces *declared* policy. It does not verify identity, inspect content
or detect poisoning it was not told about.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .digest import is_hex_digest
from .ledger import Ledger

SOURCE_KINDS = ("conversation", "user_profile", "tool_result", "external_memory_provider", "curator")
_KEYS = {"tenant", "window_start", "window_end", "consent_binding", "source_kind",
         "quality_receipt_digest", "content_digest", "poisoning_marker"}


def _seen(ledger: Ledger) -> Tuple[set, Dict[Tuple[str, int, int, str], str]]:
    contents, slots = set(), {}
    for e in ledger.find("memory_event"):
        b = e["body"]
        if b.get("decision") != "accept":
            continue
        contents.add(b["content_digest"])
        slots[(b["tenant"], b["window_start"], b["window_end"], b["source_kind"])] = b["content_digest"]
    return contents, slots


def admit(ledger: Ledger, receipt: Any) -> Dict[str, Any]:
    reasons = []
    decision = "accept"
    if not isinstance(receipt, dict) or set(receipt) != _KEYS:
        return {"decision": "reject", "reasons": ["receipt_shape"]}
    r = receipt
    ints_ok = all(isinstance(r[k], int) and not isinstance(r[k], bool) for k in ("window_start", "window_end"))
    if not ints_ok or r["window_end"] < r["window_start"]:
        reasons.append("window_invalid")
    if r["source_kind"] not in SOURCE_KINDS:
        reasons.append("source_kind_unknown")
    if not isinstance(r["consent_binding"], str) or not r["consent_binding"]:
        reasons.append("consent_missing")
    if not isinstance(r["tenant"], str) or not r["tenant"]:
        reasons.append("tenant_missing")
    if not is_hex_digest(r["content_digest"]):
        reasons.append("content_digest_invalid")
    if not isinstance(r["poisoning_marker"], bool):
        reasons.append("poisoning_marker_invalid")
    if reasons:
        return {"decision": "reject", "reasons": reasons}
    contents, slots = _seen(ledger)
    slot = (r["tenant"], r["window_start"], r["window_end"], r["source_kind"])
    if r["content_digest"] in contents:
        decision, reasons = "reject", ["duplicate"]
    elif slot in slots and slots[slot] != r["content_digest"]:
        decision, reasons = "reject", ["conflict"]
    elif r["poisoning_marker"]:
        decision, reasons = "quarantine", ["declared_poisoning"]
    elif not is_hex_digest(r["quality_receipt_digest"] or ""):
        decision, reasons = "quarantine", ["quality_unknown"]
    ledger.append("memory_event", {
        "decision": decision, "reasons": reasons, "tenant": r["tenant"][:64],
        "window_start": r["window_start"], "window_end": r["window_end"], "source_kind": r["source_kind"],
        "content_digest": r["content_digest"],
    })
    return {"decision": decision, "reasons": reasons}

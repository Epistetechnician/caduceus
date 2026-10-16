"""Variant archive, lineage and tenant -> shared promotion (PRD 5.5, 6.8).

Archive, not just latest: every promoted variant is recorded as a lineage
manifest (metadata only) in the ledger:
    {skill, scope, tenant, parent_tree, child_tree, candidate_digest}
so any variant's ancestry is recoverable and the archive can be searched.

Topology: evolution is tenant-local by default (`scope: tenant`). Promoting a
tenant variant to the shared scope is a *separate* reviewed proposal:
  * optimistic parent check: the shared head must still be the parent the
    proposal was made against, else it is rejected (no lost updates);
  * it needs a verified review signature with role `shared-reviewer` by
    someone other than the tenant's proposer;
  * rollback is scoped: rolling back the shared head never touches tenant
    lineages, and vice versa.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .digest import is_hex_digest
from .ledger import Ledger

SCOPES = ("tenant", "shared")


class LineageError(Exception):
    pass


def _manifests(ledger: Ledger) -> List[Dict[str, Any]]:
    return [e["body"] for e in ledger.find("audit_note") if e["body"].get("event") == "lineage"]


def head(ledger: Ledger, skill: str, scope: str, tenant: str = "") -> Optional[str]:
    """Current tree digest for (skill, scope[, tenant]), honouring scoped rollbacks."""
    cur: Optional[str] = None
    for m in _manifests(ledger):
        if m["skill"] != skill or m["scope"] != scope or (scope == "tenant" and m["tenant"] != tenant):
            continue
        cur = m["child_tree"] if m["op"] == "promote" else m["parent_tree"]
    return cur


def record_promotion(ledger: Ledger, skill: str, scope: str, tenant: str, parent_tree: str,
                     child_tree: str, candidate_digest: str) -> Dict[str, Any]:
    if scope not in SCOPES:
        raise LineageError("unknown scope")
    if not all(is_hex_digest(x) for x in (parent_tree, child_tree, candidate_digest)):
        raise LineageError("digests must be hex")
    current = head(ledger, skill, scope, tenant)
    if current is not None and current != parent_tree:
        raise LineageError("stale parent: head is %s" % current[:12])
    body = {"event": "lineage", "op": "promote", "skill": skill, "scope": scope,
            "tenant": tenant if scope == "tenant" else "", "parent_tree": parent_tree,
            "child_tree": child_tree, "candidate_digest": candidate_digest}
    ledger.append("audit_note", body)
    return body


def promote_to_shared(ledger: Ledger, skill: str, tenant: str, expected_shared_parent: str,
                      signatures: List[Dict[str, str]], proposer: str) -> Dict[str, Any]:
    """Promote a tenant's head to shared. Signatures must be pre-verified (signing.verified_signatures)."""
    tenant_head = head(ledger, skill, "tenant", tenant)
    if tenant_head is None:
        raise LineageError("tenant has no variant of %s" % skill)
    reviewers = {s["signer"] for s in signatures if s.get("role") == "shared-reviewer" and s.get("signer") != proposer}
    if not reviewers:
        raise LineageError("needs a shared-reviewer signature by someone other than the proposer")
    tenant_m = [m for m in _manifests(ledger) if m["scope"] == "tenant" and m["tenant"] == tenant
                and m["skill"] == skill and m["op"] == "promote" and m["child_tree"] == tenant_head]
    return record_promotion(ledger, skill, "shared", "", expected_shared_parent, tenant_head,
                            tenant_m[-1]["candidate_digest"])


def rollback(ledger: Ledger, skill: str, scope: str, tenant: str = "") -> Dict[str, Any]:
    """Scoped rollback of the latest promotion in exactly one scope."""
    ms = [m for m in _manifests(ledger) if m["skill"] == skill and m["scope"] == scope
          and (scope != "tenant" or m["tenant"] == tenant)]
    if not ms or ms[-1]["op"] != "promote":
        raise LineageError("nothing to roll back in this scope")
    last = ms[-1]
    body = dict(last, op="rollback")
    ledger.append("audit_note", body)
    return body


def ancestry(ledger: Ledger, skill: str, tree: str) -> List[str]:
    """Walk parent links from `tree` back to the first recorded parent."""
    parents: Dict[str, str] = {}
    for m in _manifests(ledger):  # first record wins: the variant's origin, not a later re-scoping
        if m["skill"] == skill and m["op"] == "promote":
            parents.setdefault(m["child_tree"], m["parent_tree"])
    chain, seen = [tree], {tree}
    while chain[-1] in parents and parents[chain[-1]] not in seen:
        chain.append(parents[chain[-1]])
        seen.add(chain[-1])
    return chain

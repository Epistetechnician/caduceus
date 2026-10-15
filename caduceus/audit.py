"""Read-only audit of a Hermes home: skills, usage, and the curator ledger.

Never writes inside the audited home. Reports counts and digests only (no skill
bodies). The curator ledger is "telemetry, not a gate" in Hermes and is not
hash-chained; `anchor_lines` gives it a Merkle root so later rewrites of the
anchored prefix become detectable.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from . import merkle, skills
from .digest import domain_digest


def _load_json(path: str) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def curator_ledger_lines(path: str) -> List[bytes]:
    if not os.path.isfile(path):
        return []
    with open(path, "rb") as fh:
        return [ln for ln in fh.read().split(b"\n") if ln]


def anchor_lines(lines: List[bytes]) -> Dict[str, Any]:
    leaves = [merkle.leaf_hash(hashlib.sha256(ln).hexdigest()) for ln in lines]
    return {"size": len(lines), "root": merkle.root_hex(leaves)}


def verify_append_only(lines: List[bytes], anchored_size: int, anchored_root: str) -> bool:
    """True iff the first `anchored_size` lines still hash to the anchored root."""
    if len(lines) < anchored_size:
        return False
    return anchor_lines(lines[:anchored_size])["root"] == anchored_root


def check_curator_entries(lines: List[bytes]) -> Dict[str, Any]:
    required = {"id", "ts", "actor", "action", "skill"}
    bad = 0
    actors: Counter = Counter()
    actions: Counter = Counter()
    ids = set()
    dup_ids = 0
    for ln in lines:
        try:
            obj = json.loads(ln.decode("utf-8"))
        except ValueError:
            bad += 1
            continue
        if not isinstance(obj, dict) or not required <= set(obj):
            bad += 1
            continue
        actors[str(obj["actor"])] += 1
        actions[str(obj["action"])] += 1
        if obj["id"] in ids:
            dup_ids += 1
        ids.add(obj["id"])
    return {
        "entries": len(lines),
        "malformed": bad,
        "duplicate_ids": dup_ids,
        "by_actor": dict(sorted(actors.items())),
        "by_action": dict(sorted(actions.items())),
        "hash_chained": False,
    }


def audit_hermes_home(hermes_home: str, min_use_for_protected: int = 3) -> Dict[str, Any]:
    root = os.path.join(os.path.expanduser(hermes_home), "skills")
    report: Dict[str, Any] = {"skills_root_exists": os.path.isdir(root)}
    if not report["skills_root_exists"]:
        return report
    rel_dirs = skills.discover(root)
    usage = _load_json(os.path.join(root, ".usage.json")) or {}
    by_state: Counter = Counter()
    by_creator: Counter = Counter()
    protected: List[Dict[str, Any]] = []
    errors = 0
    digests: Dict[str, List[str]] = {}
    for rel in rel_dirs:
        try:
            rec = skills.load_skill(root, rel)
        except skills.SkillError:
            errors += 1
            continue
        digests.setdefault(rec.tree_digest, []).append(rec.name)
        u = usage.get(rec.name) or usage.get(os.path.basename(rel)) or {}
        by_state[str(u.get("state", "unknown"))] += 1
        by_creator[str(u.get("created_by") or "unset")] += 1
        if (
            u.get("use_count", 0) >= min_use_for_protected
            and not u.get("archived_at")
            and u.get("state") != "archived"
        ):
            protected.append(
                {
                    "name": rec.name,
                    "tree_digest": rec.tree_digest,
                    "use_count": int(u.get("use_count", 0)),
                    "patch_count": int(u.get("patch_count", 0)),
                    "has_contract": False,
                }
            )
    protected.sort(key=lambda p: (-p["use_count"], p["name"]))
    ledger_lines = curator_ledger_lines(os.path.join(root, ".curator_ledger.jsonl"))
    report.update(
        {
            "skill_count": len(rel_dirs),
            "unreadable": errors,
            "by_state": dict(sorted(by_state.items())),
            "by_creator": dict(sorted(by_creator.items())),
            "duplicate_content_groups": sum(1 for v in digests.values() if len(v) > 1),
            "protected_candidates": protected,
            "curator_ledger": check_curator_entries(ledger_lines),
            "curator_anchor": anchor_lines(ledger_lines),
            "skills_without_contracts": len(rel_dirs) - errors,
            "claim": "LocalDevelopmentAudit; counts and digests only; read-only",
        }
    )
    report["inventory_digest"] = domain_digest(
        "caduceus.audit.inventory.v1",
        sorted([{"d": d, "n": sorted(n)} for d, n in digests.items()], key=lambda x: x["d"]),
    )
    return report

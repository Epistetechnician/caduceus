"""Append-only, digest-chained, Merkle-anchored Evolution Ledger.

Stores digests and bounded metadata only. Raw payloads (transcripts, skill
bodies, model output) never enter the ledger; bodies that look like raw payloads
are rejected.

Single-writer model: one process appends at a time. Concurrent writers are not
supported and are detected on the next append by re-verifying the tail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import merkle
from .digest import DigestError, canonical_bytes, domain_digest, is_hex_digest, loads_strict

ENTRY_DOMAIN = "caduceus.ledger.entry.v1"
GENESIS_PREV = "0" * 64
MAX_BODY_BYTES = 64 * 1024
MAX_STRING = 4096

KINDS = (
    "candidate",
    "holdout_commitment",
    "prediction_lock",
    "assessment",
    "verdict",
    "lyre_report",
    "capability_issued",
    "capability_consumed",
    "review_signature",
    "snapshot",
    "release",
    "rollback",
    "canary_observation",
    "breaker_event",
    "memory_event",
    "audit_note",
)

FORBIDDEN_KEYS = frozenset(
    {
        "raw",
        "raw_payload",
        "transcript",
        "messages",
        "prompt",
        "completion",
        "secret",
        "api_key",
        "password",
        "token",
        "authorization",
        "credential",
    }
)


class LedgerError(Exception):
    """Ledger is malformed, tampered, or an append was rejected."""


def _scan_body(value: Any, path: str = "body") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in FORBIDDEN_KEYS:
                raise LedgerError("forbidden key %r at %s" % (key, path))
            _scan_body(item, path + "." + key)
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _scan_body(item, "%s[%d]" % (path, i))
    elif isinstance(value, str) and len(value) > MAX_STRING:
        raise LedgerError("string too long at %s (raw payloads are not allowed)" % path)


def entry_digest(seq: int, kind: str, prev: str, body: Dict[str, Any]) -> str:
    return domain_digest(
        ENTRY_DOMAIN, {"seq": seq, "kind": kind, "prev": prev, "body": body}
    )


@dataclass(frozen=True)
class TreeHead:
    size: int
    root: str
    last_digest: str

    def as_dict(self) -> Dict[str, Any]:
        return {"size": self.size, "root": self.root, "last_digest": self.last_digest}


class Ledger:
    def __init__(self, path: str):
        self.path = os.fspath(path)
        self._entries: List[Dict[str, Any]] = []
        if os.path.exists(self.path):
            self._load()

    # -- loading / verification -------------------------------------------------
    def _load(self) -> None:
        entries: List[Dict[str, Any]] = []
        prev = GENESIS_PREV
        with open(self.path, "r", encoding="ascii") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.endswith("\n"):
                    raise LedgerError("line %d: missing trailing newline" % lineno)
                try:
                    obj = loads_strict(line)
                except DigestError as exc:
                    raise LedgerError("line %d: %s" % (lineno, exc)) from exc
                self._check_entry(obj, lineno, prev)
                if canonical_bytes(obj) + b"\n" != line.encode("ascii"):
                    raise LedgerError("line %d: not in canonical form" % lineno)
                entries.append(obj)
                prev = obj["digest"]
        self._entries = entries

    @staticmethod
    def _check_entry(obj: Any, lineno: int, prev: str) -> None:
        if not isinstance(obj, dict) or set(obj) != {"seq", "kind", "prev", "body", "digest"}:
            raise LedgerError("line %d: unexpected entry fields" % lineno)
        if obj["seq"] != lineno - 1:
            raise LedgerError("line %d: sequence mismatch" % lineno)
        if obj["kind"] not in KINDS:
            raise LedgerError("line %d: unknown kind %r" % (lineno, obj["kind"]))
        if obj["prev"] != prev:
            raise LedgerError("line %d: broken chain" % lineno)
        if not isinstance(obj["body"], dict):
            raise LedgerError("line %d: body must be an object" % lineno)
        if not is_hex_digest(obj["digest"]):
            raise LedgerError("line %d: malformed digest" % lineno)
        if entry_digest(obj["seq"], obj["kind"], obj["prev"], obj["body"]) != obj["digest"]:
            raise LedgerError("line %d: digest mismatch" % lineno)

    # -- reading ----------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    def _leaves(self, size: Optional[int] = None) -> List[bytes]:
        subset = self._entries if size is None else self._entries[:size]
        return [merkle.leaf_hash(e["digest"]) for e in subset]

    def head(self, size: Optional[int] = None) -> TreeHead:
        if size is None:
            size = len(self._entries)
        if not 0 <= size <= len(self._entries):
            raise LedgerError("tree size out of range")
        leaves = self._leaves(size)
        last = self._entries[size - 1]["digest"] if size else GENESIS_PREV
        return TreeHead(size=size, root=merkle.root_hex(leaves), last_digest=last)

    def inclusion_proof(self, seq: int) -> List[str]:
        leaves = self._leaves()
        return [p.hex() for p in merkle.inclusion_proof(seq, leaves)]

    def consistency_proof(self, first_size: int) -> List[str]:
        leaves = self._leaves()
        return [p.hex() for p in merkle.consistency_proof(first_size, leaves)]

    def find(self, kind: str) -> List[Dict[str, Any]]:
        return [e for e in self._entries if e["kind"] == kind]

    # -- writing ----------------------------------------------------------------
    def append(self, kind: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if kind not in KINDS:
            raise LedgerError("unknown kind %r" % kind)
        if not isinstance(body, dict):
            raise LedgerError("body must be an object")
        _scan_body(body)
        try:
            body_bytes = canonical_bytes(body)
        except DigestError as exc:
            raise LedgerError(str(exc)) from exc
        if len(body_bytes) > MAX_BODY_BYTES:
            raise LedgerError("body exceeds %d bytes" % MAX_BODY_BYTES)
        # Detect concurrent writers: file must still match what we hold.
        self._verify_tail()
        prev = self._entries[-1]["digest"] if self._entries else GENESIS_PREV
        seq = len(self._entries)
        entry = {
            "seq": seq,
            "kind": kind,
            "prev": prev,
            "body": body,
            "digest": entry_digest(seq, kind, prev, body),
        }
        line = canonical_bytes(entry) + b"\n"
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        self._entries.append(entry)
        return entry

    def _verify_tail(self) -> None:
        if not self._entries:
            if os.path.exists(self.path) and os.path.getsize(self.path) != 0:
                raise LedgerError("ledger file changed underneath this writer")
            return
        expected = sum(len(canonical_bytes(e)) + 1 for e in self._entries)
        if not os.path.exists(self.path) or os.path.getsize(self.path) != expected:
            raise LedgerError("ledger file changed underneath this writer")

"""Canonical JSON and domain-separated digests.

Values allowed in digested documents: dict (str keys), list, str, int, bool,
None. Floats are rejected on purpose: ledger bodies use fixed-point integers so
that canonical bytes never depend on float formatting.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_DEPTH = 32


class DigestError(ValueError):
    """Raised for values that cannot be canonically digested."""


def _check(value: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise DigestError("document nesting too deep")
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        raise DigestError("floats are not allowed in digested documents")
    if isinstance(value, (list, tuple)):
        for item in value:
            _check(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DigestError("object keys must be strings")
            _check(item, depth + 1)
        return
    raise DigestError("unsupported type: %s" % type(value).__name__)


def canonical_bytes(value: Any) -> bytes:
    """Compact, key-sorted, ASCII-escaped JSON bytes."""
    _check(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def domain_digest(domain: str, value: Any) -> str:
    """sha256(domain || 0x00 || canonical_bytes(value)) as lowercase hex."""
    return sha256_hex(domain.encode("ascii") + b"\x00" + canonical_bytes(value))


def _reject_duplicates(pairs):
    seen = {}
    for key, val in pairs:
        if key in seen:
            raise DigestError("duplicate object key: %r" % key)
        seen[key] = val
    return seen


def _reject_float(text):
    raise DigestError("floats are not allowed: %s" % text)


def _reject_constant(text):
    raise DigestError("non-finite constant: %s" % text)


def loads_strict(text: str) -> Any:
    """Parse JSON rejecting duplicate keys, floats and NaN/Infinity."""
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise DigestError("invalid JSON: %s" % exc) from exc
    _check(value)
    return value


def is_hex_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )

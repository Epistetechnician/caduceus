"""Independent ledger readback validator.

Deliberately does NOT import caduceus.ledger, caduceus.digest or
caduceus.merkle. It re-implements canonical JSON encoding by hand and the
Merkle root iteratively, so a bug in the writer's code path does not silently
validate itself. Shared axes (disclosed): same language, same standard-library
SHA-256 and JSON *parser*. Agreement is shown by differential tests, not proof.
"""

from __future__ import annotations

import hashlib
import json

_ENTRY_DOMAIN = b"caduceus.ledger.entry.v1"
_KINDS = frozenset(
    (
        "candidate holdout_commitment prediction_lock assessment verdict lyre_report "
        "capability_issued capability_consumed review_signature snapshot release rollback "
        "canary_observation breaker_event memory_event audit_note"
    ).split()
)
_HEX = frozenset("0123456789abcdef")


class ReadbackError(Exception):
    pass


def enc(value, depth=0):
    """Hand-written canonical encoder (sorted keys, compact, ASCII only)."""
    if depth > 32:
        raise ReadbackError("too deep")
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _enc_str(value)
    if isinstance(value, list):
        return "[" + ",".join(enc(v, depth + 1) for v in value) + "]"
    if isinstance(value, dict):
        parts = []
        for key in sorted(value):
            if not isinstance(key, str):
                raise ReadbackError("non-string key")
            parts.append(_enc_str(key) + ":" + enc(value[key], depth + 1))
        return "{" + ",".join(parts) + "}"
    raise ReadbackError("unsupported type")


_SHORT = {0x22: '\\"', 0x5C: "\\\\", 0x08: "\\b", 0x0C: "\\f", 0x0A: "\\n", 0x0D: "\\r", 0x09: "\\t"}


def _enc_str(text):
    out = ['"']
    for ch in text:
        cp = ord(ch)
        if cp in _SHORT:
            out.append(_SHORT[cp])
        elif 0x20 <= cp <= 0x7E:
            out.append(ch)
        elif cp < 0x10000:
            out.append("\\u%04x" % cp)
        else:
            cp -= 0x10000
            out.append("\\u%04x\\u%04x" % (0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)))
    out.append('"')
    return "".join(out)


def _no_dups(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ReadbackError("duplicate key %r" % k)
        d[k] = v
    return d


def _bad_num(_):
    raise ReadbackError("float or non-finite number")


def _digest(seq, kind, prev, body):
    doc = {"seq": seq, "kind": kind, "prev": prev, "body": body}
    return hashlib.sha256(_ENTRY_DOMAIN + b"\x00" + enc(doc).encode("ascii")).hexdigest()


def _merkle_root(digests):
    """Iterative RFC 6962 root computed with a stack (no recursion)."""
    if not digests:
        return hashlib.sha256(b"").hexdigest()
    stack = []  # (hash, subtree_size)
    for d in digests:
        node = (hashlib.sha256(b"\x00" + bytes.fromhex(d)).digest(), 1)
        while stack and stack[-1][1] == node[1]:
            left = stack.pop()
            node = (hashlib.sha256(b"\x01" + left[0] + node[0]).digest(), left[1] * 2)
        stack.append(node)
    h, _ = stack.pop()
    while stack:
        left = stack.pop()
        h = hashlib.sha256(b"\x01" + left[0] + h).digest()
    return h.hex()


def verify_file(path):
    """Validate a ledger file. Returns {'size', 'root', 'last_digest'} or raises."""
    digests = []
    prev = "0" * 64
    with open(path, "rb") as fh:
        data = fh.read()
    if data and not data.endswith(b"\n"):
        raise ReadbackError("missing trailing newline")
    for lineno, raw in enumerate(data.split(b"\n")[:-1] if data else [], 1):
        try:
            obj = json.loads(
                raw.decode("ascii"),
                object_pairs_hook=_no_dups,
                parse_float=_bad_num,
                parse_constant=_bad_num,
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReadbackError("line %d: %s" % (lineno, exc)) from exc
        if not isinstance(obj, dict) or set(obj) != {"seq", "kind", "prev", "body", "digest"}:
            raise ReadbackError("line %d: bad fields" % lineno)
        if obj["seq"] != lineno - 1 or isinstance(obj["seq"], bool):
            raise ReadbackError("line %d: bad seq" % lineno)
        if obj["kind"] not in _KINDS:
            raise ReadbackError("line %d: bad kind" % lineno)
        if obj["prev"] != prev:
            raise ReadbackError("line %d: broken chain" % lineno)
        if not isinstance(obj["body"], dict):
            raise ReadbackError("line %d: body not object" % lineno)
        dig = obj["digest"]
        if not (isinstance(dig, str) and len(dig) == 64 and set(dig) <= _HEX):
            raise ReadbackError("line %d: malformed digest" % lineno)
        if _digest(obj["seq"], obj["kind"], obj["prev"], obj["body"]) != dig:
            raise ReadbackError("line %d: digest mismatch" % lineno)
        if enc(obj).encode("ascii") != raw:
            raise ReadbackError("line %d: not canonical" % lineno)
        digests.append(dig)
        prev = dig
    return {
        "size": len(digests),
        "root": _merkle_root(digests),
        "last_digest": digests[-1] if digests else "0" * 64,
    }

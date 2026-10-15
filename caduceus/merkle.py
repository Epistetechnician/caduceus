"""RFC 6962 / RFC 9162 style Merkle tree over ledger entry digests.

Leaf hash = SHA-256(0x00 || entry_digest_bytes); node = SHA-256(0x01 || l || r).
Inclusion and consistency proofs follow RFC 9162 section 2.1.
"""

from __future__ import annotations

import hashlib
from typing import List, Sequence

EMPTY_ROOT = hashlib.sha256(b"").hexdigest()


def _h(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def leaf_hash(entry_digest_hex: str) -> bytes:
    return _h(b"\x00" + bytes.fromhex(entry_digest_hex))


def _node(left: bytes, right: bytes) -> bytes:
    return _h(b"\x01" + left + right)


def _split(n: int) -> int:
    """Largest power of two strictly less than n (n >= 2)."""
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def root(leaves: Sequence[bytes]) -> bytes:
    n = len(leaves)
    if n == 0:
        return _h(b"")
    if n == 1:
        return leaves[0]
    k = _split(n)
    return _node(root(leaves[:k]), root(leaves[k:]))


def root_hex(leaves: Sequence[bytes]) -> str:
    return root(leaves).hex()


def inclusion_proof(index: int, leaves: Sequence[bytes]) -> List[bytes]:
    n = len(leaves)
    if not 0 <= index < n:
        raise IndexError("leaf index out of range")
    if n == 1:
        return []
    k = _split(n)
    if index < k:
        return inclusion_proof(index, leaves[:k]) + [root(leaves[k:])]
    return inclusion_proof(index - k, leaves[k:]) + [root(leaves[:k])]


def verify_inclusion(
    index: int, size: int, leaf: bytes, proof: Sequence[bytes], expected_root: bytes
) -> bool:
    if not 0 <= index < size:
        return False
    fn, sn = index, size - 1
    r = leaf
    for p in proof:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = _node(p, r)
            while not (fn & 1) and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            r = _node(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == expected_root


def _subproof(m: int, leaves: Sequence[bytes], complete: bool) -> List[bytes]:
    n = len(leaves)
    if m == n:
        return [] if complete else [root(leaves)]
    k = _split(n)
    if m <= k:
        return _subproof(m, leaves[:k], complete) + [root(leaves[k:])]
    return _subproof(m - k, leaves[k:], False) + [root(leaves[:k])]


def consistency_proof(first: int, leaves: Sequence[bytes]) -> List[bytes]:
    n = len(leaves)
    if not 0 <= first <= n:
        raise IndexError("first tree size out of range")
    if first == 0 or first == n:
        return []
    return _subproof(first, leaves, True)


def verify_consistency(
    first: int,
    second: int,
    first_root: bytes,
    second_root: bytes,
    proof: Sequence[bytes],
) -> bool:
    if first < 0 or second < first:
        return False
    if first == second:
        return not proof and first_root == second_root
    if first == 0:
        return not proof and first_root == _h(b"")
    proof = list(proof)
    if first & (first - 1) == 0:
        proof = [first_root] + proof
    if not proof:
        return False
    fn, sn = first - 1, second - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1
    fr = sr = proof[0]
    for c in proof[1:]:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            fr = _node(c, fr)
            sr = _node(c, sr)
            while not (fn & 1) and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            sr = _node(sr, c)
        fn >>= 1
        sn >>= 1
    return sn == 0 and fr == first_root and sr == second_root

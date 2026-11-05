"""Ed25519 (RFC 8032, section 5.1) in pure standard-library Python, for operator review signatures (PRD R22).

Why this exists: the plugin may add no dependency and the standard library has no asymmetric signatures. HMAC proves "a keyring holder approved"; Ed25519 lets the
verifier hold only public keys, so the machine that checks a signature cannot forge one.

Limits, stated plainly: this is a plain big-integer implementation, NOT constant-time, so it must not be used where an attacker can time private-key operations
(signing happens on the operator's own machine, offline; verification touches only public data). It is checked against the RFC 8032 test vectors and, in development,
against an independent implementation, but it has had no external review.
"""

from __future__ import annotations

import hashlib

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
I = pow(2, (P - 1) // 4, P)  # sqrt(-1)


def _inv(x: int) -> int:
    return pow(x, P - 2, P)


def _recover_x(y: int, sign: int):
    if y >= P:
        return None
    x2 = (y * y - 1) * _inv(D * y * y + 1) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * I % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


_GY = 4 * _inv(5) % P
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % P)  # extended coordinates (X, Y, Z, T)


def _add(a, b):
    A, B = (a[1] - a[0]) * (b[1] - b[0]) % P, (a[1] + a[0]) * (b[1] + b[0]) % P
    C, Dd = 2 * a[3] * b[3] * D % P, 2 * a[2] * b[2] % P
    E, F, Gg, H = B - A, Dd - C, Dd + C, B + A
    return (E * F % P, Gg * H % P, F * Gg % P, E * H % P)


def _mul(s: int, pt):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _equal(a, b) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


def _compress(pt) -> bytes:
    zi = _inv(pt[2])
    x, y = pt[0] * zi % P, pt[1] * zi % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


def _h(m: bytes) -> bytes:
    return hashlib.sha512(m).digest()


def _clamp(h: bytes) -> int:
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    return a | (1 << 254)


def public_key(seed: bytes) -> bytes:
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    return _compress(_mul(_clamp(_h(seed)), G))


def sign(seed: bytes, msg: bytes) -> bytes:
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    h = _h(seed)
    a, prefix = _clamp(h), h[32:]
    A = _compress(_mul(a, G))
    r = int.from_bytes(_h(prefix + msg), "little") % L
    R = _compress(_mul(r, G))
    k = int.from_bytes(_h(R + A + msg), "little") % L
    return R + int.to_bytes((r + k * a) % L, 32, "little")


def verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    if len(pub) != 32 or len(sig) != 64:
        return False
    A = _decompress(pub)
    R = _decompress(sig[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= L:
        return False
    k = int.from_bytes(_h(sig[:32] + pub + msg), "little") % L
    return _equal(_mul(s, G), _add(R, _mul(k, A)))

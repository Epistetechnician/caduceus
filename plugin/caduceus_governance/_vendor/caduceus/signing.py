"""Human review signatures for code patches (PRD 6.7.12, 9.2).

A signature is over (candidate digest, role) with a per-signer key held in an owner-only keyring file outside the repository. Two schemes:

* hmac (default, legacy): HMAC-SHA256 with a symmetric per-signer key. Whoever holds the keyring can forge any signer in it, so this proves "a keyring holder
  approved", not who.
* ed25519 (PRD R22): a pure-standard-library RFC 8032 implementation (caduceus/ed25519.py). The signer keeps the seed; a verifier needs only a public-only keyring
  (`export-public`), so the machine that checks signatures, such as the broker host, cannot forge them. The implementation is not constant-time and unreviewed; signing
  is an offline operator action.

The ledger stores signer, role, candidate digest and the MAC or signature (plus the public key for ed25519); never a private key.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import stat
from typing import Dict, List

from . import ed25519
from .digest import domain_digest, is_hex_digest
from .ledger import Ledger

DOMAIN = "caduceus.review_signature.v1"


class SigningError(Exception):
    pass


def _message(candidate_digest: str, role: str) -> bytes:
    return domain_digest(DOMAIN, {"candidate_digest": candidate_digest, "role": role}).encode("ascii")


def _is_hex(v, n) -> bool:
    return isinstance(v, str) and len(v) == n and all(c in "0123456789abcdef" for c in v)


def _valid_entry(entry) -> bool:
    if not isinstance(entry, dict) or not isinstance(entry.get("role"), str):
        return False
    keys = set(entry)
    if keys == {"role", "key_hex"}:
        return _is_hex(entry["key_hex"], 64)
    if entry.get("scheme") == "ed25519":
        if keys == {"role", "scheme", "seed_hex", "pub_hex"}:
            return _is_hex(entry["seed_hex"], 64) and _is_hex(entry["pub_hex"], 64) and ed25519.public_key(bytes.fromhex(entry["seed_hex"])).hex() == entry["pub_hex"]
        return keys == {"role", "scheme", "pub_hex"} and _is_hex(entry["pub_hex"], 64)
    return False


def load_keyring(path: str) -> Dict[str, Dict[str, str]]:
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SigningError("keyring must be owner-only (chmod 600)")
    with open(path, "r", encoding="utf-8") as fh:
        ring = json.load(fh)
    if not isinstance(ring, dict):
        raise SigningError("keyring must be an object")
    for signer, entry in ring.items():
        if not _valid_entry(entry):
            raise SigningError("bad keyring entry for %r" % signer)
    return ring


def _write_ring(path: str, ring: Dict[str, Dict[str, str]]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(ring, fh)


def add_signer(path: str, signer: str, role: str, scheme: str = "hmac") -> None:
    ring = load_keyring(path) if os.path.exists(path) else {}
    if signer in ring:
        raise SigningError("signer exists")
    if scheme == "hmac":
        ring[signer] = {"role": role, "key_hex": secrets.token_hex(32)}
    elif scheme == "ed25519":
        seed = secrets.token_bytes(32)
        ring[signer] = {"role": role, "scheme": "ed25519", "seed_hex": seed.hex(), "pub_hex": ed25519.public_key(seed).hex()}
    else:
        raise SigningError("unknown scheme")
    _write_ring(path, ring)


def export_public(path: str, out: str) -> int:
    """Write a public-only keyring (ed25519 signers) for verifier hosts. HMAC signers are symmetric and are never exported."""
    ring = load_keyring(path)
    pub = {k: {"role": v["role"], "scheme": "ed25519", "pub_hex": v["pub_hex"]} for k, v in ring.items() if v.get("scheme") == "ed25519"}
    _write_ring(out, pub)
    return len(pub)


def sign(ledger: Ledger, keyring_path: str, signer: str, candidate_digest: str) -> Dict[str, str]:
    if not is_hex_digest(candidate_digest):
        raise SigningError("candidate digest must be hex")
    entry = load_keyring(keyring_path).get(signer)
    if entry is None:
        raise SigningError("unknown signer")
    msg = _message(candidate_digest, entry["role"])
    if entry.get("scheme") == "ed25519":
        if "seed_hex" not in entry:
            raise SigningError("this keyring is public-only; it can verify but not sign")
        body = {"candidate_digest": candidate_digest, "signer": signer, "role": entry["role"], "scheme": "ed25519",
                "pub": entry["pub_hex"], "sig": ed25519.sign(bytes.fromhex(entry["seed_hex"]), msg).hex()}
    else:
        mac = hmac.new(bytes.fromhex(entry["key_hex"]), msg, "sha256").hexdigest()
        body = {"candidate_digest": candidate_digest, "signer": signer, "role": entry["role"], "mac": mac}
    ledger.append("review_signature", body)
    return body


def verified_signatures(ledger: Ledger, keyring_path: str, candidate_digest: str) -> List[Dict[str, str]]:
    """Signatures in the ledger for this candidate that verify against the keyring.

    A signature that fails to verify raises: a forged or corrupted review record is an incident, not something to skip quietly.
    """
    ring = load_keyring(keyring_path)
    out = []
    for e in ledger.find("review_signature"):
        b = e["body"]
        if b.get("candidate_digest") != candidate_digest:
            continue
        entry = ring.get(b.get("signer", ""))
        if entry is None or entry["role"] != b.get("role"):
            raise SigningError("signature by unknown signer or wrong role at seq %d" % e["seq"])
        msg = _message(candidate_digest, b["role"])
        if b.get("scheme") == "ed25519":
            ok = (entry.get("scheme") == "ed25519" and b.get("pub") == entry["pub_hex"] and _is_hex(b.get("sig"), 128)
                  and ed25519.verify(bytes.fromhex(entry["pub_hex"]), msg, bytes.fromhex(b["sig"])))
            if not ok:
                raise SigningError("signature does not verify at seq %d" % e["seq"])
        else:
            if "key_hex" not in entry:
                raise SigningError("hmac signature but the keyring holds no hmac key for that signer at seq %d" % e["seq"])
            want = hmac.new(bytes.fromhex(entry["key_hex"]), msg, "sha256").hexdigest()
            if not hmac.compare_digest(want, str(b.get("mac", ""))):
                raise SigningError("signature MAC mismatch at seq %d" % e["seq"])
        out.append({"signer": b["signer"], "role": b["role"]})
    return out

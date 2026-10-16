"""Human review signatures for code patches (PRD 6.7.12, 9.2).

A signature is HMAC-SHA256 over (candidate digest, role) with a per-signer
key held in an owner-only keyring file outside the repository. The ledger
stores signer, role, candidate digest and the MAC; never the key.

Limit, stated plainly: HMAC is symmetric. Whoever holds the keyring can forge
any signer in it, so this proves "a keyring holder approved", not who. Python's
standard library has no asymmetric signatures; an Ed25519/Sigstore upgrade is
the Phase 3 hardening item, and the witness/ledger claims do not change.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import stat
from typing import Dict, List

from .digest import domain_digest, is_hex_digest
from .ledger import Ledger

DOMAIN = "caduceus.review_signature.v1"


class SigningError(Exception):
    pass


def _message(candidate_digest: str, role: str) -> bytes:
    return domain_digest(DOMAIN, {"candidate_digest": candidate_digest, "role": role}).encode("ascii")


def load_keyring(path: str) -> Dict[str, Dict[str, str]]:
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SigningError("keyring must be owner-only (chmod 600)")
    with open(path, "r", encoding="utf-8") as fh:
        ring = json.load(fh)
    if not isinstance(ring, dict):
        raise SigningError("keyring must be an object")
    for signer, entry in ring.items():
        if not isinstance(entry, dict) or set(entry) != {"role", "key_hex"} or len(entry["key_hex"]) != 64:
            raise SigningError("bad keyring entry for %r" % signer)
    return ring


def add_signer(path: str, signer: str, role: str) -> None:
    ring = load_keyring(path) if os.path.exists(path) else {}
    if signer in ring:
        raise SigningError("signer exists")
    ring[signer] = {"role": role, "key_hex": secrets.token_hex(32)}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(ring, fh)


def sign(ledger: Ledger, keyring_path: str, signer: str, candidate_digest: str) -> Dict[str, str]:
    if not is_hex_digest(candidate_digest):
        raise SigningError("candidate digest must be hex")
    entry = load_keyring(keyring_path).get(signer)
    if entry is None:
        raise SigningError("unknown signer")
    mac = hmac.new(bytes.fromhex(entry["key_hex"]), _message(candidate_digest, entry["role"]), "sha256").hexdigest()
    body = {"candidate_digest": candidate_digest, "signer": signer, "role": entry["role"], "mac": mac}
    ledger.append("review_signature", body)
    return body


def verified_signatures(ledger: Ledger, keyring_path: str, candidate_digest: str) -> List[Dict[str, str]]:
    """Signatures in the ledger for this candidate whose MAC verifies.

    A MAC that fails to verify raises: a forged or corrupted review record is
    an incident, not something to skip quietly.
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
        want = hmac.new(bytes.fromhex(entry["key_hex"]), _message(candidate_digest, b["role"]), "sha256").hexdigest()
        if not hmac.compare_digest(want, str(b.get("mac", ""))):
            raise SigningError("signature MAC mismatch at seq %d" % e["seq"])
        out.append({"signer": b["signer"], "role": b["role"]})
    return out

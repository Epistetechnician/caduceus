"""Device-signed operator approvals (PRD R26): the operator's phone or laptop signs "release this exact held change", the host only verifies.

A held change can be released or discarded by a human. Without this module that human acts through the CLI (or any process running as the same user). With it a policy can
require (`approval_required`) that a release carries an Ed25519 signature from an enrolled DEVICE whose private key never leaves that device: a browser generates a
non-extractable key (WebCrypto), the host stores only the public key. The signature binds the action, the surface, the exact effect digest, the HEAD it was held against,
a random nonce and a short expiry, so it cannot be replayed, retargeted, or used after HEAD moved.

What this does and does not give you (stated plainly):
* It stops a forged or cross-site request to the dashboard API from approving anything, and it stops an agent that can only reach the gateway over HTTP.
* It does NOT stop an agent with a shell running as the same OS user from editing the device registry (devices.json) or policy.json: those are verified against the ledger, so
  a silent edit is detected and refused, but the same user can still append to the ledger. Real separation needs the registry and ledger out of the agent's reach
  (an OpenShell policy that makes the state directory read-only to the agent sandbox, or another OS user). The signature is the proof that a human with the device approved;
  the file permissions are what keep the agent from skipping the check.

The signed message is plain text built from fields with fixed character sets, so a browser and this module build the identical bytes without a canonical-JSON library.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from . import ed25519
from .digest import sha256_hex
from .ledger import Ledger

DOMAIN = "caduceus.approval.v1"
MAX_VALIDITY_S = 600
ACTIONS = ("release", "discard")
SURFACES = ("skills", "memory", "config")
DEVICES_FILE = "devices.json"
# \Z, not $: Python's $ also matches before a trailing newline, which would let a newline into the signed message or a device name.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}\Z")
HEX64 = re.compile(r"^[0-9a-f]{64}\Z")
HEX32 = re.compile(r"^[0-9a-f]{32}\Z")
HEX128 = re.compile(r"^[0-9a-f]{128}\Z")


class ApprovalError(Exception):
    pass


def message(action: str, surface: str, effect_digest: str, parent: str, nonce: str, expires: int) -> bytes:
    """The exact bytes a device signs. Every field has a fixed alphabet, so the encoding is unambiguous."""
    if action not in ACTIONS or surface not in SURFACES:
        raise ApprovalError("unknown action or surface")
    if not (HEX64.match(effect_digest or "") and HEX64.match(parent or "") and HEX32.match(nonce or "")):
        raise ApprovalError("effect digest, parent and nonce must be lowercase hex of the right length")
    if isinstance(expires, bool) or not isinstance(expires, int) or expires < 0:
        raise ApprovalError("expires must be a non-negative integer")
    return ("%s\n%s\n%s\n%s\n%s\n%s\n%d" % (DOMAIN, action, surface, effect_digest, parent, nonce, expires)).encode("ascii")


def fingerprint(pub_hex: str) -> str:
    """Short, human-comparable fingerprint of a public key, grouped for reading aloud: 'a1b2-c3d4-e5f6-0718'."""
    h = sha256_hex(bytes.fromhex(pub_hex))[:16]
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


# -- device registry: public keys only, ledgered, tamper-refused -------------------------------------------------------------------------------------------------
def _ledger(base: str) -> Ledger:
    return Ledger(os.path.join(base, "transitions.jsonl"))


def _registry_path(base: str) -> str:
    return os.path.join(base, DEVICES_FILE)


def _write_registry(base: str, reg: Dict[str, Dict[str, Any]]) -> None:
    os.makedirs(base, exist_ok=True)
    tmp = _registry_path(base) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(reg, fh, sort_keys=True)
    os.replace(tmp, _registry_path(base))


def _ledgered_devices(base: str) -> Dict[str, Dict[str, Any]]:
    """Replay device_added / device_removed notes: the registry the ledger says should exist."""
    out: Dict[str, Dict[str, Any]] = {}
    for e in _ledger(base).find("audit_note"):
        b = e["body"]
        if b.get("event") == "device_added":
            out[b["name"]] = {"pub_hex": b["pub_hex"], "label": b.get("label", "")}
        elif b.get("event") == "device_removed":
            out.pop(b.get("name", ""), None)
    return out


def add_device(base: str, name: str, pub_hex: str, operator: str, label: str = "") -> Dict[str, str]:
    if not NAME_RE.match(name or ""):
        raise ApprovalError("device name must be 1-32 characters of a-z 0-9 . _ - and start with a letter or digit")
    if not HEX64.match(pub_hex or ""):
        raise ApprovalError("public key must be 64 lowercase hex characters (an Ed25519 public key)")
    if not isinstance(label, str) or len(label) > 64:
        raise ApprovalError("label must be at most 64 characters")
    reg = load_devices(base)
    if name in reg:
        raise ApprovalError("a device named %r is already enrolled" % name)
    if any(v["pub_hex"] == pub_hex for v in reg.values()):
        raise ApprovalError("that public key is already enrolled under another name")
    _ledger(base).append("audit_note", {"event": "device_added", "name": name, "pub_hex": pub_hex, "label": label, "operator": operator[:64], "at": int(time.time())})
    reg[name] = {"pub_hex": pub_hex, "label": label}
    _write_registry(base, reg)
    return {"name": name, "fingerprint": fingerprint(pub_hex)}


def remove_device(base: str, name: str, operator: str) -> Dict[str, str]:
    reg = load_devices(base)
    if name not in reg:
        raise ApprovalError("no such device")
    _ledger(base).append("audit_note", {"event": "device_removed", "name": name, "operator": operator[:64], "at": int(time.time())})
    reg.pop(name)
    _write_registry(base, reg)
    return {"removed": name}


def load_devices(base: str) -> Dict[str, Dict[str, Any]]:
    """The enrolled devices. devices.json must equal what the ledger says; a silent edit (a device slipped in, or one removed) is refused, never trusted."""
    ledgered = _ledgered_devices(base)
    path = _registry_path(base)
    if not os.path.exists(path):
        if ledgered:
            raise ApprovalError("devices.json is missing but the ledger enrolled devices; restore it or re-enrol")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            reg = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ApprovalError("devices.json unreadable: %s" % type(exc).__name__)
    if not isinstance(reg, dict) or reg != ledgered:
        raise ApprovalError("devices.json does not match the ledgered enrolments; a silent edit is refused")
    return reg


def list_devices(base: str) -> List[Dict[str, str]]:
    adds = {}
    for e in _ledger(base).find("audit_note"):
        b = e["body"]
        if b.get("event") == "device_added":
            adds[b["name"]] = b.get("at", 0)
    return [{"name": n, "fingerprint": fingerprint(v["pub_hex"]), "label": v.get("label", ""), "added_at": adds.get(n, 0)} for n, v in sorted(load_devices(base).items())]


# -- verification --------------------------------------------------------------------------------------------------------------------------------------------------
def used_nonces(kernel_ledger: Ledger) -> set:
    return {e["body"].get("nonce") for e in kernel_ledger.find("audit_note") if e["body"].get("event") == "approval_used"}


def verify(devices_dir: str, kernel_ledger: Ledger, approval: Dict[str, Any], action: str, surface: str, effect_digest: str, parent: str, now: Optional[int] = None) -> str:
    """Return the enrolled device name if `approval` is a valid, unexpired, unused signature over exactly this action; raise ApprovalError otherwise."""
    if not isinstance(approval, dict):
        raise ApprovalError("approval must be an object")
    name, nonce, sig, expires = approval.get("device"), approval.get("nonce"), approval.get("sig"), approval.get("expires")
    if not (isinstance(name, str) and isinstance(nonce, str) and isinstance(sig, str)):
        raise ApprovalError("approval needs device, nonce, expires and sig")
    if not HEX128.match(sig):
        raise ApprovalError("malformed signature")
    now = int(time.time()) if now is None else now
    if isinstance(expires, bool) or not isinstance(expires, int):
        raise ApprovalError("expires must be an integer")
    if expires < now:
        raise ApprovalError("approval expired")
    if expires > now + MAX_VALIDITY_S:
        raise ApprovalError("approval validity is longer than %d seconds" % MAX_VALIDITY_S)
    reg = load_devices(devices_dir)
    if name not in reg:
        raise ApprovalError("unknown device")
    if nonce in used_nonces(kernel_ledger):
        raise ApprovalError("approval already used")
    msg = message(action, surface, effect_digest, parent, nonce, expires)
    if not ed25519.verify(bytes.fromhex(reg[name]["pub_hex"]), msg, bytes.fromhex(sig)):
        raise ApprovalError("signature does not verify")
    return name


def record_use(kernel_ledger: Ledger, device: str, action: str, effect_digest: str, nonce: str) -> None:
    kernel_ledger.append("audit_note", {"event": "approval_used", "device": device, "action": action, "effect_digest": effect_digest, "nonce": nonce, "at": int(time.time())})


def sign_with_keyring(keyring_path: str, signer: str, action: str, surface: str, effect_digest: str, parent: str, now: Optional[int] = None, ttl_s: int = 300) -> Dict[str, Any]:
    """Build an approval with an ed25519 signer from a local keyring (the CLI path; browsers sign with WebCrypto instead)."""
    import secrets

    from . import signing

    entry = signing.load_keyring(keyring_path).get(signer)
    if not entry or entry.get("scheme") != "ed25519" or "seed_hex" not in entry:
        raise ApprovalError("signer must be an ed25519 signer with a private key in that keyring")
    nonce, expires = secrets.token_hex(16), (int(time.time()) if now is None else now) + min(ttl_s, MAX_VALIDITY_S)
    sig = ed25519.sign(bytes.fromhex(entry["seed_hex"]), message(action, surface, effect_digest, parent, nonce, expires)).hex()
    return {"device": signer, "nonce": nonce, "expires": expires, "sig": sig}

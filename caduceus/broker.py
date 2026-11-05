"""Broker: applies an admitted change in its own process (PRD 5.6, Phase 3).

The caller (Hermes side, plugin, CLI) never writes governed state. It sends one
JSON request to `python -I broker.py <repo>` on stdin and gets one JSON reply.
The broker trusts nothing in the request:

  * candidate and verdict are re-parsed strictly and their digests recomputed;
  * the writes must hash to the candidate's `content_digest`, so a capability
    for one content cannot carry another;
  * the capability must exist in the ledger, be unconsumed, unexpired, bound
    to this candidate AND to this exact verdict digest (a forged `accept`
    verdict cannot ride a real token);
  * clock is the broker's own wall clock, never the caller's;
  * operator controls are re-read: frozen or `refinery` off => refuse;
    `code_patch` additionally needs `self_patch` on and verified review
    signatures from the keyring;
  * writes are bounded (count, bytes), confined to declared paths under root
    (Refinery rejects escapes and symlinks), and followed by a retention check
    that protected files are byte-identical; any drift rolls back.

A second request shape, `{"op": "restore", ...}`, rolls back to the
pre-apply snapshot of a ledgered release (allowed while frozen).

On macOS the broker runs under `sandbox-exec` with a profile that denies all
network access and every file write outside the request's root, store and
ledger directory (`sandbox_profile`). On Linux with Landlock it runs under `linux_confine` (writes only under the same three
directories, no TCP/UDP, reads limited to system roots plus the request's own paths). Elsewhere it runs unsandboxed and the
claim drops accordingly.

`apply_via_broker` runs the subprocess in its own process group with a timeout
and an output byte cap, killing the group on overrun.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict

BROKER_FILE = os.path.abspath(__file__)
REPO = os.path.dirname(os.path.dirname(BROKER_FILE))
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_WRITES = 64
MAX_WRITE_BYTES = 1024 * 1024
MAX_REPLY_BYTES = 64 * 1024
_REQUEST_KEYS = {"root", "store", "ledger", "control_dir", "candidate", "verdict", "token",
                 "writes", "protected", "keyring"}


class BrokerError(Exception):
    pass


_RESTORE_KEYS = {"op", "root", "store", "ledger", "snapshot_id", "reason"}


def _restore(req: Dict[str, Any]) -> Dict[str, Any]:
    """Roll back to the pre-apply snapshot of a recorded release.

    Allowed while frozen (rolling back is the safe direction), but only to a
    snapshot that a ledgered `release` names as its `pre_snapshot_id`.
    """
    from caduceus.ledger import Ledger
    from caduceus.refinery import Refinery

    if set(req) != _RESTORE_KEYS:
        raise BrokerError("restore request must have exactly keys %s" % sorted(_RESTORE_KEYS))
    ledger = Ledger(req["ledger"])
    if not any(e["body"].get("pre_snapshot_id") == req["snapshot_id"] for e in ledger.find("release")):
        raise BrokerError("snapshot is not the pre-apply snapshot of any release")
    Refinery(req["root"], req["store"], ledger, clock=lambda: int(time.time())).restore(
        str(req["snapshot_id"]), str(req["reason"])[:200])
    return {"ok": True, "restored": req["snapshot_id"]}


def _handle(req: Dict[str, Any]) -> Dict[str, Any]:
    import hashlib

    if isinstance(req, dict) and req.get("op") == "restore":
        return _restore(req)

    from caduceus import control
    from caduceus.admission import verdict_from_dict
    from caduceus.candidate import candidate_from_dict
    from caduceus.lanes import lane_signature
    from caduceus.ledger import Ledger
    from caduceus.refinery import Refinery

    if not isinstance(req, dict) or set(req) != _REQUEST_KEYS:
        raise BrokerError("request must have exactly keys %s" % sorted(_REQUEST_KEYS))
    writes, protected = req["writes"], req["protected"]
    if not isinstance(writes, dict) or not 0 < len(writes) <= MAX_WRITES:
        raise BrokerError("writes must be a non-empty object of at most %d files" % MAX_WRITES)
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in writes.items()):
        raise BrokerError("writes must map path -> text")
    if sum(len(v.encode("utf-8")) for v in writes.values()) > MAX_WRITE_BYTES:
        raise BrokerError("writes exceed byte cap")
    if not isinstance(protected, dict) or not all(isinstance(v, str) for v in protected.values()):
        raise BrokerError("protected must map path -> sha256")
    if set(protected) & set(writes):
        raise BrokerError("a protected path cannot be written")

    ctl = control.load(req["control_dir"])
    if ctl.frozen or not ctl.enabled("refinery"):
        raise BrokerError("refused by operator control (frozen or refinery off)")

    candidate = candidate_from_dict(req["candidate"])
    from caduceus.candidate import writes_digest

    if not candidate.content_digest:
        raise BrokerError("candidate does not bind its content (content_digest missing)")
    if writes_digest(writes) != candidate.content_digest:
        raise BrokerError("writes do not match the candidate's content_digest")
    verdict = verdict_from_dict(req["verdict"])
    if verdict.decision != "accept" or verdict.candidate_digest != candidate.digest():
        raise BrokerError("verdict is not an accept for this candidate")
    ledger = Ledger(req["ledger"])
    from caduceus.digest import sha256_hex

    th = sha256_hex(str(req["token"]).encode())
    issued = [e for e in ledger.find("capability_issued") if e["body"]["token_sha256"] == th]
    if len(issued) != 1 or issued[0]["body"].get("verdict_digest") != verdict.digest():
        raise BrokerError("capability not issued for this verdict")
    if candidate.kind == "code_patch":
        if not ctl.enabled("self_patch"):
            raise BrokerError("self_patch subsystem is off")
        from caduceus.signing import verified_signatures

        sigs = verified_signatures(ledger, req["keyring"], candidate.digest()) if req["keyring"] else []
        lane = lane_signature(candidate, sigs, executor_identity="caduceus-broker")
        if lane["status"] != "Checked":
            raise BrokerError("signature lane %s: %s" % (lane["status"], lane.get("detail", "")))

    root = os.path.realpath(req["root"])

    def retention():
        failing = []
        for rel, want in sorted(protected.items()):
            path = os.path.join(root, *rel.split("/"))
            try:
                with open(path, "rb") as fh:
                    got = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                got = ""
            if got != want:
                failing.append(rel)
        return failing

    pre = retention()
    if pre:
        raise BrokerError("protected files already differ before apply: %s" % ",".join(pre[:3]))
    refinery = Refinery(root, req["store"], ledger, clock=lambda: int(time.time()))
    result = refinery.apply(candidate, verdict, str(req["token"]), dict(writes), retention)
    return {"ok": True, "applied": result.applied, "reason": result.reason, "snapshot_id": result.snapshot_id}


def main() -> int:
    if len(sys.argv) > 1:
        sys.path.insert(0, sys.argv[1])
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    try:
        if len(raw) > MAX_REQUEST_BYTES:
            raise BrokerError("request exceeds byte cap")
        from caduceus.candidate import parse_json_strict

        reply = _handle(parse_json_strict(raw.decode("utf-8")))
    except Exception as exc:  # every failure is a structured refusal
        reply = {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
    sys.stdout.write(json.dumps(reply))
    return 0 if reply.get("ok") else 3


def _broker_env() -> Dict[str, str]:
    env = {k: os.environ[k] for k in ("PATH", "HOME", "HERMES_HOME") if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _sb_quote(path: str) -> str:
    if '"' in path or "\\" in path:
        raise BrokerError("path not representable in a sandbox profile")
    return '"%s"' % path


def sandbox_profile(request: Dict[str, Any]) -> str:
    """macOS Seatbelt profile: no network; writes only under root, store, ledger dir."""
    allowed = []
    for key in ("root", "store"):
        if isinstance(request.get(key), str):
            allowed.append("(subpath %s)" % _sb_quote(os.path.realpath(request[key])))
    if isinstance(request.get("ledger"), str):
        allowed.append("(subpath %s)" % _sb_quote(os.path.dirname(os.path.realpath(request["ledger"]))))
    allowed.append('(literal "/dev/null")')
    return ("(version 1)(allow default)(deny network*)(deny file-write*)"
            "(allow file-write* %s)" % " ".join(allowed))


def broker_command(request: Dict[str, Any]) -> list:
    cmd = [sys.executable, "-I", BROKER_FILE, REPO]
    if sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC):
        return [SANDBOX_EXEC, "-p", sandbox_profile(request)] + cmd
    if sys.platform.startswith("linux"):
        from . import linux_confine

        if linux_confine.abi_version() > 0:  # Landlock + seccomp: writes only under ledger dir, root, store; no TCP/UDP; reads limited to system roots and the paths below
            if not isinstance(request.get("ledger"), str):
                raise BrokerError("a request without a ledger cannot be confined")
            work = os.path.dirname(os.path.realpath(request["ledger"]))
            argv = [sys.executable, "-I", linux_confine.__file__, "--workdir", work]
            for key in ("root", "store"):
                if isinstance(request.get(key), str):
                    argv += ["--rw", os.path.realpath(request[key])]
            for key in ("keyring", "control_dir"):
                if isinstance(request.get(key), str) and request[key]:
                    argv += ["--ro", os.path.dirname(os.path.realpath(request[key])) if key == "keyring" else os.path.realpath(request[key])]
            return argv + ["--ro", os.path.dirname(os.path.realpath(BROKER_FILE)), "--ro", REPO, "--"] + cmd
    return cmd


def apply_via_broker(request: Dict[str, Any], timeout_s: int = 30) -> Dict[str, Any]:
    try:
        cmd = broker_command(request)
    except BrokerError as exc:
        return {"ok": False, "error": "BrokerError: %s" % exc}
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True, env=_broker_env(),
    )
    try:
        out, _ = proc.communicate(json.dumps(request).encode("utf-8"), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return {"ok": False, "error": "BrokerError: timeout"}
    if len(out) > MAX_REPLY_BYTES:
        return {"ok": False, "error": "BrokerError: reply exceeds byte cap"}
    try:
        reply = json.loads(out.decode("utf-8"))
    except ValueError:
        return {"ok": False, "error": "BrokerError: unparseable reply (rc=%d)" % proc.returncode}
    return reply if isinstance(reply, dict) else {"ok": False, "error": "BrokerError: bad reply"}


if __name__ == "__main__":
    sys.exit(main())

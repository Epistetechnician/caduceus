"""Refinery: the sole writer of governed state, applied to a SANDBOX root.

Phase 1 scope: operates on a caller-chosen sandbox directory and refuses to
touch a real Hermes home. Applying is gated by (1) an `accept` verdict bound to
the candidate digest, and (2) a single-use, expiring capability recorded in the
ledger. The capability is consumed *before* any write, so a failed apply cannot
be retried with the same token. A separate broker process (PRD 5.6) is Phase 3;
until then this runs in-process and the claim is limited accordingly.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .admission import Verdict
from .candidate import Candidate
from .digest import canonical_bytes, domain_digest, sha256_hex
from .ledger import Ledger


class RefineryError(Exception):
    pass


@dataclass(frozen=True)
class ApplyResult:
    applied: bool
    reason: str
    snapshot_id: str


def _real_hermes_homes() -> List[str]:
    homes = [os.path.expanduser("~/.hermes")]
    env = os.environ.get("HERMES_HOME")
    if env:
        homes.append(env)
    return [os.path.realpath(h) for h in homes]


def _inside(child: str, parent: str) -> bool:
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


class Refinery:
    def __init__(
        self,
        root: str,
        store: str,
        ledger: Ledger,
        clock: Callable[[], int],
        allow_real_home: bool = False,
    ):
        self.root = os.path.realpath(root)
        self.store = os.path.realpath(store)
        self.ledger = ledger
        self.clock = clock
        if not allow_real_home:
            for home in _real_hermes_homes():
                if _inside(self.root, home) or _inside(self.store, home):
                    raise RefineryError("refusing to operate inside a real Hermes home")
        if _inside(self.store, self.root) or _inside(self.root, self.store):
            raise RefineryError("store and root must be disjoint")
        os.makedirs(os.path.join(self.store, "blobs"), exist_ok=True)
        os.makedirs(os.path.join(self.store, "snapshots"), exist_ok=True)

    # -- snapshots ------------------------------------------------------------
    def _files(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for base, dirs, files in os.walk(self.root, followlinks=False):
            dirs.sort()
            for f in sorted(files):
                full = os.path.join(base, f)
                if os.path.islink(full):
                    raise RefineryError("symlink in governed tree: %s" % full)
                rel = os.path.relpath(full, self.root).replace(os.sep, "/")
                with open(full, "rb") as fh:
                    data = fh.read()
                digest = hashlib.sha256(data).hexdigest()
                blob = os.path.join(self.store, "blobs", digest)
                if not os.path.exists(blob):
                    tmp = blob + ".tmp"
                    with open(tmp, "wb") as bh:
                        bh.write(data)
                    os.replace(tmp, blob)
                out[rel] = digest
        return out

    def snapshot(self) -> str:
        manifest = self._files()
        sid = domain_digest("caduceus.snapshot.v1", manifest)
        path = os.path.join(self.store, "snapshots", sid + ".json")
        if not os.path.exists(path):
            with open(path, "wb") as fh:
                fh.write(canonical_bytes(manifest))
        self.ledger.append("snapshot", {"snapshot_id": sid, "file_count": len(manifest)})
        return sid

    def restore(self, snapshot_id: str, reason: str) -> None:
        path = os.path.join(self.store, "snapshots", snapshot_id + ".json")
        with open(path, "rb") as fh:
            import json

            manifest = json.loads(fh.read().decode("ascii"))
        if domain_digest("caduceus.snapshot.v1", manifest) != snapshot_id:
            raise RefineryError("snapshot manifest does not match its id")
        current = self._files()
        for rel in sorted(set(current) - set(manifest)):
            os.remove(os.path.join(self.root, *rel.split("/")))
        for rel, digest in sorted(manifest.items()):
            with open(os.path.join(self.store, "blobs", digest), "rb") as fh:
                data = fh.read()
            if hashlib.sha256(data).hexdigest() != digest:
                raise RefineryError("blob corrupted: " + digest)
            dest = os.path.join(self.root, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(data)
        self.ledger.append("rollback", {"snapshot_id": snapshot_id, "reason": reason[:200]})

    # -- capabilities ---------------------------------------------------------
    def issue_capability(self, candidate: Candidate, verdict: Verdict, ttl_seconds: int) -> str:
        if verdict.decision != "accept" or verdict.candidate_digest != candidate.digest():
            raise RefineryError("capability requires an accept verdict for this candidate")
        if not 0 < ttl_seconds <= 3600:
            raise RefineryError("ttl must be between 1 and 3600 seconds")
        token = secrets.token_hex(32)
        self.ledger.append(
            "capability_issued",
            {
                "token_sha256": sha256_hex(token.encode()),
                "candidate_digest": candidate.digest(),
                "verdict_digest": verdict.digest(),
                "expires_at": self.clock() + ttl_seconds,
            },
        )
        return token

    def _consume(self, token: str, candidate: Candidate) -> None:
        th = sha256_hex(token.encode())
        issued = [e for e in self.ledger.find("capability_issued") if e["body"]["token_sha256"] == th]
        if not issued:
            raise RefineryError("unknown capability")
        if any(e["body"]["token_sha256"] == th for e in self.ledger.find("capability_consumed")):
            raise RefineryError("capability already consumed")
        body = issued[0]["body"]
        if body["candidate_digest"] != candidate.digest():
            raise RefineryError("capability bound to a different candidate")
        if self.clock() > body["expires_at"]:
            raise RefineryError("capability expired")
        self.ledger.append("capability_consumed", {"token_sha256": th, "at": self.clock()})

    # -- apply ----------------------------------------------------------------
    def _target(self, rel: str, candidate: Candidate) -> str:
        if rel not in candidate.paths:
            raise RefineryError("path not declared by candidate: %s" % rel)
        dest = os.path.realpath(os.path.join(self.root, *rel.split("/")))
        if not _inside(dest, self.root):
            raise RefineryError("path escapes root: %s" % rel)
        return dest

    def apply(
        self,
        candidate: Candidate,
        verdict: Verdict,
        token: str,
        writes: Dict[str, str],
        retention_check: Callable[[], List[str]],
    ) -> ApplyResult:
        if verdict.decision != "accept" or verdict.candidate_digest != candidate.digest():
            raise RefineryError("apply requires an accept verdict for this candidate")
        targets = {rel: self._target(rel, candidate) for rel in writes}  # validate first
        self._consume(token, candidate)
        sid = self.snapshot()
        for rel, dest in sorted(targets.items()):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".caduceus-tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(writes[rel])
            os.replace(tmp, dest)
        failing = retention_check()
        if failing:
            self.restore(sid, "retention_failed:" + ",".join(failing[:3]))
            return ApplyResult(False, "retention_failed", sid)
        self.ledger.append(
            "release",
            {
                "candidate_digest": candidate.digest(),
                "verdict_digest": verdict.digest(),
                "pre_snapshot_id": sid,
                "paths": sorted(writes),
            },
        )
        return ApplyResult(True, "applied", sid)

"""Freshness checking for injected snapshots (e.g. MEMORY.md / USER.md).

Hermes deliberately injects MEMORY.md / USER.md as a snapshot frozen at session
start so the cached system-prompt prefix stays byte-stable. Disk drift *within*
a session is therefore expected, not an incident. Incidents are:

  * `injection_mismatch`: the snapshot's recorded hash differs from the hash of
    the file content at injection time (injector bug or tampering);
  * `stale_at_boundary`: at a session boundary the injected snapshot does not
    match current disk content;
  * `checker_disagreement`: the two independent hashing implementations disagree.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

FRAME_DOMAIN = b"caduceus.freshness.v1\x00"


def hash_whole_read(paths: Iterable[str]) -> str:
    """Implementation A: read each file fully, length-prefix frame, hash once."""
    parts = []
    for path in sorted(paths):
        with open(path, "rb") as fh:
            data = fh.read()
        parts.append(len(os.path.basename(path).encode()).to_bytes(4, "big"))
        parts.append(os.path.basename(path).encode())
        parts.append(len(data).to_bytes(8, "big"))
        parts.append(data)
    return hashlib.sha256(FRAME_DOMAIN + b"".join(parts)).hexdigest()


def hash_streaming(paths: Iterable[str]) -> str:
    """Implementation B: stream in small chunks, incremental hasher, size from stat."""
    h = hashlib.sha256()
    h.update(FRAME_DOMAIN)
    for path in sorted(paths):
        name = os.path.basename(path).encode()
        h.update(len(name).to_bytes(4, "big"))
        h.update(name)
        size = 0
        chunks: List[bytes] = []
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(97), b""):
                size += len(chunk)
                chunks.append(chunk)
        h.update(size.to_bytes(8, "big"))
        for chunk in chunks:
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class Snapshot:
    content_sha256: str
    taken_at: int  # integer epoch seconds, supplied by the caller's clock
    names: Tuple[str, ...]


def make_snapshot(paths: List[str], now: int) -> Snapshot:
    a, b = hash_whole_read(paths), hash_streaming(paths)
    if a != b:
        raise RuntimeError("checker_disagreement at snapshot time")
    return Snapshot(a, now, tuple(sorted(os.path.basename(p) for p in paths)))


def classify(
    snapshot: Snapshot, paths: List[str], session_boundary: bool
) -> Dict[str, str]:
    """Return {'status': ..., 'incident': 'yes'|'no'}."""
    a, b = hash_whole_read(paths), hash_streaming(paths)
    if a != b:
        return {"status": "checker_disagreement", "incident": "yes"}
    if a == snapshot.content_sha256:
        return {"status": "ok", "incident": "no"}
    if session_boundary:
        return {"status": "stale_at_boundary", "incident": "yes"}
    return {"status": "drift_within_session_expected", "incident": "no"}


def injection_check(recorded_hash: str, paths_at_injection: List[str]) -> Dict[str, str]:
    a, b = hash_whole_read(paths_at_injection), hash_streaming(paths_at_injection)
    if a != b:
        return {"status": "checker_disagreement", "incident": "yes"}
    if a != recorded_hash:
        return {"status": "injection_mismatch", "incident": "yes"}
    return {"status": "ok", "incident": "no"}

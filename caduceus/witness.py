"""External append-only tree-head witness (consistency monitor).

A hash chain alone cannot stop a single operator from showing different
histories to different parties (split-view). A witness keeps the last tree head
it saw in a *separate* file and refuses any new head that is not a proven
extension of it. This is an unsigned local monitor: it detects rewrites and
forks between observations, it does not provide public non-repudiation. Pointing
`path` at storage controlled by another party is what gives it value.
"""

from __future__ import annotations

import os
from typing import Optional

from . import merkle
from .digest import canonical_bytes, loads_strict
from .ledger import Ledger, TreeHead


class WitnessError(Exception):
    pass


class FileWitness:
    def __init__(self, path: str):
        self.path = os.fspath(path)

    def last(self) -> Optional[TreeHead]:
        if not os.path.exists(self.path):
            return None
        last = None
        with open(self.path, "r", encoding="ascii") as fh:
            for line in fh:
                obj = loads_strict(line)
                last = TreeHead(obj["size"], obj["root"], obj["last_digest"])
        return last

    def observe(self, ledger: Ledger) -> TreeHead:
        """Record the ledger's current head iff it extends the last witnessed head."""
        new = ledger.head()
        old = self.last()
        if old is not None:
            if new.size < old.size:
                raise WitnessError("ledger shrank: %d < %d" % (new.size, old.size))
            proof = [bytes.fromhex(p) for p in ledger.consistency_proof(old.size)]
            ok = merkle.verify_consistency(
                old.size,
                new.size,
                bytes.fromhex(old.root),
                bytes.fromhex(new.root),
                proof,
            )
            if not ok:
                raise WitnessError("new head is not consistent with the witnessed head")
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "ab") as fh:
            fh.write(canonical_bytes(new.as_dict()) + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        return new

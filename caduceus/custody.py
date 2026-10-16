"""Owner-only custody for raw experiment outputs (PRD 5.4).

The ledger holds digests only. Raw model outputs, needed by a second
instrument, go here: a directory outside this repository and outside any real
Hermes home, mode 0700, files 0600, content-addressed by SHA-256 and indexed by
(experiment, probe, arm, rep). Reading back re-hashes every file.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from typing import Dict, Iterator, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CustodyError(Exception):
    pass


def _inside(child: str, parent: str) -> bool:
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


class Custody:
    def __init__(self, directory: str):
        d = os.path.realpath(os.path.expanduser(directory))
        homes = [os.path.expanduser("~/.hermes")] + ([os.environ["HERMES_HOME"]] if os.environ.get("HERMES_HOME") else [])
        if _inside(d, REPO) or any(_inside(d, h) for h in homes):
            raise CustodyError("custody must live outside the repository and any Hermes home")
        os.makedirs(d, mode=0o700, exist_ok=True)
        if os.stat(d).st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise CustodyError("custody directory must be owner-only (chmod 700)")
        self.dir = d

    def _index(self, experiment_id: str) -> str:
        return os.path.join(self.dir, "%s.index.jsonl" % experiment_id.replace("/", "_"))

    def put(self, experiment_id: str, probe_id: str, arm: str, rep: int, text: str) -> str:
        data = text.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        path = os.path.join(self.dir, digest)
        if not os.path.exists(path):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
        fd = os.open(self._index(experiment_id), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"probe_id": probe_id, "arm": arm, "rep": rep, "sha256": digest}, sort_keys=True) + "\n")
        return digest

    def items(self, experiment_id: str) -> Iterator[Tuple[Tuple[str, str, int], str]]:
        """Yield ((probe_id, arm, rep), text); a hash mismatch raises."""
        with open(self._index(experiment_id), "r", encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                with open(os.path.join(self.dir, row["sha256"]), "rb") as bh:
                    data = bh.read()
                if hashlib.sha256(data).hexdigest() != row["sha256"]:
                    raise CustodyError("custody blob corrupted: %s" % row["sha256"])
                yield (row["probe_id"], row["arm"], row["rep"]), data.decode("utf-8")

    def load(self, experiment_id: str) -> Dict[Tuple[str, str, int], str]:
        return dict(self.items(experiment_id))

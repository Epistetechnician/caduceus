"""Tier 1 of state governance: executable, operator-owned checks per skill, plus a built-in syntax lane.

* Checks live OUTSIDE the governed skills tree (`<base>/checks/<category>__<name>.json`), so a mutation cannot weaken the oracle that judges it.
* A check is argv + timeout + expected exit code (+ optional sha256 of stdout). It runs against a COPY of the candidate skill directory, with a
  minimal environment, no network and no writes outside that copy (macOS Seatbelt). Where that confinement is unavailable the result is
  `Unavailable`, never a pass. Registration requires the check to pass on the current admitted HEAD, so a check that never passed cannot become an oracle.
* The syntax lane compiles .py (never executes), parses .json and checks `bash -n`-free shell shebangs only; it needs no sandbox.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
FIELDS = {"id", "argv", "timeout_s", "expect_exit", "expect_stdout_sha256"}
MAX_CHECKS, MAX_ARGV, MAX_TIMEOUT = 20, 16, 30
SENSITIVE = (".ssh", ".aws", ".gnupg", ".hermes", ".claude", ".config", "Library/Keychains")


class CheckError(Exception):
    pass


def spec_path(base: str, skill: str) -> str:
    return os.path.join(base, "checks", skill.replace("/", "__") + ".json")


def validate(spec: Any) -> List[Dict[str, Any]]:
    if not isinstance(spec, list) or not 0 < len(spec) <= MAX_CHECKS:
        raise CheckError("checks must be a non-empty list of at most %d" % MAX_CHECKS)
    out, seen = [], set()
    for c in spec:
        if not isinstance(c, dict) or set(c) - FIELDS or not {"id", "argv"} <= set(c):
            raise CheckError("each check needs exactly id and argv (optional timeout_s, expect_exit, expect_stdout_sha256)")
        argv = c["argv"]
        if not (isinstance(c["id"], str) and 0 < len(c["id"]) <= 64 and c["id"] not in seen):
            raise CheckError("check ids must be unique strings up to 64 characters")
        if not (isinstance(argv, list) and 0 < len(argv) <= MAX_ARGV and all(isinstance(a, str) and 0 < len(a) <= 256 and "\x00" not in a for a in argv)):
            raise CheckError("argv must be 1..%d non-empty strings" % MAX_ARGV)
        t = c.get("timeout_s", 10)
        e = c.get("expect_exit", 0)
        if not (isinstance(t, int) and not isinstance(t, bool) and 0 < t <= MAX_TIMEOUT and isinstance(e, int) and not isinstance(e, bool) and 0 <= e < 256):
            raise CheckError("timeout_s must be 1..%d and expect_exit 0..255" % MAX_TIMEOUT)
        h = c.get("expect_stdout_sha256")
        if h is not None and not (isinstance(h, str) and len(h) == 64 and all(ch in "0123456789abcdef" for ch in h)):
            raise CheckError("expect_stdout_sha256 must be 64 lowercase hex")
        seen.add(c["id"])
        out.append({"id": c["id"], "argv": list(argv), "timeout_s": t, "expect_exit": e, **({"expect_stdout_sha256": h} if h else {})})
    return out


def load(base: str, skill: str) -> Optional[List[Dict[str, Any]]]:
    p = spec_path(base, skill)
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        return validate(json.load(fh))


def specs_digest(spec: Optional[List[Dict[str, Any]]]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def _profile(workdir: str) -> str:
    home = os.path.expanduser("~")
    denies = " ".join('(subpath "%s")' % os.path.join(home, s) for s in SENSITIVE)
    return ('(version 1)(allow default)(deny network*)(deny file-write*)(deny file-read* %s)'
            '(allow file-write* (subpath "%s") (literal "/dev/null"))' % (denies, os.path.realpath(workdir)))


def confinement_available() -> bool:
    return sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC)


def run_check(skill_dir: str, check: Dict[str, Any]) -> str:
    """Pass | Fail | Unavailable for one check against a copy of skill_dir."""
    if not confinement_available():
        return "Unavailable"
    work = tempfile.mkdtemp(prefix="cadchk-")
    try:
        dest = os.path.join(work, "skill")
        shutil.copytree(skill_dir, dest, symlinks=True)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1", "HOME": work, "TMPDIR": work}
        try:
            p = subprocess.run([SANDBOX_EXEC, "-p", _profile(work)] + check["argv"], cwd=dest, env=env, capture_output=True, timeout=check["timeout_s"])
        except subprocess.TimeoutExpired:
            return "Fail"
        except OSError:
            return "Unavailable"
        if p.returncode == 71 and b"sandbox_apply" in p.stderr:  # confinement could not be applied (for example a nested sandbox)
            return "Unavailable"
        if p.returncode != check["expect_exit"]:
            return "Fail"
        want = check.get("expect_stdout_sha256")
        if want and hashlib.sha256(p.stdout).hexdigest() != want:
            return "Fail"
        return "Pass"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_skill(base: str, skills_root: str, skill: str) -> Tuple[str, str]:
    """(status, specs_digest) for one skill: Pass | Fail | Unavailable | None-as-'NoChecks'."""
    try:
        spec = load(base, skill)
    except (CheckError, ValueError, OSError):
        return "Unavailable", "invalid"
    if spec is None:
        return "NoChecks", specs_digest(None)
    skill_dir = os.path.join(skills_root, *skill.split("/"))
    if not os.path.isdir(skill_dir):
        return "NoChecks", specs_digest(spec)
    results = [run_check(skill_dir, c) for c in spec]
    status = "Fail" if "Fail" in results else ("Unavailable" if "Unavailable" in results else "Pass")
    return status, specs_digest(spec)


def syntax_errors(files: Dict[str, bytes]) -> List[str]:
    """Static syntax lane over written files; never executes anything."""
    bad = []
    for rel, data in sorted(files.items()):
        try:
            if rel.endswith(".py"):
                compile(data.decode("utf-8"), rel, "exec")
            elif rel.endswith(".json"):
                json.loads(data.decode("utf-8"))
        except (SyntaxError, ValueError, UnicodeDecodeError, RecursionError):
            bad.append(rel)
    return bad

"""Effect-addressed admission: govern the STATE a mutation produces, not the CALL that requested it.

Tool calls are an open, rewordable, multi-writer interface (skill_manage, a shell redirect, a forked review agent, a curator pass). The state they
write to is small and canonical: the skills tree. This kernel therefore:

  1. holds a HEAD: the content digest of the last admitted skills tree (trust on first use, recorded as a baseline);
  2. on every reconcile, derives the EFFECT = canonical diff(HEAD manifest, current manifest), whoever wrote it and however it was phrased;
  3. judges the effect with pure, deterministic lanes (no model, no network, no clock except the rate window);
  4. accept   -> advance HEAD (blobs retained so HEAD can always be restored);
     reject   -> restore the tree to HEAD;
     quarantine -> restore HEAD and stash the effect for a human to release or discard.

Invariant: after reconcile() returns, the on-disk governed tree is exactly an admitted tree. The verdict is a function of (policy, effect) only, so
replaying the same writes yields the same verdicts and the same ledger. Nothing here grants authority beyond restoring or advancing HEAD.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .digest import domain_digest, sha256_hex
from .ledger import Ledger
from .skills import parse_frontmatter

EFFECT_DOMAIN = "caduceus.transition.effect.v1"
VERDICT_DOMAIN = "caduceus.transition.verdict.v1"
TREE_DOMAIN = "caduceus.transition.tree.v1"
MAX_FILES = 20000
MAX_PATHS_IN_LEDGER = 25

SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*[\"']?[A-Za-z0-9/_+=-]{20,}"),
)
INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore (all |any |the )?(previous|prior|above) (instructions|rules|prompts?)"),
    re.compile(r"(?i)disregard (the |your )?(system prompt|instructions|rules)"),
    re.compile(r"(?i)do not (tell|inform|alert|show) the user"),
    re.compile(r"(?i)\b(curl|wget)\b[^\n|]*\|\s*(ba|z)?sh\b"),
    re.compile(r"(?i)base64\s+(-d|--decode)[^\n|]*\|\s*(ba|z)?sh\b"),
    re.compile(r"(?i)(~|\$HOME)/\.ssh|/etc/(passwd|shadow)|\.env\b.*(cat|send|upload|post)"),
    re.compile(r"(?i)\brm\s+-rf\s+(/|~)"),
    re.compile(r"(?i)exfiltrat"),
)


class TransitionError(Exception):
    pass


@dataclass(frozen=True)
class Policy:
    max_files_changed: int = 20
    max_added_bytes: int = 65536
    max_file_bytes: int = 32768
    max_deleted_files: int = 3
    pinned: Tuple[str, ...] = ()  # skill dirs ("category/name") that may never change
    rate_window_s: int = 3600
    rate_max: int = 12

    def as_dict(self) -> Dict[str, Any]:
        return {"max_files_changed": self.max_files_changed, "max_added_bytes": self.max_added_bytes, "max_file_bytes": self.max_file_bytes,
                "max_deleted_files": self.max_deleted_files, "pinned": sorted(self.pinned), "rate_window_s": self.rate_window_s, "rate_max": self.rate_max}

    def digest(self) -> str:
        return domain_digest("caduceus.transition.policy.v1", self.as_dict())


def _skip(rel: str) -> bool:
    # Hermes' own metadata lives in dot-entries at the top of the skills root (.usage.json, .curator_ledger.jsonl, .hub/, ...)
    return rel.split("/", 1)[0].startswith(".")


def manifest(root: str) -> Dict[str, Dict[str, Any]]:
    """rel path -> {sha256,size,exec,kind}. Symlinks and special files are recorded as such, never followed."""
    out: Dict[str, Dict[str, Any]] = {}
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        dirnames.sort()
        for name in sorted(filenames) + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
            rel = name if rel_dir == "." else rel_dir + "/" + name
            if _skip(rel):
                continue
            full = os.path.join(dirpath, name)
            st = os.lstat(full)
            if stat.S_ISLNK(st.st_mode):
                out[rel] = {"kind": "symlink", "sha256": sha256_hex(os.readlink(full).encode("utf-8", "replace")), "size": 0, "exec": False}
            elif stat.S_ISREG(st.st_mode):
                with open(full, "rb") as fh:
                    data = fh.read()
                out[rel] = {"kind": "file", "sha256": sha256_hex(data), "size": len(data), "exec": bool(st.st_mode & 0o111)}
            else:
                out[rel] = {"kind": "special", "sha256": sha256_hex(b""), "size": 0, "exec": False}
            if len(out) > MAX_FILES:
                raise TransitionError("too many files under the governed root")
    return out


def tree_digest(m: Dict[str, Dict[str, Any]]) -> str:
    return domain_digest(TREE_DOMAIN, {k: [v["kind"], v["sha256"], v["size"], v["exec"]] for k, v in sorted(m.items())})


def diff(head: Dict[str, Dict[str, Any]], cur: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    added = sorted(set(cur) - set(head))
    removed = sorted(set(head) - set(cur))
    changed = sorted(k for k in set(head) & set(cur) if head[k] != cur[k])
    return {
        "parent": tree_digest(head),
        "child": tree_digest(cur),
        "added": {k: [cur[k]["sha256"], cur[k]["size"]] for k in added},
        "changed": {k: [head[k]["sha256"], cur[k]["sha256"], cur[k]["size"]] for k in changed},
        "removed": removed,
    }


def effect_digest(effect: Dict[str, Any]) -> str:
    return domain_digest(EFFECT_DOMAIN, effect)


def _skill_of(rel: str) -> str:
    return "/".join(rel.split("/")[:2])


def lanes(effect: Dict[str, Any], cur: Dict[str, Dict[str, Any]], read: Callable[[str], bytes], policy: Policy, recent: int, recent_removed: int = 0,
          check_results: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Pure lane functions over (effect, content). status: Pass | Fail | Quarantine."""
    touched = sorted(list(effect["added"]) + list(effect["changed"]) + list(effect["removed"]))
    written = sorted(list(effect["added"]) + list(effect["changed"]))
    out: Dict[str, str] = {}
    out["scope"] = "Pass" if all(len(p.split("/")) >= 3 and ".." not in p.split("/") for p in touched) else "Fail"
    out["file_kind"] = "Pass" if all(cur[p]["kind"] == "file" for p in written) else "Fail"
    pinned = {s for s in policy.pinned}
    out["pinned"] = "Fail" if any(_skill_of(p) in pinned for p in touched) else "Pass"
    out["budget"] = "Pass" if (len(touched) <= policy.max_files_changed and sum(effect["added"][p][1] for p in effect["added"]) + sum(effect["changed"][p][2] for p in effect["changed"]) <= policy.max_added_bytes
                               and all(cur[p]["size"] <= policy.max_file_bytes for p in written)) else "Quarantine"
    deleted_skill = any(p.endswith("/SKILL.md") and len(p.split("/")) == 3 for p in effect["removed"])
    # cumulative within the rate window, so deleting one file at a time cannot slip under the per-change limit
    out["deletion"] = "Quarantine" if (len(effect["removed"]) + recent_removed > policy.max_deleted_files or deleted_skill) else "Pass"
    out["rate"] = "Quarantine" if recent >= policy.rate_max else "Pass"
    texts: Dict[str, bytes] = {}
    for p in written:
        if cur[p]["kind"] == "file" and cur[p]["size"] <= policy.max_file_bytes:
            texts[p] = read(p)
    out["secrets"] = "Fail" if any(r.search(t.decode("utf-8", "replace")) for t in texts.values() for r in SECRET_PATTERNS) else "Pass"
    out["injection"] = "Quarantine" if any(r.search(t.decode("utf-8", "replace")) for t in texts.values() for r in INJECTION_PATTERNS) else "Pass"
    out["binary_exec"] = "Quarantine" if any(b"\x00" in t or cur[p]["exec"] for p, t in texts.items()) else "Pass"
    bad_fm = False
    for p, t in texts.items():
        parts = p.split("/")
        if len(parts) == 3 and parts[2] == "SKILL.md":
            fm = parse_frontmatter(t.decode("utf-8", "replace"))
            if not fm.get("name") or not fm.get("description") or fm["name"] != parts[1]:
                bad_fm = True
    out["format"] = "Fail" if bad_fm else "Pass"
    from .checks import syntax_errors

    out["syntax"] = "Fail" if syntax_errors(texts) else "Pass"
    # tier 1: operator-owned executable checks, one result per touched skill; a check that could not run is never a pass
    cr = check_results or {}
    out["checks"] = "Fail" if "Fail" in cr.values() else ("Quarantine" if "Unavailable" in cr.values() else "Pass")
    return out


def decide(lane_results: Dict[str, str], effect: Dict[str, Any], policy: Policy) -> Dict[str, Any]:
    reasons = sorted("%s:%s" % (k, v) for k, v in lane_results.items() if v != "Pass")
    if any(v == "Fail" for v in lane_results.values()):
        decision = "reject"
    elif any(v == "Quarantine" for v in lane_results.values()):
        decision = "quarantine"
    else:
        decision = "accept"
    v = {"decision": decision, "reason_codes": reasons, "lane_results": dict(sorted(lane_results.items())), "effect_digest": effect_digest(effect),
         "policy_digest": policy.digest(), "grants_authority": False}
    v["verdict_digest"] = domain_digest(VERDICT_DOMAIN, {k: v[k] for k in v if k != "verdict_digest"})
    return v


def _bounded(paths: List[str]) -> List[str]:
    return [p[:200] for p in paths[:MAX_PATHS_IN_LEDGER]]


class Kernel:
    def __init__(self, skills_root: str, base: str, policy: Optional[Policy] = None, clock: Callable[[], int] = lambda: int(time.time()), allow_live: bool = False):
        real = os.path.realpath(os.path.expanduser(skills_root))
        live = os.path.realpath(os.path.expanduser("~/.hermes"))
        if (real == live or real.startswith(live + os.sep)) and not allow_live:
            raise TransitionError("refusing to govern a real Hermes home without allow_live")
        self.root, self.base, self.policy, self.clock = real, os.path.realpath(base), policy or Policy(), clock
        for d in ("blobs", "manifests", "effects"):
            os.makedirs(os.path.join(self.base, d), exist_ok=True)
        self.ledger_path = os.path.join(self.base, "transitions.jsonl")

    # -- storage
    def _blob(self, sha: str) -> str:
        return os.path.join(self.base, "blobs", sha)

    def _retain(self, m: Dict[str, Dict[str, Any]]) -> None:
        for rel, e in m.items():
            if e["kind"] == "file" and not os.path.exists(self._blob(e["sha256"])):
                with open(os.path.join(self.root, *rel.split("/")), "rb") as fh:
                    data = fh.read()
                if sha256_hex(data) != e["sha256"]:
                    raise TransitionError("file changed while retaining blobs: " + rel)
                tmp = self._blob(e["sha256"]) + ".tmp"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, self._blob(e["sha256"]))

    def _save_manifest(self, m: Dict[str, Dict[str, Any]]) -> str:
        d = tree_digest(m)
        p = os.path.join(self.base, "manifests", d + ".json")
        if not os.path.exists(p):
            with open(p, "w") as fh:
                json.dump(m, fh, sort_keys=True)
        return d

    def _load_manifest(self, d: str) -> Dict[str, Dict[str, Any]]:
        with open(os.path.join(self.base, "manifests", d + ".json")) as fh:
            m = json.load(fh)
        if tree_digest(m) != d:
            raise TransitionError("stored manifest does not match its digest")
        return m

    def ledger(self) -> Ledger:
        return Ledger(self.ledger_path)

    def head(self) -> Optional[str]:
        heads = [e["body"]["tree"] for e in self.ledger().find("transition_head")]
        return heads[-1] if heads else None

    # -- restore HEAD onto disk
    def _restore(self, target: Dict[str, Dict[str, Any]]) -> None:
        cur = manifest(self.root)
        for rel in sorted(set(cur) - set(target), reverse=True):
            full = os.path.join(self.root, *rel.split("/"))
            if os.path.islink(full) or os.path.isfile(full):
                os.remove(full)
        for rel, e in sorted(target.items()):
            full = os.path.join(self.root, *rel.split("/"))
            if cur.get(rel) == e:
                continue
            if os.path.islink(full) or os.path.isfile(full):
                os.remove(full)
            elif os.path.isdir(full):
                shutil.rmtree(full)
            if e["kind"] != "file":
                continue  # only regular files are ever admitted into HEAD
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(self._blob(e["sha256"]), "rb") as fh:
                data = fh.read()
            if sha256_hex(data) != e["sha256"]:
                raise TransitionError("blob corrupted: " + rel)
            with open(full, "wb") as fh:
                fh.write(data)
            os.chmod(full, 0o755 if e["exec"] else 0o644)
        for dirpath, dirnames, filenames in os.walk(self.root, topdown=False):  # prune directories emptied by the restore
            if dirpath != self.root and not os.listdir(dirpath) and not _skip(os.path.relpath(dirpath, self.root).replace(os.sep, "/")):
                os.rmdir(dirpath)

    # -- the one operation
    def reconcile(self) -> Dict[str, Any]:
        with open(os.path.join(self.base, ".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._reconcile()

    def _reconcile(self) -> Dict[str, Any]:
        led = self.ledger()
        cur = manifest(self.root)
        head = self.head()
        if head is None:  # trust on first use: the tree present now becomes the admitted baseline
            self._retain(cur)
            d = self._save_manifest(cur)
            led.append("transition_baseline", {"tree": d, "files": len(cur), "policy_digest": self.policy.digest()})
            led.append("transition_head", {"tree": d, "reason": "baseline"})
            return {"status": "baseline", "tree": d}
        hm = self._load_manifest(head)
        if tree_digest(cur) == head:
            return {"status": "clean", "tree": head}
        effect = diff(hm, cur)
        ed = effect_digest(effect)
        now = self.clock()
        window = [e["body"] for e in led.find("transition_verdict") if e["body"].get("at", 0) >= now - self.policy.rate_window_s]
        recent = len(window)
        recent_removed = sum(b.get("n_removed", 0) for b in window if b.get("decision") == "accept")

        def read(rel: str) -> bytes:
            with open(os.path.join(self.root, *rel.split("/")), "rb") as fh:
                return fh.read()
        from . import checks as chk

        skills = sorted({_skill_of(p) for p in list(effect["added"]) + list(effect["changed"]) + list(effect["removed"])})  # removals can break a check too
        check_results, spec_digests = {}, {}
        for sk in skills:
            st, sd = chk.run_skill(self.base, self.root, sk)
            if st != "NoChecks":
                check_results[sk], spec_digests[sk] = st, sd
        verdict = decide(lanes(effect, cur, read, self.policy, recent, recent_removed, check_results), effect, self.policy)
        checks_digest = domain_digest("caduceus.transition.checks.v1", {"results": check_results, "specs": spec_digests})
        touched = list(effect["added"]) + list(effect["changed"]) + list(effect["removed"])
        led.append("transition_verdict", {"effect_digest": ed, "verdict_digest": verdict["verdict_digest"], "decision": verdict["decision"],
                                          "reason_codes": verdict["reason_codes"], "parent": effect["parent"], "child": effect["child"],
                                          "paths": _bounded(sorted(touched)), "n_paths": len(touched), "n_removed": len(effect["removed"]), "checks_digest": checks_digest, "checks_run": len(check_results), "at": now})
        if verdict["decision"] == "accept":
            self._retain(cur)
            d = self._save_manifest(cur)
            led.append("transition_head", {"tree": d, "effect_digest": ed, "verdict_digest": verdict["verdict_digest"], "reason": "accept"})
            return {"status": "accepted", "verdict": verdict, "tree": d}
        if verdict["decision"] == "quarantine":
            self._retain({k: v for k, v in cur.items() if v["kind"] == "file" and k in set(effect["added"]) | set(effect["changed"])})
            self._save_manifest(cur)
            with open(os.path.join(self.base, "effects", ed + ".json"), "w") as fh:
                json.dump({"effect": effect, "child_manifest": cur}, fh, sort_keys=True)
            led.append("transition_quarantined", {"effect_digest": ed, "verdict_digest": verdict["verdict_digest"]})
        self._restore(hm)
        if tree_digest(manifest(self.root)) != head:
            raise TransitionError("restore did not reproduce HEAD")
        led.append("transition_restored", {"effect_digest": ed, "to_tree": head, "decision": verdict["decision"]})
        return {"status": "restored", "verdict": verdict, "tree": head}

    def release(self, effect_digest_hex: str, operator: str, reason: str) -> Dict[str, Any]:
        """Operator-approved release of a quarantined effect. Fails if HEAD moved, the effect is unknown, or evolution is frozen."""
        from . import control

        if control.load(self.base).frozen:
            raise TransitionError("evolution is frozen by the operator")
        with open(os.path.join(self.base, ".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            led = self.ledger()
            if not any(e["body"].get("effect_digest") == effect_digest_hex for e in led.find("transition_quarantined")):
                raise TransitionError("no such quarantined effect")
            if any(e["body"].get("effect_digest") == effect_digest_hex for e in led.find("transition_released")):
                raise TransitionError("effect already released")
            path = os.path.join(self.base, "effects", effect_digest_hex + ".json")
            with open(path) as fh:
                stored = json.load(fh)
            if effect_digest(stored["effect"]) != effect_digest_hex:
                raise TransitionError("stored effect does not match its digest")
            if stored["effect"]["parent"] != self.head():
                raise TransitionError("HEAD moved since quarantine; re-propose the change")
            child = stored["child_manifest"]
            if tree_digest(child) != stored["effect"]["child"]:
                raise TransitionError("stored child manifest does not match the effect")
            self._restore(child)
            if tree_digest(manifest(self.root)) != stored["effect"]["child"]:
                self._restore(self._load_manifest(self.head()))
                raise TransitionError("release did not reproduce the quarantined tree")
            self._retain(child)
            d = self._save_manifest(child)
            led.append("transition_released", {"effect_digest": effect_digest_hex, "operator": operator[:64], "reason": reason[:200]})
            led.append("transition_head", {"tree": d, "effect_digest": effect_digest_hex, "reason": "released"})
            return {"status": "released", "tree": d}

    def register_check(self, skill: str, check: Dict[str, Any], operator: str) -> Dict[str, Any]:
        """Operator adds an executable check for one skill. It must PASS on the admitted HEAD copy of that skill first."""
        import tempfile

        from . import checks as chk

        if len(skill.split("/")) != 2 or ".." in skill.split("/"):
            raise TransitionError("skill must be 'category/name'")
        head = self.head()
        if head is None:
            raise TransitionError("no admitted HEAD yet; run reconcile first")
        hm = self._load_manifest(head)
        prefix = skill + "/"
        files = {k: v for k, v in hm.items() if k.startswith(prefix) and v["kind"] == "file"}
        if not files:
            raise TransitionError("skill not present in the admitted HEAD")
        existing = chk.load(self.base, skill) or []
        spec = chk.validate(existing + [check])
        tmp = tempfile.mkdtemp(prefix="cadreg-")
        try:
            for rel, e in files.items():
                dest = os.path.join(tmp, *rel[len(prefix):].split("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(self._blob(e["sha256"]), dest)
                os.chmod(dest, 0o755 if e["exec"] else 0o644)
            status = chk.run_check(tmp, spec[-1])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if status != "Pass":
            raise TransitionError("check did not pass on the admitted HEAD (%s); it cannot become an oracle" % status)
        os.makedirs(os.path.join(self.base, "checks"), exist_ok=True)
        with open(chk.spec_path(self.base, skill), "w") as fh:
            json.dump(spec, fh, sort_keys=True)
        self.ledger().append("audit_note", {"event": "check_registered", "skill": skill[:120], "check_id": check["id"][:64], "specs_digest": chk.specs_digest(spec), "operator": operator[:64]})
        return {"skill": skill, "check_id": check["id"], "specs_digest": chk.specs_digest(spec)}

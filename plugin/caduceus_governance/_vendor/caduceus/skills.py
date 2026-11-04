"""Read-only adapter for Hermes skill directories.

Hermes skills are directories containing SKILL.md with YAML-style frontmatter
(name, description, version, ...) plus optional reference files. This module
never writes to a skills directory. Layout facts were checked against a Hermes
checkout (see docs/HERMES_INTERFACES.md).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .digest import domain_digest, sha256_hex

TREE_DOMAIN = "caduceus.skill.tree.v1"
MAX_SKILLS = 5000
MAX_FILES_PER_SKILL = 500
MAX_FILE_BYTES = 8 * 1024 * 1024


class SkillError(Exception):
    pass


def parse_frontmatter(text: str) -> Dict[str, str]:
    """Minimal top-level `key: value` parser for the leading --- block.

    Nested mappings and lists are ignored (only flat scalar keys are returned).
    No YAML dependency on purpose.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: Dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        if not line or line[0] in " \t#":
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value:
            out[key.strip()] = value
    return {}  # unterminated block: treat as no frontmatter


@dataclass(frozen=True)
class SkillFile:
    rel_path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class SkillRecord:
    name: str
    rel_dir: str
    files: Tuple[SkillFile, ...]
    tree_digest: str
    frontmatter: Dict[str, str]


def _walk_files(skill_dir: str) -> List[str]:
    found: List[str] = []
    for base, dirs, files in os.walk(skill_dir, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            full = os.path.join(base, fname)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            found.append(os.path.relpath(full, skill_dir).replace(os.sep, "/"))
            if len(found) > MAX_FILES_PER_SKILL:
                raise SkillError("too many files in %s" % skill_dir)
    return sorted(found)


def _hash_file(path: str) -> Tuple[str, int]:
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        raise SkillError("file too large: %s" % path)
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest(), size


def tree_digest(files: List[SkillFile]) -> str:
    return domain_digest(
        TREE_DOMAIN,
        [{"path": f.rel_path, "sha256": f.sha256, "size": f.size} for f in files],
    )


def load_skill(skills_root: str, rel_dir: str) -> SkillRecord:
    skill_dir = os.path.join(skills_root, rel_dir)
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md) or os.path.islink(skill_md):
        raise SkillError("no SKILL.md in %s" % rel_dir)
    files = []
    for rel in _walk_files(skill_dir):
        digest, size = _hash_file(os.path.join(skill_dir, rel))
        files.append(SkillFile(rel, digest, size))
    with open(skill_md, "r", encoding="utf-8", errors="replace") as fh:
        fm = parse_frontmatter(fh.read(65536))
    name = fm.get("name") or os.path.basename(rel_dir.rstrip("/"))
    return SkillRecord(
        name=name,
        rel_dir=rel_dir.replace(os.sep, "/"),
        files=tuple(files),
        tree_digest=tree_digest(files),
        frontmatter=fm,
    )


def discover(skills_root: str) -> List[str]:
    """Relative directories (posix) containing a SKILL.md, hidden dirs skipped."""
    found: List[str] = []
    for base, dirs, files in os.walk(skills_root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        if "SKILL.md" in files and base != skills_root:
            found.append(os.path.relpath(base, skills_root).replace(os.sep, "/"))
            dirs[:] = []  # a skill dir's children are references, not skills
        if len(found) > MAX_SKILLS:
            raise SkillError("too many skills")
    return sorted(found)


def skill_identity(record: SkillRecord) -> str:
    """Identity by behavior-relevant content, not by name or path."""
    return record.tree_digest

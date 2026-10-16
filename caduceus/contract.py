"""Skill behavior contracts and probe bundles.

A skill is a tested abstraction: it ships with a behavior contract and a probe
bundle with four probe kinds.

  visible       the author may see these
  held_out      sealed from the proposer; used for equivalence/assessment
  perturbation  small input changes around a visible probe (`neighbor_of`);
                tests sensitivity, not just point outputs
  negative      inputs the skill must refuse / not act on

Why: in Theseus Lab a replacement that matched every visible probe failed the
held-out ones, and a fitted module matched values but not local derivatives
(see docs/PRD.md section 6.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .digest import domain_digest

PROBE_KINDS = ("visible", "held_out", "perturbation", "negative")
EXPECT_TYPES = ("exact", "contains", "not_contains", "regex")
SIDE_EFFECTS = ("none", "read", "write", "external")
MAX_REGEX = 200
MAX_INPUT = 8192


@dataclass(frozen=True)
class Probe:
    probe_id: str
    kind: str
    input: str
    expect: Tuple[Tuple[str, str], ...]  # sorted (key, value) pairs, hashable
    neighbor_of: Optional[str] = None
    # Natural-language grading rubric for a second, independent instrument
    # (an LLM judge). Omitted from the dict when empty, so existing contract
    # digests are unchanged.
    rubric: str = ""

    def expect_dict(self) -> Dict[str, str]:
        return dict(self.expect)

    def as_dict(self) -> Dict[str, Any]:
        d = {
            "probe_id": self.probe_id,
            "kind": self.kind,
            "input": self.input,
            "expect": self.expect_dict(),
            "neighbor_of": self.neighbor_of,
        }
        if self.rubric:
            d["rubric"] = self.rubric
        return d


def make_probe(
    probe_id: str,
    kind: str,
    input: str,
    expect_type: str,
    expect_value: str,
    neighbor_of: Optional[str] = None,
) -> Probe:
    return Probe(
        probe_id, kind, input, (("type", expect_type), ("value", expect_value)), neighbor_of
    )


def judge(expect: Dict[str, str], output: Optional[str]) -> bool:
    if output is None:
        return False
    kind, value = expect.get("type"), expect.get("value", "")
    if kind == "exact":
        return output == value
    if kind == "contains":
        return value in output
    if kind == "not_contains":
        return value not in output
    if kind == "regex":
        return re.search(value, output) is not None
    return False


@dataclass(frozen=True)
class ContractPolicy:
    min_visible: int = 2
    min_held_out: int = 2
    min_perturbation: int = 1
    min_negative: int = 1


@dataclass(frozen=True)
class SkillContract:
    name: str
    io_schema: Tuple[Tuple[str, str], ...]
    preconditions: Tuple[str, ...]
    side_effect_class: str
    max_output_chars: int
    probes: Tuple[Probe, ...]
    expiry_review_date: str  # YYYY-MM-DD

    def probes_of(self, kind: str) -> List[Probe]:
        return [p for p in self.probes if p.kind == kind]

    def as_dict(self, include_held_out: bool = True) -> Dict[str, Any]:
        probes = [p.as_dict() for p in self.probes if include_held_out or p.kind != "held_out"]
        return {
            "name": self.name,
            "io_schema": dict(self.io_schema),
            "preconditions": list(self.preconditions),
            "side_effect_class": self.side_effect_class,
            "max_output_chars": self.max_output_chars,
            "probes": probes,
            "expiry_review_date": self.expiry_review_date,
        }

    def digest(self) -> str:
        return domain_digest("caduceus.contract.v1", self.as_dict())

    def proposer_view_digest(self) -> str:
        """Digest of what a proposer may see (held-out probes excluded)."""
        return domain_digest("caduceus.contract.proposer-view.v1", self.as_dict(False))


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_contract(c: SkillContract, policy: ContractPolicy = ContractPolicy()) -> List[str]:
    """Return a list of problems; empty means valid. Fails closed."""
    problems: List[str] = []
    if not c.name:
        problems.append("name_empty")
    if c.side_effect_class not in SIDE_EFFECTS:
        problems.append("side_effect_class_invalid")
    if not (0 < c.max_output_chars <= 1_000_000):
        problems.append("max_output_chars_out_of_range")
    if not _DATE.match(c.expiry_review_date):
        problems.append("expiry_review_date_invalid")
    ids = [p.probe_id for p in c.probes]
    if len(ids) != len(set(ids)):
        problems.append("probe_ids_not_unique")
    by_id = {p.probe_id: p for p in c.probes}
    for p in c.probes:
        if p.kind not in PROBE_KINDS:
            problems.append("probe_kind_invalid:%s" % p.probe_id)
        if len(p.input) > MAX_INPUT:
            problems.append("probe_input_too_long:%s" % p.probe_id)
        ed = p.expect_dict()
        if ed.get("type") not in EXPECT_TYPES:
            problems.append("expect_type_invalid:%s" % p.probe_id)
        elif ed["type"] == "regex":
            if len(ed.get("value", "")) > MAX_REGEX:
                problems.append("regex_too_long:%s" % p.probe_id)
            else:
                try:
                    re.compile(ed["value"])
                except re.error:
                    problems.append("regex_invalid:%s" % p.probe_id)
        if p.kind == "perturbation":
            base = by_id.get(p.neighbor_of or "")
            if base is None or base.kind != "visible":
                problems.append("perturbation_needs_visible_neighbor:%s" % p.probe_id)
            elif base.input == p.input:
                problems.append("perturbation_identical_to_neighbor:%s" % p.probe_id)
        elif p.neighbor_of is not None:
            problems.append("neighbor_of_only_for_perturbation:%s" % p.probe_id)
    proposer_inputs = {p.input for p in c.probes if p.kind in ("visible", "perturbation", "negative")}
    for p in c.probes_of("held_out"):
        if p.input in proposer_inputs:
            problems.append("held_out_leaks_into_visible:%s" % p.probe_id)
    counts = {k: len(c.probes_of(k)) for k in PROBE_KINDS}
    for kind, minimum in (
        ("visible", policy.min_visible),
        ("held_out", policy.min_held_out),
        ("perturbation", policy.min_perturbation),
        ("negative", policy.min_negative),
    ):
        if counts[kind] < minimum:
            problems.append("too_few_%s_probes:%d<%d" % (kind, counts[kind], minimum))
    return problems


# --- contract files ---------------------------------------------------------
#
# A contract for a real Hermes skill lives in `contracts/<skill>.json`:
#   {"schema": CONTRACT_FILE_SCHEMA, "skill": <name>, "skill_rel_dir": <cat/name>,
#    "written_against_tree_digest": <hex>, "contract": {...as_dict()...}}
# The pinned tree digest records which skill version the probes were written
# against; a different digest means the contract needs review, not that it holds.

CONTRACT_FILE_SCHEMA = "caduceus.contract.file.v1"
_FILE_KEYS = {"schema", "skill", "skill_rel_dir", "written_against_tree_digest", "contract"}
_CONTRACT_KEYS = {
    "name", "io_schema", "preconditions", "side_effect_class",
    "max_output_chars", "probes", "expiry_review_date",
}
_PROBE_KEYS = {"probe_id", "kind", "input", "expect", "neighbor_of"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ContractFileError(ValueError):
    pass


def _exact_keys(obj: Any, keys: set, where: str) -> Dict[str, Any]:
    if not isinstance(obj, dict) or set(obj) != keys:
        raise ContractFileError("%s: expected exactly keys %s" % (where, sorted(keys)))
    return obj


def contract_from_dict(d: Dict[str, Any]) -> SkillContract:
    d = _exact_keys(d, _CONTRACT_KEYS, "contract")
    probes = []
    for i, p in enumerate(d["probes"] if isinstance(d["probes"], list) else []):
        if isinstance(p, dict) and "rubric" in p:
            rubric = p["rubric"]
            if not isinstance(rubric, str) or not rubric or len(rubric) > 1000:
                raise ContractFileError("probe[%d].rubric must be a non-empty string <= 1000 chars" % i)
            p = {k: v for k, v in p.items() if k != "rubric"}
        else:
            rubric = ""
        p = _exact_keys(p, _PROBE_KEYS, "probe[%d]" % i)
        expect = _exact_keys(p["expect"], {"type", "value"}, "probe[%d].expect" % i)
        if not all(isinstance(p[k], str) for k in ("probe_id", "kind", "input")):
            raise ContractFileError("probe[%d]: id/kind/input must be strings" % i)
        if not all(isinstance(v, str) for v in expect.values()):
            raise ContractFileError("probe[%d].expect: values must be strings" % i)
        if p["neighbor_of"] is not None and not isinstance(p["neighbor_of"], str):
            raise ContractFileError("probe[%d].neighbor_of: string or null" % i)
        probe = make_probe(p["probe_id"], p["kind"], p["input"], expect["type"], expect["value"], p["neighbor_of"])
        if rubric:
            from dataclasses import replace

            probe = replace(probe, rubric=rubric)
        probes.append(probe)
    if not isinstance(d["io_schema"], dict) or not isinstance(d["preconditions"], list):
        raise ContractFileError("contract: io_schema must be an object, preconditions a list")
    if not isinstance(d["max_output_chars"], int) or isinstance(d["max_output_chars"], bool):
        raise ContractFileError("contract: max_output_chars must be an integer")
    return SkillContract(
        name=str(d["name"]),
        io_schema=tuple(sorted((str(k), str(v)) for k, v in d["io_schema"].items())),
        preconditions=tuple(str(x) for x in d["preconditions"]),
        side_effect_class=str(d["side_effect_class"]),
        max_output_chars=d["max_output_chars"],
        probes=tuple(probes),
        expiry_review_date=str(d["expiry_review_date"]),
    )


@dataclass(frozen=True)
class ContractFile:
    skill: str
    skill_rel_dir: str
    written_against_tree_digest: str
    contract: SkillContract


def load_contract_file(path: str, policy: ContractPolicy = ContractPolicy()) -> ContractFile:
    """Load and validate a contract file. Any unknown field or problem raises."""
    import json

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh, parse_float=lambda s: (_ for _ in ()).throw(ContractFileError("float in contract")))
    raw = _exact_keys(raw, _FILE_KEYS, path)
    if raw["schema"] != CONTRACT_FILE_SCHEMA:
        raise ContractFileError("%s: unknown schema %r" % (path, raw["schema"]))
    if not isinstance(raw["written_against_tree_digest"], str) or not _HEX64.match(raw["written_against_tree_digest"]):
        raise ContractFileError("%s: written_against_tree_digest must be 64 hex chars" % path)
    contract = contract_from_dict(raw["contract"])
    if contract.name != raw["skill"]:
        raise ContractFileError("%s: contract.name != skill" % path)
    problems = validate_contract(contract, policy)
    if problems:
        raise ContractFileError("%s: %s" % (path, ", ".join(problems)))
    return ContractFile(str(raw["skill"]), str(raw["skill_rel_dir"]), raw["written_against_tree_digest"], contract)


def load_contracts_dir(directory: str) -> Dict[str, ContractFile]:
    """Load every `*.json` contract in a directory; fails on the first bad file."""
    import os

    out: Dict[str, ContractFile] = {}
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".json"):
            continue
        cf = load_contract_file(os.path.join(directory, fname))
        if cf.skill in out:
            raise ContractFileError("duplicate contract for skill %r" % cf.skill)
        out[cf.skill] = cf
    return out

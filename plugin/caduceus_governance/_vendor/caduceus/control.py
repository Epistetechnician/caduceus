"""Operator controls: kill switch, freeze, subsystem toggles, rate limit (PRD 6.10, 9.3).

State lives in `<caduceus dir>/control.json`:
    {"frozen": bool, "subsystems": {<name>: bool, ...}}
with exactly the subsystem names below. A missing file means defaults (not
frozen, every subsystem on). A malformed file fails closed: `load` reports
frozen=True with every subsystem off except `admission` (so the gate keeps
blocking mutations in enforce mode). Every change is ledgered.

`frozen` stops evolution (no releases, no capabilities, enforce-mode plugin
blocks every skill mutation) without stopping the agent. Turning `admission`
off makes the plugin a no-op: upstream-equivalent behaviour.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict

from .ledger import Ledger

SUBSYSTEMS = ("telemetry", "freshness", "fast_path", "admission", "refinery", "self_patch")
DEFAULTS = {"telemetry": True, "freshness": True, "fast_path": False, "admission": True,
            "refinery": True, "self_patch": False}
FILENAME = "control.json"


@dataclass(frozen=True)
class Control:
    frozen: bool
    subsystems: Dict[str, bool]
    valid: bool = True

    def enabled(self, name: str) -> bool:
        if name not in SUBSYSTEMS:
            raise KeyError(name)
        return self.subsystems[name]


def _fail_closed() -> Control:
    return Control(True, {name: name == "admission" for name in SUBSYSTEMS}, valid=False)


def load(base: str) -> Control:
    path = os.path.join(base, FILENAME)
    if not os.path.exists(path):
        return Control(False, dict(DEFAULTS))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if set(raw) != {"frozen", "subsystems"} or not isinstance(raw["frozen"], bool):
            return _fail_closed()
        subs = raw["subsystems"]
        if not isinstance(subs, dict) or set(subs) != set(SUBSYSTEMS) or not all(isinstance(v, bool) for v in subs.values()):
            return _fail_closed()
        return Control(raw["frozen"], dict(subs))
    except (OSError, ValueError):
        return _fail_closed()


def save(base: str, control: Control, ledger: Ledger, operator: str, reason: str) -> None:
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, FILENAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"frozen": control.frozen, "subsystems": control.subsystems}, fh, sort_keys=True)
    os.replace(tmp, path)
    ledger.append("audit_note", {"event": "control_changed", "frozen": control.frozen,
                                 "subsystems": dict(control.subsystems),
                                 "operator": operator[:64], "reason": reason[:200]})


def freeze(base: str, ledger: Ledger, operator: str, reason: str) -> Control:
    c = load(base)
    out = Control(True, c.subsystems if c.valid else dict(DEFAULTS))
    save(base, out, ledger, operator, "freeze:" + reason)
    return out


def unfreeze(base: str, ledger: Ledger, operator: str, reason: str) -> Control:
    c = load(base)
    out = Control(False, c.subsystems if c.valid else dict(DEFAULTS))
    save(base, out, ledger, operator, "unfreeze:" + reason)
    return out


def set_subsystem(base: str, ledger: Ledger, name: str, on: bool, operator: str) -> Control:
    if name not in SUBSYSTEMS:
        raise KeyError(name)
    c = load(base)
    subs = dict(c.subsystems if c.valid else DEFAULTS)
    subs[name] = on
    out = Control(c.frozen if c.valid else True, subs)
    save(base, out, ledger, operator, "%s=%s" % (name, on))
    return out


def candidate_rate_ok(ledger: Ledger, now: int, window_s: int, max_candidates: int) -> bool:
    """Candidate-generation rate limit over ledgered `candidate` entries with `at`."""
    recent = [e for e in ledger.find("candidate") if isinstance(e["body"].get("at"), int)
              and now - e["body"]["at"] < window_s]
    return len(recent) < max_candidates

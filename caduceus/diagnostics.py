"""`caduceus diagnostics`: the lane vector (PRD 6.1, 11). No aggregate score.

Each lane reports Checked | Failed | NotRun | Unavailable with the evidence it
used. Lanes this repository cannot run (Bend, Lean, temporal stress, overhead
measurement inside a live Hermes) are reported `Unavailable`, never passed.
Everything is read-only.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List, Optional

from . import autoapply, control, readback
from .corpus import run_corpus
from .ledger import Ledger


def _lane(status: str, detail: str = "", **evidence: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"status": status, "detail": detail}
    out.update(evidence)
    return out


def _latest(pattern: str) -> Optional[str]:
    paths = sorted(glob.glob(pattern), key=os.path.getmtime)
    return paths[-1] if paths else None


def diagnostics(repo: str, ledger_paths: List[str], caduceus_dir: Optional[str]) -> Dict[str, Any]:
    lanes: Dict[str, Dict[str, Any]] = {}
    ledgers: List[Ledger] = []
    bad = []
    for p in ledger_paths:
        try:
            readback.verify_file(p)
            ledgers.append(Ledger(p))
        except Exception as exc:  # any readback failure is a Failed lane
            bad.append("%s: %s" % (os.path.basename(p), type(exc).__name__))
    lanes["ledger_integrity"] = (_lane("Failed", "; ".join(bad)) if bad else
                                 _lane("Checked", "independent readback", ledgers=len(ledgers)) if ledgers else
                                 _lane("NotRun", "no ledgers given"))

    corpus = run_corpus()
    lanes["patch_safety_adversarial_corpus"] = _lane("Checked" if corpus["ok"] else "Failed",
                                                     "%d/%d refused" % (corpus["refused"], corpus["cases"]),
                                                     misses=corpus["misses"])

    verdicts = [e["body"] for led in ledgers for e in led.find("verdict") if "experiment_id" in e["body"]]
    if verdicts:
        lanes["skill_effect_experiments"] = _lane(
            "Checked", "preregistered paired tests",
            results={v["experiment_id"]: v["verdict"] for v in verdicts})
    else:
        lanes["skill_effect_experiments"] = _lane("NotRun", "no experiment verdicts in the given ledgers")

    baseline = _latest(os.path.join(repo, "baselines", "switch-latency-*.json"))
    if baseline:
        with open(baseline) as fh:
            b = json.load(fh)
        lanes["switch_latency"] = _lane(
            "NotRun", "baseline pinned; no fast path exists to compare (PRD amendment 2)",
            baseline=os.path.relpath(baseline, repo), p99_us=b["latency_us"]["p99"]["point"],
            hermes_tree_dirty=b["pin"]["hermes_tree_dirty"])
    else:
        lanes["switch_latency"] = _lane("NotRun", "no baseline pinned")

    if caduceus_dir:
        ctl = control.load(caduceus_dir)
        lanes["operator_controls"] = _lane("Checked" if ctl.valid else "Failed",
                                           "control.json " + ("valid" if ctl.valid else "malformed: failing closed"),
                                           frozen=ctl.frozen, subsystems=ctl.subsystems)
    else:
        lanes["operator_controls"] = _lane("NotRun", "no caduceus dir given")

    lanes["retention_protected_suite"] = _lane("NotRun", "no refinement has been applied to a real skill")
    lanes["promotion_rollback_rate"] = _lane("NotRun", "no promotions yet")
    fr = _latest(os.path.join(repo, "baselines", "freshness-*.json"))
    if fr:
        with open(fr) as fh:
            f = json.load(fh)
        lanes["freshness_differential"] = _lane(
            "Checked" if f["passed"] else "Failed", "frozen corpus against real Hermes memory injection",
            baseline=os.path.relpath(fr, repo), cases=len(f["cases"]),
            silent_stale_injections=f["silent_stale_injections"], negative_controls_caught=f["negative_controls_caught"])
    else:
        lanes["freshness_differential"] = _lane("NotRun", "run scripts/freshness_probe.py")
    ovh = _latest(os.path.join(repo, "baselines", "plugin-overhead-*.json"))
    if ovh:
        with open(ovh) as fh:
            o = json.load(fh)
        lanes["overhead"] = _lane(
            "Checked" if o["passed"] else "Failed", o["pass_rule"],
            baseline=os.path.relpath(ovh, repo), non_gated_p99_delta_us=o["delta_us"]["read_file"]["p99"],
            budget_us=o["budget_us"], gated_p50_delta_us=o["delta_us"]["skill_manage"]["p50"],
            note="observe-mode gated path appends in O(1); full chain verified by ledger_integrity")
    else:
        lanes["overhead"] = _lane("NotRun", "run scripts/plugin_overhead.py")
    for name in ("semantic_oracle_bend", "formal_contract_lean", "temporal_stress"):
        lanes[name] = _lane("Unavailable", "optional lane not implemented here (PRD 8, 10)")

    return {
        "lanes": lanes,
        "auto_apply": autoapply.eligibility(ledgers),
        "claim_ceiling": "LocalDevelopmentEvidenceOnly",
        "note": "lane vector only; there is no aggregate score",
    }

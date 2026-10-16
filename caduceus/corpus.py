"""Adversarial corpus: every seeded bad case must be refused (PRD 8, Phase 2 exit).

Each case is a small function that tries one attack through the real code path
and returns True iff the attack was refused. Patterns follow the Statebook
TD-004 replays named in the PRD: stale evidence, budget slow-drain, cancel
races, model-confidence bypass, plus intake, signature and capability attacks.
`run_corpus` also replays the gate on an accept case to check determinism.
A miss blocks release: `caduceus corpus` exits non-zero.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Callable, Dict, List, Tuple

from .admission import REQUIRED_LANES, AdmissionPolicy, evaluate
from .candidate import (REQUIRED_ARMS, REQUIRED_NONCLAIM, Candidate, CandidateParseError,
                        candidate_from_dict, parse_json_strict)
from .digest import sha256_hex
from .lanes import lane_signature
from .ledger import Ledger
from .refinery import Refinery, RefineryError
from .release import Breaker, Budget, CanaryPlan, Release, ReleaseError, ReleaseManager


def _cand(**over) -> Candidate:
    base = dict(
        candidate_id="corpus", kind="skill_refine", parent_digest=sha256_hex(b"parent"), tenant_scope="t",
        paths=("skills/a/SKILL.md",),
        preregistration={"metric": "pass_rate", "threshold_micro": 0, "min_effect_micro": 100000,
                         "alpha_micro": 50000, "n": 40, "seeds": [1, 2], "arms": list(REQUIRED_ARMS),
                         "holdout_commitment": sha256_hex(b"holdout"), "max_search_cost_units": 10},
        compute_budget=10, spend_budget_micro_usd=0, rollback_plan_digest=sha256_hex(b"rollback"),
        nonclaims=(REQUIRED_NONCLAIM,), proposer="hermes",
    )
    base.update(over)
    return Candidate(**base)


def _checked() -> Dict[str, Dict[str, str]]:
    return {lane: {"status": "Checked", "detail": ""} for lane in REQUIRED_LANES}


def _not_accepted(c: Candidate, ev=None, policy=None) -> bool:
    return evaluate(c, policy or AdmissionPolicy(), _checked() if ev is None else ev).decision != "accept"


def _with(lane: str, status: str, **extra) -> Dict[str, Dict[str, str]]:
    ev = _checked()
    ev[lane] = dict({"status": status, "detail": ""}, **extra)
    return ev


def _prereg_without(field: str) -> Candidate:
    pr = dict(_cand().preregistration)
    pr.pop(field)
    return _cand(preregistration=pr)


def _raises(fn: Callable[[], object], *errors) -> bool:
    try:
        fn()
    except errors:
        return True
    return False


def _release_mgr(tmp: str, now: List[int]) -> ReleaseManager:
    return ReleaseManager(Ledger(os.path.join(tmp, "rel.jsonl")), lambda: now[0], Budget(10, 3600),
                          {"canary": Breaker("canary", 300)}, max_evidence_age_s=600)


def _rel(rid: str, units: int = 3, evidence_at: int = 1000) -> Release:
    return Release(rid, "c" * 64, "p" * 64, "q" * 64, evidence_at, CanaryPlan(2, (("errors", 0),)), units)


def _cases(tmp: str) -> List[Tuple[str, Callable[[], bool]]]:
    cases: List[Tuple[str, Callable[[], bool]]] = []

    def case(name):
        def deco(fn):
            cases.append((name, fn))
            return fn
        return deco

    # -- candidate shape ----------------------------------------------------
    for field in ("metric", "n", "seeds", "arms", "holdout_commitment", "max_search_cost_units"):
        cases.append(("prereg_missing_" + field, lambda f=field: _not_accepted(_prereg_without(f))))
    cases += [
        ("arms_without_random_control", lambda: _not_accepted(_cand(preregistration=dict(
            _cand().preregistration, arms=["untouched_base", "fixed_policy", "candidate"])))),
        ("single_seed", lambda: _not_accepted(_cand(preregistration=dict(_cand().preregistration, seeds=[1])))),
        ("path_escape_dotdot", lambda: _not_accepted(_cand(paths=("skills/../../etc/passwd",)))),
        ("path_absolute", lambda: _not_accepted(_cand(paths=("/etc/passwd",)))),
        ("path_outside_allow_list", lambda: _not_accepted(_cand(paths=("plugins/x.py",)))),
        ("missing_nonclaim", lambda: _not_accepted(_cand(nonclaims=()))),
        ("bad_parent_digest", lambda: _not_accepted(_cand(parent_digest="xyz"))),
        ("unknown_kind", lambda: _not_accepted(_cand(kind="core_rewrite"))),
        ("code_patch_without_policy", lambda: _not_accepted(_cand(kind="code_patch", required_signer_roles=("r",)))),
        ("zero_compute_budget", lambda: _not_accepted(_cand(compute_budget=0))),
    ]

    # -- evidence / lanes ---------------------------------------------------
    cases += [
        ("lane_failed", lambda: _not_accepted(_cand(), _with("retention", "Failed"))),
        ("lane_missing_is_not_pass", lambda: _not_accepted(_cand(), {k: v for k, v in _checked().items() if k != "holdout"})),
        ("lane_notrun_is_not_pass", lambda: _not_accepted(_cand(), _with("controls", "NotRun"))),
        ("lane_unavailable_is_not_pass", lambda: _not_accepted(_cand(), _with("independent_verification", "Unavailable"))),
        ("lane_status_invented", lambda: _not_accepted(_cand(), _with("controls", "Passed"))),
        ("unknown_lane_injected", lambda: _not_accepted(_cand(), dict(_checked(), model_says_ok={"status": "Checked"}))),
        ("model_confidence_bypass", lambda: _not_accepted(_cand(), _with("controls", "Failed", model_confidence="0.99", override="true"))),
        ("instrument_disagreement", lambda: _not_accepted(_cand(), _with("instrument_validity", "Inconclusive"))),
        ("evidence_not_object", lambda: _not_accepted(_cand(), "all good")),  # type: ignore[arg-type]
    ]

    # -- intake -------------------------------------------------------------
    good = json.dumps(_cand().as_dict())
    cases += [
        ("json_duplicate_key", lambda: _raises(lambda: parse_json_strict('{"kind": "skill_refine", "kind": "code_patch"}'), CandidateParseError)),
        ("json_float", lambda: _raises(lambda: parse_json_strict('{"n": 40.0}'), CandidateParseError)),
        ("json_nan", lambda: _raises(lambda: parse_json_strict('{"n": NaN}'), CandidateParseError)),
        ("candidate_extra_field", lambda: _raises(lambda: candidate_from_dict(dict(json.loads(good), grants_authority=True)), CandidateParseError)),
        ("candidate_bool_as_int", lambda: _raises(lambda: candidate_from_dict(dict(json.loads(good), compute_budget=True)), CandidateParseError)),
    ]

    # -- signatures ---------------------------------------------------------
    cp = _cand(kind="code_patch", required_signer_roles=("reviewer", "security"))
    cases += [
        ("self_signed_by_proposer", lambda: lane_signature(cp, [{"signer": "hermes", "role": "reviewer"}, {"signer": "b", "role": "security"}], "runner")["status"] == "Failed"),
        ("signed_by_executor", lambda: lane_signature(cp, [{"signer": "runner", "role": "reviewer"}, {"signer": "b", "role": "security"}], "runner")["status"] == "Failed"),
        ("one_signer_two_roles", lambda: lane_signature(cp, [{"signer": "a", "role": "reviewer"}, {"signer": "a", "role": "security"}], "runner")["status"] == "Failed"),
        ("missing_role", lambda: lane_signature(cp, [{"signer": "a", "role": "reviewer"}], "runner")["status"] != "Checked"),
    ]

    # -- capabilities -------------------------------------------------------
    def _refinery(sub: str, now: List[int]):
        root, store = os.path.join(tmp, sub, "root"), os.path.join(tmp, sub, "store")
        os.makedirs(os.path.join(root, "skills", "a"))
        return Refinery(root, store, Ledger(os.path.join(tmp, sub, "l.jsonl")), clock=lambda: now[0])

    def replay():
        now = [1000]
        r = _refinery("replay", now)
        c = _cand()
        v = evaluate(c, AdmissionPolicy(), _checked())
        tok = r.issue_capability(c, v, 60)
        r.apply(c, v, tok, {"skills/a/SKILL.md": "x"}, lambda: [])
        return _raises(lambda: r.apply(c, v, tok, {"skills/a/SKILL.md": "y"}, lambda: []), RefineryError)

    def expired():
        now = [1000]
        r = _refinery("expired", now)
        c = _cand()
        v = evaluate(c, AdmissionPolicy(), _checked())
        tok = r.issue_capability(c, v, 60)
        now[0] += 61
        return _raises(lambda: r.apply(c, v, tok, {"skills/a/SKILL.md": "x"}, lambda: []), RefineryError)

    def cross_candidate():
        now = [1000]
        r = _refinery("cross", now)
        c1, c2 = _cand(), _cand(candidate_id="other")
        v1, v2 = (evaluate(c, AdmissionPolicy(), _checked()) for c in (c1, c2))
        tok = r.issue_capability(c1, v1, 60)
        return _raises(lambda: r.apply(c2, v2, tok, {"skills/a/SKILL.md": "x"}, lambda: []), RefineryError)

    def quarantined_gets_no_capability():
        now = [1000]
        r = _refinery("quar", now)
        c = _cand()
        v = evaluate(c, AdmissionPolicy(), _with("controls", "NotRun"))
        return _raises(lambda: r.issue_capability(c, v, 60), RefineryError)

    def content_swap():
        from .broker import apply_via_broker
        from .candidate import writes_digest

        root, store = os.path.join(tmp, "swap", "root"), os.path.join(tmp, "swap", "store")
        os.makedirs(os.path.join(root, "skills", "a"))
        lpath = os.path.join(tmp, "swap", "l.jsonl")
        c = _cand(content_digest=writes_digest({"skills/a/SKILL.md": "approved"}))
        v = evaluate(c, AdmissionPolicy(), _checked())
        tok = Refinery(root, store, Ledger(lpath), clock=lambda: int(time.time())).issue_capability(c, v, 60)
        r = apply_via_broker({"root": root, "store": store, "ledger": lpath, "control_dir": os.path.join(tmp, "swap", "ctl"),
                              "candidate": c.as_dict(), "verdict": v.as_dict(), "token": tok,
                              "writes": {"skills/a/SKILL.md": "malicious"}, "protected": {}, "keyring": ""})
        return r.get("ok") is False and not os.path.exists(os.path.join(root, "skills", "a", "SKILL.md"))

    cases += [("capability_content_swap", content_swap)]
    cases += [("capability_replay", replay), ("capability_expired", expired),
              ("capability_cross_candidate", cross_candidate),
              ("quarantine_cannot_get_capability", quarantined_gets_no_capability)]

    # -- release state machine (Statebook patterns) -------------------------
    def stale_evidence():
        now = [5000]
        m = _release_mgr(os.path.join(tmp, "stale"), now)
        m.enqueue(_rel("r", evidence_at=1000))
        return m.revalidate("r", "p" * 64, "q" * 64) == "rejected"

    def slow_drain():
        now = [1000]
        m = _release_mgr(os.path.join(tmp, "drain"), now)
        for i in range(3):
            m.enqueue(_rel("r%d" % i, units=3))
        return _raises(lambda: m.enqueue(_rel("r3", units=3)), ReleaseError)

    def cancel_race():
        now = [1000]
        m = _release_mgr(os.path.join(tmp, "race"), now)
        m.enqueue(_rel("r"))
        m.revalidate("r", "p" * 64, "q" * 64)
        m.start_canary("r")
        m.observe("r", {"errors": 0})
        m.observe("r", {"errors": 0})
        m.cancel("r", "operator")
        return _raises(lambda: m.promote("r"), ReleaseError)

    def breaker_silent_rearm():
        now = [1000]
        m = _release_mgr(os.path.join(tmp, "brk"), now)
        m.trip("canary", "drill")
        now[0] += 10_000
        m.enqueue(_rel("r"))
        return m.revalidate("r", "p" * 64, "q" * 64) == "rejected"

    def early_promotion():
        now = [1000]
        m = _release_mgr(os.path.join(tmp, "early"), now)
        m.enqueue(_rel("r"))
        m.revalidate("r", "p" * 64, "q" * 64)
        m.start_canary("r")
        m.observe("r", {"errors": 0})
        return _raises(lambda: m.promote("r"), ReleaseError)

    for sub in ("stale", "drain", "race", "brk", "early"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    cases += [("release_stale_evidence", stale_evidence), ("release_budget_slow_drain", slow_drain),
              ("release_cancel_vs_promote", cancel_race), ("breaker_no_silent_rearm", breaker_silent_rearm),
              ("release_promote_before_window", early_promotion)]
    return cases


def run_corpus() -> Dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="caduceus-corpus-") as tmp:
        results = {}
        for name, fn in _cases(tmp):
            try:
                results[name] = bool(fn())
            except Exception as exc:  # an attack that crashes the code is a finding, not a refusal
                results[name] = False
                results[name + ":exception"] = type(exc).__name__  # type: ignore[assignment]
    c, p = _cand(), AdmissionPolicy()
    replays = {evaluate(c, p, _checked()).digest() for _ in range(5)}
    misses = sorted(k for k, v in results.items() if v is False)
    total = sum(1 for k in results if ":" not in k)
    return {
        "cases": total,
        "refused": total - len(misses),
        "misses": misses,
        "gate_deterministic": len(replays) == 1 and evaluate(c, p, _checked()).decision == "accept",
        "ok": not misses and len(replays) == 1,
    }

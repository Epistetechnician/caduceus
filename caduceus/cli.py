"""Command line: audit, anchor, verify, demo. Read-only unless stated."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__, audit, readback
from .demo import run_demo
from .digest import canonical_bytes


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="caduceus", description="Verified evolution for Hermes (Phase 1 substrate).")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="read-only audit of a Hermes home (skills, usage, curator ledger)")
    a.add_argument("--hermes-home", default="~/.hermes")
    a.add_argument("--min-use", type=int, default=3, help="use_count for protected-suite candidates")
    a.add_argument("--contracts", help="directory of contract files (e.g. ./contracts)")

    c = sub.add_parser("anchor-curator", help="print a Merkle anchor over the Hermes curator ledger (read-only)")
    c.add_argument("--hermes-home", default="~/.hermes")

    v = sub.add_parser("verify-curator", help="check the curator ledger still extends a previously printed anchor")
    v.add_argument("--hermes-home", default="~/.hermes")
    v.add_argument("--size", type=int, required=True)
    v.add_argument("--root", required=True)

    r = sub.add_parser("verify-ledger", help="independently validate a Caduceus ledger file")
    r.add_argument("path")

    d = sub.add_parser("demo", help="run the synthetic end-to-end sandbox demo")
    d.add_argument("--out", help="empty output directory (default: a temp dir)")

    pr = sub.add_parser("preregister", help="lock a preregistration into a ledger (pins contracts and skill trees)")
    pr.add_argument("prereg")
    pr.add_argument("--ledger", required=True)
    pr.add_argument("--contracts", required=True)
    pr.add_argument("--hermes-home", default="~/.hermes", help="read-only; used to pin skill tree digests")

    rp = sub.add_parser("run-paired", help="run a locked paired test through Hermes (spends model calls)")
    rp.add_argument("prereg")
    rp.add_argument("--ledger", required=True)
    rp.add_argument("--contracts", required=True)
    rp.add_argument("--hermes-home", default="~/.hermes", help="read-only; skills are copied from here")
    rp.add_argument("--hermes-src", default="~/.hermes/hermes-agent")
    key = rp.add_mutually_exclusive_group(required=True)
    key.add_argument("--api-key-env", help="name of the env var holding the provider key")
    key.add_argument("--use-hermes-login", action="store_true",
                     help="use the provider login stored by `hermes auth` (resolved inside the Hermes runner)")
    rp.add_argument("--allow-model-calls", action="store_true", help="required: this command calls a model")
    rp.add_argument("--candidate-root", help="skills root holding the proposed skill (candidate designs)")
    rp.add_argument("--custody", help="owner-only dir outside the repo to keep raw outputs (for a second instrument)")

    jg = sub.add_parser("judge", help="second instrument: LLM judge over custody outputs (spends model calls)")
    jg.add_argument("prereg")
    jg.add_argument("--contracts", required=True)
    jg.add_argument("--custody", required=True)
    jg.add_argument("--judge-model", required=True)
    jg.add_argument("--judge-provider", default="anthropic")
    jg.add_argument("--judge-base-url", default="https://api.anthropic.com")
    jg.add_argument("--hermes-src", default="~/.hermes/hermes-agent")
    jg.add_argument("--use-hermes-login", action="store_true")
    jg.add_argument("--api-key-env")
    jg.add_argument("--allow-model-calls", action="store_true")
    jg.add_argument("--out", required=True, help="where to write the instrument report JSON")

    ex = sub.add_parser("exchange", help="assemble all 13 lanes for a candidate and decide (read-only unless --record)")
    ex.add_argument("candidate", help="candidate JSON (as printed by `propose`)")
    ex.add_argument("prereg")
    ex.add_argument("--ledger", required=True, help="the candidate experiment's ledger")
    ex.add_argument("--contracts", required=True)
    ex.add_argument("--instrument-report", help="JSON from `caduceus judge`")
    ex.add_argument("--keyring", help="for code_patch signatures")
    ex.add_argument("--record", help="governance ledger to record candidate, lyre report digest and verdict")

    ac = sub.add_parser("admit-call", help="issue a single-use capability for ONE exact skill_manage call from an accepted verdict")
    ac.add_argument("candidate", help="candidate JSON (as printed by `propose`)")
    ac.add_argument("call", help='JSON file {"tool": "skill_manage", "args": {...}}')
    ac.add_argument("--governance-ledger", required=True, help="ledger holding the candidate and verdict (`exchange --record`)")
    ac.add_argument("--plugin-base", required=True, help="the plugin's own caduceus directory, e.g. <HERMES_HOME>/caduceus")
    ac.add_argument("--ttl", type=int, default=600)

    rc = sub.add_parser("reconcile", help="state governance: judge the skills tree against its last admitted HEAD and restore anything not admitted")
    rc.add_argument("--skills-root", required=True)
    rc.add_argument("--base", required=True, help="state directory (the plugin's <HERMES_HOME>/caduceus)")
    rc.add_argument("--allow-live", action="store_true", help="permit governing a real Hermes home")
    for name, hlp in (("status", "one-screen operator view of state governance (reads only)"), ("review-effect", "bounded diff of a held effect, for a human deciding whether to release it")):
        sp = sub.add_parser(name, help=hlp)
        if name == "review-effect":
            sp.add_argument("effect_digest")
        sp.add_argument("--skills-root", required=True)
        sp.add_argument("--base", required=True)
        sp.add_argument("--allow-live", action="store_true")
    rl = sub.add_parser("release-effect", help="operator releases a quarantined effect (HEAD must not have moved)")
    rl.add_argument("effect_digest")
    rl.add_argument("--skills-root", required=True)
    rl.add_argument("--base", required=True)
    rl.add_argument("--operator", required=True)
    rl.add_argument("--reason", required=True)
    rl.add_argument("--allow-live", action="store_true")

    ck = sub.add_parser("check-add", help="register an executable check for a skill (must pass on the admitted HEAD); argv follows --")
    ck.add_argument("--skills-root", required=True)
    ck.add_argument("--base", required=True)
    ck.add_argument("--skill", required=True, help="category/name")
    ck.add_argument("--id", required=True)
    ck.add_argument("--operator", required=True)
    ck.add_argument("--timeout", type=int, default=10)
    ck.add_argument("--expect-exit", type=int, default=0)
    ck.add_argument("--expect-stdout-sha256")
    ck.add_argument("--allow-live", action="store_true")
    ck.add_argument("argv", nargs="+")

    sub.add_parser("corpus", help="run the adversarial corpus; non-zero exit on any miss")

    dg = sub.add_parser("diagnostics", help="lane vector report (read-only)")
    dg.add_argument("--ledger", action="append", default=[], help="ledger file (repeatable)")
    dg.add_argument("--caduceus-dir", help="directory holding control.json (e.g. $HERMES_HOME/caduceus)")

    ct = sub.add_parser("control", help="operator controls: freeze, unfreeze, subsystem on/off")
    ct.add_argument("action", choices=("show", "freeze", "unfreeze", "enable", "disable"))
    ct.add_argument("subsystem", nargs="?")
    ct.add_argument("--caduceus-dir", required=True)
    ct.add_argument("--operator", default=os.environ.get("USER", "operator"))
    ct.add_argument("--reason", default="")

    kr = sub.add_parser("add-signer", help="add a reviewer key to an owner-only keyring")
    kr.add_argument("--keyring", required=True)
    kr.add_argument("--signer", required=True)
    kr.add_argument("--role", required=True)

    sg = sub.add_parser("sign", help="record a review signature for a candidate digest")
    sg.add_argument("candidate_digest")
    sg.add_argument("--keyring", required=True)
    sg.add_argument("--signer", required=True)
    sg.add_argument("--ledger", required=True)

    ad = sub.add_parser("admit-source", help="admit a memory/learning-signal receipt (JSON file)")
    ad.add_argument("receipt")
    ad.add_argument("--ledger", required=True)

    pp = sub.add_parser("propose", help="wrap a proposed skill directory as a candidate (prints candidate JSON)")
    pp.add_argument("proposed_dir")
    pp.add_argument("--rel-dir", required=True, help="category/name of the skill under skills/")
    pp.add_argument("--hermes-home", default="~/.hermes")
    pp.add_argument("--proposer", required=True)
    pp.add_argument("--holdout-commitment", help="defaults to the contract's held-out commitment (needs --contracts)")
    pp.add_argument("--contracts", help="contracts dir; computes the holdout commitment from the skill's contract")
    pp.add_argument("--experiment-prereg", help="bind the candidate to this locked candidate experiment")
    pp.add_argument("--max-search-cost", type=int, default=400)

    dr = sub.add_parser("drill", help="supervised-apply drills on a sandbox copy of one skill")
    dr.add_argument("--rel-dir", default="software-development/codebase-inspection")
    dr.add_argument("--hermes-home", default="~/.hermes", help="read-only; the skill is copied from here")

    ap_ = sub.add_parser("apply", help="send an apply request (JSON file) to the broker process")
    ap_.add_argument("request")

    args = ap.parse_args(argv)
    if args.cmd in ("corpus", "diagnostics", "control", "add-signer", "sign", "admit-source", "admit-call", "reconcile", "release-effect", "status", "review-effect", "check-add", "propose", "apply", "drill"):
        return _ops(args)
    if args.cmd in ("preregister", "run-paired"):
        return _experiment(args)
    if args.cmd in ("judge", "exchange"):
        return _exchange(args)
    if args.cmd == "audit":
        _print(audit.audit_hermes_home(args.hermes_home, args.min_use, args.contracts))
        return 0
    if args.cmd in ("anchor-curator", "verify-curator"):
        path = os.path.join(os.path.expanduser(args.hermes_home), "skills", ".curator_ledger.jsonl")
        lines = audit.curator_ledger_lines(path)
        if args.cmd == "anchor-curator":
            _print(audit.anchor_lines(lines))
            return 0
        ok = audit.verify_append_only(lines, args.size, args.root)
        _print({"append_only_extension_of_anchor": ok})
        return 0 if ok else 2
    if args.cmd == "verify-ledger":
        try:
            _print(readback.verify_file(args.path))
            return 0
        except readback.ReadbackError as exc:
            print("INVALID: %s" % exc, file=sys.stderr)
            return 2
    if args.cmd == "demo":
        _print(run_demo(args.out))
        return 0
    return 1


def _ops(args) -> int:
    from .ledger import Ledger

    if args.cmd == "corpus":
        from .corpus import run_corpus

        r = run_corpus()
        _print(r)
        return 0 if r["ok"] else 2
    if args.cmd == "diagnostics":
        from .diagnostics import diagnostics

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _print(diagnostics(repo, args.ledger, args.caduceus_dir))
        return 0
    if args.cmd == "control":
        from . import control

        base = args.caduceus_dir
        led = Ledger(os.path.join(base, "ledger.jsonl")) if args.action != "show" else None
        if args.action == "freeze":
            control.freeze(base, led, args.operator, args.reason)
        elif args.action == "unfreeze":
            control.unfreeze(base, led, args.operator, args.reason)
        elif args.action in ("enable", "disable"):
            if args.subsystem not in control.SUBSYSTEMS:
                print("subsystem must be one of %s" % ", ".join(control.SUBSYSTEMS), file=sys.stderr)
                return 2
            control.set_subsystem(base, led, args.subsystem, args.action == "enable", args.operator)
        c = control.load(base)
        _print({"frozen": c.frozen, "subsystems": c.subsystems, "valid": c.valid})
        return 0
    if args.cmd == "add-signer":
        from . import signing

        signing.add_signer(args.keyring, args.signer, args.role)
        _print({"added": args.signer, "role": args.role})
        return 0
    if args.cmd == "sign":
        from . import signing

        body = signing.sign(Ledger(args.ledger), args.keyring, args.signer, args.candidate_digest)
        _print({k: body[k] for k in ("candidate_digest", "signer", "role")})
        return 0
    if args.cmd == "admit-call":
        from .candidate import candidate_from_dict, parse_json_strict
        from .toolcall import ToolCallError, issue_for_call

        with open(args.candidate, "r", encoding="utf-8") as fh:
            raw = parse_json_strict(fh.read())
        cand = candidate_from_dict(raw.get("candidate", raw) if isinstance(raw, dict) else raw)
        with open(args.call, "r", encoding="utf-8") as fh:
            call = parse_json_strict(fh.read())
        try:
            _print(issue_for_call(Ledger(args.governance_ledger), cand, call.get("tool", ""), call.get("args"), args.plugin_base, args.ttl))
        except ToolCallError as exc:
            print("refused: %s" % exc, file=sys.stderr)
            return 3
        return 0
    if args.cmd == "check-add":
        from .checks import CheckError
        from .transition import Kernel, TransitionError

        chk = {"id": args.id, "argv": args.argv, "timeout_s": args.timeout, "expect_exit": args.expect_exit}
        if args.expect_stdout_sha256:
            chk["expect_stdout_sha256"] = args.expect_stdout_sha256
        try:
            _print(Kernel(args.skills_root, args.base, allow_live=args.allow_live).register_check(args.skill, chk, args.operator))
        except (TransitionError, CheckError) as exc:
            print("refused: %s" % exc, file=sys.stderr)
            return 3
        return 0
    if args.cmd in ("status", "review-effect"):
        from .transition import Kernel, TransitionError

        try:
            k = Kernel(args.skills_root, args.base, allow_live=args.allow_live)
            _print(k.status() if args.cmd == "status" else k.review(args.effect_digest))
        except TransitionError as exc:
            print("refused: %s" % exc, file=sys.stderr)
            return 3
        return 0
    if args.cmd in ("reconcile", "release-effect"):
        from .transition import Kernel, TransitionError

        try:
            k = Kernel(args.skills_root, args.base, allow_live=args.allow_live)
            out = k.reconcile() if args.cmd == "reconcile" else k.release(args.effect_digest, args.operator, args.reason)
        except TransitionError as exc:
            print("refused: %s" % exc, file=sys.stderr)
            return 3
        v = out.get("verdict") or {}
        _print({"status": out["status"], "tree": out["tree"], "decision": v.get("decision"), "reason_codes": v.get("reason_codes"), "effect_digest": v.get("effect_digest")})
        return 0 if out["status"] in ("clean", "baseline", "accepted", "released") else 3
    if args.cmd == "admit-source":
        from .candidate import parse_json_strict
        from .source_admission import admit

        with open(args.receipt, "r", encoding="utf-8") as fh:
            r = admit(Ledger(args.ledger), parse_json_strict(fh.read()))
        _print(r)
        return 0 if r["decision"] == "accept" else 3
    if args.cmd == "propose":
        from .propose import default_preregistration, propose_skill

        root = os.path.join(os.path.expanduser(args.hermes_home), "skills")
        commitment = args.holdout_commitment
        if args.contracts:
            from .contract import load_contracts_dir
            from .lanes import holdout_commitment

            cfs = [c for c in load_contracts_dir(args.contracts).values() if c.skill_rel_dir == args.rel_dir]
            if len(cfs) != 1:
                print("no unique contract for %s" % args.rel_dir, file=sys.stderr)
                return 2
            commitment = holdout_commitment(cfs[0].contract.probes_of("held_out"))
        if not commitment:
            print("need --holdout-commitment or --contracts", file=sys.stderr)
            return 2
        pre = default_preregistration(commitment, 40, [1, 2])
        pre["max_search_cost_units"] = args.max_search_cost
        if args.experiment_prereg:
            from .experiment import load_prereg

            ep = load_prereg(args.experiment_prereg)
            pre.update(experiment_id=ep.experiment_id, experiment_prereg_digest=ep.digest())
        cand, writes = propose_skill(root, args.rel_dir, args.proposed_dir, args.proposer, pre)
        _print({"candidate": cand.as_dict(), "candidate_digest": cand.digest(), "paths": sorted(writes)})
        return 0
    if args.cmd == "drill":
        from .drill import run_drills

        r = run_drills(os.path.join(os.path.expanduser(args.hermes_home), "skills"), args.rel_dir)
        _print(r)
        return 0 if r["ok"] else 2
    if args.cmd == "apply":
        from .broker import apply_via_broker
        from .candidate import parse_json_strict

        with open(args.request, "r", encoding="utf-8") as fh:
            reply = apply_via_broker(parse_json_strict(fh.read()))
        _print(reply)
        return 0 if reply.get("ok") else 3
    return 1


def _exchange(args) -> int:
    from . import exchange, experiment
    from .candidate import candidate_from_dict, parse_json_strict
    from .contract import load_contracts_dir
    from .ledger import Ledger

    prereg = experiment.load_prereg(args.prereg)
    contracts = load_contracts_dir(args.contracts)
    skill = prereg.raw["skills"][0]["skill"]
    if args.cmd == "judge":
        if not args.allow_model_calls:
            print("refusing: judge calls a model; pass --allow-model-calls", file=sys.stderr)
            return 2
        from . import judge
        from .custody import Custody
        from .executor import HermesExecutor, default_config_from_env

        cfg = default_config_from_env(args.hermes_src, args.judge_model, args.judge_provider, args.judge_base_url,
                                      args.api_key_env, use_hermes_login=args.use_hermes_login)
        outputs = Custody(args.custody).load(prereg.experiment_id)
        with HermesExecutor(cfg, None, None, allow_model_calls=True) as ex:
            report = judge.grade(ex, contracts[skill], outputs)
        report.update(judge_model=args.judge_model, experiment_id=prereg.experiment_id)
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
        _print(report)
        return 0
    with open(args.candidate, "r", encoding="utf-8") as fh:
        raw = parse_json_strict(fh.read())
    cand = candidate_from_dict(raw.get("candidate", raw) if isinstance(raw, dict) else raw)
    instrument = None
    if args.instrument_report:
        with open(args.instrument_report) as fh:
            r = json.load(fh)
        if r.get("experiment_id") != prereg.experiment_id:
            print("instrument report is for a different experiment", file=sys.stderr)
            return 2
        instrument = {"agree": r["agree"], "total": r["total"]}
    sigs = []
    if args.keyring:
        from .signing import verified_signatures

        sigs = verified_signatures(Ledger(args.record or args.ledger), args.keyring, cand.digest())
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = exchange.assemble(cand, contracts[skill], prereg, args.ledger, repo, sigs, instrument)
    if args.record:
        exchange.record(Ledger(args.record), cand, out["verdict"], out["lyre_report"])
    _print({"decision": out["verdict"].decision, "reason_codes": list(out["verdict"].reason_codes),
            "verdict_digest": out["verdict"].digest(), "evidence": out["evidence"]})
    return 0 if out["verdict"].decision == "accept" else 3


def _experiment(args) -> int:
    from . import experiment, skills
    from .contract import load_contracts_dir
    from .ledger import Ledger

    prereg = experiment.load_prereg(args.prereg)
    contracts = load_contracts_dir(args.contracts)
    root = os.path.join(os.path.expanduser(args.hermes_home), "skills")
    trees = {
        s["skill"]: skills.load_skill(root, contracts[s["skill"]].skill_rel_dir).tree_digest
        for s in prereg.raw["skills"] if s["skill"] in contracts
    }
    ledger = Ledger(args.ledger)
    if args.cmd == "preregister":
        entry = experiment.lock(prereg, ledger, contracts, trees)
        _print({"locked": prereg.experiment_id, "prereg_digest": prereg.digest(), "seq": entry["seq"]})
        return 0
    if not args.allow_model_calls:
        print("refusing: run-paired calls a model; pass --allow-model-calls", file=sys.stderr)
        return 2
    from .executor import HermesExecutor, default_config_from_env

    ex = prereg.raw["executor"]
    cfg = default_config_from_env(args.hermes_src, ex["model"], ex["provider"], ex["base_url"], args.api_key_env,
                                 use_hermes_login=args.use_hermes_login)
    opened = []

    if tuple(prereg.raw["arms"]) == experiment.ARMS_CANDIDATE and not args.candidate_root:
        print("candidate design needs --candidate-root", file=sys.stderr)
        return 2

    def make(arm, cf):
        loads = arm != "without_skill"
        src = os.path.abspath(os.path.expanduser(args.candidate_root)) if arm == "candidate" else root
        e = HermesExecutor(cfg, src if loads else None, cf.skill_rel_dir if loads else None, allow_model_calls=True)
        opened.append(e)
        return e

    try:
        from .custody import Custody

        custody = Custody(args.custody) if args.custody else None
        _print(experiment.run_paired(prereg, ledger, contracts, trees, make, custody=custody))
    finally:
        for e in opened:
            e.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

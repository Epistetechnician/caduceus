"""exp-008 runner: verified-repair adapter vs controls on sealed assess bugs (Phase 6c).

Stages (resumable; state in $EXP008_WORK, default ~/hsi-runs/exp-008, outside the repo):
  lock   snapshot the repo, regenerate fit/tune tasks, check them against the manifest, witness the lock
  gen    base model attempts on fit tasks, verified by the oracle
  train  verified adapter and pool-matched unverified adapter, per seed
  eval   4 arms x seeds on assess (and tune for retention); assess tasks regenerated here, never cached
  report contrasts, retention, decision; ledger entries and result file
Run under hsi-lab's venv (needs mlx): PYTHONPATH=<caduceus>:<hsi-lab>/src <venv>/bin/python scripts/run_exp008.py <stage>
"""

import json
import os
import random
import subprocess
import sys
import tarfile
import io
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from caduceus import bugrepair as br, bugtasks, realbugs  # noqa: E402
try:
    from hsi.learning import llm as _hsi_llm
    _hsi_llm.MODELS["llama3.1-8b"] = ("mlx-community/Meta-Llama-3.1-8B-Instruct-4bit", "241a666dad6cb93c8ff213d39a7f34a36bf26db4", "llama3.1")
    _hsi_llm.MODELS["qwen2.5-coder-7b"] = ("mlx-community/Qwen2.5-Coder-7B-Instruct-4bit", "019cc73c45c770444708a6dd8690c66243cc5c80", "apache-2.0")
except ImportError:
    pass
from caduceus.digest import domain_digest  # noqa: E402
from caduceus.ledger import Ledger  # noqa: E402

REAL = ("020", "021", "022", "023", "024")
WORK = os.environ.get("EXP008_WORK", os.path.expanduser("~/hsi-runs/exp-%s" % os.environ.get("EXP", "008")))
N = os.environ.get("EXP", "008")
PREREG = os.path.join(REPO, "prereg", {"008": "exp-008-bugrepair-round1", "009": "exp-009-bugrepair-round2", "010": "exp-010-bugrepair-round3", "011": "exp-011-gentle-round1", "012": "exp-012-gentle-replication", "013": "exp-013-recursion-round2", "014": "exp-014-recursion-round2b", "015": "exp-015-distill-scale", "016": "exp-016-second-trainee", "017": "exp-017-llama-trainee", "018": "exp-018-coder-trainee", "019": "exp-019-structural-bugs", "020": "exp-020-real-bugs", "021": "exp-021-real-bugs-spec", "022": "exp-022-real-spec-32b", "023": "exp-023-real-spec-scaled", "024": "exp-024-real-scaled-remote7b"}[N] + ".json")
MANIFEST = os.path.join(REPO, "tasks", "realbugs-v2.manifest.json" if N in ("023", "024") else "realbugs-v1.manifest.json") if N in REAL else os.path.join(REPO, "tasks", "bugtasks-v%d.manifest.json" % {"008": 1, "009": 2, "010": 2, "011": 3, "012": 4, "013": 5, "014": 6, "015": 7, "016": 7, "017": 7, "018": 7, "019": 8}[N])
LEDGER = os.path.join(REPO, "ledgers", "exp-%s-bugrepair.jsonl" % N)
GENTLE = ("011", "012", "013", "014", "015", "016", "017", "018", "019", "020", "021", "022", "023", "024")
CLONES = os.path.expanduser(os.environ.get("REALBUGS_CLONES", "~/hsi-runs/realbugs/repos"))
MAX_EXAMPLES = 150 if N == "019" else None
TRAINEE = {"016": "qwen2.5-1.5b", "017": "llama3.1-8b", "018": "qwen2.5-coder-7b", "019": "qwen2.5-coder-7b", "020": "qwen2.5-coder-7b", "021": "qwen2.5-coder-7b", "023": "qwen2.5-coder-7b"}.get(N, "qwen2.5-7b")
K_ATTEMPTS, GEN_TEMP, MAX_NEW = (8 if N in ("013", "014") else 12), 0.8, 400
RANK, LAYERS, LR, SCALE = (8, 16, 1e-5, 4.0) if os.environ.get("EXP") in GENTLE else (8, 16, 1e-4, 20.0)
MAX_TRAIN_PROMPT, FEWSHOT_K = 3500, 4
ARMS = ("untouched_base", "fixed_policy", "candidate", "deterministic_random")


def p(*a):
    return os.path.join(WORK, *a)


def jload(path):
    with open(path) as fh:
        return json.load(fh)


def jdump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, sort_keys=True)


def prereg():
    return jload(PREREG)


EID = prereg()["experiment_id"]


def modules_of(split):
    m = jload(MANIFEST)
    # manifest is digest-only; module names are public in it per task
    return sorted({t["module"] for t in m["tasks"] if t["split"] == split})


def gen_split(m, split):
    if m.get("family") == "real":
        return realbugs.build(CLONES, [e for e in m["tasks"] if e["split"] == split], m["split_seed"], with_spec=(N in ("021", "022", "023", "024")))
    ts = bugtasks.generate(p("repo"), modules_of(split), m["split_seed"], m["per_module"], m.get("family", "syntactic"))
    return [t for t in ts if t["split"] == split]


def tasks_for(split, cache=True):
    path = p("tasks_%s.json" % split)
    if cache and os.path.exists(path):
        return jload(path)
    m = jload(MANIFEST)
    want = {t["task_id"] for t in m["tasks"] if t["split"] == split}
    ts = gen_split(m, split)
    if {t["task_id"] for t in ts} != want:
        sys.exit("task regeneration does not match manifest for %s (repo changed?)" % split)
    if cache:
        jdump(path, ts)
    return ts


def stage_lock():
    os.makedirs(WORK, exist_ok=True)
    if os.path.exists(p("lock.json")):
        sys.exit("already locked")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain", "--", "caduceus", "tests", "prereg", "tasks"], cwd=REPO, text=True).strip():
        sys.exit("working tree has uncommitted changes under caduceus/ tests/ prereg/ tasks/; commit first")
    raw = subprocess.check_output(["git", "archive", "HEAD"], cwd=REPO)
    tarfile.open(fileobj=io.BytesIO(raw)).extractall(p("repo"))
    pr = prereg()
    m = jload(MANIFEST)
    assert m["manifest_digest"] == pr["task_manifest_digest"], "manifest differs from prereg binding"
    for s in ("fit", "tune"):
        tasks_for(s)
    body = {"experiment_id": EID, "prereg_digest": domain_digest("caduceus.prereg.bugrepair.v1", pr),
            "manifest_digest": m["manifest_digest"], "split_commitments": pr["split_commitments"], "repo_commit": head}
    Ledger(LEDGER).append("prediction_lock", body)
    jdump(p("lock.json"), body)
    print("locked", body["prereg_digest"][:16], head[:8])


def load_llm():
    from hsi.learning.llm import LLM
    llm = LLM(name=TRAINEE)
    llm.attach_lora(RANK, LAYERS, 0, SCALE)
    llm.set_adapter(False)
    return llm


def load_proposer(llm):
    """exp-013: the proposer is the admitted round-1 adapter; its digest must match the preregistration."""
    spec = prereg()["proposer_adapter"]
    llm.load_adapter(os.path.expanduser(spec["path"]))
    llm.set_adapter(True)
    if llm.adapter_digest() != spec["adapter_digest"]:
        sys.exit("proposer adapter digest differs from preregistration")


def chat(llm, messages):
    return llm.tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)


def messages_for(view, shots=()):
    msgs = [{"role": "system", "content": br.SYSTEM}]
    for ex in shots:
        msgs.append({"role": "user", "content": br.user_prompt(ex["view"])})
        msgs.append({"role": "assistant", "content": ex["completion"]})
    msgs.append({"role": "user", "content": br.user_prompt(view)})
    return msgs


def sample(llm, prompt, temp, seed):
    import mlx.core as mx
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler
    mx.random.seed(seed)
    return generate(llm.model, llm.tok, prompt=prompt, max_tokens=MAX_NEW, sampler=make_sampler(temp=temp, top_p=0.95 if temp > 0 else 1.0))


def judge(task, text):
    """Apply the proposed edit and run the oracle. Returns (parsed, passed, reason, repaired_digest)."""
    e = br.parse_edit(text)
    if e is None:
        return False, False, "no_edit", ""
    new = br.apply_edit(task["mutated_source"], *e)
    if new is None:
        return False, False, "edit_not_applicable", ""
    r = realbugs.verify_repair(CLONES, task, new) if N in REAL else bugtasks.verify_repair(p("repo"), task, new)
    return True, r["passed"], r["reason"], r.get("repaired_digest", "")


def judge_all(tasks_by_id, recs):
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(lambda r: judge(tasks_by_id[r["task_id"]], r["text"]), recs))
    for r, (pa, ok, why, dg) in zip(recs, res):
        r.update(parsed=pa, passed=ok, reason=why, repaired_digest=dg)


def stage_export():
    from transformers import AutoTokenizer
    from hsi.learning.llm import model_path
    tok = AutoTokenizer.from_pretrained(str(model_path(TRAINEE)))
    with open(p("prompts.jsonl"), "w") as fh:
        for t in tasks_for("fit"):
            msgs = messages_for(bugtasks.proposer_view(t))
            if len(tok.encode(tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False))) <= MAX_TRAIN_PROMPT:
                fh.write(json.dumps({"task_id": t["task_id"], "messages": msgs}) + "\n")
    print("exported", sum(1 for _ in open(p("prompts.jsonl"))))


def stage_ingest():
    recs = [json.loads(ln) for ln in open(sys.argv[2])]
    tasks = {t["task_id"]: t for t in tasks_for("fit")}
    judge_all(tasks, recs)
    jdump(p("attempts_fit.json"), recs)
    print("attempts", len(recs), "verified", sum(r["passed"] for r in recs))


def stage_gen():
    tasks = tasks_for("fit")
    llm = load_llm()
    if N in ("013", "014"):
        load_proposer(llm)
    out = p("attempts_fit.json")
    recs = jload(out) if os.path.exists(out) else []
    done = {(r["task_id"], r["k"]) for r in recs}
    seed0 = prereg()["split_seed"]
    for t in tasks:
        view = bugtasks.proposer_view(t)
        prompt = chat(llm, messages_for(view))
        if len(llm.tok.encode(prompt)) > MAX_TRAIN_PROMPT:
            continue
        for k in range(K_ATTEMPTS):
            if (t["task_id"], k) in done:
                continue
            text = sample(llm, prompt, GEN_TEMP, seed0 + int(t["task_id"][:6], 16) + k)
            recs.append({"task_id": t["task_id"], "k": k, "text": text})
        jdump(out, recs)
        print("gen", t["task_id"], len(recs), flush=True)
    todo = [r for r in recs if "passed" not in r]
    judge_all({t["task_id"]: t for t in tasks}, todo)
    jdump(out, recs)
    ok = sum(r["passed"] for r in recs)
    print("attempts", len(recs), "verified", ok)


def training_sets(seed):
    recs = jload(p("attempts_fit.json"))
    tasks = {t["task_id"]: t for t in tasks_for("fit")}
    seen, ver = set(), []
    per_task = {}
    for r in recs:
        if r["passed"] and (r["task_id"], r["repaired_digest"]) not in seen and per_task.get(r["task_id"], 0) < (3 if N in GENTLE else 2):
            seen.add((r["task_id"], r["repaired_digest"]))
            per_task[r["task_id"]] = per_task.get(r["task_id"], 0) + 1
            e = br.parse_edit(r["text"])
            ver.append({"task_id": r["task_id"], "completion": br.format_edit(*e)})
    if MAX_EXAMPLES and len(ver) > MAX_EXAMPLES:  # deterministic, seed-independent cap (digest order)
        ver = sorted(ver, key=lambda e: domain_digest("caduceus.exp019.cap.v1", [e["task_id"], e["completion"]]))[:MAX_EXAMPLES]
    rng = random.Random("pool/%d" % seed)
    pool_src = [r for r in recs if r["text"].strip()]
    pool = [{"task_id": r["task_id"], "completion": r["text"].strip()} for r in rng.sample(pool_src, min(len(ver), len(pool_src)))]
    return ver, pool, tasks


def train_one(llm, examples, tasks, seed):
    import mlx.core as mx
    import mlx.optimizers as optim
    from mlx import nn
    llm.reset_lora(seed)
    eos = llm.tok.eos_token_id
    data = []
    for ex in examples:
        pids = llm.tok.encode(chat(llm, messages_for(bugtasks.proposer_view(tasks[ex["task_id"]]))))
        cids = llm.tok.encode(ex["completion"]) + [eos]
        data.append((pids, cids))

    def loss_fn(model, x, y, mask):
        ce = nn.losses.cross_entropy(model(x), y, reduction="none")
        return (ce * mask).sum() / mask.sum()

    vg = nn.value_and_grad(llm.model, loss_fn)
    opt = optim.Adam(learning_rate=LR)
    rng = random.Random("train/%d" % seed)
    steps = 3 * len(data)
    last = first = 0.0
    for s in range(steps):
        pids, cids = data[rng.randrange(len(data))]
        ids = pids + cids
        x = mx.array([ids[:-1]])
        y = mx.array([ids[1:]])
        mask = mx.array([[0.0] * (len(pids) - 1) + [1.0] * len(cids)])
        loss, grads = vg(llm.model, x, y, mask)
        opt.update(llm.model, grads)
        mx.eval(llm.model.parameters(), opt.state, loss)
        last = float(loss.item())
        first = first if s else last
        if N in GENTLE and last > max(2.0, 5 * first):
            raise SystemExit("loss spike %.3f at step %d (first %.3f): training aborted" % (last, s, first))
        if s % 10 == 0:
            print("  step", s, "/", steps, round(last, 4), "peak_gb", round(llm.peak_gb(), 1), flush=True)
    return steps, last


def stage_import():
    """exp-022: the fit attempts are the exp-021 attempts (same tasks, prompts, oracle); copied and re-verified, never regenerated."""
    src = os.path.expanduser(os.environ.get("IMPORT_FROM", "~/hsi-runs/exp-021/attempts_fit.json"))
    recs = [{k: r[k] for k in ("task_id", "k", "text")} for r in jload(src)]
    judge_all({t["task_id"]: t for t in tasks_for("fit")}, recs)
    jdump(p("attempts_fit.json"), recs)
    print("attempts", len(recs), "verified", sum(r["passed"] for r in recs))


def stage_rexport():
    """Write remote jobs: train.jsonl (one job per adapter) and eval.jsonl (one row per arm/seed/task)."""
    pr = prereg()
    jobs = []
    for seed in pr["seeds"]:
        ver, pool, tasks = training_sets(seed)
        if len(ver) < 60:
            Ledger(LEDGER).append("verdict", {"experiment_id": EID, "verdict": "NotRun", "reason": "training floor not met", "verified_examples": len(ver)})
            sys.exit("training floor not met: %d" % len(ver))
        for arm, ex in (("candidate", ver), ("deterministic_random", pool)):
            jobs.append({"key": "%s-%d" % (arm, seed), "seed": seed, "examples": [
                {"messages": messages_for(bugtasks.proposer_view(tasks[e["task_id"]])), "completion": e["completion"]} for e in ex],
                "data_digest": domain_digest("caduceus.exp008.train.v1", sorted((e["task_id"], e["completion"]) for e in ex))})
    with open(p("train.jsonl"), "w") as fh:
        for j in jobs:
            fh.write(json.dumps(j) + "\n")
    m = jload(MANIFEST)
    assess, tune = gen_split(m, "assess"), tasks_for("tune")
    if {t["task_id"] for t in assess} != {t["task_id"] for t in m["tasks"] if t["split"] == "assess"}:
        sys.exit("assess regeneration does not match manifest")
    recs = jload(p("attempts_fit.json"))
    ftasks = {t["task_id"]: t for t in tasks_for("fit")}
    bank, seen = [], set()
    for r in recs:
        if r["passed"] and r["task_id"] not in seen:
            seen.add(r["task_id"])
            bank.append({"view": bugtasks.proposer_view(ftasks[r["task_id"]]), "completion": br.format_edit(*br.parse_edit(r["text"]))})
    plan = [("untouched_base", 0), ("fixed_policy", 0)] + [(a, s) for s in pr["seeds"] for a in ("candidate", "deterministic_random")]
    n = 0
    with open(p("eval.jsonl"), "w") as fh:
        for arm, seed in plan:
            for split, ts in (("assess", assess), ("tune", tune if arm in ("untouched_base", "candidate") else [])):
                for t in ts:
                    view = bugtasks.proposer_view(t)
                    shots = fewshot(view, bank) if arm == "fixed_policy" else ()
                    fh.write(json.dumps({"arm": arm, "seed": seed, "split": split, "task_id": t["task_id"], "messages": messages_for(view, shots)}) + "\n")
                    n += 1
    print("jobs", len(jobs), "eval rows", n)


def stage_ringest():
    """argv: adapters.json from the node, eval_out.jsonl from the node."""
    jdump(p("adapters.json"), jload(sys.argv[2]))
    outs = [json.loads(ln) for ln in open(sys.argv[3])]
    m = jload(MANIFEST)
    byid = {t["task_id"]: t for t in gen_split(m, "assess") + tasks_for("tune")}
    judge_all(byid, outs)
    jdump(p("outcomes.json"), outs)
    print("outcomes", len(outs), "passed", sum(o["passed"] for o in outs))


def stage_train():
    pr = prereg()
    llm = load_llm()
    meta_path = p("adapters.json")
    meta = jload(meta_path) if os.path.exists(meta_path) else {}
    for seed in pr["seeds"]:
        ver, pool, tasks = training_sets(seed)
        if len(ver) < {"008": 1, "009": 30, "010": 30, "011": 100, "012": 100, "013": 100, "014": 100, "015": 100, "016": 100, "017": 100, "018": 100, "019": 100, "020": 60, "021": 60, "022": 60, "023": 100, "024": 100}[N]:
            Ledger(LEDGER).append("verdict", {"experiment_id": EID, "verdict": "NotRun", "reason": "training floor not met", "verified_examples": len(ver)})
            sys.exit("training floor not met: %d verified examples" % len(ver))
        for arm, ex in (("candidate", ver), ("deterministic_random", pool)):
            key = "%s-%d" % (arm, seed)
            if key in meta:
                continue
            steps, loss = train_one(llm, ex, tasks, seed)
            path = p("adapters", key + ".safetensors")
            dg = llm.save_adapter(__import__("pathlib").Path(path))
            meta[key] = {"adapter_digest": dg, "examples": len(ex), "steps": steps, "final_loss_milli": int(loss * 1000),
                         "data_digest": domain_digest("caduceus.exp008.train.v1", sorted((e["task_id"], e["completion"]) for e in ex))}
            jdump(meta_path, meta)
            print("trained", key, meta[key], flush=True)


def overlap(a, b):
    import re
    ta, tb = set(re.findall(r"[A-Za-z_]+", a)), set(re.findall(r"[A-Za-z_]+", b))
    return len(ta & tb) / max(1, len(ta | tb))


def fewshot(view, bank):
    ranked = sorted(bank, key=lambda ex: -overlap(view["source"], ex["view"]["source"]))
    return ranked[:FEWSHOT_K]


def stage_eval():
    pr = prereg()
    meta = jload(p("adapters.json"))
    m = jload(MANIFEST)
    assess = gen_split(m, "assess")
    if {t["task_id"] for t in assess} != {t["task_id"] for t in m["tasks"] if t["split"] == "assess"}:
        sys.exit("assess regeneration does not match manifest")
    tune = tasks_for("tune")
    recs = jload(p("attempts_fit.json"))
    ftasks = {t["task_id"]: t for t in tasks_for("fit")}
    bank, seen = [], set()
    for r in recs:
        if r["passed"] and r["task_id"] not in seen:
            seen.add(r["task_id"])
            bank.append({"view": bugtasks.proposer_view(ftasks[r["task_id"]]), "completion": br.format_edit(*br.parse_edit(r["text"]))})
    llm = load_llm()
    outp = p("outcomes.json")
    outs = jload(outp) if os.path.exists(outp) else []
    done = {(o["arm"], o["seed"], o["split"], o["task_id"]) for o in outs}
    plan = [("untouched_base", 0), ("fixed_policy", 0)] + [(a, s) for s in pr["seeds"] for a in ("candidate", "deterministic_random")]
    if N in ("014", "015"):
        plan.append(("parent", 0))
    for arm, seed in plan:
        if arm == "parent":
            llm.load_adapter(os.path.expanduser(pr["proposer_adapter"]["path"]))
            llm.set_adapter(True)
        elif arm in ("candidate", "deterministic_random"):
            llm.load_adapter(p("adapters", "%s-%d.safetensors" % (arm, seed)))
            llm.set_adapter(True)
        else:
            llm.set_adapter(False)
        for split, ts in (("assess", assess), ("tune", tune if (N not in GENTLE or arm in ("untouched_base", "candidate")) else [])):
            for t in ts:
                if (arm, seed, split, t["task_id"]) in done:
                    continue
                view = bugtasks.proposer_view(t)
                shots = fewshot(view, bank) if arm == "fixed_policy" else ()
                text = sample(llm, chat(llm, messages_for(view, shots)), 0.0, seed)
                outs.append({"arm": arm, "seed": seed, "split": split, "task_id": t["task_id"], "text": text})
            jdump(outp, outs)
        print("eval", arm, seed, len(outs), flush=True)
    byid = {t["task_id"]: t for t in assess + tune}
    todo = [o for o in outs if "passed" not in o]
    judge_all(byid, todo)
    jdump(outp, outs)


def stage_report():
    pr = prereg()
    outs = jload(p("outcomes.json"))
    meta = jload(p("adapters.json"))
    lock = jload(p("lock.json"))

    def rate_map(arm, seed, split):
        src = seed if arm in ("candidate", "deterministic_random") else 0
        return {o["task_id"]: int(o["passed"]) for o in outs if o["arm"] == arm and o["seed"] == src and o["split"] == split}

    per_seed, retention, rates = [], True, {}
    for seed in pr["seeds"]:
        pa = {a: rate_map(a, seed, "assess") for a in ARMS}
        per_seed.append(br.contrasts(pa, seed, pr["bootstrap_resamples"]))
        rates[str(seed)] = {a: sum(v.values()) * br.MICRO // max(1, len(v)) for a, v in pa.items()}
        tb, tc = rate_map("untouched_base", seed, "tune"), rate_map("candidate", seed, "tune")
        if sum(tc.values()) * br.MICRO // max(1, len(tc)) < sum(tb.values()) * br.MICRO // max(1, len(tb)) - 20000:
            retention = False
    extra = {}
    if N in ("014", "015"):  # parent arm is reported, not gating
        pm = rate_map("parent", 0, "assess")
        extra["parent_rate_micro"] = sum(pm.values()) * br.MICRO // max(1, len(pm))
        for seed in pr["seeds"]:
            cm = rate_map("candidate", seed, "assess")
            extra["candidate_minus_parent_micro_%d" % seed] = (sum(cm.values()) - sum(pm.values())) * br.MICRO // max(1, len(cm))
    verdict = br.decide(per_seed, retention, pr["min_effect_micro"])
    led = Ledger(LEDGER)
    for a in ARMS:
        for seed in pr["seeds"]:
            rs = [o for o in outs if o["arm"] == a and o["split"] == "assess" and o["seed"] == (seed if a in ("candidate", "deterministic_random") else 0)]
            led.append("assessment", {"experiment_id": EID, "arm": a, "seed": seed, "units": len(rs), "passes": sum(o["passed"] for o in rs),
                                      "outcomes_digest": br.outcome_digest([{"arm": a, "seed": seed, "task_id": o["task_id"], "passed": o["passed"]} for o in rs])})
    body = dict(verdict, **extra, experiment_id=EID, prereg_digest=lock["prereg_digest"], rates_micro=rates,
                contrasts={str(s): c for s, c in zip(pr["seeds"], per_seed)},
                adapters={k: {"adapter_digest": v["adapter_digest"], "examples": v["examples"]} for k, v in meta.items()})
    led.append("verdict", body)
    jdump(os.path.join(REPO, "ledgers", "exp-%s-bugrepair.result.json" % N), body)
    print(json.dumps(body, indent=1, sort_keys=True))


if __name__ == "__main__":
    {"lock": stage_lock, "export": stage_export, "ingest": stage_ingest, "gen": stage_gen, "train": stage_train, "import": stage_import, "rexport": stage_rexport, "ringest": stage_ringest, "eval": stage_eval, "report": stage_report}[sys.argv[1]]()

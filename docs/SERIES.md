# Bug-repair distillation series (exp-008 to exp-019)

State slice `caduceus-phase1-substrate-v1`, claim ceiling `LocalDevelopmentEvidenceOnly`.
This is the bug-repair series run through `scripts/run_exp008.py`. A separate SWE-bench
series reuses some experiment numbers; its ids differ (see `docs/CLAIMS.md`).

## Question

Does training an adapter on oracle-verified repairs beat training on an equal amount of
unverified attempts (the pool-matched control), the untouched base, and a few-shot policy,
on sealed bugs from a held-out set of modules? The oracle is the repo's own tests; no model
labels anything. Splits are by module, so fit data never shares a module with assess.

## Result in one paragraph

**Verification helps when a stronger model proposes the repairs.** With a Qwen2.5-Coder-32B
proposer and a Qwen-family 7B trainee, the verified adapter beat the matched unverified
adapter by 8 to 34 points on 5 of 5 preregistered tests (exp-011, 012, 015, 018, 019), on two
synthetic bug families. **Self-proposed recursion did not work** (exp-013 Inconclusive,
exp-014 Fail). Two weaker or unfamiliar trainees were floor-limited and tell us nothing
(exp-016, 017). No claim of self-improvement, compounding, cross-family generality, or
real-bug performance is made.

## Experiments

| id | what changed | verified | pool-matched unverified | base | verdict |
|---|---|---|---|---|---|
| 008, 010 (series 1) | aggressive recipe (lr 1e-4, scale 20), 7B base proposer / 32B | 0 to 5.8% | 0 to 1.9% | 11% | Fail (recipe damaged every adapter); 009 NotRun |
| 011 | gentle recipe (lr 1e-5, scale 4), 32B proposer, Qwen2.5-7B | 42.0 to 45.7% | 27.5 to 32.6% | 9.4% | **Pass** |
| 012 | replication, fresh split | 48.8 to 53.5% | 26.7 to 36.0% | 9.3% | **Pass** |
| 013 | proposer = admitted exp-012 adapter (7B) | 35.6 to 45.2% | 26.0 to 32.9% | 12.3% | Inconclusive |
| 014 | repeat of 013, larger split (109) | 31.2 to 34.9% | 31.2 to 32.1% | 8.3% | **Fail** (child 20 points below its parent) |
| 015 | 32B proposer, 189 assess bugs | 34.9 to 40.7% | 23.8 to 26.5% | 6.9% | **Pass** |
| 016 | trainee Qwen2.5-1.5B | 3.2 to 5.8% | 2.1 to 4.2% | 0.0% | Inconclusive (floor) |
| 017 | trainee Llama-3.1-8B | 5.8 to 7.4% | 1.6 to 3.7% | 0.0% | Inconclusive (floor) |
| 018 | trainee Qwen2.5-Coder-7B | 58.7 to 59.3% | 24.9 to 43.4% | 6.9% | **Pass**, 3 of 3 seeds |
| 019 | structural bug family, Coder-7B | 34.4 to 36.5% | 22.9 to 24.0% | 6.2% | **Pass** |

Decision rule (fixed before every run, never tuned): Pass needs, in at least 2 of 3
seeds, candidate minus each other arm with paired-bootstrap lower bound at least 50,000
micro and mean at least 100,000 micro, with tune-split retention holding. Fail if any
contrast mean is negative or retention breaks. Otherwise Inconclusive.

## What the evidence supports

1. A verified-data adapter beats an unverified one trained on the same volume, when the
   proposer is stronger than the trainee, for Qwen-family trainees, on two synthetic bug
   families (syntactic and structural).
2. Much of the gain over the base is task and format learning: the unverified adapter also
   gains about 15 to 25 points. Verification adds roughly 10 to 30 points on top.
3. Self-proposed data (exp-013, 014) did not show the verification effect, and the round-2
   adapter scored far below its parent. The exp-011/012 gains are best read as
   distillation from the 32B.

## What it does not support

- Self-improvement or compounding across rounds.
- Cross-family trainee generality: Llama and the 1.5B model started at 0%, so those tests had no power.
  Coder-7B shares a lineage with the proposer.
- Real bugs. Every task is an injected mutant. Mining this repo's own history found no
  usable set: of 68 commits, only feature commits changed modules with tests, and the 9
  (commit, module) pairs whose parent version turns the commit's tests red are new
  features, not repairs. A real-bug test needs an external corpus of fix commits with
  regression tests (for example SWE-bench-style repositories), which is a separate
  experiment with its own oracle audit.
  Feasibility check on the one real-bug corpus available locally (SWE-bench Lite, Docker
  oracle): 111 of 300 instances have a local image (django 58, sympy 17, scikit-learn 8,
  pytest 7, sphinx 7, others 14). Gold patches resolve through the harness (3 of 3, 46 s
  for three in parallel), so the oracle works. The experiment is still underpowered by
  construction: a module-disjoint split leaves about 58 django instances for fit and
  about 53 others for assess, so even if the 32B solved a quarter of the fit instances the
  training set would be on the order of 15 repairs, far below the 100-example floor used
  here, and a 53-task assess set cannot resolve a 10-point effect. Lite is also public,
  so the 32B proposer may have seen the fixes. This is an estimate, not a run; no
  experiment was preregistered. A powered real-bug test needs hundreds of fix commits
  with regression tests from repositories outside the proposer's likely training data.
- Other repositories. One repo, stdlib Python only.

## Process notes

- Lock before any model run: every experiment appends a `prediction_lock` to its own
  ledger (`ledgers/exp-0NN-bugrepair.jsonl`) after the prereg, manifest digest and split
  commitments are committed; assess tasks are regenerated at eval time, never cached on disk.
- Post-lock changes are ledger `audit_note`s. Examples: the dedupe key fix (task, repaired
  digest) that superseded an exp-011 NotRun, and manifest regeneration through the runner's
  per-split path when a load-sensitive broker mutant made lock fail.
- Admission: the exp-012 result passes through `caduceus/bugrepair_bridge.py` into Exchange
  lanes. Seven lanes are Checked by evidence; the rest were waived by the repo owner as
  ledger audit_notes, not evidence. The verdict grants no authority.
- Cost: the 32B proposer ran on a rented H100 (gman) for four experiments; all training
  and evaluation ran locally with MLX LoRA (rank 8, 16 layers, steps = 3 x examples).

## Reproduce

```sh
EXP=0NN PYTHONPATH=$PWD:<hsi-lab>/src <hsi-lab venv python> scripts/run_exp008.py lock|export|gen|ingest|train|eval|report
```

Manifests: `tasks/bugtasks-v1..v8.manifest.json`. Preregs: `prereg/exp-0NN-*.json`.
Results: `ledgers/exp-0NN-bugrepair.result.json`. Per-experiment detail: `docs/PRD.md`
(phase 6c) and `docs/CLAIMS.md`.

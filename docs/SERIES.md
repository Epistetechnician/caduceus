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

## Real-bug attempt (exp-020: NotRun)

Mined 384 verified fix commits from 24 outside Python libraries (`scripts/mine_realbugs.py`:
tests green on the fix tree, red with only the source file reverted, run in network-less
Docker). Split by repository (fit 191 / tune 36 / assess 157). The 32B proposer, shown only
the buggy file and the test file names, solved 6 of 191 fit tasks (20 of 2,292 attempts), so
the training floor (60) was not met: **NotRun**, no training, no assess run. Real fixes need
the intended behaviour stated, which the injected-mutant prompts never needed. A new
candidate (exp-021) adds a specification to the prompt; exp-020 is closed.

### exp-021: real changes with a specification in the prompt (Inconclusive)

Same mined set (realbugs-v1), prompt now carries the commit subject and the commit's test
diff. The 32B verified 346 of 2,220 attempts (89 distinct repairs on 47 of 185 tasks), above
the floor of 60. On 157 sealed assess tasks from held-out libraries: verified adapter 7.0 to
7.6%, pool-matched unverified 3.2 to 7.0%, base 3.2%, few-shot 3.2%. Candidate minus pool
-0.0 to +4.4 points, lower bounds -4.5 to +1.2. Rule met in 0 of 3 seeds: **Inconclusive**.
The direction is positive in 2 of 3 seeds, but everything is near floor (a 7B trainee solves
3 to 8% of real changes), so the test cannot resolve a 10-point effect. Real-change results
are not a pass and not a fail: the effect is not shown to carry over to real code changes,
and it is not shown to be absent.

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
- Cost: the 32B proposer ran on a rented H100 (gman) for four experiments; exp-008..021 training and evaluation ran locally with MLX LoRA; exp-022 trained and evaluated on the H100 (scripts/train_remote.py, eval_remote.py) (rank 8, 16 layers, steps = 3 x examples).

### exp-022: real changes, 32B self-trained (Fail)

Same 157 held-out real-change tasks and spec prompt as exp-021, but the trainee is
Qwen2.5-Coder-32B (bf16, PEFT LoRA on the H100, vLLM greedy eval). Training data: the 346
exp-021 verified attempts (89 examples after the 3-per-task cap), so this is self-training.

| Arm | Repair rate |
|---|---|
| Untouched base | 15.9% |
| Few-shot policy | 16.6% |
| Verified adapter (3 seeds) | 17.2 / 18.5 / 17.8% |
| Unverified pool adapter (3 seeds) | 17.8 / 16.6 / 15.3% |

Verdict **Fail** by the preregistered rule (a contrast mean was negative: seed 1 verified
was 0.6 pt below pool). No seed met the Pass rule. The base is now well above floor, so
this is not a floor artifact: verified self-training moved the rate +1 to +3 pts over base
and 0 to +3 over the pool, all inside noise (n=157). Retention held. Together with
exp-014, self-proposed verified data is not shown to help; it helps only when a stronger
proposer supplies the repairs (exp-015, exp-018).

### exp-023 / exp-024: scaled real-change corpus (NotRun, then Fail at floor)

Mined 36 more libraries on a GCP CPU VM (`scripts/mine_gcp.sh`): 704 tasks over 60
libraries (fit 353 / tune 179 / assess 172, fresh split seed). The 32B proposer solved
816 of 4,140 attempts. exp-023 (local MLX 7B training) was killed by memory twice before
any adapter existed and is closed NotRun. exp-024 reran the same data and rule with the 7B
trained and evaluated on the H100 (PEFT bf16 + vLLM), new seeds.

| Arm | Repair rate (assess, n=172) |
|---|---|
| Untouched base | 2.9% |
| Few-shot policy | 4.1% |
| Verified adapter (3 seeds) | 4.1 / 3.5 / 4.1% |
| Unverified pool adapter (3 seeds) | 1.7 / 1.2 / 2.3% |

Verdict **Fail** by the rule (the verified-minus-few-shot mean was negative in one seed).
The reading is floor-limited, not a clean negative: the 7B solves 3-4% of real changes,
so 5-7 tasks decide each contrast. Verified beat the unverified adapter by 1.7-2.3 pts with
lower bounds ~0, the right direction but not resolvable. Retention held. Real-change
distillation to a 7B is not shown and not ruled out; the 7B is the limit, not the data.

### exp-025: real changes, 14B trainee (Fail)

Same corpus, spec prompt, 32B-proposer data (816 verified of 4,140) and rule as exp-024;
only the trainee changes to Qwen2.5-Coder-14B (bf16, PEFT on the H100). 172 assess tasks.

| Arm | Repair rate |
|---|---|
| Untouched base | 8.7% |
| Few-shot policy | 6.4% |
| Verified adapter (3 seeds) | 7.6 / 7.0 / 7.6% |
| Unverified pool adapter (3 seeds) | 7.0 / 6.4 / 7.0% |

Verdict **Fail**. Verified beat unverified by 0.6 pt (one task) in every seed, within noise,
and both adapters scored 1-2 pts *below* the untouched base. So on real changes, fine-tuning
on the 32B's verified repairs did not help a 14B trainee, verified or not. The base is only
8.7%, still low, so absolute headroom is small; but this is a clean null, not a floor
artifact like exp-021/024. Taken with exp-015/018/019, verified distillation helps on injected
bugs and is not shown on real single-file changes.

### exp-026: fresh-commit eval (calibration, not a hypothesis test)

232 real fix commits dated 2025-07-01 or later from 26 libraries (after the open models'
training cutoffs), each scored by its own tests in a network-less container, spec in the
prompt, greedy. Manifest `tasks/fresh-v1.manifest.json`, locked in `ledgers/exp-026-fresh.jsonl`
before any model ran.

| Model (base, zero-shot) | Pass |
|---|---|
| Qwen2.5-Coder-32B | 45 / 232 (19.4%) |
| Qwen2.5-Coder-14B | 42 / 232 (18.1%) |
| Qwen2.5-Coder-7B | 13 / 232 (5.6%) |

The set separates the 7B from the larger two. It does not separate 32B from 14B (3 tasks,
inside noise). The 32B scored 19.4% here versus ~16% on the older assess split, so there is
no sign that the earlier numbers were inflated by exposure. Caveats: 26 libraries, uneven
(mistune, validators, dnspython and packaging dominate); 228 of the 232 commits also sit in
the realbugs-v2 corpus, so this set must not be used to evaluate models trained on that corpus.

Agent arm (same 232 tasks): Claude Code CLI with Haiku 5.5, run on a GCP VM, 30-turn limit, tests
runnable via a sandboxed `./runtests.sh`, scored by the commit's own tests restored from the fix
commit. Result **231 / 232 (99.6%)**, about $68 of usage (avg 8.2 turns, 7 tasks edited files beyond
the target). So this set does not discriminate agents: it saturates the moment a model can run the
tests and iterate, while it still separates bare single-shot models (5.6 / 18.1 / 19.4%). The gap
is feedback, not capability alone. Caveat: the VM had outbound network (needed for the API), so an
agent could in principle fetch a fixed library release; commands were not logged, so this is
unchecked. A discriminating agent benchmark needs harder tasks, no test visibility, and a network
allowlist.

### exp-027: hidden-test agent benchmark (redesign of the exp-026 agent arm)

Fixes for the saturation above: a harder subset (72 tasks, 19 libraries: fix touches >=2 hunks and
>=10 lines, <=8 per library), hidden tests (the agent sees only a Haiku-written issue statement
generated from commit message + test diff, never the fix source), and a network allowlist (the
agent runs as an unprivileged user; iptables rejects all egress except a proxy that allows only
anthropic.com/claude.ai; denied attempts are logged). Prereg and a lock with the statements digest
were committed before any agent ran. Agent: Claude Code CLI, Haiku 5.5, `--effort low`, 30 turns.

Result: **61 / 72 (84.7%)**, 95% interval about 74-92%, $27 of usage, 12.4 turns on average, 1 error.
The set is no longer saturated (the exp-026 agent arm was 99.6% with tests visible), so hiding the
tests and shrinking to harder tasks cost the agent about 15 points. Whether the set discriminates
between agents is **not tested**: that needs a second agent (Sonnet or higher effort) and I ran one
agent to conserve usage. The proxy denied a handful of connections (npm registry, PyPI, GitHub raw);
a scan of the transcripts found no pip/curl/wget/git-fetch commands, so the denials most likely
came from the CLI's own checks, but this is a regex scan, not proof. Statements were not audited
for leakage beyond a spot check (code blocks in statements are usage examples).

### exp-028: admission-gate A/B (does verify-before-accept help a cheap agent?)

Question: if a harness admits an agent's patch only when a deterministic gate passes, does the agent resolve more hidden-test tasks?
Gate (no model): the agent must add a regression test that fails on the parent source (G2), passes on the patched tree (G3), and the
existing suite must not regress (G4, NotRun when the parent suite was not green within 180 s); a rejection resumes the same session with the
verdict, at most 2 retries. Same 72 tasks, statements and agent as exp-027 (Haiku 5.5 low, 30 turns). Prereg and lock were committed before the run.

Result: **Fail.** Attempt 1 (control) 61/72, final (treatment) 61/72, paired lift 0.0, no task changed outcome. 64 of 72 first attempts were admitted;
8 were rejected (7 at G2, 1 at G1) and retried, and every retry was admitted, but none of the 8 flipped a hidden-test result. 11 of the 72 admitted
patches fail the hidden tests (false admits) and 7 of the 8 rejected first attempts would have passed (false rejects). G4 was NotRun on 39 of 72 tasks.
Cost $30. Reading: when the same agent writes both the test and the fix, the test encodes the agent's own reading of the issue, so a green gate
certifies consistency, not correctness; it filtered nothing useful here. The candidate identity is closed per the prereg; a different gate (for example
tests authored independently of the fixer) would be a new candidate with a new prereg.

### exp-029: independent-tester gate (the exp-028 fix)

exp-028 failed because the fixer wrote its own test. Here a separate tester session sees only the issue statement and the parent tree (never the
fixer's tree) and writes a pytest file that must fail on the parent (valid for 72/72 tasks). The fixer is gated on those tests plus the existing suite,
with up to 2 retries and only the pytest output tail as feedback. Same 72 tasks, statements and agent. Prereg and lock committed first.

Result: **Fail.** Attempt 1 61/72, final 61/72, paired lift 0.0 (bootstrap interval about -4 to +4 points). 7 first attempts were rejected and retried;
one flipped to a pass and one to a fail. 11 admitted patches fail the hidden tests; 5 of the 7 rejected first attempts would have passed. Three runs
of the same agent (exp-027, exp-028 attempt 1, exp-029 attempt 1) all scored exactly 61/72, but not on the identical set of tasks, so the ceiling
looks like a property of the statements and the agent, not of verification. Reading: the tester and fixer are the same model reading the same statement,
so they share misreadings; execution checks catch implementation slips but not a wrong reading of the spec. Cost figures in this run's results are on a
different accounting basis from exp-028 (cost_usd 1.37 vs 30.26 for similar turn counts) and should not be compared; turns were 648 vs 981.

## Reproduce

```sh
EXP=0NN PYTHONPATH=$PWD:<hsi-lab>/src <hsi-lab venv python> scripts/run_exp008.py lock|export|gen|ingest|train|eval|report
```

Manifests: `tasks/bugtasks-v1..v8.manifest.json`. Preregs: `prereg/exp-0NN-*.json`.
Results: `ledgers/exp-0NN-bugrepair.result.json`. Per-experiment detail: `docs/PRD.md`
(phase 6c) and `docs/CLAIMS.md`.

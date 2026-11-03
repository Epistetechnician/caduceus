# Claim boundary

Ceiling: **LocalDevelopmentEvidenceOnly** (Level 1 or below). Every candidate
must carry that nonclaim.

What this repository does establish (by tests in `tests/`):
* canonical encoding, digest chain and RFC 9162 Merkle proofs behave as specified
  (inclusion proofs checked exhaustively for tree sizes 1-33, consistency proofs for all (m, n) pairs with n up to 39) and two independent code paths agree on
  500 random documents and on every ledger the tests produce;
* the admission core is deterministic and fails closed; no lane failure is
  masked; unready lanes quarantine;
* a visible-probe-equivalent replacement is caught by held-out and perturbation
  probes in the synthetic skill set;
* single-use capabilities, snapshot/rollback, and apply-time retention
  revalidation work on a sandbox directory;
* the plugin's decision function is correct for the verified `skill_manage`
  call shape;
* in a throw-away home with a stub model, Hermes' background-review fork
  (Hermes commit `b20cc5f787`) sends `skill_manage` through the plugin hook:
  observe records it, enforce blocks it (`scripts/probe_background_review.py`);
* the executor isolates a skill copy and reaches a model only in the
  treatment arm (stub model, `scripts/smoke_executor.py`);
* a switch-latency baseline for one machine, one Hermes commit (dirty tree)
  and one synthetic workload (`baselines/`). It is a reference point, not a
  benchmark;
* every one of 43 seeded attacks in `corpus.py` is refused, and the gate gives
  the same verdict digest on replay;
* on a sandbox copy of a real skill, the broker process applies only with a
  ledgered capability bound to the exact verdict, refuses replay, path escape,
  forged tokens/verdicts, symlinks, oversize writes and frozen state, and
  restores the exact pre-apply tree on a canary abort (`caduceus drill`); on
  macOS its sandbox profile blocks writes outside root/store/ledger and all
  network access (tested);
* the release state machine refuses stale evidence, budget slow-drain,
  promotion before the canary window, promotion after cancel, and silent
  breaker re-arm (tests + corpus);
* with the plugin loaded (observe mode), Hermes' pre-tool dispatch costs
  +0.11 ms p99 per non-gated tool call (budget 2.84 ms) and +0.29 ms p99 per
  gated `skill_manage` call, independent of ledger size (O(1) locked append;
  it was ~31 ms at 2,000 entries before). The hot path checks only the last
  ledger line; full-chain verification is `verify-ledger` / `diagnostics`;
* against real Hermes memory injection (stub model), 4/4 freshness cases show
  no silent stale injection at the session boundary and the seeded stale
  injector is caught;
* exp-002 (Claude Haiku 5.5, three arms, 200 units, 0 excluded) passed its
  preregistered rule against both controls: vs no skill +0.245 [+0.11, +0.39];
  vs a wrong-source skill +0.24 [+0.095, +0.39];
* exp-003 (Claude Sonnet 5.5, contract v2 with harder probes for the two
  ceilinged skills, three arms, 200 units, 0 excluded) passed against both
  controls: vs no skill +0.305 [+0.17, +0.445]; vs wrong-source +0.27
  [+0.13, +0.415]. v2 made systematic-debugging discriminate (36 -> 50 of 50);
* the Exchange computes all 13 lanes from artifacts and decides: on fixtures an
  improving candidate is accepted and broker-applied, a regressing one rejected,
  and evidence from another experiment cannot be attached; candidates bind
  their exact contents (`content_digest`) and the broker refuses any other
  content;
* exp-004, the first real candidate (a refinement of requesting-code-review,
  Haiku 5.5), **failed** its preregistered rule: +0.08 [0, +0.24] vs the
  current skill (base was 46/50), though +0.30 vs a wrong-source skill. The
  candidate identity is closed. The proposer also wrote that contract's
  held-out probes, so it was contaminated and declared as such in its lock.
  The Exchange rejected it on exactly one lane, `controls`; the other 12 were
  Checked (`ledgers/governance.jsonl`). Second instrument: a Claude Sonnet 5.5
  judge with rubrics agreed with the regex judge on 136/150 outputs (90.7%,
  threshold 90%); 13 of the 14 disagreements were on the wrong-source arm.
* exp-005, the first clean candidate, ran on CL-bench (external benchmark,
  single-turn tasks <= 15k chars, all-or-nothing rubric grading). Hermes on
  Haiku 5.5 proposed `context-learning` 1.1.0 from 18 failures on a 20-task
  FIT split; the 60-task assessment split was sealed by digest before the
  proposer ran and never shown to it. Result on Haiku 5.5: base 5/60, random
  skill 4/60, candidate 3/60; -0.033 [-0.117, +0.05] vs base. **Fail**,
  closed. Judges: Sonnet 5.5 primary, Haiku 5.5 second, 169/180 agreement.
  The Exchange rejected it on `controls`, `indispensability` and
  `preregistration`. The last is a real gap: the bench design has no
  `fixed_policy` arm.
* exp-006 (adds the `fixed_policy` arm, Sonnet proposer, 40 fit / 80
  assessment) was **NotRun**: 21/80 tasks lost to executor errors (limit 10%).
* exp-007, same design with retried and logged executor calls: no call
  errors, 0 excluded. Haiku 5.5 solved base 10/80, fixed policy 10/80,
  random skill 10/80, candidate 9/80; -0.0125 [-0.0625, +0.0375] vs base.
  **Fail**, closed. Judges agreed on 288/320. The Exchange rejected it on
  `controls` and `indispensability` only; the other 11 lanes, including
  `preregistration`, were Checked.
* exp-008, restricted to Procedural Task Execution and Rule System
  Application: base 11/80, fixed policy 11/80, random skill 10/80, candidate
  9/80; -0.025 [-0.0875, +0.0375] vs base. **Fail**, closed; Exchange
  rejected on `controls` and `indispensability`. Judges agreed on 290/320.
* exp-008-bugrepair-round1 (injected-bug repair on this repo, Qwen2.5-7B 4-bit, 36 sealed assess bugs, three seeds): base 4/36, fixed policy 2/36, verified-data adapter 0-2/36, pool-matched adapter 0/36. **Fail**, closed (candidate mean below base, retention broke). Training set was 8 verified repairs, so this is a null on a tiny-data regime, not evidence about verified recursion at scale.
* exp-010-bugrepair-round3 (same task set, 34 verified repairs from a 32B proposer, 52 sealed assess bugs): base 6/52, verified adapter 0-3/52. **Fail**, closed. Series total: three rounds, no positive result.
* exp-011-gentle-round1 (series 2, injected-bug repair, 138 sealed assess bugs, three seeds): verified-data adapter 42-46%, pool-matched unverified adapter 28-33%, untouched base 9.4%, few-shot policy 8.0%. **Pass** on the preregistered rule (2 of 3 seeds, retention held). Verified data beat matched unverified data by 10-18 points. Scope: Qwen2.5-7B 4-bit trained on repairs written by a 32B model, one repo, syntactic mutants; series 1 (three rounds) did not pass. Not a claim of self-improvement, transfer, or compounding.
* exp-012-gentle-replication (series 2 replication, fresh split seed, 86 sealed assess bugs, three seeds, same recipe): verified-data adapter 48.8-53.5%, pool-matched unverified adapter 26.7-36.0%, untouched base 9.3%, few-shot policy 8.1%. **Pass** on the preregistered rule (2 of 3 seeds, retention held); verified beat matched unverified by 13-27 points. Same scope as exp-011: distillation from a 32B proposer into a 7B, one repo, syntactic mutants, much of the gain over base is task/format learning. Not a claim of self-improvement, transfer, or compounding.
* exp-012 admission: with `bugrepair_bridge`, seven lanes are Checked by evidence; independent_verification, behavior_equivalence, instrument_validity, lyre_report, search_cost and signature were waived by the repo owner (ledger audit_notes). The waivers are not evidence, add no claim, and the resulting accept grants no authority; the ceiling stays `LocalDevelopmentEvidenceOnly`.
* exp-013-recursion-round2 (round 2, proposer = admitted exp-012 adapter, 73 sealed assess bugs, three seeds): verified-data adapter 35.6-45.2%, pool-matched unverified 26.0-32.9%, untouched base 12.3%, few-shot policy 8.2%. **Inconclusive** on the preregistered rule (0 of 3 seeds; verified-over-pool lower bounds 0 to +4.1 points against a 5-point floor). No claim that the loop compounds or that self-proposed verified data beats matched unverified data. Candidate identity closed; a retry needs a new id, seed and data.
* exp-014-recursion-round2b (round 2 repeat, larger split, 109 sealed assess bugs, three seeds, parent arm reported): verified adapter 31.2-34.9%, pool-matched unverified 31.2-32.1%, base 8.3%, few-shot 3.7%, parent exp-012 adapter 53.2%. **Fail**: verified did not beat matched unverified data and the child scored below its parent by about 20 points. Withdraws any suggestion that self-proposed verified data compounds; the exp-011/012 Pass claims stand only as distillation from a 32B proposer.
* exp-015-distill-scale (32B proposer, fresh split, 189 sealed assess bugs, three seeds): verified adapter 34.9-40.7%, pool-matched unverified 23.8-26.5%, base 6.9%, few-shot 5.3%. **Pass** (2 of 3 seeds, retention held); verified beat matched unverified by 8-16 points. Third Pass of the distillation effect (exp-011, 012, 015). Scope: distillation from a stronger proposer into a 7B, one repo, syntactic mutants. The parent arm (62.4%) is not leakage-controlled on this split and is not a baseline claim. No self-improvement or compounding claim: exp-013 Inconclusive, exp-014 Fail.
* exp-016-second-trainee (Qwen2.5-1.5B 4-bit trainee, same 189 sealed assess bugs and verified data as exp-015): verified 3.2-5.8%, pool-matched unverified 2.1-4.2%, base 0.0%, few-shot 1.1%. **Inconclusive** (0 of 3 seeds). Floor effect: the small trainee barely solves the task. The distillation effect is claimed for the 7B trainee only; trainee generality is not shown.
* exp-017-llama-trainee (Llama-3.1-8B 4-bit trainee, same 189 sealed assess bugs and verified data): verified 5.8-7.4%, pool-matched unverified 1.6-3.7%, base 0.0%, few-shot 0.0%. **Inconclusive** (0 of 3 seeds): same direction as the Qwen 7B result but at floor. Cross-trainee and cross-family generality are not shown; the distillation-with-verification claim stays scoped to Qwen2.5-7B.
* exp-018-coder-trainee (Qwen2.5-Coder-7B 4-bit trainee, same 189 sealed assess bugs and verified data): verified 58.7-59.3%, pool-matched unverified 24.9-43.4%, base 6.9%, few-shot 5.8%. **Pass** (3 of 3 seeds, retention held); verified beat matched unverified by 15-34 points. Second trainee confirming the distillation-with-verification effect (with exp-011/012/015 on Qwen2.5-7B). Scope: same Coder lineage as the proposer, so not cross-family; floor-limited trainees (exp-016/017) are Inconclusive; one repo, syntactic mutants, assess split reused across trainees. No self-improvement claim.
* exp-019-structural-bugs (new structural mutant family, 96 sealed assess bugs, Coder-7B trainee, 32B proposer, three seeds): verified 34.4-36.5%, pool-matched unverified 22.9-24.0%, base 6.2%, few-shot 15.6%. **Pass** (2 of 3 seeds, retention held); verified beat matched unverified by 10-14 points. The distillation-with-verification effect holds on a second synthetic bug family. Scope: still injected bugs, one repo, Coder-lineage trainee; bugs mined from real history are untested.
* Real-bug mining (negative finding, no experiment run): this repository's 68-commit history has no bug-fix commits with regression tests; the 9 (commit, module) pairs whose parent turns the commit's tests red are feature additions. A real-bug test needs an external corpus. The only local real-bug corpus (SWE-bench Lite, 111 instances with Docker images) is too small for a powered distillation test and likely contaminated; see `docs/SERIES.md`.
* exp-020-real-bugs (384 fix commits mined from 24 outside libraries, 32B proposer shown only the buggy file and test names): 6 of 191 fit tasks solved, 20 verified attempts, below the training floor of 60: **NotRun**. No claim on real bugs. See `docs/SERIES.md`.
* exp-021-real-bugs-spec (same mined real-change set with commit subject and test diff in the prompt; 157 sealed assess tasks from held-out libraries, Coder-7B trainee, 32B proposer): verified 7.0-7.6%, pool-matched unverified 3.2-7.0%, base 3.2%, few-shot 3.2%. **Inconclusive** (0 of 3 seeds, near floor). No claim that the verification effect carries over to real code changes; none that it does not.
* exp-009, first SWE-bench Lite candidate (oracle retrieval, Haiku 5.5, no
  tools, graded by the benchmark's own tests; Sonnet-proposed patch-writing
  1.1.0 from 30 fit tasks where the base resolved 14): **NotRun**, 34/80
  tasks excluded. Cause: my driver mapped "patch did not apply" (reported as
  a harness error) to infra failure instead of a 0, and the locked rule
  excludes harness errors. The verdict stands under the locked rule; the
  Exchange quarantined it (controls, indispensability NotRun). Not a
  verdict, but descriptively, scoring non-applying patches as 0 gives
  base 44/80, fixed 45, random 43, candidate 45: no visible effect.
  The two harness runs agreed on 272/274 graded patches. Driver fixed for
  any next experiment.
* exp-010 (agentic SWE-bench: Hermes terminal + file tools inside each
  task's image, no network) was closed **NotRun**: Hermes' persistent Docker
  mode shares one container per profile, so concurrent agents edited each
  other's repositories. Fixed with one non-persistent sandbox per call,
  verified on two concurrent tasks.
* exp-011, agentic, clean (0 call errors, 0 excluded; base resolved 13/20 on
  fit): Haiku 5.5 resolved base 42/60, fixed policy 44, random skill 44,
  candidate 43; +0.017 [0, +0.05] vs base, -0.017 vs fixed and random.
  **Fail**, closed. The two harness runs agreed on 239/240. The Exchange
  rejected it on `controls` and `indispensability`.
* exp-012-swe-agentic-positive-control (SWE-bench series; distinct from the
  bug-repair exp-012 above), method sensitivity check, never counts toward
  auto-apply. 57/60 tasks graded (3 excluded), harness runs agreed 226/228.
  Resolved: base 44, fixed policy 42, hand-written repo-aware positive
  control 42, **sabotage skill ("never change code") 41**. Positive control
  **Fail** (-0.035 [-0.105, +0.035] vs base). Sensitivity check **Fail**:
  base minus sabotage +0.053 [0, +0.123], lower bound not above 0. Finding:
  in this executor the preloaded skill barely steers the agent; even an
  instruction to never edit code left resolves almost unchanged. The skill
  nulls in exp-005..011 therefore say little about skill quality; the next
  step is to fix how the skill reaches the agent, then re-run the controls.
* Across four clean CL-bench attempts and the clean SWE-bench one (exp-011), no skill text (learned, hand-written
  or unrelated) moved Haiku's solve rate: the skill is not the bottleneck on
  this benchmark.
* Phase 4 auto-apply therefore stays Blocked: 0 passing candidate experiments.

* `adapter_update` candidates: `hsi_bridge.py` recomputes hsi-lab receipts with
  their standalone validator and maps them to seven lanes; with hsi evidence alone
  the verdict is quarantine, never accept (11 tests, incl. the real E6 verified and
  pool-control receipts). This shows the plumbing, not that verified recursion works.

* the bug-repair oracle (`bugtasks.py`): mutants are generated deterministically,
  only those killed by covering tests within 60 s become tasks, a repair passes
  only if the covering tests are green and nothing else changed, and the proposer
  view hides the mutation and the original. n=33 assess tasks is small; nothing
  here says any model can repair them.

What it does **not** establish:
* anything about real Hermes skills' quality, or that gating improves Hermes;
* that the review fork is gated under a real model or a live Hermes session;
* that proposed refinements improve skills: the only candidate experiment
  failed, and Phase 4 stays **Blocked** (it counts candidate experiments
  only, not skill-vs-no-skill effects);
* that skills improve Hermes in general. exp-001 (Claude Haiku 5.5, 200 paired
  units, 0 excluded) passed its preregistered rule: mean +0.245, 95% CI
  [+0.12, +0.38]. The effect is almost all `codebase-inspection` (10 -> 50 of
  50); `requesting-code-review` 37 -> 48; `systematic-debugging` 50 -> 50 and
  `test-driven-development` 49 -> 47 sit at ceiling, so their probes do not
  discriminate. exp-002 reproduced the pattern with a random-control arm
  (codebase-inspection 10 / random 10 / skill 50 of 50). Both runs use one
  model and the same contracts; the pooled effect rests mainly on one skill;
* that review signatures identify a person (HMAC; amendment 8), that the broker
  is kernel-sandboxed outside macOS (amendment 11), or that any of this has run
  in enforce mode inside a live Hermes;
* protection against a hostile process running as the same user, or against an
  attacker who can rewrite both content and digest (plaintext, no signatures);
* independent verification, independent replication, production readiness,
  security, SOTA, or any benchmark result. The witness is an unsigned local
  consistency monitor, not a transparency log;
* statistical power for any real experiment: bootstrap helpers exist, no
  preregistered real-world test has run;
* equivalence between the two canonical encoders on arbitrary input (only
  differential tests); a Lean/formal lane is not implemented.
* that verified-data recursion compounds, that data proximity improves the learning ratio, or that hsi-lab's synthetic-world PROMOTE (single seed, verified arm below real-only, held-out-rule-types guardrail failed) transfers to real tasks.
* exp-022 (Fail, LocalDevelopmentEvidenceOnly): Coder-32B self-trained on its own 89 verified real-change repairs did not beat a pool-matched unverified adapter on 157 held-out real changes (17.2-18.5% verified vs 15.3-17.8% pool, base 15.9%). No claim that verification helps self-training on real code.
* exp-024 (Fail, LocalDevelopmentEvidenceOnly; exp-023 NotRun): on 172 held-out real changes a 7B trained on 203 verified 32B repairs scored 3.5-4.1% vs 1.2-2.3% for a pool-matched unverified adapter and 2.9% base. Floor-limited; no claim that verified distillation helps on real code.
* exp-025 (Fail, LocalDevelopmentEvidenceOnly): on 172 held-out real changes a 14B trained on 203 verified 32B repairs scored 7.0-7.6% vs 6.4-7.0% for a pool-matched unverified adapter and 8.7% base. No claim that verified distillation helps on real code; fine-tuning on this data did not beat the untouched base.
* exp-026 (calibration, LocalDevelopmentEvidenceOnly): on 232 real fix commits dated 2025-07 or later, Qwen2.5-Coder 32B/14B/7B base score 19.4/18.1/5.6%. Shows the set separates 7B from larger models only; no claim about any agent or model beyond these three, and the set overlaps the realbugs-v2 training corpus.
* exp-026 agent arm (LocalDevelopmentEvidenceOnly): Claude Code with Haiku 5.5 solved 231/232 fresh fix commits when it could see and run the commit's tests; the set is saturated for tool-using agents. No claim of agent skill beyond that; network was not restricted and commands were not logged.
* exp-027 (LocalDevelopmentEvidenceOnly): Claude Code + Haiku 5.5 (low effort) solves 61/72 (84.7%) of fresh hidden-test tasks under a network allowlist; the set is non-saturated for this agent. No claim that it discriminates between agents (second agent not run), and statement leakage was only spot-checked.

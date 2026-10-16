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
* Phase 4 auto-apply therefore stays Blocked: 0 passing candidate experiments.

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

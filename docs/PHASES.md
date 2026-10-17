# Phase status (against PRD v2.1 section 12)

| Phase | Scope | Status |
|---|---|---|
| 0 Foundation | digest-chained ledger, Merkle proofs, witness, independent readback, read-only Hermes audit, freshness checker, diagnostics | **Implemented, tested.** `caduceus diagnostics` reports the lane vector. Freshness checked against real Hermes memory injection (`baselines/freshness-*.json`) |
| 1 Skill substrate | contracts + probe kinds, identity by tree digest, behavior/composition/indispensability controls, protected suite + retention gate, switch fast path | **Implemented.** Contracts for 4 real skills; real executor; switch baseline pinned. Fast path deliberately not built (amendments 2, 12) |
| 2 Admission + Refinery (propose-only) | typed candidates, deterministic gate, lanes, retention, random control, sealed holdout, adversarial corpus, wrap self-evolution output | **Implemented.** Corpus: 43/43 attacks refused, gate deterministic. Strict intake; source admission; generic proposal wrapper (amendment 9); three-arm random-control design |
| 3 Supervised apply | broker process, single-use capabilities, staged release, canary, breakers, rollback drills, human signature, budgets | **Implemented on a sandbox.** Broker process (only writer, rollback included; macOS Seatbelt sandbox); release state machine; HMAC signatures (amendment 8); freeze/kill switch/toggles; drills pass (`caduceus drill`). Not yet: kernel sandbox for the broker, asymmetric signatures, a live Hermes install in enforce mode |
| 4 Auto-apply | blocked until a replicated result beats the random control | **Gate implemented, report-only; Blocked.** Counts only candidate experiments (refinement vs current vs random); exp-004, exp-005, exp-007 and exp-008 (CL-bench) failed; exp-006 NotRun |
| 5 Optional lanes / upstream | Bend, Lean, temporal stress, config routing | Not started; reported `Unavailable` |
| 6 Closed-loop quality-data recursion | `adapter_update` admission, hsi-lab bridge, lock ordering, real-domain round, recursion, one-layer ablations (PRD 6.11, 7.1) | **6a bridge and 6b lock witnessing implemented** (branch `adapter-update-bridge`); 6c run (exp-008-bugrepair-round1: Fail, closed; base yield too low to train on); 6d-6f not started |

## Done 2026-10-15
1. Background-review fork routes `skill_manage` through `pre_tool_call`
   (`scripts/probe_background_review.py`, observe + enforce).
2. Contracts for `systematic-debugging`, `test-driven-development`,
   `requesting-code-review`, `codebase-inspection` (high use, zero patches).
3. `HermesExecutor` + `experiment.py`; exp-001 locked and run on Claude Haiku 5.5
   via the Hermes login: **Pass** (+0.245, CI [+0.12, +0.38]); ledger in
   `ledgers/exp-001.jsonl`. Plumbing check: `scripts/smoke_executor.py`.
4. Switch-latency baseline pinned: `baselines/switch-latency-b20cc5f787.json`
   (Hermes tree had pre-existing uncommitted edits; recorded as dirty).

## Done 2026-10-15 (PRD build-out)
* Phase 2: corpus (43/43), strict intake, source admission, proposal wrapper,
  three-arm random control. exp-002 **Pass** vs both controls
  (`ledgers/exp-002.jsonl`).
* Phase 3: broker process with restore, release state machine, HMAC review
  signatures, operator controls, drills.
* Phase 4: eligibility gate (report-only). Status: **Blocked**, 1 of 2
  qualifying three-arm experiments (exp-001 has no random arm).
* Lanes now Checked against real Hermes: overhead
  (`baselines/plugin-overhead-*.json`), freshness (`baselines/freshness-*.json`).

## Done 2026-10-16
* exp-003 **Pass** on Sonnet 5.5 with contract v2 (`contracts/v2/`).
* Exchange (`exchange.py`): all 13 lanes from artifacts; content binding;
  custody + LLM-judge second instrument; lineage/tenant topology; broker
  sandbox (macOS); O(1) plugin hot path.
* exp-004, first real candidate through the Exchange: **Fail** (closed).
* exp-005, first clean candidate, on CL-bench (`caduceus/bench.py`,
  `scripts/bench_cl.py`): **Fail** (closed); Exchange rejected.

## Next concrete steps
1. Next clean candidate on CL-bench: add the `fixed_policy` arm the PRD
   requires, and a stronger proposer or more fit data (new id, new split seed).
2. Install the plugin into the live `~/.hermes` in observe mode (operator step).
3. Ed25519 signatures and a Linux broker sandbox.
4. Re-pin the switch baseline on a clean Hermes checkout.

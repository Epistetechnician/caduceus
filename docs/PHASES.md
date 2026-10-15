# Phase status (against PRD v2.1 section 12)

| Phase | Scope | Status |
|---|---|---|
| 0 Foundation | digest-chained ledger, Merkle proofs, witness, independent readback, read-only Hermes audit, freshness checker | **Implemented, tested** |
| 1 Skill substrate | contracts + probe kinds, identity by tree digest, behavior/composition/indispensability controls, protected suite + retention gate | **Implemented on synthetic skills**. Not yet: contracts for real Hermes skills; a real executor (running a Hermes skill requires an agent/model call) |
| 2 Admission + Refinery (propose-only) | typed candidates, deterministic gate, lanes, sandbox apply with single-use capability and rollback | **Implemented** (sandbox only; refuses a real Hermes home) |
| 3 Supervised apply | separate broker process, canary/breaker, human `approve` signature, witness hosted elsewhere | Not started. Plugin `observe` mode exists; `enforce` mode is implemented but unproven in a live Hermes |
| 4 Auto-apply | blocked until a replicated result beats the random control | Blocked by design |
| 5 Optional lanes / upstream | Bend, Lean, temporal stress, config routing | Not started |

## Next concrete steps
1. Install the plugin in `observe` mode into a throw-away `HERMES_HOME` and
   confirm intents are recorded, including from the background-review fork.
2. Write contracts for 3-5 of the most-used, least-patched skills (see
   `python -m caduceus audit`; high use with zero patches are the safest start).
3. Build a real executor adapter that runs a Hermes skill under a fixed model
   and temperature in an isolated `HERMES_HOME`, then preregister the first
   real paired test (n, minimum effect, seeds) before running it.
4. Pin the switch-latency baseline before touching any fast-path work.

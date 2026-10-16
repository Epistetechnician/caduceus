# Amendments to PRD v2.1 after reading the Hermes checkout

`docs/PRD.md` is the design baseline (v2.1). Reading the real code changed these
points; this file is authoritative where they differ.

1. **Plugin, not fork.** PRD 3/6.10 spoke of a "hardened fork". Hermes' own
   rubric makes the core a narrow waist and asks third-party products to be
   standalone plugin repos. Caduceus is a plugin + CLI; no core edits.
2. **No mid-session refresh (6.2/6.3).** Prompt caching is sacred, and
   MEMORY.md/USER.md are frozen at session start by design. Freshness incidents
   are redefined: `injection_mismatch`, `stale_at_boundary`,
   `checker_disagreement`. Drift within a session is informational.
   "Conversation-switch fast path" work must not rebuild the system prompt; it is
   deferred until measured against the real switch path.
3. **Reuse the existing ledger, add the gate.** Hermes already records every
   skill mutation (`.curator_ledger.jsonl`) with rollback, as telemetry. Caduceus
   anchors it (Merkle root, append-only check) and adds admission before
   mutation, instead of building a second mutation log.
4. **Gate point is concrete.** `pre_tool_call` on `skill_manage`, fail-closed on
   timeout, with `block`/`approve` directives. This replaces the abstract
   "broker" for Phase 1; the separate-process broker remains the Phase 3 target
   for applying changes outside Hermes' own tool path.
5. **Probe kinds.** `wrong_source_negative` became `negative` (inputs a skill
   must refuse). Wrong-source substitution is a separate *composition control*
   (`controls.composition_control`).
6. **Independent lanes available in Phase 1** are the ledger readback (distinct
   canonical encoder + iterative Merkle) and the two-implementation freshness
   hash. The Bend, formal (Lean) and temporal lanes in PRD 8 are `Unavailable`
   here and are reported as such, never as passes.
7. **Capabilities are in-process in Phase 1.** Single-use, expiring, ledgered
   tokens exist (`refinery.py`), but not yet in a separate sandboxed process.
8. **Signatures are HMAC (stdlib).** PRD 5.4/6.7.12 assume asymmetric signing.
   Python's standard library has none, so `signing.py` uses per-signer
   HMAC-SHA256 keys in an owner-only keyring. This proves "a keyring holder
   approved", not who; Ed25519/Sigstore is the hardening step.
9. **Self-evolution is wrapped generically.** `hermes-agent-self-evolution` is
   not vendored or fetched (no network, no new dependencies). `propose.py`
   turns any proposed skill directory, including that tool's output branch, into
   a typed candidate; the producer is never the gate.
10. **Random control = wrong-source skill.** PRD 7's "deterministic random arm
    with matched compute" is realised for skills as another skill from the same
    contract set (seeded derangement): same prompt size and cost, wrong content.
11. **Broker scope.** The broker is a separate OS process with byte caps,
    timeout and process-group kill. On macOS it runs under a `sandbox-exec`
    (Seatbelt) profile: network denied, file writes allowed only under the
    request's root, store and ledger directory. `sandbox-exec` is deprecated by
    Apple but present; on other platforms there is no kernel sandbox yet. It is
    the only writer of governed state and the only path for rollback.
12. **Fast path stays deferred.** Amendment 2 holds: a switch-latency baseline
    is pinned (`baselines/`), but no fast path is built, because the only large
    win (skipping prompt rebuild) conflicts with Hermes' prompt-cache rule.
13. **Admission extends to training data and adapters.** `adapter_update` is a
    candidate kind, off by default (the default policy refuses it; roots must
    include `adapters`). Evidence comes from hsi-lab receipts through
    `hsi_bridge.py`, which recomputes each receipt with hsi-lab's standalone
    validator in a separate process, converts to integer micro-units, and
    requires the candidate's preregistration to bind the exact receipt digests.
    Lanes the bridge cannot honestly fill stay `NotRun` (quarantine). A pipeline
    `lock_digest` is not a prediction lock until a ledger proves ordering.
14. **Recursion is evidence-gated, one layer at a time.** Ghost-state memory,
    the AHCL monitor, interpretability checks and selection policies enter as
    single ablations against the previous rung (PRD 7.1). They report
    `Unavailable` as lanes until a model-backed result exists. The thesis is a
    hypothesis; no scaling relation is claimed.

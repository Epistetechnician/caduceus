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

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
  call shape.

What it does **not** establish:
* anything about real Hermes skills' quality, or that gating improves Hermes;
* that `pre_tool_call` covers the autonomous background-review path (unverified);
* protection against a hostile process running as the same user, or against an
  attacker who can rewrite both content and digest (plaintext, no signatures);
* independent verification, independent replication, production readiness,
  security, SOTA, or any benchmark result. The witness is an unsigned local
  consistency monitor, not a transparency log;
* statistical power for any real experiment: bootstrap helpers exist, no
  preregistered real-world test has run;
* equivalence between the two canonical encoders on arbitrary input (only
  differential tests); a Lean/formal lane is not implemented.

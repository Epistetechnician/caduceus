# Add caduceus-governance

**What it does.** Governs what Hermes may write into its own skills. A deterministic, model-free admission kernel judges the *effect* on the skills tree (not the tool call), restores anything not admitted, and records every verdict in a digest-chained ledger. A provenance lane holds any skill write made in a session where `web_extract`, `web_search`, `browser_*` or `mcp_*` returned content (Hermes' own untrusted-tool list), whatever the write says; code with operator-registered passing checks is discharged, prose never is. Operators use `caduceus status`, `review-effect` and `release-effect`. Observe mode is the default.

**Surfaces used.** Hooks: `pre_tool_call`, `transform_tool_result`, `post_tool_call`, `on_skill_lifecycle`. A Desktop review pane and status-bar chip through the Desktop SDK (`desktop/plugin.js`, imports only `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`; it cannot sign), a dashboard tab (`dashboard/manifest.json`, `dist/index.js`, `dist/style.css`, `plugin_api.py`, using `window.__HERMES_PLUGIN_SDK__` and `SDK.fetchJSON`) and a standalone page `/ui`. The API is read-only except `POST /approve`, which releases or declines ONE held change only on a valid Ed25519 signature from a device the operator enrolled with the CLI (no signature, forged, replayed, expired, stale: refused); policy, freeze, enrolment and mode stay CLI-only. The page is served with a per-response nonce CSP, no inline style attributes and no external resources. No core override, no tools, no middleware.

**Disclosure (rule 13).**
* Reads and writes only under `$HERMES_HOME/skills` (boundary mode restores it to its last admitted state) and `$HERMES_HOME/caduceus` (ledger, blobs, taint metadata: tool names and digest prefixes, never content).
* No network calls, no model calls, no telemetry, no credential reads, no background processes, no self-update.
* Operator-registered check commands (off unless the operator adds one) run in a copy of the skill under macOS Seatbelt or Linux Landlock plus seccomp (no network, no writes outside the copy); where confinement is unavailable they return `Unavailable` and the change is held, never passed.
* Optional governed surfaces (`mode.json` `surfaces`): `skills` (default), `memory` (`MEMORY.md`, `USER.md`) and `config` (`config.yaml`); boundary mode restores a non-admitted change to any of them.
* Ships a vendored copy of its kernel under `_vendor/` (the catalog installs only the subdirectory); a test fails if it drifts from source.

**UI generated from one source** (`ui/src`, `scripts/build_ui.py`; a test fails if the generated files drift). Verified inside a real `hermes dashboard` (tab discovered, API behind the session token, full sign-and-release flow); the Desktop pane is lint-clean but not rendered in Desktop. See `docs/UI.md`.

**Validation.** `hermes plugins validate plugin/caduceus_governance` passes (15/15, security scan safe) against Hermes `67d47601bc`. Live check through Hermes' real hook dispatch: `scripts/e2e_hermes_provenance.py`.

**Evidence and limits.** `docs/CLAIMS.md` (ceiling: local development evidence only). Content lanes miss semantic evasions; the provenance lane cannot stop malice with no untrusted input.

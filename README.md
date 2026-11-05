# Caduceus

Verified evolution for [Hermes Agent](https://github.com/NousResearch/hermes-agent):
a governed release process around self-improvement. Hermes proposes; a
deterministic admission core decides; every change is recorded in an
append-only, Merkle-anchored ledger; anything that cannot survive preregistered
controls, a sealed holdout and independent checks is quarantined, not retuned.

Status: **Phase 0-2 substrate, local development only** (`docs/CLAIMS.md`).
Stdlib-only Python 3.9+ (tested on 3.9, 3.13, 3.14), 108 hermetic tests, no network, no model calls (`run-paired` calls a model only with `--allow-model-calls`).

## What is here

| Piece | Module | Purpose |
|---|---|---|
| Ledger | `ledger.py`, `merkle.py`, `witness.py` | Digest-chained JSONL; RFC 9162 inclusion/consistency proofs; external consistency witness |
| Independent verifier | `readback.py` | Re-implements canonical encoding and Merkle root without importing the writer |
| Skill contracts | `contract.py`, `probes.py` | Visible / held-out / perturbation / negative probes; identity by tree digest (`skills.py`) |
| Controls | `controls.py`, `protected.py`, `stats.py` | Refactor-equivalence, composition & indispensability controls, retention-at-1.0 gate, seeded bootstrap |
| Gate | `candidate.py`, `lanes.py`, `admission.py` | 13 lanes -> `accept` / `reject` / `quarantine`; `grants_authority` always false |
| Refinery | `refinery.py` | Sandbox-only apply: single-use expiring capability, snapshot, apply-time revalidation, auto rollback |
| Hermes seam | `plugin/caduceus_governance/`, `audit.py`, `freshness.py` | `pre_tool_call` gate on `skill_manage` (observe by default); read-only audit; anchor for Hermes' curator ledger |
| Real skills | `contracts/`, `executor.py`, `hermes_runner.py`, `experiment.py`, `prereg/` | Contracts for 4 real skills; isolated-home executor; preregistered paired test (lock, one-shot run, cluster bootstrap) |
| Supervised apply | `broker.py`, `release.py`, `signing.py`, `control.py`, `drill.py` | Separate broker process (only writer; re-checks everything, byte caps, timeout); staged release with canary, breakers, budgets; HMAC review signatures; freeze / kill switch / subsystem toggles; end-to-end drills |
| Phase 2 gates | `corpus.py`, `source_admission.py`, `propose.py` | Adversarial corpus (every seeded attack must be refused); memory receipt admission; wrap any proposed skill dir as a candidate |
| Exchange | `exchange.py`, `judge.py`, `custody.py`, `lineage.py` | All 13 gate lanes computed from real artifacts for one candidate; LLM-judge second instrument over owner-only custody; variant archive with tenant/shared scopes |
| Reporting | `diagnostics.py`, `autoapply.py` | Lane vector (no aggregate score); Phase 4 eligibility, report-only |
| Hermes probes | `scripts/` | Background-review hook probe, executor smoke test, switch-latency baseline (`baselines/`); run with the Hermes venv, stub model on 127.0.0.1 |

## Try it

```sh
python3 -m unittest discover -s tests -t .
python3 -m caduceus demo --out /tmp/caduceus-demo      # synthetic end-to-end scenarios
python3 -m caduceus verify-ledger /tmp/caduceus-demo/ledger.jsonl
python3 -m caduceus audit --hermes-home ~/.hermes       # read-only inventory of a real install
python3 -m caduceus anchor-curator                      # Merkle anchor over Hermes' curator ledger
python3 -m caduceus corpus                              # adversarial corpus; non-zero exit on any miss
python3 -m caduceus drill                               # promote / replay / rollback / frozen drills on a sandbox copy
python3 -m caduceus diagnostics --ledger ledgers/exp-001.jsonl --ledger ledgers/exp-002.jsonl
```

The demo runs seven scenarios on a sandbox (refactor accepted and applied;
visible-overfit rejected; disagreeing instruments quarantined; side-effect on a
protected skill rejected; stale retention evidence caught at apply time and
rolled back; self-signed code patch rejected; replayed capability refused).

## Install the plugin

The plugin is `plugin/caduceus_governance/`: self-contained (it ships a vendored copy of the kernel in `_vendor/`, kept in sync by `scripts/vendor_plugin.py` and a test),
hooks only (`pre_tool_call`, `transform_tool_result`, `post_tool_call`), stdlib only, no network, no model calls, no telemetry, no credential reads, no self-update.
`hermes plugins validate plugin/caduceus_governance` passes. Once listed in the Hermes catalog: `hermes plugins install caduceus-governance`, then enable it.
From a checkout (operator steps; this repository never writes to `~/.hermes` itself):

```sh
ln -s "$PWD/plugin/caduceus_governance" ~/.hermes/plugins/caduceus_governance
mkdir -p ~/.hermes/caduceus && echo '{"mode": "observe"}' > ~/.hermes/caduceus/mode.json
```

```yaml
# ~/.hermes/config.yaml  (user plugins are gated by this list)
plugins:
  enabled:
    - caduceus-governance
```

Modes (`mode.json`; the default is `observe`, anything stronger is an explicit opt-in):
* `observe` records one ledger entry per `skill_manage` call (args digest and action names, never content) and never blocks.
* `boundary` governs the *effect*: after each skill write, and before each skill read, the skills tree is compared with its last admitted state; anything not admitted is restored and the model is told.
  A skill write made after untrusted content entered the session (`web_extract`, `web_search`, `browser_*`, `mcp_*`: Hermes' own untrusted-tool list) is held for you; code or data whose
  skill has your registered passing checks is admitted. Review with `caduceus status` and `caduceus review-effect <digest>`; release with `caduceus release-effect`.
* `"surfaces": ["skills", "memory"]` in `mode.json` also governs `MEMORY.md` / `USER.md` (default: skills only). `caduceus policy-set` installs a ledgered policy; `caduceus attest-baseline` replaces trust-on-first-use.
* `enforce` blocks `skill_manage` mutations that lack an admitted single-use capability and fails closed.

Disclosure: reads and writes only under `$HERMES_HOME/skills` (boundary mode restores it) and `$HERMES_HOME/caduceus` (ledger, blobs, taint metadata: tool names and digest prefixes, never content).
Operator-registered check commands (`caduceus check-add`; off unless you add one) run in a copy of the skill under macOS Seatbelt: no network, no writes outside the copy.
**On Linux and anywhere Seatbelt is unusable a check returns `Unavailable` and the change is held, never passed**, so boundary mode there holds more than on macOS until Linux confinement is built.

Freeze or switch it off without uninstalling: `python3 -m caduceus control freeze --caduceus-dir ~/.hermes/caduceus` / `control disable admission ...`.
Check the ledger with `python3 -m caduceus diagnostics --ledger ~/.hermes/caduceus/ledger.jsonl --caduceus-dir ~/.hermes/caduceus`. Read `docs/CLAIMS.md` before leaving observe.

## Read next

* `docs/PRD.md` - design baseline (v2.1) with research and code references
* `docs/PRD_AMENDMENTS.md` - what changed after reading the real Hermes code
* `docs/HERMES_INTERFACES.md` - verified integration facts and open questions
* `docs/REAL_HOME_AUDIT.md` - first audit of a real install (counts only)
* `docs/PHASES.md`, `docs/CLAIMS.md`

## License

Apache-2.0 (`LICENSE`, `NOTICE`). Contains no Hermes code.

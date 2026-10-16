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

## Install the plugin (observe mode)

Operator steps; this repository never writes to `~/.hermes` itself.

```sh
~/.hermes/hermes-agent/venv/bin/pip install -e .          # Hermes' interpreter must import caduceus
ln -s "$PWD/plugin/caduceus_governance" ~/.hermes/plugins/caduceus_governance
mkdir -p ~/.hermes/caduceus && echo '{"mode": "observe"}' > ~/.hermes/caduceus/mode.json
```

Then add the plugin to `plugins.enabled` in `~/.hermes/config.yaml`
(user plugins are gated by that list):

```yaml
plugins:
  enabled:
    - caduceus-governance
```

Observe mode records one ledger entry (args digest + action names, never
content) per `skill_manage` call, including the background-review fork's, and
never blocks. Check it with
`python3 -m caduceus diagnostics --ledger ~/.hermes/caduceus/ledger.jsonl --caduceus-dir ~/.hermes/caduceus`.
Freeze or switch it off without uninstalling:
`python3 -m caduceus control freeze --caduceus-dir ~/.hermes/caduceus` /
`control disable admission ...`. `enforce` mode blocks mutations that lack an
admitted single-use capability and fails closed; run observe first and read
`docs/CLAIMS.md` before switching.

## Read next

* `docs/PRD.md` - design baseline (v2.1) with research and code references
* `docs/PRD_AMENDMENTS.md` - what changed after reading the real Hermes code
* `docs/HERMES_INTERFACES.md` - verified integration facts and open questions
* `docs/REAL_HOME_AUDIT.md` - first audit of a real install (counts only)
* `docs/PHASES.md`, `docs/CLAIMS.md`

## License

Apache-2.0 (`LICENSE`, `NOTICE`). Contains no Hermes code.

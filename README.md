# Caduceus

Verified evolution for [Hermes Agent](https://github.com/NousResearch/hermes-agent):
a governed release process around self-improvement. Hermes proposes; a
deterministic admission core decides; every change is recorded in an
append-only, Merkle-anchored ledger; anything that cannot survive preregistered
controls, a sealed holdout and independent checks is quarantined, not retuned.

Status: **Phase 0-2 substrate, local development only** (`docs/CLAIMS.md`).
Stdlib-only Python 3.9+ (tested on 3.9, 3.13, 3.14), 52 hermetic tests, no network, no model calls.

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

## Try it

```sh
python3 -m unittest discover -s tests -t .
python3 -m caduceus demo --out /tmp/caduceus-demo      # synthetic end-to-end scenarios
python3 -m caduceus verify-ledger /tmp/caduceus-demo/ledger.jsonl
python3 -m caduceus audit --hermes-home ~/.hermes       # read-only inventory of a real install
python3 -m caduceus anchor-curator                      # Merkle anchor over Hermes' curator ledger
```

The demo runs seven scenarios on a sandbox (refactor accepted and applied;
visible-overfit rejected; disagreeing instruments quarantined; side-effect on a
protected skill rejected; stale retention evidence caught at apply time and
rolled back; self-signed code patch rejected; replayed capability refused).

## Install the plugin (observe mode)

Copy or symlink `plugin/caduceus_governance/` into `~/.hermes/plugins/` and
`pip install -e .` so the plugin can import `caduceus`. Default mode is
`observe`: it records an intent entry (digest + action names, never content)
for each `skill_manage` call and never blocks. `enforce` mode
(`$HERMES_HOME/caduceus/mode.json`) blocks mutations lacking an admitted,
single-use capability and fails closed on error. Do not enable `enforce` before
reading `docs/PHASES.md` ("unverified" items).

## Read next

* `docs/PRD.md` - design baseline (v2.1) with research and code references
* `docs/PRD_AMENDMENTS.md` - what changed after reading the real Hermes code
* `docs/HERMES_INTERFACES.md` - verified integration facts and open questions
* `docs/REAL_HOME_AUDIT.md` - first audit of a real install (counts only)
* `docs/PHASES.md`, `docs/CLAIMS.md`

## License

Apache-2.0 (`LICENSE`, `NOTICE`). Contains no Hermes code.

# Agent rules for this repository

State slice: `caduceus-phase1-substrate-v1`. Claim ceiling: `LocalDevelopmentEvidenceOnly`.

* Python standard library only. No network, no model calls, no new dependencies.
* Never write inside a real Hermes home (`~/.hermes` or `$HERMES_HOME`). The
  Refinery refuses it; `audit` and `anchor-curator` are read-only. Tests use
  temporary directories.
* Do not copy Hermes source here. Interfaces are recorded in
  `docs/HERMES_INTERFACES.md` with file references and re-verified, not vendored.
* The admission core is deterministic and never calls a model. A model may
  propose; it never decides. Keep `grants_authority=False`.
* Fail closed. Unknown lanes, statuses, kinds, fields, or malformed ledger lines
  are errors. A lane that did not run is `NotRun`/`Unavailable`, never a pass.
* Ledger bodies hold digests and bounded metadata only; no raw payloads, no
  secrets, no floats.
* A failed preregistered gate closes that candidate identity. Do not retune
  thresholds to rescue a result; create a new candidate with new data.
* Do not add claims above `docs/CLAIMS.md`. Update that file first if evidence changes.
* Commits are authored by the repository owner only; no co-author trailers.

Validation:
```sh
python3 -m compileall -q caduceus plugin tests
python3 -m unittest discover -s tests -t . -v
python3 -m caduceus demo --out "$(mktemp -d)/demo" && python3 -m caduceus verify-ledger <that>/ledger.jsonl
```

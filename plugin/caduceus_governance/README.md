# Caduceus governance for Hermes

Hermes carries the message; Caduceus is the staff that makes the carrier trustworthy.

A self-improving agent writes skills, memory and config that outlive the session. Caduceus judges what a run **leaves behind**, with a deterministic, model-free gate, a tamper-evident ledger and operator release and rollback. It never calls a model or the network.

## What it does

* **Judges the effect, not the request.** After a skill write (or any tool that can write the skills tree) and before a skill read, the tree is compared with its last admitted state. Anything not admitted is restored and the model is told why.
* **Provenance across sessions.** If untrusted content entered the session (`web_extract`, `web_search`, `browser_*`, `mcp_*`: Hermes' own untrusted-tool list), a skill write is held for you whatever it says. This catches paraphrased, encoded and non-English instructions that no text pattern can. Code or data in a skill whose operator-registered check still passes is admitted; prose never is.
* **Operator control.** `caduceus status`, `review-effect <digest>` (a bounded diff), `release-effect`, `policy-set`, `attest-baseline`, `control freeze`. Releasing is CLI-only; the Desktop pane is read-only.
* **Also governs** `MEMORY.md` / `USER.md` and `config.yaml` (opt-in via `mode.json` `surfaces`).

## Install

```sh
hermes plugins install caduceus-governance      # once listed; then enable it
```
Default mode is `observe` (records one ledger line per `skill_manage` call and never blocks). Opt in to `boundary` or `enforce` in `$HERMES_HOME/caduceus/mode.json`; see the repository README for modes and the full CLI.

## Disclosure

Reads and writes only under `$HERMES_HOME/skills`, `$HERMES_HOME/memories`, `$HERMES_HOME/config.yaml` (restoring them in boundary mode) and `$HERMES_HOME/caduceus`. No network, no model calls, no telemetry, no credential reads, no self-update, no background process.
Operator-registered check commands (none unless you add one) run in a copy of the skill under macOS Seatbelt or Linux Landlock plus seccomp (no network, no writes outside the copy); where confinement cannot be applied a check is `Unavailable` and the change is held, never passed.
The package ships a vendored copy of its kernel in `_vendor/` because the catalog installs only this directory.

## What it does not do

It does not catch malice with no untrusted input (a user-instructed or purely model-originated write), and static lanes cannot see intent. The evidence is author-written and descriptive; see `docs/CLAIMS.md` in the repository for exactly what is and is not claimed.

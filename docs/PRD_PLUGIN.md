# Caduceus for Hermes: product requirements

Status: v2, 2026-11-04 (built to the extent each requirement's status line says; unbuilt items are named, not hidden). State slice: `caduceus-phase1-substrate-v1`. Claim ceiling: `LocalDevelopmentEvidenceOnly`. Nothing here is a claim; every
"shall" is a requirement with an acceptance test, and `docs/CLAIMS.md` changes first when evidence does. Supersedes nothing: `docs/PRD.md` remains the
research PRD for the admission substrate; this document specifies the Hermes plugin product built on it.

## 1. One line

Hermes carries the message; Caduceus is the staff that makes the carrier trustworthy. **OpenShell-style runtimes bound what an agent run can do. Caduceus bounds what
a run can leave behind**: every change to the agent's own future (skills now; memory, config and adapters next) crosses a deterministic, model-free, ledgered gate.

## 2. Problem

Self-improving agents write to the one place that outlives the session: skills, memory, prompts, adapters. That is a supply chain with the agent as both author and consumer.

* Public skill stores already carry real poison: Snyk's ToxicSkills audit found 13.4% of 3,984 skills with a critical issue; scanners miss prompt-injection.
* In-session defences (CaMeL, FIDES, Progent) stop untrusted data steering *this* run. They do not govern what the run writes down for the next one.
* Self-modifying agents game their own evaluators (the Darwin Gödel Machine paper documents objective hacking, including removing the logging its checker relied on).
* Repeated sealed-holdout queries leak holdout information into the next proposal, so reported gains drift above true gains (our `holdout-ledger`: the usual 95% bound was wrong in 100% of attacked runs).
* Our own evidence: tier-0 content lanes missed 4/4 semantic evasions; exp-004..011 show skill text rarely moves solve rates, so "better skills" is not the product, **safe, auditable change** is.

## 3. Positioning

| Layer | Question it answers | Examples | Caduceus relation |
|---|---|---|---|
| Runtime isolation | What may this run touch (fs, net, process, inference)? | NVIDIA OpenShell (Landlock + seccomp + netns, declarative YAML, out-of-process, hot-reloadable network policy), NemoClaw | **Complement.** Caduceus emits an OpenShell policy that makes the broker the only writer of governed state, and can use it as the Linux confinement for checks |
| In-session flow control | Can untrusted data steer this run? | CaMeL, FIDES, Progent | Same idea (integrity labels, deterministic policy), applied **across sessions** |
| Content scanning | Does this text look dangerous? | Hermes `security-guidance` (warn-only), Snyk, SkillSieve | Tier 0 only; we assume it is evadable |
| Self-improvement search | Does the change score higher? | Darwin Gödel Machine, Voyager-style libraries, reflection loops | **Release process** around them: sealed holdout priced in bits, oracle outside the mutated tree |
| Evidence integrity | Is the reported gain real? | preregistration, adaptive-data-analysis bounds | Built in: failed gates close the candidate identity; no retuning |

OpenShell details come from NVIDIA's overview page and third-party write-ups and are unverified against its source; re-verify before any integration claim (AGENTS.md: record interfaces with file references).

## 4. Users

* **Operator** (runs Hermes on a laptop, server or fleet; owns policy, checks, release). Needs: nothing changes my agent's future without my say-so unless a rule I chose allows it; one inbox for what was held; one command to undo.
* **Skill author / maintainer** (writes or reviews skills). Needs: executable checks that guard their skill, a clear verdict reason, no surprise reverts.
* **Researcher** (studies self-improvement). Needs: preregistered candidates, sealed holdouts with a leakage account, replayable ledgers, honest nulls.
* **Hermes maintainer / catalog reviewer**. Needs: public surfaces only, declared capabilities that match, disclosed behaviour, readable source.

## 5. Principles (inherited, binding)

1. A model may propose; it never decides. `grants_authority=False` everywhere.
2. Fail closed. Unknown lane/status/kind/field/ledger line is an error; a lane that did not run is `NotRun`/`Unavailable`, never a pass.
3. Judge the effect, not the request. Verdicts are pure functions of (policy, effect, in-scope provenance).
4. Ledger bodies hold digests and bounded metadata only: no raw payloads, no secrets, no floats.
5. The oracle lives outside the thing it judges.
6. A failed preregistered gate closes that candidate identity; new data means a new candidate.
7. Stdlib-only Python in the plugin; no network, no model calls in the admission path.
8. Never write inside a real Hermes home except as the operator-installed plugin does under `$HERMES_HOME/caduceus` and the governed tree.

## 6. Product architecture

```
 Hermes agent loop ──hooks──▶  OBSERVE  ──▶  JUDGE  ──▶  RELEASE  ──▶ governed state
 (pre/post_tool_call,           taint        kernel       broker         (skills, memory,
  transform_tool_result,        events       lanes        canary          memory/config)
  on_skill_lifecycle,           digests      + checks     rollback            │
  on_session_*)                                │              │               ▼
                                               ▼              ▼         LEDGER + WITNESS
                                           EVIDENCE ◀──── OPERATOR INBOX (quarantine, release, freeze)
                                      (Exchange, experiments,
                                       holdout-ledger bit account)
```

* **Observe** (plugin, in-process, cheap): taint events, intent entries, lifecycle facts. Never blocks except where a mode says so.
* **Judge** (`transition.py`, pure): content lanes, provenance lane, operator-owned executable checks.
* **Evidence** (`exchange.py`, `experiment.py`): the 13-lane exchange for candidates that claim an improvement; sealed holdout; controls; second instrument.
* **Release** (`broker.py`, `release.py`): single-writer, capability-bound apply; canary window; breakers; rollback.
* **Ledger/Witness**: digest-chained JSONL, RFC 9162 proofs, independent readback, external consistency witness.
* **Operator surface**: CLI first (`status`, `review-effect`, `release-effect`, `policy-set`, `attest-baseline`, `control`); a read-only Hermes Desktop pane through SDK slots; releasing is CLI-only by design.

## 7. Requirements

Priority: P0 = ships for the hackathon (due 2026-11-11 5PM EST); P1 = next; P2 = research.
"AC" = acceptance criterion; each is a test or a scripted eval with a ledgered result.

### 7.1 Catalog and install (P0)

* R1. Self-contained plugin directory; works from the catalog's sparse subdir checkout. AC: `hermes plugins validate plugin/caduceus_governance` passes; `scripts/vendor_plugin.py --check` in CI. *(done)*
* R2. *(done; no `requires_hermes` declared because the pinned tree reports version 0.0.0, so no truthful SemVer floor exists yet)* Manifest declares exactly the hooks it registers; `requires_hermes` is a SemVer floor verified against the Hermes tree used for the live checks. AC: validator green; floor tested on the pinned Hermes commit.
* R3. *(done: `tests/test_plugin_package.py`)* No binaries, no `.pyc`, no self-update, no core override, no credential reads. AC: validator + a repo test listing tracked file headers.
* R4. *(done: README install and disclosure, `docs/CATALOG_PR_BODY.md`)* README and PR disclosure cover rule 13: reads/writes (`$HERMES_HOME/skills`, `$HERMES_HOME/caduceus`), no network, no model calls, optional operator check commands under confinement, boundary mode can restore the skills directory. AC: reviewer checklist in the PR.
* R5. *(done: test)* Default mode is `observe`; every stronger mode is an explicit operator opt-in recorded in `mode.json`. AC: fresh install writes nothing but a ledger line per gated call.

### 7.2 Judge (P0)

* R6. Effect-addressed kernel with the existing lanes (scope, file kind, pinned, budget, deletion, rate, secrets, injection, binary/exec, format, syntax, checks). *(done)*
* R7. **Provenance lane.** Untrusted-source tool results taint the session; a write whose scope holds an in-window observation is quarantined. Policy-configurable tool list, window, on/off. *(done, `docs/STATE_GOVERNANCE.md`)*
* R8. *(done: 11/11 preregistered expectations, one preregistered miss)* **Taint discharge** (reduces the measured 4/4 benign-after-taint friction). Split effects by file role: prose instructions (SKILL.md, references) stay quarantined; executable or data files whose skill has operator-registered checks that Pass may be admitted while tainted. AC: eval shows benign tainted code edits admitted, hostile tainted prose and code-with-failing-check held, nothing admitted that tier 0 plus provenance held before except check-passing code. Preregister the scenario set before implementing; no retuning to the result.
* R9. *(done: `review-effect`)* **Operator review pack** for a quarantined effect: unified diff, lanes, taint tools, source count, one command to release or discard. AC: `release-effect` output reproducible from the ledger; the diff never enters the ledger body.
* R10. *(done)* **Policy as code**: `policy.json` with digest recorded in every verdict; changing it is itself a ledgered operator action. AC: replay with the recorded policy digest reproduces every verdict byte for byte.

### 7.3 Boundary hardening (P0/P1)

* R11. *(done to the documented schema, verified 2026-11-04 against NVIDIA's Policy Schema Reference: `include_workdir`, `read_only`, `read_write`, `landlock.compatibility: hard_requirement`; the emitted file has never been run in a real OpenShell sandbox, so the AC's OS-level shell-redirect test is NOT done)* (P1) **Single writer by construction.** `caduceus openshell-policy` emits an OpenShell filesystem policy where the skills tree is read-only to the agent sandbox and writable only by the broker; the governed-tree mutation window and out-of-band writers disappear rather than being detected. AC: with the policy, a shell redirect into the skills tree fails at the OS; without it, boundary mode still reverts it. Schema verified; the sandbox acceptance test needs an OpenShell install.
* R12. *(done: Landlock + seccomp via ctypes, checks and broker, 11 tests in a Linux container plus the whole suite on Linux; x86_64 and older kernels untested)* (P1) **Linux confinement for checks** via Landlock, stdlib-only via ctypes. Where it cannot be applied a check is `Unavailable` and the change is held (fail-closed, documented). AC: confinement probe on Linux: no network, no writes outside the copy; else `Unavailable`.
* R13. *(request drafted in `docs/HOOK_REQUEST.md`; NOT filed: filing is outward-facing and `gh` is not authenticated; the window itself is documented but not measured in milliseconds)* (P1) Close the prompt-index window: Hermes builds the skill index before any hook fires. Request or use a pre-index seam via the catalog's "Asking for a hook" route; until then document the window. AC: issue filed with a concrete consumer; window measured.
* R14. *(done; with `attest_keyring` the attestation is an Ed25519 signature by a trusted signer, otherwise an operator name in the ledger)* (P1) Trust-on-first-use baseline replaced by operator attestation: `caduceus attest-baseline` shows the tree and records an operator signature before HEAD is trusted. AC: enforce/boundary refuse an unattested baseline unless `tofu: true`.

### 7.4 Evidence and self-improvement (P1)

* R15. *(done: folded into the existing `holdout` lane instead of adding a 14th required lane; exercised through the real Exchange (budget Checked, then Failed and reject once exceeded) and its radius cross-checked against `holdout-ledger`'s own bounds; no live self-improvement experiment has been run through it)* **Holdout leakage account.** Every verdict bit released to a proposer from a sealed holdout is charged in a hash-chained account; a candidate's certified gain is the lower of the information route and the description-length route (`holdout-ledger`). AC: lane `holdout_budget` Checked; a proposer that exhausts its budget gets `Quarantine`; replay equals the ledger.
* R16. *(done for in-skill evaluator paths via `protected_globs`; judge rubrics and contracts are outside the governed tree by construction)* **Oracle outside the tree**: already true for checks; extend to evaluators and judge rubrics (custody, owner-only). AC: a mutation touching an evaluator path is rejected by `scope`/`pinned`, tested with a DGM-style "delete the logging the checker reads" scenario.
* R17. *(unchanged by design)* Candidate pipeline stays as built (Exchange, 13 lanes, controls, preregistration, second instrument). Phase 4 auto-apply remains Blocked until a replicated candidate beats the random control. No change to that gate.
* R18. *(unchanged by design)* Distillation-with-verification lane (adapter updates) stays research-scoped: Pass on Qwen-lineage 7B trainees only, no self-improvement or compounding claim.

### 7.5 State coverage (P1)

* R19. *(done: memory (13-scenario eval) and `config.yaml` (unit and plugin tests); no live Hermes memory-tool or config run, and other config files are out of scope)* Extend the kernel from skills to **memory** (reuse freshness checks and receipt admission) and **plugin/config surfaces the agent can write**. Same lanes, per-surface policy. AC: containment eval per surface with fresh scenarios.
* R20. *(done, opt-in)* `on_skill_lifecycle` consumed as a second observation channel for skill facts, reconciled against the tree diff (disagreement is a quarantine reason).

### 7.6 Release and rollback (P1)

* R21. Staged release for promoted candidates: canary window, breakers, budgets, freeze/kill switch, rollback drills. *(done through a real Hermes loop in a throw-away home, `scripts/e2e_hermes_release.py`; a real user home is deliberately never touched)*
* R22. *(done: pure-stdlib Ed25519, RFC 8032 vectors, public-only verifier keyrings; Biscuit-style grants not built)* Asymmetric review signatures (Ed25519) replace HMAC; Biscuit-style attenuable grants evaluated against `playbox`'s model (P2).

### 7.7 Operator experience (P0 CLI, P2 Desktop)

* R23. *(done: `status`; p99 0.19 ms on a 900-file tree, `baselines/operator-cost-2026-11-04.json`)* One-screen `caduceus status`: HEAD, last verdicts, held effects, taint scope, frozen flag, ledger size. AC: under 100 ms on a 900-file tree.
* R24. *(done: the e2e scripts assert what happened, the reason code, that the tree was restored, "Do not retry" and the operator command)* Every refusal tells the model and the operator exactly what happened and what to do (existing notes in tool results). AC: scripted e2e asserts the message text class.
* R25. *(built and validator-clean; not rendered in Hermes Desktop)* (P2) Desktop SDK view of the inbox and ledger; SDK slots only (rule 8). Risky for the hackathon window; skip unless time remains.

### 7.8 Non-functional

* N1. *(done: taint observation p99 0.73 ms, scope read 0.11 ms, `baselines/operator-cost-2026-11-04.json`)* Hot path: +0.11 ms p99 non-gated, +0.29 ms p99 gated (earlier baseline); taint observe adds at most 1 ms p99. AC: baseline pinned.
* N2. *(done: tests)* Determinism: same writes + policy + taint ⇒ same verdict digests and ledger.
* N3. *(done)* Hermetic tests: stdlib only; macOS-sandbox-dependent tests skip with a reason where confinement is unavailable instead of failing.
* N4. *(partly: one Hermes commit, `67d47601bc`; re-verify on every Hermes bump)* Compatibility: tested on the pinned Hermes commit and re-verified per Hermes release; interfaces in `docs/HERMES_INTERFACES.md`.

## 8. Threat model

In scope: prompt-injected content reaching a skill write; model-originated poison; out-of-band writers (shell, forked review agent, curator); symlink/path/secret/binary payloads; mass deletion and rate bursts; replayed or forged capabilities; evaluator tampering; sealed-holdout overfitting; ledger tampering.

Not defended: malice with no untrusted input (a user-instructed or purely model-originated write: the provenance lane admits it, measured 3/3); a compromised operator; a compromised Hermes or kernel; side channels; a skill that is benign in text and harmful only in later semantics, unless an operator-registered check covers it. Static lanes cannot see intent.

## 9. Success metrics (descriptive until independently replicated)

| Metric | Target | Source |
|---|---|---|
| Fresh hostile effects contained, tainted | all in a preregistered, independently authored set | `scripts/eval_provenance.py` successor |
| Benign effects held by taint (friction) | below the pre-R8 4/4, no containment loss | R8 eval |
| Out-of-band writers contained | detected-and-reverted without the OpenShell policy (measured, containment eval); prevented at the OS with it (NOT measured: needs an OpenShell sandbox) | containment eval |
| Governed-tree reconcile cost | ≤ 30 ms at 900 files | met: clean p99 26 ms, one change p99 98 ms (`baselines/operator-cost-2026-11-04.json`) |
| Verdict replay | 100% byte-identical | met in tests (same writes, policy and taint give the same verdict digest) |
| Catalog admission | validate green (met); reviewer disclosure accepted (PR not opened) | R1-R5 |
| Honest nulls published | every closed candidate in `docs/CLAIMS.md` | existing practice |

## 10. Hackathon cut and what is left (today 2026-11-04, due 2026-11-11 5PM EST)

Built and verified: R1-R10, R12, R14-R16, R19-R25, N1-N3 (statuses above). Demo: `scripts/demo_hackathon.py` and `docs/DEMO.md` (the video is not recorded).
Not done, and why:
* Open the catalog PR and submit the form: blocked on making the repository public and on a valid `gh` login (operator actions).
* Record and post the demo video (an operator action).
* R13: file the hook request (outward-facing; needs `gh`).
* R11 acceptance: run the emitted policy in a real OpenShell sandbox.
* Biscuit-style attenuable grants (R22, P2) and any independent scenario author (the research-grade claim gate).
Day plan: D1-2 push and open the PR early; D2-5 only fixes for reviewer findings; D6 record the demo; D7 post and submit both links.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Friction: legitimate web-sourced learning is held | R8 discharge, R9 review pack, per-tool and per-window policy, `provenance: off` |
| Tool list drifts from Hermes' untrusted surface | the default equals Hermes' own list today (`docs/HERMES_INTERFACES.md`); re-diff on every Hermes bump; list is policy |
| Validator or scan flags subprocess/Seatbelt use | currently scan = safe; disclose under rule 13; keep checks optional |
| Judges read it as "just a scanner" | lead with effect-addressing, provenance across sessions, ledger, release; show the contrast with `security-guidance` |
| Claims drift above evidence | `docs/CLAIMS.md` first; ceiling stays `LocalDevelopmentEvidenceOnly` |
| Author-written eval sets | label descriptive; recruit an independent scenario author before any claim |
| OpenShell behaviour differs from its documentation | schema verified against the docs; never run in a real sandbox here; integration is optional |

## 12. Open questions

1. *Resolved*: the background-review fork shares the parent's `session_id` in every hook it triggers (`scripts/probe_fork_session.py`), so taint observed in the parent applies to the fork's skill writes.
2. *Tool list resolved*: Hermes' own classification is `web_extract`, `web_search`, `browser_*`, `mcp_*` (`docs/HERMES_INTERFACES.md`). Still open: gateway messages, file reads of downloaded files, connectors.
3. Can taint discharge (R8) be made sound for scripts without becoming a content scanner again? Not shown; the preregistered D11 miss stands. Needs a threat-model note before any claim.
4. Is a pre-index hook acceptable to Hermes maintainers (R13)? Not asked yet.
5. Who is the independent scenario author? Unassigned; every eval here is author-written.
6. Does the emitted OpenShell policy actually leave Hermes runnable? Needs a real sandbox.

## 13. References

Hermes plugin catalog rules and developer guide (hermes-agent.nousresearch.com); NVIDIA OpenShell overview (docs.nvidia.com/openshell); Snyk ToxicSkills; Darwin Gödel Machine (arXiv 2505.22954); FIDES (arXiv 2505.23643); CaMeL (Google Research); RFC 9162; local repos `holdout-ledger`, `formasec`, `security-alignment-os`, `playbox`, `hsi-lab`. Third-party summaries must be checked against the primary source before citation.

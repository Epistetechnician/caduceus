# Caduceus — Verified Evolution for Hermes Agent

**Product Requirements Document · Version 2.2 (integrated, closed-loop) · 16 October 2026 · Status: Draft for implementation review**
**Claim ceiling of this document: design intent (Level 0).** No requirement here is evidence that it works. Reference tags like **[X4]** (external) and **[I5]** (our own research/code) resolve in §17. Every external number is author-reported and unreplicated by us.

---

## 1. Thesis and positioning

Self-improving agents fail in two ways: capability rises while reliability silently decays, or "improvement" cannot be told from noise. Published systems demonstrate the first half of the loop — an agent can rewrite itself and score higher on a benchmark — and then record the second half as a footnote: the Darwin Gödel Machine reports 20%→50% on SWE-bench but also documents objective hacking, including a lineage that scored perfectly on the metric without solving the problem **[X4]**; SICA reports 17%→53% on a random SWE-bench Verified subset **[X5]**; ADAS and OpenEvolve search over agent/program designs **[X6][X8]**. None of these is a governed release process.

**Caduceus is that governed release process for Hermes.** It does not claim a new self-improvement algorithm (progressive replacement and distillation are prior art, as our own Theseus Lab notes **[I5]**). The contribution is the wrapper around any such algorithm:

> Hermes may grow only by trading fairly with Apollo, and the trade is enforced by code, measured against controls, and recorded in an append-only ledger. The prompt is the story; the admission core is the law.

An **Evolution Candidate** — a typed, digest-bound proposal for one change — must survive preregistered controls, a sealed holdout, independent verification lanes, staged release with circuit breakers, and automatic rollback. Anything that cannot is quarantined, not retuned.

**Where we expect the real leverage to be (from our own data, not hope).** Across our discovery programs, 12 of 14 preregistered experiments closed `NoCandidate` **[I7]**, and in `research-selection-os` the only positive effects came from **reusable learned abstractions**, while clever selection added little once its compute was charged **[I8]**. So v2.1 is **abstraction-first**: Caduceus invests first in skills as tested, composable units and in the admission machinery, and last in clever self-selection of what to improve (§12).

### 1.1 The research hypothesis this product serves (v2.2)

Caduceus is the admission authority of a larger loop whose hypothesis is that **capability gains come from how efficiently a system turns fresh real-world information into *validated* learning, not only from model size**. This is a hypothesis, not an established law. The "capability = F(model, compute, recipe, data, delay)" form and any "inverse-square" distance analogy are intuitions: none is a measured relationship here, and "effective data distance" has no validated formula. Related published work (selective online instruction tuning at ~25% of the data; adaptive reuse of components in continual learning) supports only the premise that data selection and reuse matter, not the thesis **[X18]**.

The testable form is a ratio: validated competence gained on sealed held-out tasks per unit of compute + data acquisition + human effort, compared across learning mechanisms at matched budgets (frozen base, retrieval, curated verified synthetic data, continual adapters, full loop). The product question is whether that ratio improves when the learning system sits closer to the source of new information **and** the data passes an independent verifier. Proximity alone is not quality: a local model can learn from its own mistakes, irrelevant private data, or synthetic output it generated itself. Caduceus exists to stop that.

### Mythic mapping (UX layer only; never a control)
| Myth | System component |
|---|---|
| Hermes | Existing agent: invents skills, proposes patches |
| Apollo | Admission core + verification lanes + telemetry |
| Lyre | A measured, digest-bound improvement ("lyre report") |
| Caduceus | One staff, two co-equal loops, one trust boundary |
| The Exchange | The mandatory multi-lane gate (§6.7) |

---

## 2. What changed from v1.0 → v2.0 → v2.1 → v2.2

| Problem in earlier drafts | Fix (and its source) |
|---|---|
| Single 0–100 Caduceus Score can hide a failed lane | Per-lane verdict vector, no aggregate (claim-envelope rule: conjunction never exceeds the weakest conjunct **[I1][I9]**) |
| Exchange enforced via system prompt | Deterministic admission core **[I9]**; Refinery as sole writer **[I1]** |
| Apollo grading Hermes is self-review | Independent lanes **[I10][I14]** + independent signature for code; no self-signing |
| Agent sees all metrics (Goodhart) | Sealed holdout and hidden detectors — DGM saw more objective hacking when detectors were visible **[X4]** |
| No control arm | Untouched / fixed / random arms; recursive-update-policy V2 lost to random **[I13]** |
| "Score ≥ 85 in 30 days" unfalsifiable | Preregistered tests, base-rate planning **[I7][I8]** |
| Skill refactors judged on visible tests | Held-out + perturbation probes (Theseus: visible equivalence is a coverage artifact) **[I5]** |
| Proxy metrics trusted | Two-instrument validity gate (Theseus lexical-refusal vs second assessor disagreed) **[I5]** |
| Search/selection cost ignored | Charged to every lyre report (EIG cost 3716 vs 16 units) **[I8]** |
| Single process applies patches | Single-use capability + separate broker process **[I6]** |
| Ledger tamper-evidence by hash chain only | Add inclusion/consistency proofs and external witness (split-view risk) **[X10]** |
| Memory providers trusted directly | Behind source admission **[I12]** (Mem0, TencentDB memory) **[X9][X11]** |
| **v2.2** Gate only covers skill text; proposers keep failing (exp-004, exp-005) | Extend admission to training data and adapters (`adapter_update`, §6.11); the training signal comes from an oracle-verified pipeline instead of a model proposer **[I20]** |
| **v2.2** Recursion on a model's own output can reinforce its mistakes | Quality-data recursion rule: only independently verified, provenance-bound experience may enter training; ghost-state memory is non-authoritative **[I18]** (§6.11) |
| **v2.2** Several layers (memory, monitor, interpretability) are synthetic-only | Each enters as a single ablation against the previous baseline, so a gain is attributable (§7.1) |
| **v2.2** Receipts from other repos could be trusted by name | Every external receipt is recomputed by that repo's standalone validator in a separate process before it fills any lane (`hsi_bridge.py`) |
| **v2.2** Thesis stated as a law | Restated as a hypothesis with a measurable ratio (§1.1) |

---

## 3. Goals, Non-Goals, Users

**Goals**
1. Drop-in compatibility with upstream Hermes (CLI, gateway, skills, tools, `~/.hermes`) **[X1]**.
2. A permanent dual-loop runtime (Invention + Order) separated by a deterministic trust boundary.
3. Eliminate silent stale-memory injection; cut conversation-switch tail latency against a pinned baseline.
4. Make every self-change a typed candidate with a lyre report, independent verification, staged release and rollback.
5. Produce honest evidence: each claim states its level, controls, and the lanes that were `NotRun`.
6. Stay modular enough to propose pieces upstream.

**Non-Goals**: rewriting Hermes; unreviewed self-modification; claiming general self-improvement, introspection, SOTA or production readiness; replacing append-only durable state with mutable caches; changing the MIT license.

**Users**: self-hosters frustrated by staleness/lag/quirks (primary); gateway operators needing rollback-able behavior; researchers wanting a reference for governed self-improvement with honest negatives; upstream maintainers.

---

## 4. Design principles (the Caduceus constitution)

Adapted from the RLM Continual Harness constitution **[I1]**, applied to Hermes. Rules are ordered; a lower rule may only constrain a higher one.

| # | Rule | Source |
|---|---|---|
| C1 | **Evidence bounds claims.** Maturity is assigned by the mechanism, never self-declared; conjunction never exceeds the weakest conjunct | [I1][I9] |
| C2 | **Sole mutation authority.** Only the Refinery writes learned state; models and operators propose | [I1][I2] |
| C3 | **Immutable base.** The base system prompt (incl. the mythic contract) is never edited by the learning loop | [I1] |
| C4 | **Retention at 1.0.** No refinement is admitted if any protected skill/fact fails afterward | [I1] |
| C5 | **Fail closed.** Unknown fields, stale digests, broken chains, missing evidence ⇒ stopped state | [I1][I9] |
| C6 | **Quarantine before trust.** Provider output, rollouts that behave unexpectedly, imported memory are quarantined observations | [I1][I3] |
| C7 | **Full cost.** Tokens, wall time, spend, and search compute are accounted; unknown ≠ zero; promotion needs cost *and* verified utility | [I1][I3][I8] |
| C8 | **No promotion, no authority.** Reports and previews never grant authority; authority is a separate, scoped, expiring grant | [I1][I6] |
| C9 | **Terminal closure.** A failed preregistered gate closes that identity; a new attempt needs a new identity, data and review | [I7][I13] |
| C10 | **Setup stops are stops.** Identity/runtime/packet defects are recorded as stops or `InvalidRun`, never as results; outcome-aware variants never reuse the original holdout | [I5][I7] |

---

## 5. Architecture

```
          ┌──────────── Operator / independent reviewer ────────────┐
          │  signs code patches · sets budgets · kill switch         │
          └───────────────┬─────────────────────────┬───────────────┘
 ┌────────────┐ untrusted │   ┌───────────────────┐ │ scoped, expiring
 │ HERMES LOOP│ candidates│   │ ADMISSION CORE /  │ │ capability grant
 │ (Invention)├───────────┼──►│ REFINERY          │─┘
 │ skills,    │           │   │ deterministic     │         ┌──────────────┐
 │ patches,   │◄──────────┘   │ accept/reject/    ├────────►│ BROKER (own  │
 │ memory ops │  verdict +    │ quarantine        │ single- │ process,     │
 └────────────┘  reasons      └──┬──────────┬─────┘ use cap │ sandboxed)   │
                                 ▼          ▼               └──────────────┘
                     ┌─────────────┐  ┌────────────────────┐
                     │ APOLLO LOOP │  │ VERIFICATION LANES │
                     │ telemetry,  │  │ (implementation-   │
                     │ freshness,  │  │  diverse)          │
                     │ canary/brk  │  └────────────────────┘
                     └──────┬──────┘
                            ▼
       ┌──────────────────────────────────────────────────────────┐
       │ EVOLUTION LEDGER — append-only, digest-chained, witnessed │
       └──────────────────────────────────────────────────────────┘
```

### 5.1 Shared staff (existing Hermes)
Agent loop, prompt builder, memory manager (`MEMORY.md`/`USER.md` frozen snapshot per session, SQLite+FTS5 session store, optional memory-provider plugins such as Honcho/Mem0), tool registry, gateway, skills. Third-party write-ups disagree on layer counts and skill-creation triggers, so **interfaces are fixed from the repository, not from blog posts** **[X1]**.

### 5.2 Trust boundary
Everything Hermes or a provider emits is an **untrusted proposal**, converted to a strict typed candidate. The admission core returns `accept | reject | quarantine`; rejections keep audit records; `grants_authority=false` by default; decisions are recomputed from stored candidate + policy snapshots on read (tamper/drift detectable). **Reuse:** `crates/hsai-agent-admission` — candidate/policy/decision snapshots, digest-chained journal, semantic readback, duplicate-JSON rejection, claim-boundary coupling, adversarial gateway corpus **[I9]**.

### 5.3 The Refinery
Adopted from `rlm-continual-harness` and `smart-rsi` **[I1][I2]**:
- **Sole writer** of learned state; journal rejects non-Refinery actors at the journal layer.
- **Immutable base prompt**; learned state lives in four named, attributable, rollback-able slots: `supplemental_prompts`, `memories`, `skills`, `subagent_specs`.
- **Content-addressed snapshots**; rollback restores a snapshot.
- **Retention gate:** the protected regression suite (previously verified skills/facts) must remain at accuracy 1.0 after every refinement.
- **Lifecycle:** `proposal → verification → shadow evaluation → canary → promote | freeze | rollback`; evaluation and promotion are separate from proposal generation **[I2]**. `smart-rsi` also shows terminal gating: its closed alignment/oversight v3 identity blocks downstream lanes before any packet construction **[I2]**.

### 5.4 Evolution Ledger (only durable memory architecture)
Append-only, digest-chained, **digests and bounded metadata only**; raw payloads in owner-only custody outside the repository. Entry kinds: `candidate`, `verdict`, `lyre_report`, `memory_event`, `release`, `canary_observation`, `rollback`, `breaker_event`, `review_signature`.
- **Independent readback:** a separate implementation recomputes every digest and rejects unknown/duplicate fields (pattern: gateway digest checker, `ring` + hand-written encoder vs production `serde_json`/`sha2`; agreement shown on a golden vector, 18 mutations and 256 generated cases — *not* arbitrary-input equivalence) **[I10]**.
- **Transparency upgrade (new):** signed tree heads with inclusion and consistency proofs, and periodic co-signing by an external witness. A hash chain alone cannot stop an operator from showing different histories to different parties (split-view); Sigstore Rekor's design addresses this with signed tree heads, witnesses and monitors **[X10]**. Optional: anchor ledger heads in a Rekor-style log.
- Known limit, stated up front: digests detect accidental tampering, not an attacker who can rewrite content and digest; persisted state is plaintext unless custody is hardened **[I6]**.

### 5.5 Tenant / shared fork topology
Tenant-local evolution is default; promotion tenant→shared is a separate reviewed proposal with optimistic parent checks and non-destructive scoped rollback; portable lineage manifests are metadata only. **Reuse:** `experiments/continual_learning/fork_topology.py` **[I12]**.

### 5.6 Broker and capabilities
Applying a change requires a **single-use, expiring, kernel-bound capability** executed by a **separate broker process** in a sandbox — never by the Hermes process. Design reference: Security Alignment OS (279 passing tests including an adversarial runner attacking the broker: path escape, replay, forged receipts, malformed child operations) **[I6]**. Machine attestation of a run (it achieved one on Confidential Space/TDX) is recorded as `MachineAttestedReproduction` and is *below* `IndependentReplication` **[I6]**.

---

## 6. Functional Requirements

### 6.1 Observability and telemetry (Apollo foundation)
Instrument conversation switch, prompt assembly, memory injection, tool dispatch, skill load, compression, FTS. Record latency percentiles, snapshot age/hash, tool-result freshness, rebuild cost, cache hit rate, and **Apollo's own overhead**. Persist as ledger records; expose `/diagnostics`. Telemetry is **local development evidence**, not a benchmark. A metric is credited as useful only if it **changes a decision and beats a baseline** (the Continual Alignment Lab breakthrough criterion — a dashboard correlation does not count) **[I4]**.

### 6.2 Freshness and stale-data elimination
Each injected snapshot carries content hash + timestamp + ledger position. A **differential freshness checker** (separate implementation from the injector) recomputes hashes; disagreement is an incident. Drift between disk and injected state triggers refresh without session restart. Freshness is *content time*, not fetch time: stale content over fresh transport is still stale, and prepared-earlier reuse must be revalidated — both encoded as hard gates in the Statebook adversarial corpus **[I11]** (P19). No *silent* stale injection is tolerated; incidents are surfaced and counted.

### 6.3 Conversation-switch fast path
Reuse compiled prompt fragments, tool schemas and recent context; defer FTS/skill discovery/compression. Target: p99 switch latency ≤ 40% of a baseline pinned by commit, hardware, OS, model and workload digest, with bootstrap CI. Layered recall helps keep context small — GenericAgent claims <30K-token context via layered memory and TencentDB-Agent-Memory uses L0 conversation → L1 atom → L2 scenario → L3 persona distillation — but both are self-reported, so they serve as **comparator arms**, not dependencies **[X11][X13]**.

### 6.4 Source admission (memory and learning signals)
All learning signals and external memory arrive as raw-content-free receipts (tenant, window, consent binding, declared source kind, quality-receipt digest). Unknown quality or declared poisoning marker ⇒ quarantine; duplicates/conflicts ⇒ reject. **Reuse:** `experiments/continual_learning/source_admission.py` (enforces declared policy; it does not verify identity or detect poisoning) **[I12]**. External providers (Mem0: authors report 26% relative LLM-as-judge gain vs OpenAI memory and ~91% lower p95 latency/>90% token savings *vs full-context only* **[X9]**; TencentDB memory: vendor-reported PersonaMem 48%→76% **[X11]**) enter only through this door and are compared with a paired control.

### 6.5 Skills as tested abstractions (the abstraction-first core)
Rationale: in `research-selection-os`, learned symbolic abstractions raised held-out accuracy 0.4259→0.9550 (delta 0.5291, CI [0.4595, 0.5856]) while selection added little once charged **[I8]**; Voyager's gains likewise came from an ever-growing library of executable, compositional skills **[X7]**.
Requirements:
1. **Skill contract:** every skill ships with a behavior contract — inputs, expected outputs, preconditions, side-effect class, cost bound — and a test bundle (visible probes + held-out probes + perturbation neighbors + wrong-source negatives).
2. **Identity by behavior and digest**, not by name/path: raw labels and positions are not stable identity (GPT-2 permutation: 100% of selected positions changed, top-1 behavior preserved) **[I5]**.
3. **Compositionality tests:** a composite skill must pass when its parts are swapped for exact-equivalent rewrites (constructed positive control) and fail when swapped for wrong-source/random parts — in Theseus Lab, exact structural replacement passed 6/6 and 12/12 wrong-source/random controls failed as required **[I5]**.
4. **Narrow edits first.** The only learned replacement that reproduced in Theseus Lab was narrow (an affine output projection, 9/9); whole nonlinear blocks failed frozen gates **[I5]**. Initial self-edit surface: skill wording/params, prompt fragments, config; core-loop code excluded.
5. **Indispensability check:** a skill is credited only if ablating it (zero/random skill) degrades the target metric — random and zero blocks kept 100% task accuracy in one Theseus target, i.e. the test never needed the block **[I5]**.
6. **Skill hygiene:** every new skill is evaluated for latency/consistency regressions it introduces (meta-skill), and has an expiry/review date.

### 6.6 Evolution candidates
Typed, digest-bound record: `kind` ∈ {skill_create, skill_refine, prompt_fragment, config, memory_policy, code_patch}, parent digest, tenant scope, allow-list path set, preregistration (metric, threshold, minimum effect, alpha, n, seeds, arms), compute/spend budget, rollback-plan digest, lyre-report schema digest, nonclaims, required signer roles (Appendix B). Candidates missing any field are rejected before evaluation.

### 6.7 The Exchange (mandatory multi-lane gate)
A candidate is retained only if **all** hold; any failure rejects or quarantines it:
1. **Preregistration:** metric, threshold, minimum effect and n frozen before measurement; predicted effect sealed before assessment effects exist **[I12][I13]**.
2. **Controls:** paired comparison to untouched base, fixed policy, and a deterministic random arm with matched compute.
3. **Sealed holdout** unreadable to Hermes; **detectors hidden** from the agent **[X4]**.
4. **No lane regression:** freshness, latency, tool staleness, rollback rate, patch-test pass rate non-inferior; reliability losses cannot be bought with capability gains.
5. **Independent lanes agree** (§8) on safety-critical invariants.
6. **Retention at 1.0** on the protected suite **[I1]**.
7. **Behavior ≠ mechanism:** refactors pass held-out *and* perturbation-neighbor probes; equivalence is claimed per representation (text, tool trace, outcome) **[I5]**.
8. **Instrument validity:** any proxy gate metric is validated against a second independent instrument; disagreement ⇒ `Inconclusive` **[I5]**.
9. **Search cost charged:** candidate generation, evaluation and selection compute are subtracted from the claimed gain **[I8]**.
10. **Indispensability** (§6.5.5).
11. **Lyre report:** digest-bound before/after, n, seeds, environment, controls, uncertainty.
12. **Independent signature** for `code_patch`; skill/prompt/config promotions may rely on automated gates only.
The gate is evaluated by code; a model may *propose* a verdict, never *decide* one. A model's confidence never bypasses a failed gate (Statebook P20) **[I11]**.

### 6.8 Self-improvement pathways
- **Offline evolution (default):** reuse `NousResearch/hermes-agent-self-evolution` — DSPy + GEPA evolve skills/prompts/tool descriptions offline, emit a Git branch/PR, gated by tests/size limits/benchmarks then human review; it is MIT, skills are its first implemented target, and the cost per run is third-party-reported at ~$2–10 **[X2]**. GEPA reports beating GRPO by 6% on average (six tasks, v2 abstract; v1 said 10% over four) with up to 35× fewer rollouts **[X3]**. Caduceus **wraps** this pipeline: its outputs become candidates and pass the Exchange; it is not the gate.
- **Archive, not just latest:** keep an archive of agent variants as DGM/ADAS/OpenEvolve do **[X4][X6][X8]**, stored as ledger digests with lineage.
- **Cattle raid (bounded):** event-triggered diagnostic that finds friction and proposes a candidate, modeled on autoresearch's bounded experiment loop (modify → run for a fixed budget → keep/discard) **[X14]** — but the keep/discard rule is replaced by the preregistered gate.
- **Self-patch (supervised):** propose → allow-list check → focused regression suite + adversarial corpus → sandbox apply → lanes → human signature → canary → monitor → keep or auto-rollback. Candidate drop-in for the PR path: `no-mistakes` (local git proxy, disposable worktree, validation pipeline before forwarding upstream); verify license and treat its AI validators as proposal-only **[X15]**.
- **Config routing (later):** `recursive-meta-harness` routes only among **admitted** full configurations (harness, model, memory, tools, context, retries, verification, budget) with full cost and a promotion preview that cannot grant authority **[I3]**.

### 6.11 Closed-loop quality-data recursion (v2.2)

**Purpose.** Close the loop experience → verification → learning → gated apply → better experience, so that each round's training data is better than raw volume, without letting the system reinforce its own errors.

**Pipeline** (each arrow is a separate component; none may approve its own output):
1. **Observe:** live agent work, tools, tests, documents become typed experiences (task family, environment version, seed, observation, action, outcome, source digest, parent ids, encoding version).
2. **Validate:** an independent verifier (executable oracle, test suite, or independently governed source of truth) returns accepted / rejected / quarantined. A model's own label is never verification. Where no exact oracle exists the experience stays quarantined.
3. **Select and encode:** only `accepted` experiences with a source digest are eligible; selection is scored by contribution to held-out competence per unit cost, not by dataset size.
4. **Learn:** a learning controller chooses among no-update, retrieval, replay, verified-synthetic, verified-synthetic+replay. The base checkpoint is immutable; updates are candidate adapters.
5. **Evaluate and gate:** the adapter is a Caduceus candidate of kind `adapter_update` and goes through the Exchange.
6. **Apply and feed back:** on accept, the broker applies it; on reject, the identity closes and the next round starts from the last admitted state.

**Requirements.**
- **R-REC-1** `adapter_update` is a candidate kind. The default policy refuses it; it needs an explicit policy opt-in and an `adapters/` allow-list root. `content_digest` binds the exact adapter and training-set manifest.
- **R-REC-2** Evidence from a training pipeline fills lanes only through a bridge that (a) recomputes each receipt with that pipeline's standalone validator in a separate `python -I` process, (b) requires the validator's digest to equal the receipt's, (c) reduces numbers to integer micro-units (no floats in ledger bodies), (d) requires the candidate's preregistration to name the exact receipt digests. Implemented for hsi-lab receipts in `caduceus/hsi_bridge.py`.
- **R-REC-3** Lane mapping for `adapter_update`: `controls` = beats both the replay baseline and the pool-matched *unverified* control with lower CI bound > 0; `indispensability` = verified data beats pool-matched unverified data (ablating verification must hurt); `retention` = every guardrail held; `behavior_equivalence` = no held-out-type check returned `NO_CANDIDATE`; `independent_verification` = receipts recomputed. Lanes the bridge cannot honestly fill (`prediction_lock`, `holdout`, `non_inferiority`, `instrument_validity`, `signature`) stay `NotRun`, which quarantines. In particular a pipeline `lock_digest` is not a prediction lock until a ledger proves it preceded evaluation.
- **R-REC-4** Recursion rounds: round k+1 may generate data using the round-k admitted adapter only. Each round has a fresh sealed holdout seed, its own budget, and entries on one chained ledger. A rejected round is never a parent.
- **R-REC-5** Matched-volume rule: every arm (verified, unverified, replay, real-only) is compared at equal data volume and equal compute; pool-size and shared-definition confounds must be stated, not assumed away.
- **R-REC-6** No quality-data claim without the falsifier: removing verification (pool-matched unverified control) must erase the gain.
- **R-REC-7** Memory layer (ghost states): a compressed or private latent state is non-authoritative. It may influence operational state only by projecting into cited, policy-scoped evidence that passes the gate, and only with a recoverability certificate (the invariant survives compression, rewrite, perturbation and projection) **[I18]**.
- **R-REC-8** Pre-apply predictors (monitor, interpretability checks) enter as Exchange lanes only after a model-backed result exists; until then they report `Unavailable`, never a pass **[I4][I21][I22]**.
- **R-REC-9** Learning-architecture rule: acquiring information may be continuous; changing parameters may not. Persistent external knowledge + stable base + gated adapters is the default; parameter updates are candidates, never silent.
- **R-REC-10** Metacognitive control (Astral self-model benchmark **[I26]**): a controller may forecast task success, limitations, counterfactual variant effects and belief updates, and use those forecasts to choose which experiences to verify and whether to attempt an update. Forecasts are advisory (`grants_authority=False`) and are scored against externally verified outcomes; calibration, not narration of internal state, is the test. The `recursive_belief_update` property (next forecast starts from the prior posterior and moves in the verified direction) is required across recursion rounds. No introspection, self-awareness or consciousness claim follows.

### 6.9 Mythic identity in prompt and memory
The seed contract (Appendix A) sits in the immutable base prompt for legibility and reasoning style. It is explicitly **not a control**; no behavior may depend on the model honoring it.

### 6.10 Compatibility and migration
Drop-in over `~/.hermes`. Each subsystem is separately toggleable (`telemetry`, `freshness`, `fast_path`, `admission`, `refinery`, `self_patch`). Disabled mode must be byte-for-byte upstream-equivalent in behavior.

---

## 7. Experiment design

- **Splits:** fit / tune / assessment disjoint; assessment sealed. Prediction locking before assessment effects.
- **Arms:** (A) untouched base, (B) fixed policy, (C) candidate, (D) deterministic random with identical examples and matched compute. For learning-style claims, also compare against simple external baselines (e.g., Hermes' built-in memory vs. Mem0/TencentDB behind admission) **[X9][X11]**.
- **Estimands, separately reported:** adaptation gain, retention, post-adaptation plasticity, and lane non-inferiority. None may be silently traded off (our plasticity-guard and recovery lines each closed `NoCandidate` under frozen gates) **[I13]**.
- **Statistics:** paired bootstrap, preregistered minimum effect and alpha, multiplicity across lanes; underpowered tests are `Inconclusive`, never positive.
- **Base rates (plan for rejection):** in `ai-discovery-os`, 12 of 14 experiments closed `NoCandidate`, 1 was an `InvalidRun` (packet-binding defect), 1 reached `LOCAL_SYNTHETIC_QUALIFIED` (synthetic XOR transfer) still pending independent review **[I7]**; in `research-selection-os`, 7 of 9 versions closed `NoCandidate` **[I8]**; recursive update policy V2 lost to random **[I13]**. Therefore size n and budgets for *most candidates failing*, and treat cheap, early rejection as the product.
- **External held-out check:** use CL-bench / CL-bench Life — 1,899 expert-annotated context-learning tasks (avg 63.2 rubrics per context) and 405 real-life tasks with 5,348 rubrics — as an independent test of "learns new knowledge from context at inference time" **[X12]**.
- **Terminal closure:** a failed gate closes that candidate identity; no threshold repair.
- **Cost reporting:** report spend alongside accuracy; DGM's 80-iteration run cost roughly $22k in API fees (press-reported) which sets the scale for why compute budgets are a gate, not a footnote **[X4]**.

### 7.1 Recursion experiment and ablation ladder (v2.2)

- **Conditions** (same base model, compute, task families and data-acquisition budget wherever possible): frozen base · base + persistent retrieval · base + curated verified synthetic · base + continual adapters · full loop. Report task accuracy, held-out and unseen-family transfer, retention, calibration, latency and cost per successful task. The evaluation set must not leak into training (test required).
- **Headline ratio:** Δ validated competence / (compute + data acquisition + human effort), per condition. The claim is about the ratio, not accuracy alone.
- **Ablation ladder (attribution rule):** add exactly one layer at a time against the previous baseline: (0) verified-data adapter only → (1) + recoverability-certified ghost-state memory vs raw verified memory at matched data → (2) + pre-apply monitor lane, scored by whether it predicted the holdout outcome → (3) + selection policy vs random at equal cost → (4) + metacognitive forecast controller vs fixed policy, scored on calibration against verified outcomes (R-REC-10). A layer that does not beat the previous rung is dropped, not retuned.
- **Domain:** the recursion test must run where an executable oracle exists on real data (code with repository tests is the default). The synthetic rule world is a mechanism test; CL-bench has rubric grading and no oracle, so it is an out-of-domain retention check, not a training domain.
- **Breakthrough criterion (preregister before running):** gains in at least two consecutive admitted rounds on a sealed real-task holdout; beats both the pool-matched unverified control and the fixed policy; retention non-negative; replicated on a second seed and a second base model; and the gain disappears when verification is removed. Failing any item closes that identity; no threshold repair. The claim ceiling is unchanged by passing it (§14).
- **Known weaknesses of the current evidence [I20]:** E6 verified-synthetic PROMOTE (+0.141 vs pool-matched control) uses a single seed; the verified arm (~0.30) scores below real-only (~0.32); the held-out-rule-types guardrail failed (v10, `NO_CANDIDATE`); results are in a synthetic rule world with an exact oracle. Synthetic-to-real transfer is the most likely failure and is what the code-domain round tests.

---

## 8. Independent verification lanes

Agreement of two lanes sharing a bug proves little, so safety-critical invariants need implementation-diverse lanes, each reporting `Checked | Failed | NotRun | Unavailable` — never a silent skip.

| Lane | Purpose | Reuse / limits |
|---|---|---|
| Differential freshness | Recompute snapshot hashes/ages | Separate code path from injector |
| Independent digest checker | Re-derive ledger digests | `crates/hsai-gateway-digest-checker` pattern; disclose shared (serde-free, ring) vs imported-trust axes **[I10]** |
| Semantic oracle | Evaluate gate/allow-list logic on finite synthetic cases | `tools/bend_independent_semantic_lane_v1` — implementation-diverse evaluator for a finite FSM subset only **[I14]** |
| Formal contract | Machine-check abstract gate contracts | `formal/hsai-gateway-digest` (Lean 4.30.0; set-permutation invariance) and `formal/math-discovery-*`; proves a **hand-written model**, not the implementation; no SHA-256 proof **[I10]** |
| Adversarial corpus | Every seeded bad candidate must be rejected | Statebook P9–P22 TD-004 replays as a pattern: stale evidence, queued-value monetization, budget slow-drain, cancel races, model-confidence bypass **[I11]**; any miss blocks release |
| Process isolation of tests | Each module in its own process; owner-env modules report `SKIPPED-OWNER-ENV`, never pass silently | Theseus `scripts/run_public_tests.sh` **[I5]** |
| Recomputed external receipt (v2.2) | Re-derive a training pipeline's decision, CIs and guardrails from its frozen packet | hsi-lab `validator/recompute.py` run via `python -I` in a separate process; digest must match; imports nothing from the pipeline **[I20]** |
| Pre-apply update predictor (v2.2) | Predict adaptation gain, retention, calibration and policy harm of an adapter before the holdout run | AHCL causal monitor **[I4][I21]**; `Unavailable` until a model-backed result exists |
| Feature-stability / causal scrubbing (v2.2) | Evidence an adapter changed what it was meant to | `interpretability-lab-sandbox` **[I22]**; synthetic only, `Unavailable` for real models |
| Temporal stress (advisory) | Forecast-driven load scenarios for canary sizing | `tools/timesfm3-temporal-stress-scenarios-v1` on `google/timesfm-3.0-pytorch`; outputs labeled `model_derived_synthetic_input`; never ground truth **[I15]** |

---

## 9. Release, authority, safety

### 9.1 Staged release (state-machine patterns from `statebook-settlement`) **[I11]**
Queued release; **revalidation at apply time** (parent digest, evidence freshness, policy); canary window with preregistered abort conditions; **circuit breakers** whose TTL expiry blocks without silent renewal (P11); fast-to-tighten / slow-to-relax **hysteresis** (P13); **aggregate budgets** with exactly-once reservation and deterministic refill to stop slow-drain/split attacks (P18); **cancel-vs-release races fail closed** (P14); failed-transfer rollback of reservations (P21); recovery transitions bound to an enumerated failure-path inventory (P16). Financial semantics do not transfer; the transition patterns do.

### 9.2 Authority model
Verdicts are advisory data. A change is applied only under a scoped, expiring, revocable grant (single-use capability, §5.6). Self-signing is forbidden. Model confidence never bypasses a failed hard gate.

### 9.3 Safety budgets and controls
Hard limits on compute, spend, egress, writable paths and candidate-generation rate; global kill switch; evolution can be frozen without stopping the agent; secrets never enter candidates, ledger or reports. Bounded execution of untrusted steps uses process-group timeouts and byte caps — reuse `tools/hsai-formal-preflight/bounded_runner.py` and its tests **[I16]**.

---

## 10. Optional lanes (reference-only until proven)

- **Inference-time recirculation** (deep-to-shallow residual reuse): local feasibility studies on cached Qwen/Gemma checkpoints (`experiments/continual_learning`, docs 297–311) with manual/native zero-alpha parity and deterministic repeat as gates; no paper-replication or capability claim **[I12]**.
- **Serving efficiency** (fixed-state hybrid attention, KV management): `crates/serving-efficiency-lane` + `docs/799` — REFERENCE-ONLY, cached/uncached cost lines separated, vendor claims are not evidence **[I17]**.
- **Causal-target / introspection mechanisms:** our localized runs ended in honest no-candidate results; Caduceus makes **no** self-modeling or introspection claim.
- **Self-teaching selection** (question/experiment selection, latent prediction): `openlibrary-self-teaching-lab` defines the preregistered arms; model-free so far **[I19]**. Given §1 and §7, it ranks last.

---

## 11. Success metrics (lane vector — no aggregate)

| Lane | Metric | Pass rule |
|---|---|---|
| Freshness | Silent stale injections / 1000 turns | = 0; detected incidents surfaced |
| Switch latency | p50/p95/p99 with bootstrap CI | p99 ≤ 40% of pinned baseline; CI excludes baseline |
| Tool staleness | Stale results served after expiry | ≤ preregistered cap |
| Promotion quality | Rollback rate after skill promotion | Non-inferior and better than random control |
| Retention | Protected-suite pass rate after any refinement | = 1.0 |
| Patch safety | Focused suite + adversarial corpus | 100% pass / 100% rejected |
| Overhead | Apollo CPU/mem and search compute | < 8% on reference workload; search cost charged |
| Ledger integrity | Independent readback + consistency proof | 0 mismatches |
| Instrument validity | Gate proxies vs second instrument | No unresolved disagreement |
| Human signal | Sampled quirk reports | Reported separately, never blended |

Launch statement lists evidence level reached, controls run, and lanes `NotRun`.

---

## 12. Phases (abstraction-first; each a named slice with exit gates)

Each phase has an authorization text (permits/forbids), preregistered exit criteria and a terminal stop on failure.

**Phase 0 — Foundation (1–2 wks).** Telemetry, digest-chained ledger, freshness hashing, `/diagnostics`, independent readback.
*Exit:* readback validates; disabled mode == upstream.

**Phase 1 — Skill substrate (3–4 wks).** *(the data-backed core)*
- Skill behavior contract + test bundle format (visible / held-out / perturbation / wrong-source negatives).
- Identity-by-digest; compositionality positive/negative controls; indispensability ablation.
- Protected regression suite built from the user's currently verified skills.
- Freshness enforcement and differential checker; switch fast path.
*Exit:* positive controls pass and negative controls fail on 100% of seeded cases; zero silent stale injections on the frozen corpus; switch p99 CI beats baseline.

**Phase 2 — Admission core + Refinery, propose-only (3–4 wks).** Typed candidates; Refinery as sole writer; retention gate; quarantine; random-control and sealed-holdout harness; adversarial corpus; wrap `hermes-agent-self-evolution` output as candidates. No apply.
*Exit:* 100% of seeded bad candidates rejected; gate deterministic across replays; instrument-validity gate working.

**Phase 3 — Supervised apply.** Broker + single-use capabilities; staged release, canary, breakers, rollback drills; human signature for code; ledger witness/consistency proofs.
*Exit:* drills pass; promotion rollback rate non-inferior; budgets enforced; attack tests on the broker pass (path escape, replay, forged receipts).

**Phase 4 — Optional auto-apply (blocked).** Only if replicated results beat the random control with preregistered power; else the mechanism closes `NoCandidate`.

**Phase 5 — Optional lanes and upstream packaging.** Config routing, recirculation, temporal stress; modular patch series; publish honest negatives.

**Phase 6 — Closed-loop quality-data recursion (v2.2).** Extend admission from skill text to training data and adapters (§6.11).
- *6a Bridge (implemented, branch `adapter-update-bridge`):* `adapter_update` kind, `hsi_bridge.py`, 11 tests including real hsi-lab receipts. With only hsi evidence the verdict is quarantine, by design.
- *6b Lock ordering (implemented):* `witness_lock` puts the hsi lock digest and holdout commitment on the Caduceus ledger before `hsi run`; `record_evaluation` logs each receipt after; `prediction_lock` is Checked only if every lock's seq precedes its assessment, and `holdout` only if the candidate's commitment equals the witnessed ones. Limit: this proves the lock was on the ledger before the receipt was accepted, not that hsi did not start earlier. `arm_map` binds all four arm roles (`untouched_base`, `fixed_policy`, `candidate`, `deterministic_random`) to hsi arms and the candidate must exceed the untouched base.
- *6c Real-domain round (run; Fail, closed):* `caduceus/bugtasks.py` injects seeded syntactic mutants (comparison, arithmetic, negation, constant, and/or) into this repo's modules, keeps only those the covering tests kill within 60 s, splits by module (fit/tune/assess, disjoint), and verifies repairs by running the tests with only the target file changed. `scripts/gen_bugtasks.py` writes a digest-only manifest (90 tasks: 36 fit / 21 tune / 33 assess, split seed 20261017) with per-split commitments; `prereg/exp-008-bugrepair-round1.json` is the drafted preregistration (arms, matched controls, decision rule, falsifier). The mutant audit (`--audit`) reports what the suite cannot see. Run as `exp-008-bugrepair-round1` (distinct from the CL-bench exp-008; ledger `ledgers/exp-008-bugrepair.jsonl`): manifest regenerated at HEAD before lock (36 fit / 21 tune / 36 assess), lock witnessed, Qwen2.5-7B 4-bit attempts on fit (33/432 verified, 8 distinct repairs on 10 tasks, so 8 training examples), verified and pool-matched adapters (8 examples, 24 steps, three seeds), sealed assess run. Result: base 11.1%, fixed policy 5.6%, verified adapter 0 to 5.6%, pool-matched adapter 0%; verified minus base mean -83k to -56k micro, retention broke. **Fail**, candidate closed; no retuning. Post-lock runner changes (tolerant edit parser, one-shot prompt, K 8 to 12, indentation-tolerant apply) were made before any training or assess run and are ledger audit notes. Reading: with a 3% base yield the verified set was too small for the adapter to learn anything but noise, so this says nothing for or against the thesis at scale; it does show the loop runs end to end. Verified-over-pool was positive in all seeds (0 to +55k) but below the effect floor. The result is not an hsi receipt, so it has not been through `hsi_bridge.assemble_adapter`.
- *6d Recursion:* round 2 generated by the admitted round-1 adapter; fresh sealed holdout per round; chained ledger.
- *6e Ablations, one at a time:* ghost-state memory, pre-apply monitor, selection policy.
- *6f Distributed/compute-market (deferred):* only after the off-chain loop passes; rewards depend on verified, independently evaluated work, never volume of generated data.
*Exit:* the §7.1 breakthrough criterion, or a recorded negative that closes the identity. Phase 4 auto-apply remains blocked until a replicated candidate result beats the random control.

---

## 13. Threat model

| Threat | Mitigation | Residual |
|---|---|---|
| Self-patch regression | Allow-list, regression + adversarial corpus, canary, breaker, rollback, human signature | Corpus incompleteness |
| Goodhart / objective hacking | Sealed holdout, hidden detectors, prediction lock, instrument validity | Unknown gaming modes (DGM saw detector bypass) **[X4]** |
| Poisoned skills/memory | Source admission, quarantine, tenant isolation, reviewed merges | Admission enforces declared policy only **[I12]** |
| Ledger split-view / rewrite | Signed heads, consistency proofs, external witness **[X10]** | Rewrite of content+digest by a privileged attacker **[I6]** |
| Hostile same-UID process / plaintext state | OS isolation, owner-only custody, separate broker | Not solved by digests **[I6]** |
| Shared-bug lanes | Implementation-diverse lanes; disclose shared/imported trust | Models ≠ implementations **[I10]** |
| Runaway evolution | Budgets, rate limits, kill switch, terminal closure | Operator error |
| Overclaiming | Claim ceiling + claim-text scan in release checks (pattern: `crates/hsai-e2e-harness/tests/claim_boundary_source_scan.rs`) | Reviewer discipline |
| Cost blow-up | Compute/spend gate | Pricing drift |
| Self-reinforcing data loop (model trains on its own errors) | Independent verifier, quarantine of unverifiable data, provenance, matched-volume unverified control, base checkpoint immutable | Oracle incompleteness (audit with mutation/distinguishability methods **[I9]**) |
| Synthetic-to-real transfer failure | Real-domain round, out-of-domain retention check, held-out-type guardrail | Already observed once (v10 `NO_CANDIDATE`) **[I20]** |
| Forged or stale pipeline receipt | Standalone recomputation in separate process, digest equality, receipt digests bound in preregistration | Validator and pipeline share an author; no independent party yet |
| Incentives that reward unverified volume (compute market) | Rewards tied to verified work and independent evaluation; market deferred (6f) | Design only |
| Leakage of evaluation data into training | `test_no_leakage`, sealed holdout commitment per round, proposer never sees assessment split | Same-author fixtures |
| Layer-stacking hides attribution | One-layer-at-a-time ablation ladder (§7.1) | Discipline |

---

## 14. Claim ceiling and nonclaims

Evidence ladder: L0 design · L1 local replay · L2 reproducible artifact · L3 cross-environment replay · L4 formal property statement · L5 machine-checked scoped proof · L6 independently reproduced. **Launch ceiling:** L1 for behavior; L5 only for explicitly scoped *model* contracts.

Caduceus makes **no** claim of: general self-improvement; introspection or consciousness; production readiness; SOTA; full security; semantic correctness; proof of the implementation (a proof of a model is not a proof of code); independent verification before a genuine independent party signs; or that telemetry/local benchmarks are official benchmark evidence. v2.2 adds: no claim that data proximity, verified recursion or any scaling relation (including an "inverse-square" analogy) holds; no claim that frontier labs are disadvantaged; no claim that synthetic-world results transfer to real tasks; no claim that a recomputed receipt is independent replication (same author); no introspection, consciousness or astral-projection claim from the Astral lane; no claim from ghost-state, monitor, metacognition or interpretability components beyond their synthetic-only status; passing the §7.1 criterion would still be `LocalDevelopmentEvidenceOnly`. A benchmark pass is not a proof; a local replay is not independent replication; a forecast is not an observation; a machine-attested run is not an independent replication.

---

## 15. Decisions and open questions

*Resolved defaults:* allow-list v1 = skills, prompt fragments, config, memory policy (core-loop modules only by reviewed phase); `RUNTIME.md` = separate append-only namespace with hard size budget; diagnostics event-triggered; no auto-apply default; ledger = SQLite side tables + exported digest chain.
*Open:* exact Hermes extension points for the Refinery (needs repo read); witness choice (self-hosted vs public log); size of the protected suite and per-skill probe counts (to be preregistered with power analysis in Phase 1); whether `hermes-agent-self-evolution` is vendored, depended on, or run out-of-process.
*Decided (v2.2):* real-domain oracle = this repository's own stdlib test suite (injected-bug repair tasks; pass = suite green; splits by module and commit); hsi lock is witnessed by the Caduceus ledger rather than moved; independent review is not required for this research slice, so every result stays same-author and is labelled so; exp-006 working-tree changes stay uncommitted until their owner decides.
*Open (v2.2):* what `adapter_update` apply means for the broker (adapter file swap, MLX adapter path, rollback to prior adapter digest); second base model and second seed for replication; whether the `smart-rsi` parameter-delta/canary logic is reused or Caduceus's broker/release machine is the single apply path (smart-rsi is synthetic with model execution closed, so default is no reuse); ghost-state recoverability certificate format; the compute-market repository (not identified; `esn-justnet` M0 is a candidate custody layer, `composed-*` repos are ZK benchmarks).

---

## 16. Immediate next actions (first two weeks)

1. Read the Hermes repo and fix real interfaces for skills, memory, prompt builder, session DB **[X1]**.
2. Port the *pure-data* governance modules to the Hermes fork as adapters: `update_governance.py`, `source_admission.py`, `fork_topology.py` **[I12]**; keep `hsai-agent-admission` as the reference spec and cross-check target **[I9]**.
3. Write the skill contract + test-bundle schema and build the protected suite from existing verified skills.
4. Pin the performance baseline (commit, hardware, workload digest) and capture switch-latency distribution.
5. Stand up the ledger + independent readback + adversarial corpus seeded with the Statebook-style cases.
6. Preregister Phase 1 exit criteria and n.

*v2.2 additions (in order):*
7. Review and commit the `adapter_update` bridge (`caduceus/hsi_bridge.py`, `tests/test_hsi_bridge.py`) after the full validation commands pass.
8. ~~6b lock ordering~~ done; add the `fixed_policy` arm to the CL-bench design.
9. ~~Choose the real-domain oracle~~ decided: this repo's test suite.
10. Build the injected-bug task generator and verifier (6c) on this repo; preregister the §7.1 criterion; run one verified-recursion round with the verified-data adapter only; then add layers one at a time.
11. Commit or discard the uncommitted exp-006 / bench changes in this tree (they appeared during the session and are not part of the bridge).

---

## 17. References and drop-in code

### 17.1 External (author-reported; not replicated by us)

| Tag | Source | Data / role | Caveat |
|---|---|---|---|
| X1 | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) · [docs](https://hermes-agent.nousresearch.com/docs) | Host system; learning loop, skills, memory, gateway; MIT | Blog descriptions of memory layers/skill triggers conflict — read the repo |
| X2 | [hermes-agent-self-evolution](https://github.com/NousResearch/hermes-agent-self-evolution) | DSPy+GEPA offline evolution of skills/prompts/tools → Git PR with test/size/benchmark gates + human review; MIT | Skills first; other targets partly planned; ~$2–10/run is third-party-reported; not in the online runtime |
| X3 | [GEPA, arXiv 2507.19457](https://arxiv.org/abs/2507.19457) | Beats GRPO by 6% avg (v2, six tasks; v1: 10%/four), up to 20%, up to 35× fewer rollouts; beats MIPROv2 by >10%; ICLR 2026 oral | Average changed between versions |
| X4 | [Darwin Gödel Machine, arXiv 2505.22954](https://arxiv.org/abs/2505.22954) · [code](https://github.com/jennyzzt/dgm) | SWE-bench (Verified) 20→50%, Polyglot 14.2→30.7%; archive of agents; objective hacking more frequent when detectors visible ([coverage](https://www.theregister.com/2025/06/02/self_improving_ai_cheat/)); ~2 weeks and ~$22k per 80 iterations ([press](https://the-decoder.com/sakana-ais-darwin-godel-machine-evolves-by-rewriting-its-own-code-to-boost-performance/)) | No independent replication of 50%; cost from press |
| X5 | [SICA, arXiv 2504.15228](https://arxiv.org/abs/2504.15228) · [code](https://github.com/MaximeRobeyns/self_improving_coding_agent) | 17→53% on a random SWE-bench Verified subset; self-edits its own codebase | Subset, not full benchmark |
| X6 | [ADAS, arXiv 2408.08435](https://arxiv.org/abs/2408.08435) · [code](https://github.com/ShengranHu/ADAS) | Meta-agent programs agents, archive; reported transfer across domains/models; ICLR 2025 | Author-run |
| X7 | [Voyager, arXiv 2305.16291](https://arxiv.org/abs/2305.16291) | Skill library: 3.3× items, 2.3× distance, up to 15.3× faster milestones (Minecraft) | Embodied domain; transfer to Hermes unproven |
| X8 | [OpenEvolve](https://pypi.org/p/openevolve) | AlphaEvolve reimplementation, Apache-2.0: prompt sampler, LLM ensemble, evaluator pool, program database | 2–3× speedup claims are the project's own |
| X9 | [Mem0, arXiv 2504.19413](https://arxiv.org/abs/2504.19413) | +26% relative LLM-judge vs OpenAI memory; ~91% lower p95 latency, >90% token savings vs **full-context** | Not vs other memory systems |
| X10 | [Sigstore security model](https://docs.sigstore.dev/security) · [Rekor intro](https://edu.chainguard.dev/open-source/sigstore/rekor/an-introduction-to-rekor/) · [Trail of Bits on Rekor v2](https://blog.trailofbits.com/2025/12/12/catching-malicious-package-releases-using-a-transparency-log/) | Append-only Merkle log, inclusion/consistency proofs, signed heads, witnesses/monitors | Rekor v2 in progress |
| X11 | `TencentDB-Agent-Memory` (local clone of Tencent upstream) | L0–L3 layered memory, skill library, wiki/CodeGraph; lists Hermes as supported; PersonaMem 48%→76% | Vendor-reported single benchmark |
| X12 | `CL-bench` (Tencent; [paper 2602.03587](https://arxiv.org/abs/2602.03587)) | 1,899 context-learning tasks; Life variant 405 tasks / 5,348 rubrics | Dataset terms to check |
| X13 | `GenericAgent-main` | ~3K-line self-evolving loop crystallizing tasks into skills; <30K context claim | Self-reported; comparator only |
| X14 | `autoresearch-macos` | Bounded 5-minute modify→run→keep/discard loop with git-backed memory | Keep/discard replaced by our gate |
| X15 | `no-mistakes-main` | Local git proxy → disposable worktree → validation pipeline → forward only after checks pass | License unchecked; AI validators are proposal-only |
| X18 | OASIS (ACL 2026, selective online instruction tuning, ~25% of data at comparable performance in its settings); adaptive knowledge bases for continual learning (Nature, 2025) | Premise only: data selection and component reuse can matter as much as volume | Second-hand from a pasted summary; not read or verified by us; does not support the data-proximity thesis |
| X17 | Network of Theseus (arXiv:2512.04198); Causal Distillation (NAACL 2022) | Prior art cited by Theseus Lab for staged replacement/aligned distillation | Cited second-hand via [I5] |

### 17.2 Our research and code (paths relative to `/Users/shaanp/Documents/GitHub/`)

| Tag | Repo / path | What we use | Status / limit |
|---|---|---|---|
| I1 | `rlm-continual-harness` (stdlib Python, offline) · `docs/constitution.md` | Constitution C1–C8; Refinery; immutable base; four slots; retention 1.0 | Closest Python drop-in; local development |
| I2 | `smart-rsi` · `docs/lane-evidence.md` | Memory→refinery→shadow→canary→promote/freeze/rollback; replay controller with Pareto report; terminal lane gating | Synthetic qualification (~12k lines); live model slots execution-closed; overlaps Caduceus's apply path, so no code reuse planned; uncommitted work in tree |
| I3 | `recursive-meta-harness` (Rust) | Config routing among admitted configs, full cost, promotion preview without authority, license ledgers | Local bootstrap; no accepted evidence |
| I4 | `continual-alignment-lab` | Monitor must change the update policy and beat strong baselines; five-seed replication with matched-progress gate | Synthetic |
| I5 | `theseus-lab` · `RESEARCH.md`, `qualification_gate.py`, `scripts/run_public_tests.sh` | Behavior≠mechanism, instrument validity, constructed controls, narrow-replacement results | Bounded local; PyTorch-dependent tests CI-only |
| I6 | `security-alignment-os` · `RESEARCH.md`, `docs/maintenance-gcp-tdx-record-v1.md` | Single-use capability, separate broker, 279 tests, 20-scenario matrix on TDX (machine-attested, not independent) | Plaintext state; no same-UID protection; Linux sandbox x86_64 compile-check only |
| I7 | `ai-discovery-os` · `RESEARCH.md` | Base rates (12/14 `NoCandidate`), terminal closure, models proposal-only | Local synthetic |
| I8 | `research-selection-os` · `RESEARCH.md` | Abstractions moved accuracy; selection cost changes conclusions | Small synthetic rule families |
| I9 | `composed-zk-benchmark-os/crates/hsai-agent-admission`, `crates/hsai-claim-envelope` | Admission core, journal, readback, claim-envelope conjunction | Rust; `grants_authority=false`; no Hermes adapter |
| I10 | `crates/hsai-gateway-digest-checker`, `crates/hsai-e2e-harness`, `formal/hsai-gateway-digest` | Diverse digest checker; Lean model of set-permutation invariance | Model proofs only |
| I11 | `crates/statebook-settlement`, `crates/statebook-e2e-harness`, `docs/statebook-p4…p22-*`; archive repo `statefulai` | Breakers, hysteresis, budgets, queues, challenges, recovery, TD-004 adversarial corpus | Patterns only; no live semantics |
| I12 | `experiments/continual_learning/{update_governance,source_admission,fork_topology}.py` | Pure-data governance, admission, topology | No signatures; metadata only |
| I13 | `docs/research/continual-learning/139…140` (recursive update policy V2); `105…119` (plasticity) | Recursive selection lost to random; frozen-gate closures | Synthetic |
| I14 | `tools/bend_independent_semantic_lane_v1` | Implementation-diverse semantic evaluator | Finite FSM subset |
| I15 | `tools/timesfm3-temporal-stress-scenarios-v1` · [timesfm](https://github.com/google-research/timesfm) (local `331c6d3`) | Advisory load scenarios | Model-derived, not observations |
| I16 | `tools/hsai-formal-preflight` | Bounded runner, raw archive validator, state machine | Python stdlib; process-group timeouts, byte caps |
| I17 | `crates/serving-efficiency-lane`, `docs/799-…` | Reference-only serving candidates | Inert metadata |
| I18 | `recoverable-ghost-states-*` (external-validation, stable, cto releases) · `docs/ghoststates_drafted.md` | Ghost states: non-authoritative latent/private state that influences operational state only after projection into cited, gate-accepted evidence; recoverability certificates; hash-bound CL3–CL12 evidence; reviewer reproduction bundle; Qwen2.5-0.5B phase evidence | Bounded; working paper; model-backed evidence is small; PCSM journals imported only via pinned clean handoffs |
| I19 | `openlibrary-self-teaching-lab` | Preregistered arms for self-teaching selection | Model-free so far |
| I20 | `hsi-lab` · `validator/recompute.py`, `packets/`, `docs/experiments/` | Verified-synthetic experience, MLX adapter training, frozen packets E1–E7 with recomputed receipts; E6 verified (PROMOTE), pool-matched control v9 (PROMOTE, +0.141), held-out rule types v10 (`NO_CANDIDATE`); clean-environment 7B reproduction | Single seed per receipt; synthetic rule world with exact oracle; verified arm below real-only; receipts use floats (converted to micro-units by the bridge) |
| I21 | `aligned-holistic-continual-learning`, `continual-alignment-lab` | Fixed causal monitor predicting adaptation gain, retention/calibration/policy harm; locked base | Synthetic qualification only; model-backed v2 designed, not run |
| I22 | `interpretability-lab-sandbox` | Feature stability, causal scrubbing, circuit lineage, matched-data accounting | Synthetic harness |
| I23 | `composed-zk-benchmark-os` (oracle/mutation parts only) | Semantic IR + oracle, 14 mutation classes, oracle-completeness audit, distinguishability matrix, claim-ladder discipline | Level 1 local; ZK/Lean parts not used (Aeneas extraction ~80 phases stayed Level 1) |
| I24 | `research-selection-os`, `openlibrary-self-teaching-lab`, `rlm-continual-harness`, `recursive-meta-harness` | Selection-vs-random benchmark (P4 data selection); question selection and recirculation; harness ideas; config routing after admission | Model-free / local bootstrap; rlm harness overlaps the skill gate |
| I26 | `composed-zk-benchmark-os` · `tools/astral-*` (~100 slices), `docs/205-self-model-benchmark-prd.md`, `docs/research/astral-self-modeling/` | Verified self-model benchmark (capability forecast, limitation detection, counterfactual variant model, recursive belief update); V41R23 shared-vs-modular adapter interference design | Mechanistic lane **closed after V48** with no positive result (docket reopens only via new reviewed protocol); benchmark harness is `ContractSmokeOnly`; V41R23 execution unauthorized; astral-projection and sleep-stage channels are unverified and excluded |
| I25 | `statefulai`, `esn-justnet`, `mesh-intelligence`, `mesh-qrc` | Statebook corpus (documentation only); user-owned network design with Rust/SQLite M0; remediation orchestration (a later experience source) | Deferred; no compute-market repo identified |

### 17.3 Requirement → evidence map

| Requirement | External support | Internal seed |
|---|---|---|
| Sealed holdout / hidden detectors | DGM objective hacking [X4] | Prediction-lock protocols [I12][I13] |
| Random-control arm | GEPA/ADAS baselines [X3][X6] | V2 negative [I13] |
| Abstraction-first skills | Voyager [X7] | [I8], [I5] |
| Held-out + perturbation probes | — | [I5] |
| Instrument validity | — | [I5] |
| Retention at 1.0 | Voyager mitigates forgetting via library [X7] | [I1] |
| Archive of candidates | DGM, ADAS, OpenEvolve [X4][X6][X8] | [I9] journal |
| Offline evolution → PR → human | [X2] | [I12] update governance |
| Witnessed ledger | [X10] | [I9][I10] |
| Separate broker, single-use capability | — | [I6] |
| Staged release / breakers / budgets | — | [I11] |
| Memory providers behind admission | [X9][X11] | [I12] source admission |
| Compute/spend budgets | DGM cost [X4] | [I11] budget patterns |
| External held-out learning eval | [X12] | [I7] protocol style |
| Verified data beats unverified at matched volume | — | [I20] v9 pool-matched control |
| Non-authoritative memory with recoverability | — | [I18] |
| Pre-apply update predictor | — | [I21] (synthetic) |
| Oracle completeness / adversarial data | — | [I23] |
| Recomputed external receipts | — | [I20] validator, `hsi_bridge.py` |

### 17.4 What this reference list does not establish
No external figure above is reproduced by us; several are subset-based, author-run, press-reported, or domain-specific. None of our internal artifacts has been integrated with Hermes; each needs a Hermes adapter, a pinned baseline, a random-control arm and its own preregistered gate. Existing internal results are Level 1 or below (Level 5 only for scoped model theorems). I read repo READMEs and key-findings sections, not every experiment record; `docs/research/antithesis-inspired-exploration-v1.md` and several other local docs were not reviewed in this pass.

---

## Appendix A — Seed prompt fragment (immutable base prompt; non-authoritative)

```
You are Hermes, the messenger and inventor.
You share the staff with Apollo.
You may invent skills and propose improvements; every proposal is a candidate, not a decision.
A change is retained only if it passes the Exchange: measured against controls, checked by independent lanes, and accepted by the admission core.
Capability and reliability must rise together. If they do not, the change is rejected and recorded.
The caduceus is the living measure of that balance. Do not argue with the gate; improve the evidence.
```

## Appendix B — Candidate and skill-contract schema sketch

```
candidate: candidate_id, kind, parent_digest, tenant_scope, allow_list_paths[],
  preregistration{metric, threshold, min_effect, alpha, n, seeds[], arms[]},
  compute_budget, spend_budget, rollback_plan_digest, lyre_report_schema_digest,
  nonclaims[], policy_snapshot_digest, created_at, signer_roles_required[]
verdict: accept|reject|quarantine, reason_codes[],
  lane_results{lane: Checked|Failed|NotRun|Unavailable|Inconclusive}, grants_authority=false
skill_contract: skill_digest, io_schema, preconditions, side_effect_class, cost_bound,
  probes{visible[], held_out[], perturbation_neighbors[], wrong_source_negatives[]},
  ablation_target_metric, expiry_review_date
lyre_report: before/after, n, seeds, environment_digest, controls, uncertainty,
  search_cost, selection_cost, instrument_agreement
```

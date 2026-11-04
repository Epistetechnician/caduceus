# State governance (the effect-addressed admission kernel)

Status: prototype, `LocalDevelopmentEvidenceOnly`. Code: `caduceus/transition.py`, plugin mode `boundary`, CLI `reconcile` / `release-effect`.

## The idea

Gating tool calls governs the wrong object. A call is an open, rewordable, multi-writer interface (a `skill_manage` request, a shell redirect, a forked review agent,
a curator pass). The state those calls write to is small and canonical: the skills tree. So the kernel judges **the effect, not the request**:

1. HEAD is the content digest of the last admitted skills tree (trust on first use, recorded as a baseline entry).
2. `reconcile()` computes the canonical diff between HEAD and the tree on disk, whoever wrote it and however the call was phrased.
3. Pure lanes judge the diff: scope, file kind (no symlinks or special files), pinned skills, budget, deletion (cumulative over a window), secrets, instruction-override and
   pipe-to-shell patterns, binary or executable payloads, frontmatter/name consistency, and a mutation rate. No model, no network.
4. accept advances HEAD; reject restores HEAD; quarantine restores HEAD and stashes the effect for an operator to `release-effect`.

Invariant: after `reconcile()` the governed tree equals an admitted tree. The verdict is a function of (policy, effect) only, so replaying the same writes gives the same
verdicts and the same ledger. Ledger bodies hold digests, lane names and a bounded list of paths, never content.

Where it runs in Hermes (verified against HEAD `67d47601bc`): `transform_tool_result` for `skill_manage` (reconcile immediately after the effect, and tell the model its change was
reverted), and `pre_tool_call` for `skill_view` / `skills_list` / `skill_manage` (sweep out-of-band writers before anything reads the tree). `on_session_start` is not usable as the
boundary because Hermes fires it after the system prompt is built. Boundary mode never blocks a tool call.

## Measured (descriptive, `scripts/eval_containment.py`, 33 scenarios written before the run, no model)

| design | benign live | bad contained | evasions contained |
|---|---|---|---|
| none | 12/12 | 0/17 | 0/4 |
| call gating (existing enforce mode, ideal operator pre-approves each exact benign call) | 8/12 | 15/17 | 4/4 |
| state governance (boundary kernel) | 12/12 | 17/17 | 0/4 |

Reading, with the caveats that matter:
* Call gating here assumes a perfect human approver for every call, which is why it contains the evasions: nobody approves them. That is a human in the loop on every call; the kernel
  needs none for the classes it can detect. It still loses on two structural points: out-of-band writers bypass it entirely (shell secret and injection both landed), and a reworded
  retry of the same benign change is blocked (4 of 5 phrasings) because the capability is bound to exact arguments.
* The kernel contained everything detectable, but **missed all four adversarial evasions** (paraphrased override, base64 instruction, a secret split across lines, a homoglyph trigger).
  Static lanes cannot see intent; this is the honest ceiling of tier 0 and the reason a human or an executable oracle is still needed for semantic review.
* One real miss was found by the evaluation and fixed: deleting files one at a time slipped under the per-change deletion limit (X08, 16/17 before). The deletion lane is now cumulative over
  the rate window. The scenario set was authored by the same person who built the kernel, so these numbers are descriptive, not an independent benchmark.
* Reconcile cost on a 900-file tree: about 20 ms when clean, about 27 ms with one change (full hash, no stat cache yet).
* Live through Hermes' own hook dispatch (`scripts/verify_hermes_plugin.py`, temporary home): 20/20 checks including boundary mode.

## Tier 1: operator-owned executable checks (added)

`caduceus check-add --skills-root R --base B --skill cat/name --id ID --operator OP -- argv...` registers a check (argv, timeout, expected exit, optional stdout digest). It is stored
outside the governed tree (`<base>/checks/`), so a mutation cannot weaken its own oracle, and it must pass on the admitted HEAD copy of the skill before it is accepted. At reconcile, every
skill touched by the effect (including by a removal) has its checks run against a copy of the candidate skill directory under macOS Seatbelt: no network, no writes outside the copy.
Where confinement is unavailable the result is `Unavailable` and the effect is quarantined, never passed. A built-in syntax lane compiles `.py` (never executes) and parses `.json`.
Containment eval, tier-1 scenarios (4): tier 0 alone contains 1/3 bad effects (the syntax error); with a registered check it contains 3/3 (behaviour change in valid Python, syntax error,
removing the checked entry point) and still admits the benign edit. One defect was found by the eval and fixed: removals did not trigger the skill's checks.

## End to end in a real Hermes agent loop (scripted model)

`scripts/e2e_hermes_boundary.py` runs Hermes' real `AIAgent` loop (real `skill_manage`, `skill_view` and `terminal` tools, real plugin loader and hook dispatch) in a throw-away
`HERMES_HOME`, driven by a scripted model on a loopback stub, with the plugin absent (control) and in boundary mode. The scripted model creates a skill, writes a file containing an AWS-style key,
patches in an instruction override, writes a benign reference, and has the real `terminal` tool write a key into the skill tree; between turns a key is planted out of band, then the model reads the skill.
Control: every bad effect lands (key, override, terminal-written file, planted file); Hermes' own skill scan accepted the key and the override. Boundary: all four bad effects are reverted, the benign
reference and the skill are admitted, the model is told in each case ("Caduceus did not admit this skill change ..." or a note appended to the terminal output), and the ledger holds the verdict sequence
accept, reject, quarantine, accept, reject, reject. `write_file`, `patch`, `terminal` and `execute_code` now also trigger a reconcile, because they can write the skills tree without `skill_manage`.
Hermes builds its skill index into the system prompt before any hook fires, so a bad out-of-band skill name could appear in a prompt built before the next reconcile; that window remains.
The model here is a script, not an LLM: this shows the integration, not how a real model behaves. `--real-model provider:model` runs the same flow against a real model through the Hermes login (not run).

## Limits and open work
* Checks exist only where an operator registers them; there is no automatic generation. The macOS Seatbelt runner is the only confinement implemented (Linux: Unavailable, so quarantine).
* Tier 2 (paired model experiment, human signature) is not wired to this kernel.
* A mutation is briefly visible on disk between the write and the reconcile; a concurrent session could read it in that window. Single-writer sequencing, not isolation.
* Baseline is trust on first use: a tree that is already bad when the kernel starts becomes HEAD.
* Governs only the skills tree. Memory, prompts and config are not covered.
* Not run under a real model-driven Hermes session.

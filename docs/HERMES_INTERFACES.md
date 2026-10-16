# Hermes interfaces (verified against a local checkout)

Checked read-only against `~/.hermes/hermes-agent` (HEAD `b20cc5f787`; the
working tree had unrelated uncommitted edits in `agent/tool_dispatch_helpers.py`,
`agent/transports/chat_completions.py` and one desktop test, none touched here).
Re-verify before relying on any line; Hermes moves fast.

| Fact | Where | Consequence for Caduceus |
|---|---|---|
| "Per-conversation prompt caching is sacred": no mutating past context, swapping toolsets, or rebuilding the system prompt mid-conversation | `AGENTS.md` (top) | **PRD 6.2/6.3 amended:** no mid-session refresh. Freshness is checked at session boundaries (see `freshness.py`); drift inside a session is *expected*, not an incident |
| Core is a narrow waist; capability belongs at the edges. Footprint ladder: extend code → CLI+skill → service-gated tool → **plugin** → MCP → core tool | `AGENTS.md` "The Footprint Ladder" | Caduceus ships as a **standalone plugin + CLI**. It adds no model tool and no core edit |
| Third-party products must be standalone plugin repos installed into `~/.hermes/plugins/`, not added under `plugins/` | `AGENTS.md` contribution rubric | This repo is that standalone repo |
| New behavioral settings go in `config.yaml`, not new `HERMES_*` env vars | `AGENTS.md` | The plugin reads a small `mode.json` in its own directory; folding this into `config.yaml` is an open item (needs the plugin-settings API) |
| `pre_tool_call` hook: callback `(tool_name, args, task_id, **kw)`; may return `{"action":"block","message":...}`, `{"action":"approve",...}` (escalate to human approval) or `{"action":"modify","args":...}`; **times out fail-closed** (default `plugins.hook_callback_timeout` 30s) | `hermes_cli/plugins.py` (`VALID_HOOKS`, `_HOOK_TIMEOUT_FAIL_CLOSED_HOOKS`), `website/docs/user-guide/features/hooks.md` | A real, documented gate point exists with no core change. The plugin uses `block`; `approve` is the planned path for human signatures on risky patches |
| Plugin shape: directory with `plugin.yaml` (`name, version, description, author, hooks`) and `register(ctx)` calling `ctx.register_hook(...)` | `plugins/disk-cleanup/` | `plugin/caduceus_governance/` mirrors it |
| `skill_manage` tool: call is an `operations` array; each op has `name` and `action` ∈ {create, patch, delete, write_file, remove_file}; atomic across ops; legacy flat shape still accepted | `tools/skill_manager_tool.py` (`SKILL_MANAGE_SCHEMA`) | Every `skill_manage` call is a mutation; the plugin gates the tool by name |
| Skills: `~/.hermes/skills/<category>/<name>/SKILL.md` with frontmatter (`name`, `description`, `version`, `metadata.hermes.tags`, ...) plus optional reference files | `skills/*/*/SKILL.md` | `skills.py` hashes the whole directory tree for identity |
| `~/.hermes/skills/.usage.json`: per-skill `use_count`, `patch_count`, `state` (active/stale/archived), `created_by`, timestamps | live install | Source for protected-suite selection (`audit.py`) |
| **A mutation ledger already exists**: `~/.hermes/skills/.curator_ledger.jsonl`, every mutation of any actor, before/after file manifests with content-addressed blobs and single-edit rollback. Documented as **"telemetry, not a gate"**; not hash-chained | `tools/skill_ledger.py` | Do not duplicate it. Caduceus (a) anchors it with a Merkle root so rewrites of the prefix are detectable, and (b) adds the missing *gate* |
| Curator: archives, never auto-deletes; pinned skills bypass transitions; uses an auxiliary client and never touches the main prompt cache | `agent/curator.py` | Matches Caduceus' rollback-not-delete posture; reuse `hermes curator restore` semantics |
| **Background review already writes directly**: a forked agent after each turn "should any skill/memory be saved or updated?" and "writes go straight to the memory + skill stores" | `agent/background_review.py` | This is the ungoverned path Caduceus targets. Gating it via `pre_tool_call` on `skill_manage` needs no core change |
| **The review fork does route through `pre_tool_call`** (verified 2026-10-15). The fork's tool whitelist is enforced inside `_dispatch_pre_tool_call_hooks`, the same function that then invokes plugin hooks, so every whitelisted fork call (incl. `skill_manage`) reaches them | `agent/background_review.py` (`set_thread_tool_whitelist`), `hermes_cli/plugins.py` (`_get_pre_tool_call_directive_details`), `agent/agent_runtime_helpers.py` (`_invoke_tool`) | Confirmed dynamically by `scripts/probe_background_review.py`: real `_run_review_in_thread` against a 127.0.0.1 stub; observe mode records the fork's call with the matching args digest, enforce mode blocks it and no skill is written |
| `AIAgent` has no temperature or seed parameter; one-shot mode preloads skills via `build_preloaded_skills_prompt` as `ephemeral_system_prompt`; `enabled_toolsets=[]` means no tools | `run_agent.py`, `hermes_cli/oneshot.py`, `model_tools.py` | `executor.py` mirrors one-shot; sampling is recorded as `provider_default`, not pinned |
| Memory providers: ABC with `initialize, system_prompt_block, prefetch, sync_turn, get_tool_schemas, handle_tool_call, shutdown`, hooks incl. `on_memory_write`; one external provider at a time | `agent/memory_provider.py` | Mem0/TencentDB-style memory enters only through this ABC; Caduceus observes via `on_memory_write` (future) |
| MEMORY.md / USER.md injected as a frozen snapshot at session start | `agent/memory_provider.py`, docs | Basis for the freshness semantics above |

## Not yet verified
* The exact plugin settings API (for moving `mode.json` into `config.yaml`).
* How `approve` surfaces in the gateway vs. CLI for human signatures.

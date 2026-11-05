# Draft: request for a pre-index skill seam (not filed)

For an issue on NousResearch/hermes-agent via the catalog's "Asking for a hook" route (R13).

**Need.** A plugin that governs the skills tree (Caduceus) reconciles the tree on `pre_tool_call` for `skill_view`, `skills_list` and `skill_manage`, and after writer tools. Hermes builds the skill index
into the system prompt before any of those hooks fire, so a skill written out of band between turns can appear in a prompt built before the next reconcile.

**Ask.** A hook fired once per prompt build, before the skill index is read (for example `pre_prompt_build`, kwargs `session_id`, return ignored), so a plugin can restore a governed tree first.

**Concrete consumer.** `caduceus-governance` (this repository), `plugin/caduceus_governance`; measured window and the e2e that exhibits it are in `docs/STATE_GOVERNANCE.md`.

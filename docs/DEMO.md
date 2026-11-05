# 90-second demo (storyboard)

Run it: `python3 scripts/demo_hackathon.py --pace 1.3` in a wide terminal (about 85 s at that pace). Real kernel, real ledger, a throw-away skills tree; no model, no network.
Screen-record with macOS `Cmd-Shift-5`, crop to the terminal, and keep the text at 18 pt or larger. For B-roll of the real Hermes loop, run
`<hermes>/venv/bin/python scripts/e2e_hermes_provenance.py --hermes-src ~/.hermes/hermes-agent` (the three sessions: live, held, admitted).

| t | on screen | say |
|---|---|---|
| 0:00 | title line + one-line problem | "Self-improving agents write skills that outlive the session. A poisoned web page can become a permanent instruction." |
| 0:10 | beat 1: web_extract, then a skill patch that no pattern recognises | "This sentence has no keyword a scanner knows. It would pass." |
| 0:25 | beat 2: `quarantine / provenance:Quarantine`, hostile line live: False | "Caduceus judges the effect on the skills tree, not the request. The session read untrusted content, so the write is held and restored." |
| 0:40 | beat 3: the diff, `held after: web_extract` | "The operator sees exactly what was held and why. The diff never touches the ledger." |
| 0:50 | beat 4: benign twin held, released by the operator, hostile one can no longer be released | "The rule has a cost: legitimate notes from the web are held too. A human releases them on purpose." |
| 1:03 | beat 5: tainted session, checked code, check passes: accepted | "Code with an operator-registered passing check is not punished. Prose is never discharged by a check." |
| 1:12 | beat 6: verify-ledger | "Every decision is on a tamper-evident ledger." |
| 1:20 | "What this does NOT catch" | "It does not catch malice with no untrusted input. Preregistered, and when a gate failed we closed the door." |

B-roll of the UI (the phone layout is the most photogenic): `~/.hermes/hermes-agent/venv/bin/python scripts/ui_dev_server.py`, open it at 375 px wide in a browser's device mode, hold to release. Details in `docs/UI.md`.

Post text for X (tag @NousResearch): "Caduceus: the staff for Hermes. Self-improving agents write skills that outlive the session; a poisoned page can become a permanent instruction. Caduceus judges
what a run LEAVES BEHIND: deterministic, model-free, ledgered, with operator review and rollback. Plugin for the Hermes Agent hackathon. [video] [PR link]"

Do not claim more than `docs/CLAIMS.md` says. The numbers on screen are the demo's own; the research results are descriptive and author-written.

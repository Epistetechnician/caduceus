# First read-only audit of a real Hermes home (counts only)

Command: `python -m caduceus audit --hermes-home ~/.hermes` (2026-10-15). It reads
files and writes nothing; names, bodies and digests are deliberately omitted
here. Re-run it for the full JSON.

| Measure | Value |
|---|---|
| Skills discovered / unreadable | 136 / 0 |
| By `created_by` | agent 11, installed 1, unset 124 (bundled or pre-tracking) |
| By lifecycle state | active 26, stale 58, unknown 52 |
| Distinct-content duplicate groups | 0 |
| Protected-suite candidates (`use_count >= 3`, not archived) | 29, of which 20 have `patch_count == 0` |
| Most-patched candidate | 123 patches (very heavily used too) |
| Curator ledger entries | 43 (create 2, patch 20, write_file 21); actors: curator 41, agent 2 |
| Curator ledger hash-chained | **no** (anchored by Caduceus: 43 entries) |
| Skills with a Caduceus contract | **0 / 136** |

Reading it:
* Hermes' mutation log exists and is well-formed (no malformed or duplicate
  entries) but is tamper-evident only per file blob, not as a sequence; the
  Merkle anchor fixes that going forward.
* The 20 heavily used, never-patched skills are the lowest-risk first targets for
  contracts: stable behavior, plenty of usage evidence.
* The single most-patched, most-used skill is the highest-risk candidate for a
  governed-evolution pilot, and the one where a retention gate matters most.
* This is an inventory, not an evaluation of any skill's quality.

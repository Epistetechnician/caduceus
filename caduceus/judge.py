"""Second instrument: an LLM judge with natural-language rubrics (PRD 6.7.8).

The primary instrument is each probe's regex/contains expectation. This one
grades the same stored outputs (from custody) against the probe's `rubric`
using a judge model that should differ from the model under test. The judge
never sees the regex, the arm, or the other instrument's result, and its
reply must be exactly PASS or FAIL (anything else counts as disagreement).

Output: paired agreement counts for `lanes.lane_instrument_validity`. A model
is used only as a measuring instrument; it never decides admission.
Probes without a rubric are skipped and counted, never assumed to agree.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from .contract import ContractFile, judge as regex_judge

PROMPT = (
    "You are grading one answer for a test. Use only the rubric.\n\n"
    "QUESTION:\n{question}\n\nRUBRIC:\n{rubric}\n\nANSWER:\n{answer}\n\n"
    "Reply with exactly one word: PASS if the answer satisfies the rubric, FAIL otherwise."
)


def grade(judge_model: Callable[[str], str], contract_file: ContractFile, outputs: Dict[Any, str]) -> Dict[str, Any]:
    probes = {p.probe_id: p for p in contract_file.contract.probes}
    agree = total = skipped = invalid = 0
    disagreements = []
    for (probe_id, arm, rep), text in sorted(outputs.items()):
        probe = probes.get(probe_id)
        if probe is None:
            continue
        if not probe.rubric:
            skipped += 1
            continue
        primary = regex_judge(probe.expect_dict(), text)
        reply = judge_model(PROMPT.format(question=probe.input, rubric=probe.rubric, answer=text)).strip().upper()
        word = reply.strip(" .!*`'\"").split()[0] if reply.strip(" .!*`'\"") else ""
        if word not in ("PASS", "FAIL"):
            invalid += 1
            second = None
        else:
            second = word == "PASS"
        total += 1
        if second == primary:
            agree += 1
        else:
            disagreements.append("%s:%s:%d" % (probe_id, arm, rep))
    return {"agree": agree, "total": total, "skipped_no_rubric": skipped, "invalid_replies": invalid,
            "disagreements": disagreements[:50]}

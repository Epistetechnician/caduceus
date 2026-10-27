"""Repair prompt/edit format and the preregistered decision rule for exp-008 (Phase 6c).

No model is called here. A proposer answers with one SEARCH/REPLACE block; the
block is applied to the buggy source and the result goes to the executable
oracle in `bugtasks.verify_repair`. Statistics are integer micro-units with a
seeded paired bootstrap, so a verdict is reproducible from the recorded outcomes.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .digest import domain_digest

SYSTEM = ("You repair one injected bug in a Python file. The file's unit tests currently fail. "
          "Reply with exactly one edit block and nothing else, no code fences. Copy the SEARCH lines exactly "
          "from the file, include at least one full line, and keep the block small. Example reply:\n"
          "<<<<<<< SEARCH\n    if count > limit:\n=======\n    if count >= limit:\n>>>>>>> REPLACE")
OPEN, MID, CLOSE = "<<<<<<< SEARCH\n", "\n=======\n", "\n>>>>>>> REPLACE"
MICRO = 1000000


def user_prompt(view: Dict[str, Any]) -> str:
    head = "File: %s\nFailing tests: %s\n\n" % (view["path"], " ".join(view["tests"]))
    if view.get("spec"):
        head += "Intended behaviour (from the fix commit):\n%s\n\nSource:\n" % view["spec"]
    return head + view["source"]


def format_edit(search: str, replace: str) -> str:
    return OPEN + search + MID + replace + CLOSE


def _diff_edit(text: str) -> Optional[Tuple[str, str]]:
    """Fallback: a unified-diff-style reply ('-'/'+' lines) read as one search/replace pair."""
    minus, plus = [], []
    for ln in text.splitlines():
        if ln.startswith(("---", "+++", "@@")):
            continue
        if ln.startswith("-"):
            minus.append(ln[1:])
        elif ln.startswith("+"):
            plus.append(ln[1:])
    if not minus or not plus:
        return None
    return "\n".join(minus), "\n".join(plus)


def parse_edit(text: str) -> Optional[Tuple[str, str]]:
    """The first well-formed block, or None. Code fences and prose outside the block are ignored;
    a unified-diff-style reply is accepted when no block is present."""
    text = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("```"))
    a = text.find(OPEN)
    if a < 0:
        return _diff_edit(text)
    if a < 0:
        return None
    a += len(OPEN)
    m = text.find(MID, a)
    if m < 0:
        return None
    c = text.find(CLOSE, m + len(MID))
    if c < 0:
        return None
    return text[a:m], text[m + len(MID):c]


def apply_edit(source: str, search: str, replace: str) -> Optional[str]:
    """Apply only if the search text matches exactly once and the edit changes something.
    Exact text first; failing that, a unique whole-line match ignoring indentation and
    trailing space, with the replacement re-indented to the matched block."""
    if not search or search == replace:
        return None
    if source.count(search) == 1:
        return source.replace(search, replace)
    lines, want = source.split("\n"), [ln.strip() for ln in search.split("\n")]
    hits = [i for i in range(len(lines) - len(want) + 1) if [ln.strip() for ln in lines[i:i + len(want)]] == want]
    if len(hits) != 1 or not any(want):
        return None
    i = hits[0]
    rep = replace.split("\n")
    first = next((ln for ln in rep if ln.strip()), "")
    delta = (len(lines[i]) - len(lines[i].lstrip())) - (len(first) - len(first.lstrip()))
    rep = [(" " * delta + ln if delta > 0 else ln[-delta:] if ln[:-delta].strip() == "" else ln) if ln.strip() else ln for ln in rep]
    out = lines[:i] + rep + lines[i + len(want):]
    return "\n".join(out) if "\n".join(out) != source else None


def diff_edit(buggy: str, fixed: str) -> Optional[Tuple[str, str]]:
    """Minimal line-level edit turning `buggy` into `fixed` (for building training targets)."""
    a, b = buggy.splitlines(), fixed.splitlines()
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    j = 0
    while j < min(len(a), len(b)) - i and a[-1 - j] == b[-1 - j]:
        j += 1
    if i == len(a) == len(b):
        return None
    lo, hi_a, hi_b = max(0, i - 1), len(a) - j, len(b) - j  # one line of context keeps the search unique more often
    search, replace = "\n".join(a[lo:hi_a]), "\n".join(b[lo:hi_b])
    return (search, replace) if apply_edit(buggy, search, replace) is not None else None


def bootstrap(diffs: Sequence[int], seed: int, resamples: int = 10000) -> Dict[str, int]:
    """Paired bootstrap of the mean of per-task differences (each in micro-units)."""
    n = len(diffs)
    if n == 0:
        return {"n": 0, "mean_micro": 0, "lower_micro": 0, "upper_micro": 0}
    rng = random.Random(seed)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) // n for _ in range(resamples))
    return {"n": n, "mean_micro": sum(diffs) // n, "lower_micro": means[int(resamples * 0.025)],
            "upper_micro": means[int(resamples * 0.975) - 1]}


def contrasts(passes: Dict[str, Dict[str, int]], seed: int, resamples: int = 10000) -> Dict[str, Dict[str, int]]:
    """passes[arm][task_id] in {0,1}; candidate minus each other arm."""
    ids = sorted(passes["candidate"])
    out = {}
    for arm in ("untouched_base", "fixed_policy", "deterministic_random"):
        assert sorted(passes[arm]) == ids, "arms must cover the same tasks"
        out[arm] = bootstrap([(passes["candidate"][t] - passes[arm][t]) * MICRO for t in ids], seed, resamples)
    return out


def decide(per_seed: List[Dict[str, Dict[str, int]]], retention_ok: bool, min_effect_micro: int) -> Dict[str, Any]:
    """The exp-008 rule: Pass needs, in >=2 seeds, every contrast lower >= min/2 and mean >= min and retention;
    Fail if any contrast mean < 0 or retention breaks; otherwise Inconclusive."""
    good = sum(1 for c in per_seed
               if all(v["lower_micro"] >= min_effect_micro // 2 and v["mean_micro"] >= min_effect_micro for v in c.values()))
    neg = any(v["mean_micro"] < 0 for c in per_seed for v in c.values())
    if neg or not retention_ok:
        verdict = "Fail"
    elif good >= 2:
        verdict = "Pass"
    else:
        verdict = "Inconclusive"
    return {"verdict": verdict, "seeds_meeting_rule": good, "any_negative_mean": neg, "retention_ok": retention_ok}


def outcome_digest(records: Sequence[Dict[str, Any]]) -> str:
    return domain_digest("caduceus.bugrepair.outcomes.v1", sorted(records, key=lambda r: (r["arm"], r["seed"], r["task_id"])))

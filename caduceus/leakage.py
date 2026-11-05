"""Holdout leakage account (PRD R15): price what a sealed holdout told the proposer, in bits, on the ledger.

Every verdict released from a sealed holdout to a proposer narrows the set of systems the proposer could still output (at most 2**bits of them), so a gain measured on
that holdout is optimistic unless the bits are charged. The account is a run of ledger entries (hash-chained with the rest of the ledger, so the total cannot be edited
down); bodies hold integers and digests only. The certificate is the classical union bound (Hoeffding, range 2) over the cheaper of two routes: the verdict bits released
(information route) or the description length of the shipped system (Occam route). Nothing here claims a new inequality; see holdout-ledger for the experiments that
motivated it. All outputs are integers (parts per million), rounded in the conservative direction.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from .ledger import Ledger

EVENT = "holdout_bits_released"
PPM = 1_000_000
MAX_BITS_PER_RELEASE = 1_000_000


class LeakageError(Exception):
    pass


def release(ledger: Ledger, commitment: str, bits: int, verdict_digest: str, n: int) -> Dict[str, Any]:
    """Record that `bits` bits of verdict about the holdout `commitment` (n examples) were released to a proposer. Called BEFORE the proposer sees the verdict."""
    for name, v in (("bits", bits), ("n", n)):
        if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > MAX_BITS_PER_RELEASE or (name == "n" and v == 0):
            raise LeakageError("%s must be a bounded positive integer" % name)
    if not (isinstance(commitment, str) and len(commitment) == 64 and isinstance(verdict_digest, str) and len(verdict_digest) == 64):
        raise LeakageError("commitment and verdict_digest must be 64-hex digests")
    prev = account(ledger, commitment)
    if prev["queries"] and prev["n"] != n:
        raise LeakageError("holdout size changed mid-account")
    return ledger.append("audit_note", {"event": EVENT, "commitment": commitment, "bits": bits, "verdict_digest": verdict_digest, "n": n})


def account(ledger: Ledger, commitment: str) -> Dict[str, int]:
    rows = [e["body"] for e in ledger.find("audit_note") if e["body"].get("event") == EVENT and e["body"].get("commitment") == commitment]
    return {"bits": sum(r["bits"] for r in rows), "queries": len(rows), "n": rows[-1]["n"] if rows else 0}


def radius_ppm(bits: int, n: int, delta_ppm: int, routes: int = 1) -> int:
    """Union-bound radius for 2**bits systems at confidence 1 - delta over `routes` events, rounded up."""
    if n <= 0 or not 0 < delta_ppm < PPM or bits < 0 or routes < 1:
        raise LeakageError("invalid radius arguments")
    r = math.sqrt(2.0 * (bits * math.log(2.0) + math.log(routes * PPM / delta_ppm)) / n)
    return int(math.ceil(r * PPM))


def certify(gain_micro: int, n: int, delta_ppm: int, bits_released: int, description_bits: Optional[int] = None) -> Dict[str, Any]:
    """Lower bound (ppm) on the true paired gain: gain minus the radius of the cheaper valid route, two routes charged. description_bits=None means no description route."""
    routes = 2 if description_bits is not None else 1
    charged = bits_released if description_bits is None else min(bits_released, description_bits)
    rad = radius_ppm(charged, n, delta_ppm, routes)
    return {"lower_micro": gain_micro - rad, "radius_micro": rad, "charged_bits": charged,
            "route": "information" if description_bits is None or bits_released <= description_bits else "description",
            "naive_lower_micro": gain_micro - radius_ppm(0, n, delta_ppm, 1)}


def budget_lane(ledger: Ledger, commitment: str, budget_bits: Optional[int]) -> Optional[Dict[str, str]]:
    """None when no budget is preregistered (the holdout lane is unchanged). Otherwise Failed once the released bits exceed the budget."""
    if budget_bits is None:
        return None
    if isinstance(budget_bits, bool) or not isinstance(budget_bits, int) or budget_bits < 0:
        return {"status": "Failed", "detail": "holdout_budget_bits must be a non-negative integer"}
    acct = account(ledger, commitment)
    if acct["bits"] > budget_bits:
        return {"status": "Failed", "detail": ("holdout information budget exceeded: %d > %d bits over %d queries" % (acct["bits"], budget_bits, acct["queries"]))[:200]}
    return {"status": "Checked", "detail": ("holdout information within budget: %d <= %d bits over %d queries" % (acct["bits"], budget_bits, acct["queries"]))[:200]}

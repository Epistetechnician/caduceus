"""Staged release state machine (PRD 9.1), patterns from statebook-settlement.

    queued -> revalidated -> canary -> promoted
       \\           \\           \\-> rolled_back
        \\-> rejected  \\-> rejected
    any non-terminal -> cancelled

Rules enforced here, each fail-closed:
  * Apply-time revalidation: parent digest, evidence freshness (max age) and
    policy digest must still match what was admitted.
  * Canary window with preregistered abort conditions: any observation that
    crosses an abort threshold rolls back; promotion needs the full window.
  * Circuit breakers: a tripped breaker blocks until its TTL expires; expiry
    never silently re-arms (the breaker stays `open` until explicitly reset
    by an operator event). Tightening is immediate, relaxing needs
    `relax_after` consecutive clean windows (hysteresis).
  * Aggregate budget with exactly-once reservation per release id; refill is
    deterministic per period; a failed release returns its reservation.
  * Cancel-vs-release race: a release that was cancelled can never be
    promoted, and a promote after cancel raises.
All transitions are ledgered (`release`, `canary_observation`,
`breaker_event`, `rollback`). Times are integer seconds from an injected clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .ledger import Ledger

STATES = ("queued", "revalidated", "canary", "promoted", "rolled_back", "rejected", "cancelled")
TERMINAL = frozenset({"promoted", "rolled_back", "rejected", "cancelled"})
_NEXT = {
    "queued": {"revalidated", "rejected", "cancelled"},
    "revalidated": {"canary", "rejected", "cancelled"},
    "canary": {"promoted", "rolled_back", "cancelled"},
}


class ReleaseError(Exception):
    pass


@dataclass
class Breaker:
    name: str
    ttl_s: int
    relax_after: int = 2
    state: str = "closed"  # closed | open
    opened_at: int = 0
    clean_windows: int = 0

    def blocks(self, now: int) -> bool:
        # An open breaker blocks; TTL expiry alone does not close it.
        return self.state == "open"

    def may_reset(self, now: int) -> bool:
        return self.state == "open" and now >= self.opened_at + self.ttl_s and self.clean_windows >= self.relax_after


@dataclass
class Budget:
    """Aggregate units per period; exactly-once reservation by release id."""

    capacity: int
    period_s: int
    used: int = 0
    period_start: int = 0
    reservations: Dict[str, int] = field(default_factory=dict)

    def _roll(self, now: int) -> None:
        if now >= self.period_start + self.period_s:
            # Deterministic refill: period boundaries are multiples of period_s.
            self.period_start = now - (now % self.period_s)
            self.used = sum(self.reservations.values())

    def reserve(self, release_id: str, units: int, now: int) -> None:
        self._roll(now)
        if units <= 0:
            raise ReleaseError("reservation must be positive")
        if release_id in self.reservations:
            raise ReleaseError("budget already reserved for %s" % release_id)
        if self.used + units > self.capacity:
            raise ReleaseError("budget exhausted")
        self.reservations[release_id] = units
        self.used += units

    def settle(self, release_id: str) -> None:
        self.reservations.pop(release_id, None)

    def refund(self, release_id: str) -> None:
        units = self.reservations.pop(release_id, 0)
        self.used = max(0, self.used - units)


@dataclass(frozen=True)
class CanaryPlan:
    window: int  # observations required before promotion
    abort_if: Tuple[Tuple[str, int], ...]  # (metric, max allowed value), integers only


@dataclass
class Release:
    release_id: str
    candidate_digest: str
    parent_digest: str
    policy_digest: str
    evidence_at: int
    plan: CanaryPlan
    units: int
    state: str = "queued"
    observations: List[Dict[str, int]] = field(default_factory=list)


class ReleaseManager:
    def __init__(self, ledger: Ledger, clock: Callable[[], int], budget: Budget,
                 breakers: Dict[str, Breaker], max_evidence_age_s: int):
        self.ledger, self.clock, self.budget = ledger, clock, budget
        self.breakers = breakers
        self.max_evidence_age_s = max_evidence_age_s
        self.releases: Dict[str, Release] = {}

    # -- helpers ------------------------------------------------------------
    def _move(self, r: Release, to: str, **extra) -> None:
        if r.state in TERMINAL:
            raise ReleaseError("%s is terminal (%s)" % (r.release_id, r.state))
        if to not in _NEXT[r.state]:
            raise ReleaseError("illegal transition %s -> %s" % (r.state, to))
        self.ledger.append("release", dict({"release_id": r.release_id, "from": r.state, "to": to,
                                            "candidate_digest": r.candidate_digest, "at": self.clock()}, **extra))
        r.state = to

    def _blocking(self) -> List[str]:
        now = self.clock()
        return sorted(b.name for b in self.breakers.values() if b.blocks(now))

    # -- lifecycle ----------------------------------------------------------
    def enqueue(self, r: Release) -> Release:
        if r.release_id in self.releases:
            raise ReleaseError("duplicate release id")
        if r.plan.window < 1 or not r.plan.abort_if:
            raise ReleaseError("canary plan needs a window and abort conditions")
        self.budget.reserve(r.release_id, r.units, self.clock())
        self.releases[r.release_id] = r
        self.ledger.append("release", {"release_id": r.release_id, "from": "", "to": "queued",
                                       "candidate_digest": r.candidate_digest, "at": self.clock(), "units": r.units})
        return r

    def revalidate(self, release_id: str, current_parent: str, current_policy: str) -> str:
        r = self.releases[release_id]
        problems = []
        if current_parent != r.parent_digest:
            problems.append("parent_changed")
        if current_policy != r.policy_digest:
            problems.append("policy_changed")
        if self.clock() - r.evidence_at > self.max_evidence_age_s:
            problems.append("evidence_stale")
        blocking = self._blocking()
        if blocking:
            problems.append("breaker_open:" + ",".join(blocking))
        if problems:
            self._move(r, "rejected", reasons=problems)
            self.budget.refund(release_id)
            return "rejected"
        self._move(r, "revalidated")
        return "revalidated"

    def start_canary(self, release_id: str) -> None:
        r = self.releases[release_id]
        if self._blocking():
            raise ReleaseError("breaker open")
        self._move(r, "canary")

    def observe(self, release_id: str, metrics: Dict[str, int]) -> str:
        r = self.releases[release_id]
        if r.state != "canary":
            raise ReleaseError("not in canary")
        if any(not isinstance(v, int) or isinstance(v, bool) for v in metrics.values()):
            raise ReleaseError("canary metrics must be integers")
        r.observations.append(dict(metrics))
        self.ledger.append("canary_observation", {"release_id": release_id, "n": len(r.observations), "metrics": metrics})
        tripped = [m for m, limit in r.plan.abort_if if m not in metrics or metrics[m] > limit]
        if tripped:
            self.trip("canary", "abort:" + ",".join(tripped))
            self._move(r, "rolled_back", reasons=["canary_abort:" + m for m in tripped])
            self.budget.refund(release_id)
            return "rolled_back"
        return "canary"

    def promote(self, release_id: str) -> None:
        r = self.releases[release_id]
        if r.state == "cancelled":
            raise ReleaseError("cancelled release cannot be promoted")
        if r.state != "canary" or len(r.observations) < r.plan.window:
            raise ReleaseError("canary window incomplete")
        if self._blocking():
            raise ReleaseError("breaker open")
        self._move(r, "promoted", observations=len(r.observations))
        self.budget.settle(release_id)

    def cancel(self, release_id: str, reason: str) -> None:
        r = self.releases[release_id]
        self._move(r, "cancelled", reasons=[reason[:100]])
        self.budget.refund(release_id)

    # -- breakers -----------------------------------------------------------
    def trip(self, name: str, reason: str) -> None:
        b = self.breakers[name]
        b.state, b.opened_at, b.clean_windows = "open", self.clock(), 0  # tighten immediately
        self.ledger.append("breaker_event", {"breaker": name, "event": "open", "reason": reason[:100], "at": self.clock()})

    def clean_window(self, name: str) -> None:
        b = self.breakers[name]
        if b.state == "open":
            b.clean_windows += 1

    def reset(self, name: str, operator: str) -> None:
        b = self.breakers[name]
        if not b.may_reset(self.clock()):
            raise ReleaseError("breaker %s cannot relax yet (ttl/hysteresis)" % name)
        b.state, b.clean_windows = "closed", 0
        self.ledger.append("breaker_event", {"breaker": name, "event": "reset", "operator": operator[:64], "at": self.clock()})

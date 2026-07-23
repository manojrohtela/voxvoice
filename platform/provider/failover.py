"""FailoverController (M5.6B-2) — deterministic failover. No retry loops.

Given a `RoutingDecision`'s ordered candidate chain and the set of names already attempted,
selects the next candidate — **provider selection only**, never a retry loop (no backoff, no
repeated attempts against the same candidate, no sleeping). The caller (the runtime's
intelligence path) drives the bounded walk down the chain; this controller only answers "what
next, given what's been tried."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .router import RoutingDecision


class FailoverReason(StrEnum):
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class FailoverOutcome:
    candidate: str | None
    exhausted: bool = False
    attempted: tuple[str, ...] = field(default_factory=tuple)
    reason: FailoverReason | None = None


class FailoverController:
    def __init__(self) -> None:
        self._stats = {"selections": 0, "failovers": 0, "exhausted": 0}

    def next(
        self, decision: RoutingDecision, *, attempted: tuple[str, ...], reason: FailoverReason,
    ) -> FailoverOutcome:
        """The next untried candidate in `decision.candidates`, or exhausted if none remain.
        Pure selection — no side effects beyond the local selection/failover/exhaustion
        counters. The very first pick (empty `attempted`) is a **selection**, not a failover;
        every subsequent pick (a prior candidate having failed/been excluded) is a failover."""
        tried = set(attempted)
        remaining = [c for c in decision.candidates if c not in tried]
        if not remaining:
            self._stats["exhausted"] += 1
            return FailoverOutcome(candidate=None, exhausted=True, attempted=attempted,
                                   reason=reason)
        if attempted:
            self._stats["failovers"] += 1
        else:
            self._stats["selections"] += 1
        return FailoverOutcome(
            candidate=remaining[0], exhausted=False,
            attempted=(*attempted, remaining[0]), reason=reason,
        )

    @property
    def statistics(self) -> dict[str, int]:
        return dict(self._stats)

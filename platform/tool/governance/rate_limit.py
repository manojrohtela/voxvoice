"""Rate limiting (M6.1B-2) — interfaces + a deterministic fixed-window evaluator.

No timers, no background sweeps, no distributed cache/Redis — the caller supplies
`now` on every call (same "caller-driven, deterministic" convention as
`ToolRuntime.expire`). `RateLimitStore` is the only seam holding mutable counters;
the default `InMemoryRateLimitStore` is a plain dict, evaluation only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

from .domain import RateLimitDecision, RateLimitRule, RateLimitScope


@dataclass(frozen=True, slots=True)
class RateLimitState:
    count: int = 0
    window_start: float = 0.0


def evaluate_rate_limit(
    rule: RateLimitRule, state: RateLimitState, now: float
) -> tuple[RateLimitDecision, RateLimitState]:
    """Pure function: fixed-window counter. A window rolls over once `now` has
    advanced `window_s` past `window_start`; `limit <= 0` is unbounded."""
    if now - state.window_start >= rule.window_s:
        state = RateLimitState(count=0, window_start=now)

    unbounded = rule.limit <= 0
    allowed = unbounded or state.count < rule.limit
    new_state = replace(state, count=state.count + 1) if allowed else state
    remaining = -1 if unbounded else max(rule.limit - new_state.count, 0)
    retry_after = 0.0 if allowed else max(rule.window_s - (now - state.window_start), 0.0)
    reason = "" if allowed else f"rate limit exceeded for {rule.key!r}"
    decision = RateLimitDecision(
        allowed=allowed, remaining=remaining, limit=rule.limit, scope=rule.scope, key=rule.key,
        retry_after_s=retry_after, reason=reason,
    )
    return decision, new_state


class RateLimitStore(ABC):
    @abstractmethod
    def get(self, scope: RateLimitScope, key: str) -> RateLimitState: ...
    @abstractmethod
    def put(self, scope: RateLimitScope, key: str, state: RateLimitState) -> None: ...


class InMemoryRateLimitStore(RateLimitStore):
    def __init__(self) -> None:
        self._states: dict[tuple[RateLimitScope, str], RateLimitState] = {}

    def get(self, scope: RateLimitScope, key: str) -> RateLimitState:
        return self._states.get((scope, key), RateLimitState())

    def put(self, scope: RateLimitScope, key: str, state: RateLimitState) -> None:
        self._states[(scope, key)] = state


class RateLimitEngine:
    """Ties a `RateLimitStore` to the pure evaluator. `check` peeks (no mutation);
    `consume` evaluates and persists the advanced counter."""

    def __init__(self, store: RateLimitStore | None = None) -> None:
        self._store = store or InMemoryRateLimitStore()

    def check(self, rule: RateLimitRule, now: float) -> RateLimitDecision:
        state = self._store.get(rule.scope, rule.key)
        decision, _ = evaluate_rate_limit(rule, state, now)
        return decision

    def consume(self, rule: RateLimitRule, now: float) -> RateLimitDecision:
        state = self._store.get(rule.scope, rule.key)
        decision, new_state = evaluate_rate_limit(rule, state, now)
        self._store.put(rule.scope, rule.key, new_state)
        return decision

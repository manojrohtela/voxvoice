"""CircuitBreaker (M5.6B-2) — deterministic. Injected clock only; no timers.

Classic CLOSED → OPEN → HALF_OPEN → CLOSED state machine, per adapter name. The "reset
timeout" is evaluated by comparing an **injected clock** reading against the moment the
breaker opened — never a background timer/thread/sleep. Tracks a failure count against a
threshold; a single failure while HALF_OPEN reopens immediately (conservative, mirrors the
Reasoning/Provider health FSM's conservative-recovery stance).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitBreakerPolicy:
    failure_threshold: int = 5
    reset_timeout_s: float = 30.0
    half_open_max_calls: int = 1


class CircuitBreaker:
    def __init__(
        self, policy: CircuitBreakerPolicy | None = None,
        *, clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._policy = policy or CircuitBreakerPolicy()
        self._clock = clock or time.time
        self._state: dict[str, BreakerState] = {}
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._half_open_calls: dict[str, int] = {}

    def state_for(self, name: str) -> BreakerState:
        return self._state.get(name, BreakerState.CLOSED)

    def allow(self, name: str, *, now: float | None = None) -> bool:
        """May a call to `name` proceed right now? Evaluates the reset timeout against the
        injected clock — no background timer."""
        now = self._clock() if now is None else now
        state = self.state_for(name)
        if state is BreakerState.CLOSED:
            return True
        if state is BreakerState.OPEN:
            if now - self._opened_at.get(name, 0.0) >= self._policy.reset_timeout_s:
                self._state[name] = BreakerState.HALF_OPEN
                self._half_open_calls[name] = 0
                return True
            return False
        # HALF_OPEN: allow up to half_open_max_calls probe(s)
        return self._half_open_calls.get(name, 0) < self._policy.half_open_max_calls

    def record_success(self, name: str) -> BreakerState:
        if self.state_for(name) is BreakerState.HALF_OPEN:
            self._state[name] = BreakerState.CLOSED
            self._half_open_calls[name] = 0
        self._failures[name] = 0
        return self.state_for(name)

    def record_failure(self, name: str, *, now: float | None = None) -> BreakerState:
        now = self._clock() if now is None else now
        if self.state_for(name) is BreakerState.HALF_OPEN:
            self._state[name] = BreakerState.OPEN
            self._opened_at[name] = now
            return BreakerState.OPEN
        self._failures[name] = self._failures.get(name, 0) + 1
        if self._failures[name] >= self._policy.failure_threshold:
            self._state[name] = BreakerState.OPEN
            self._opened_at[name] = now
        return self.state_for(name)

    @property
    def statistics(self) -> dict[str, int]:
        return {"open": sum(1 for s in self._state.values() if s is BreakerState.OPEN),
                "half_open": sum(1 for s in self._state.values() if s is BreakerState.HALF_OPEN),
                "closed": sum(1 for s in self._state.values() if s is BreakerState.CLOSED)}

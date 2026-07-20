"""Retry Controller (M5.5B-2) — deterministic retry orchestration.

Wraps an inference (or tool) attempt in a deterministic retry loop: a bounded budget, a
deterministic exponential backoff (from the shared `core.policies.RetryPolicy` — no jitter, no
wall-clock sleeping), a recorded retry history, and circuit-breaker hooks. It **never performs
networking** and never sleeps: it computes the backoff delay and records it; the caller (an
async provider adapter, later) decides whether to honour it. For a given failure sequence the
produced `RetryRecord` history is identical every run — replay-safe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from .domain import RetryRecord
from .policies import ReasoningPolicyEngine

T = TypeVar("T")


class RetryExhausted(Exception):
    """The retry budget was exhausted (or the circuit opened) before success."""

    def __init__(self, message: str, *, history: tuple[RetryRecord, ...]) -> None:
        super().__init__(message)
        self.history = history


@dataclass(frozen=True, slots=True)
class RetryOutcome:
    """The deterministic record of a retry sequence."""

    attempts: int = 0
    history: tuple[RetryRecord, ...] = ()
    circuit_open: bool = False


@dataclass(slots=True)
class _Stats:
    attempts: int = 0
    retries: int = 0
    exhausted: int = 0
    circuit_trips: int = 0


class RetryController:
    """Deterministic retry orchestration for a provider/tool attempt. No I/O, no sleeping."""

    def __init__(self, policy_engine: ReasoningPolicyEngine | None = None) -> None:
        self._pe = policy_engine or ReasoningPolicyEngine()
        self._stats = _Stats()

    @property
    def statistics(self) -> dict[str, int]:
        s = self._stats
        return {"attempts": s.attempts, "retries": s.retries,
                "exhausted": s.exhausted, "circuit_trips": s.circuit_trips}

    def backoff_for(self, attempt: int) -> float:
        """The deterministic delay for a 1-based attempt (never slept here)."""
        return self._pe.policies.retry.backoff.delay_for(attempt)

    async def run(
        self,
        attempt_fn: Callable[[int], Awaitable[T]],
        *,
        kind: str = "provider",
        retryable: Callable[[Exception], bool] | None = None,
        now: float = 0.0,
    ) -> tuple[T, RetryOutcome]:
        """Run `attempt_fn(attempt)` under the retry budget. On a retryable failure, record a
        `RetryRecord` (with the deterministic backoff) and retry until the budget/circuit stops
        it. Returns the value + the deterministic `RetryOutcome`. `attempt_fn` does the actual
        (async) work — the controller only orchestrates."""
        policy = self._pe.policies.retry
        history: list[RetryRecord] = []
        breaker = policy.circuit_breaker_threshold
        consecutive = 0
        attempt = 0
        while True:
            attempt += 1
            self._stats.attempts += 1
            try:
                value = await attempt_fn(attempt)
                return value, RetryOutcome(attempts=attempt, history=tuple(history))
            except Exception as exc:  # noqa: BLE001 — orchestration decides, never networks
                if retryable is not None and not retryable(exc):
                    raise
                consecutive += 1
                if breaker > 0 and consecutive >= breaker:
                    self._stats.circuit_trips += 1
                    raise RetryExhausted(
                        f"circuit open after {consecutive} failures",
                        history=tuple(history),
                    ) from exc
                verdict = self._pe.should_retry(kind, attempt)
                if not verdict.ok:
                    self._stats.exhausted += 1
                    raise RetryExhausted(verdict.reason, history=tuple(history)) from exc
                delay = policy.backoff.delay_for(attempt)
                history.append(RetryRecord(attempt=attempt, reason=str(exc), delay_s=delay, at=now))
                self._stats.retries += 1

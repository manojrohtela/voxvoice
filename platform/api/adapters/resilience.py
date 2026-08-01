"""Execution resilience (M8.14) — retry, timeout, circuit breaker, rate limiting, cancellation,
graceful degradation.

Deterministic, dependency-free utilities the provider engine wraps around every provider/tool
call. No wall clock (a "timeout" is a latency-budget check against the transport's reported
latency); no threads. Provider-specific code lives in the adapters — this is generic resilience.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .transport import TransportError


class ExecutionCancelledError(Exception):
    """The turn was cancelled (a cooperative cancel token was set)."""


class ProviderUnavailableError(Exception):
    """The provider is unavailable (breaker open or exhausted retries)."""


class ProviderTimeoutError(Exception):
    """A provider call exceeded its latency budget."""


@dataclass(slots=True)
class RetrySpec:
    max_retries: int = 2
    retryable_only: bool = True


@dataclass(slots=True)
class CircuitBreaker:
    """A minimal breaker: opens after `threshold` consecutive failures; `record_success`
    closes it. Deterministic, no timers (half-open is modelled as one probe after reset)."""

    threshold: int = 3
    failures: int = 0
    opened: bool = False

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened = True

    def record_success(self) -> None:
        self.failures = 0
        self.opened = False

    def allow(self) -> bool:
        return not self.opened


@dataclass(slots=True)
class RateLimiter:
    """A deterministic token bucket (no wall clock — capacity consumed per call, refilled
    explicitly). `try_acquire` returns False when exhausted."""

    capacity: int = 60
    tokens: int = 60

    def try_acquire(self, n: int = 1) -> bool:
        if self.tokens < n:
            return False
        self.tokens -= n
        return True

    def refill(self) -> None:
        self.tokens = self.capacity


@dataclass(slots=True)
class CancelToken:
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


def execute_with_resilience(
    fn: Callable[[], Any],
    *,
    timeout_ms: int = 30_000,
    retry: RetrySpec | None = None,
    breaker: CircuitBreaker | None = None,
    limiter: RateLimiter | None = None,
    cancel: CancelToken | None = None,
    latency_of: Callable[[Any], int] | None = None,
) -> Any:
    """Run `fn` under the resilience policy. Raises `ProviderUnavailableError` /
    `ProviderTimeoutError` / `ExecutionCancelledError` on the corresponding condition; returns
    `fn()`'s result on success. Deterministic."""
    retry = retry or RetrySpec()
    if cancel is not None and cancel.cancelled:
        raise ExecutionCancelledError("cancelled before start")
    if breaker is not None and not breaker.allow():
        raise ProviderUnavailableError("circuit breaker open")
    if limiter is not None and not limiter.try_acquire():
        raise ProviderUnavailableError("rate limit exceeded")
    attempts = 0
    last_exc: Exception | None = None
    while attempts <= retry.max_retries:
        if cancel is not None and cancel.cancelled:
            raise ExecutionCancelledError("cancelled during retry")
        try:
            result = fn()
            if latency_of is not None and latency_of(result) > timeout_ms:
                raise ProviderTimeoutError(
                    f"latency {latency_of(result)}ms exceeded budget {timeout_ms}ms"
                )
            if breaker is not None:
                breaker.record_success()
            return result
        except (TransportError, ProviderTimeoutError) as exc:
            last_exc = exc
            if breaker is not None:
                breaker.record_failure()
            retryable = getattr(exc, "retryable", True)
            if retry.retryable_only and not retryable:
                raise ProviderUnavailableError(str(exc)) from exc
            attempts += 1
    raise ProviderUnavailableError(
        f"exhausted {retry.max_retries} retries: {last_exc}"
    ) from last_exc

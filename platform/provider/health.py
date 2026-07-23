"""ProviderHealthManager (M5.6B-2) — health intelligence. No networking.

The observed-outcome feedback loop the M5.6B-1 `ProviderRegistry` FSM needed to become
automatic: debounces single blips behind a configurable consecutive-outcome threshold before
driving the registry's health FSM, tracks failure/success streaks per adapter, and exposes
exclusion (`excluded()`, delegating to the registry's `unavailable()`) + reactivation
(`reactivate()`, delegating to `registry.recover()`). **Health only evaluates state** — it
never opens a socket, never times a real call; the caller reports outcomes it already
observed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain import ProviderHealthState
from .registry import ProviderRegistry


@dataclass(frozen=True, slots=True)
class HealthPolicy:
    """Consecutive-outcome thresholds before the registry's FSM is actually driven (debounces
    single blips; the FSM edges themselves are still single-step, defined in `lifecycle.py`)."""

    failure_threshold: int = 1
    success_threshold: int = 1


class ProviderHealthManager:
    def __init__(self, registry: ProviderRegistry, *, policy: HealthPolicy | None = None) -> None:
        self._registry = registry
        self._policy = policy or HealthPolicy()
        self._fail_streak: dict[str, int] = {}
        self._success_streak: dict[str, int] = {}
        self._stats = {"failures": 0, "successes": 0, "timeouts": 0,
                       "degradations": 0, "recoveries": 0}

    def record_success(self, name: str) -> ProviderHealthState:
        self._fail_streak[name] = 0
        self._stats["successes"] += 1
        streak = self._success_streak.get(name, 0) + 1
        if streak >= self._policy.success_threshold:
            self._success_streak[name] = 0
            before = self._registry.entry(name).health
            state = self._registry.mark_outcome_success(name)
            if state != before:
                self._stats["recoveries"] += 1
            return state
        self._success_streak[name] = streak
        return self._registry.entry(name).health

    def record_failure(self, name: str) -> ProviderHealthState:
        self._success_streak[name] = 0
        self._stats["failures"] += 1
        return self._degrade(name, self._registry.mark_outcome_failure)

    def record_timeout(self, name: str) -> ProviderHealthState:
        self._success_streak[name] = 0
        self._stats["timeouts"] += 1
        return self._degrade(name, self._registry.mark_outcome_timeout)

    def _degrade(self, name: str, advance) -> ProviderHealthState:
        streak = self._fail_streak.get(name, 0) + 1
        if streak >= self._policy.failure_threshold:
            self._fail_streak[name] = 0
            state = advance(name)
            self._stats["degradations"] += 1
            return state
        self._fail_streak[name] = streak
        return self._registry.entry(name).health

    def reactivate(self, name: str) -> ProviderHealthState:
        """Manually move an UNAVAILABLE adapter back to DEGRADED (conservative — a subsequent
        recorded success is still required to reach HEALTHY)."""
        return self._registry.recover(name)

    def excluded(self) -> tuple[str, ...]:
        return self._registry.unavailable()

    @property
    def statistics(self) -> dict[str, int]:
        return dict(self._stats)

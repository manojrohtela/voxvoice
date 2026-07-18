"""Shared, generic policy infrastructure (M5.0).

Reusable, deterministic policy *value objects* every runtime module configures with —
Ranking, Selection, Retry, Timeout, Budget, Compression, Validation. They carry
parameters + tiny generic helpers; they contain **no business rules** (a module
supplies the domain — e.g. which fields to rank). All frozen, validated at
construction (no silent config), and side-effect-free.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from core.errors import PlatformError


class InvalidPolicy(PlatformError):
    """A policy was constructed with an invalid value/combination."""


def _nonneg(name: str, v: float) -> None:
    if v < 0:
        raise InvalidPolicy(f"{name} must be >= 0, got {v}")


@dataclass(frozen=True, slots=True)
class RankingPolicy:
    """Weighted linear ranking. The module names the signals; this weights them."""

    weights: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for k, v in self.weights.items():
            _nonneg(f"weight[{k}]", v)

    def score(self, signals: Mapping[str, float]) -> float:
        return sum(self.weights.get(k, 0.0) * v for k, v in signals.items())


class SelectionStrategy(StrEnum):
    TOP_K = "top_k"
    THRESHOLD = "threshold"
    FIRST = "first"


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    strategy: SelectionStrategy = SelectionStrategy.TOP_K
    k: int = 10
    threshold: float = 0.0

    def __post_init__(self) -> None:
        if self.k < 0:
            raise InvalidPolicy(f"k must be >= 0, got {self.k}")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.1
    multiplier: float = 2.0
    max_delay_s: float = 5.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise InvalidPolicy("max_attempts must be >= 1")
        _nonneg("base_delay_s", self.base_delay_s)
        _nonneg("max_delay_s", self.max_delay_s)

    def delay_for(self, attempt: int) -> float:
        """Deterministic exponential backoff for a 1-based attempt (no jitter here)."""
        d = self.base_delay_s * (self.multiplier ** max(0, attempt - 1))
        return min(d, self.max_delay_s)


@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            raise InvalidPolicy("timeout seconds must be > 0")


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """A generic resource budget (tokens, bytes, items, cost — the unit is the
    module's)."""

    limit: int = 0  # 0 → unbounded
    unit: str = "tokens"

    def __post_init__(self) -> None:
        if self.limit < 0:
            raise InvalidPolicy("budget limit must be >= 0")

    @property
    def unbounded(self) -> bool:
        return self.limit <= 0

    def fits(self, used: int, add: int = 0) -> bool:
        return self.unbounded or (used + add) <= self.limit


@dataclass(frozen=True, slots=True)
class CompressionPolicy:
    enabled: bool = False
    target_ratio: float = 1.0  # 1.0 = no compression; <1 = shrink target

    def __post_init__(self) -> None:
        if not (0.0 < self.target_ratio <= 1.0):
            raise InvalidPolicy("target_ratio must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    """Generic validation switches; the module supplies the actual checks."""

    strict: bool = True
    max_size: int = 0  # 0 → unbounded
    required_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_size < 0:
            raise InvalidPolicy("max_size must be >= 0")

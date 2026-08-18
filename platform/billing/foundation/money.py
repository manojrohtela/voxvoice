"""Money — integer minor units, always paired with a currency (M9.2).

ADR-0023 RS5 / Blueprint §8: *money is never a float.* Every amount is an integer in a
currency's minor unit and carries its currency; there is no amount without a currency,
and no arithmetic across currencies. Rounding is an explicit, declared policy — never an
implicit float artefact.

Pure value objects: immutable, deterministic, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .errors import CurrencyMismatch, InvalidMoney


class Currency(StrEnum):
    """Supported settlement currencies. Additive (§A12) — a new market adds a member,
    never a structural change. `EXPONENT` records the minor-unit scale."""

    INR = "INR"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    AED = "AED"
    SGD = "SGD"
    AUD = "AUD"
    CAD = "CAD"


#: Minor units per major unit, by currency. All currently supported currencies are
#: 2-exponent; zero/three-exponent currencies (JPY, KWD) are added here when supported.
EXPONENT: dict[Currency, int] = {c: 2 for c in Currency}


class Rounding(StrEnum):
    """Declared rounding policies. Rounding is part of a price/meter definition and is
    versioned with it — a silent rounding change is a revenue incident (Blueprint §5)."""

    HALF_UP = "half_up"
    HALF_EVEN = "half_even"
    FLOOR = "floor"
    CEILING = "ceiling"


@dataclass(frozen=True, slots=True, order=True)
class Money:
    """An exact amount: integer minor units + currency. Frozen and totally ordered
    within a currency; cross-currency comparison and arithmetic raise."""

    minor: int
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.minor, int) or isinstance(self.minor, bool):
            raise InvalidMoney(f"money minor units must be int, got {type(self.minor).__name__}")

    # ── construction ────────────────────────────────────────────────────────────
    @classmethod
    def zero(cls, currency: Currency) -> Money:
        return cls(0, currency)

    @classmethod
    def of(cls, major: int, minor: int, currency: Currency) -> Money:
        """Exact construction from whole major + minor parts (e.g. 12 rupees 50 paise)."""
        scale = 10 ** EXPONENT[currency]
        if not 0 <= minor < scale:
            raise InvalidMoney(f"minor part {minor} out of range for {currency} (0..{scale - 1})")
        sign = -1 if major < 0 else 1
        return cls(major * scale + sign * minor, currency)

    # ── arithmetic (same-currency only) ─────────────────────────────────────────
    def _same(self, other: Money) -> None:
        if self.currency is not other.currency:
            raise CurrencyMismatch(f"cannot combine {self.currency} with {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.minor - other.minor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.minor, self.currency)

    def times(self, quantity: int) -> Money:
        """Exact integer scaling — the only multiplication that needs no rounding."""
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise InvalidMoney("quantity must be an int; use `scaled` for rational factors")
        return Money(self.minor * quantity, self.currency)

    def scaled(self, numerator: int, denominator: int, rounding: Rounding) -> Money:
        """Exact rational scaling with a *declared* rounding policy — the proration and
        percentage primitive. Integer maths throughout; no float ever participates."""
        if denominator == 0:
            raise InvalidMoney("denominator must be non-zero")
        return Money(
            _divide(self.minor * numerator, denominator, rounding),
            self.currency,
        )

    def allocate(self, weights: tuple[int, ...]) -> tuple[Money, ...]:
        """Split exactly across `weights` using largest-remainder — the sum of the parts
        always equals the original to the last minor unit (no rounding leakage)."""
        if not weights or any(w < 0 for w in weights) or sum(weights) == 0:
            raise InvalidMoney("allocation weights must be non-empty, non-negative, non-zero-sum")
        total = sum(weights)
        base = [self.minor * w // total for w in weights]
        remainder = self.minor - sum(base)
        # deterministic distribution: by descending remainder, then by index
        order = sorted(
            range(len(weights)),
            key=lambda i: (-((self.minor * weights[i]) % total), i),
        )
        step = 1 if remainder >= 0 else -1
        for i in order[: abs(remainder)]:
            base[i] += step
        return tuple(Money(m, self.currency) for m in base)

    # ── predicates ──────────────────────────────────────────────────────────────
    @property
    def is_zero(self) -> bool:
        return self.minor == 0

    @property
    def is_negative(self) -> bool:
        return self.minor < 0

    def __str__(self) -> str:
        scale = 10 ** EXPONENT[self.currency]
        sign = "-" if self.minor < 0 else ""
        whole, part = divmod(abs(self.minor), scale)
        return f"{sign}{whole}.{part:0{EXPONENT[self.currency]}d} {self.currency.value}"


def _divide(numerator: int, denominator: int, rounding: Rounding) -> int:
    """Integer division under a declared rounding policy. Deterministic for negatives."""
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    q, r = divmod(numerator, denominator)  # floor semantics
    if r == 0:
        return q
    if rounding is Rounding.FLOOR:
        return q
    if rounding is Rounding.CEILING:
        return q + 1
    twice = 2 * r
    if twice > denominator:
        return q + 1
    if twice < denominator:
        return q
    # exact half
    if rounding is Rounding.HALF_UP:
        return q + 1
    return q if q % 2 == 0 else q + 1  # HALF_EVEN

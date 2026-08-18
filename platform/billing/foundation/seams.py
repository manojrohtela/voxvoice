"""Deterministic seams: Clock and IdGenerator (M9.2).

§A2.4 — billing *behaviour* is deterministic; only genuinely non-deterministic edges
(wall-clock time, id minting) are isolated behind injectable seams, so a billing run is
byte-for-byte reproducible in tests while production uses the real clock and
collision-resistant ids. Billing period boundaries, trial expiry, and renewal are
*computed* from an injected clock, never sampled ad hoc (Blueprint §6.2).

Mirrors `api.foundation.seams` deliberately: one repository-wide seam idiom.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from core.ids import gen_id

# ── id prefixes (one place; extend additively) ──────────────────────────────────
PREFIX_ACCOUNT = "bac"  # billing account
PREFIX_PLAN = "pln"
PREFIX_FEATURE = "ftr"
PREFIX_METER = "mtr"
PREFIX_PRICE_BOOK = "pbk"
PREFIX_PRICE = "prc"
PREFIX_SUBSCRIPTION = "sub"
PREFIX_ENTITLEMENT = "ent"
PREFIX_SNAPSHOT = "esn"
PREFIX_USAGE_RECORD = "usg"
PREFIX_USAGE_AGGREGATE = "uag"
PREFIX_CHARGE_LINE = "chl"
PREFIX_LEDGER_ENTRY = "led"
PREFIX_CREDIT = "crd"
PREFIX_CREDIT_MOVEMENT = "crm"
PREFIX_INVOICE = "inv"
PREFIX_CREDIT_NOTE = "cnt"
PREFIX_PAYMENT = "pay"
PREFIX_REFUND = "rfd"
PREFIX_TAX_PROFILE = "txp"
PREFIX_TAX_DETERMINATION = "txd"
PREFIX_CONTRACT = "ctr"
PREFIX_LICENSE = "lic"
PREFIX_EVENT = "bev"


class Clock(ABC):
    """A source of wall-clock seconds. The only time source the Foundation may use."""

    @abstractmethod
    def now(self) -> float: ...


class SystemClock(Clock):
    """Production clock — the real wall clock."""

    def now(self) -> float:
        return time.time()


class FixedClock(Clock):
    """Deterministic clock for tests and reproducible billing runs."""

    def __init__(self, start: float = 0.0, step: float = 1.0) -> None:
        self._t = start
        self._step = step

    def now(self) -> float:
        self._t += self._step
        return self._t


class FrozenClock(Clock):
    """A clock that does not advance — for assertions that must see one instant."""

    def __init__(self, at: float = 0.0) -> None:
        self._t = at

    def now(self) -> float:
        return self._t


class IdGenerator(ABC):
    """Mints prefixed, collision-resistant aggregate ids."""

    @abstractmethod
    def new(self, prefix: str) -> str: ...


class DefaultIdGenerator(IdGenerator):
    """Production generator — delegates to the shared `core.ids.gen_id`."""

    def new(self, prefix: str) -> str:
        return gen_id(prefix)


class SequentialIdGenerator(IdGenerator):
    """Deterministic generator for tests — `sub_1`, `sub_2`, ... per prefix."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}_{n}"

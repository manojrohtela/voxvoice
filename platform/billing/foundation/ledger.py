"""Revenue Ledger and Credits (M9.12 domain) — immutable, append-only.

ADR-0023 RS1–RS6. The ledger is the system of record for revenue, and it has **no edit
path by construction**: there is no mutation method on a ledger entry, and a correction is
a new entry that references the original. That absence is the guarantee — an append-only
posture asserted in prose but implemented with an `update()` is not append-only.

Everything here is `Money` (integer minor units + currency); a currency is fixed per
entry, and conversion is presentation, never a ledger concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .money import Currency, Money


class LedgerEntryType(StrEnum):
    """The revenue-affecting facts the ledger records. Additive."""

    CHARGE = "charge"  # rated consumption / recurring fee owed
    DISCOUNT = "discount"
    TAX = "tax"
    CREDIT_APPLIED = "credit_applied"
    PAYMENT = "payment"
    REFUND = "refund"
    CHARGEBACK = "chargeback"
    ADJUSTMENT = "adjustment"
    WRITE_OFF = "write_off"
    PAYABLE = "payable"  # marketplace publisher payable (M9.20)


class LedgerDirection(StrEnum):
    """Which way the entry moves the account's balance. Explicit rather than inferred
    from a sign, so a report can never mis-read an amount."""

    DEBIT = "debit"  # increases what the customer owes
    CREDIT = "credit"  # decreases what the customer owes


class RecognitionState(StrEnum):
    """Revenue-recognition posture. A **projection over the ledger owned by Billing**
    (ADR-0023 RS6) — not a second bookkeeping system, and never written back by an
    external accounting system."""

    DEFERRED = "deferred"
    RECOGNIZED = "recognized"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One immutable revenue fact. `references` carries the lineage (invoice, payment,
    usage period, original entry) that makes a charge explainable down to its cause."""

    id: str
    org_id: str
    account_id: str
    entry_type: LedgerEntryType
    direction: LedgerDirection
    amount: Money
    recorded_at: float
    recognition: RecognitionState = RecognitionState.NOT_APPLICABLE
    invoice_id: str = ""
    payment_id: str = ""
    subscription_id: str = ""
    reverses_entry_id: str = ""
    description: str = ""

    @property
    def signed_minor(self) -> int:
        """The entry's contribution to the balance, in minor units."""
        return self.amount.minor if self.direction is LedgerDirection.DEBIT else -self.amount.minor


@dataclass(frozen=True, slots=True)
class LedgerBalance:
    """A derived, read-only balance over a set of entries. Never stored as truth: it is
    always recomputable from the entries, which is what makes the ledger verifiable."""

    account_id: str
    currency: Currency
    outstanding: Money
    entry_count: int

    @classmethod
    def of(
        cls, account_id: str, currency: Currency, entries: tuple[LedgerEntry, ...]
    ) -> LedgerBalance:
        mismatched = [e for e in entries if e.amount.currency is not currency]
        if mismatched:
            raise ValueError(
                f"ledger entries in {mismatched[0].amount.currency}, expected {currency}"
            )
        total = sum(e.signed_minor for e in entries)
        return cls(account_id, currency, Money(total, currency), len(entries))


class CreditKind(StrEnum):
    PREPAID = "prepaid"  # purchased credit
    PROMOTIONAL = "promotional"
    GOODWILL = "goodwill"
    CONTRACT_DRAWDOWN = "contract_drawdown"


@dataclass(frozen=True, slots=True)
class CreditGrant:
    """An immutable credit issuance. Balance is never stored on the grant — it is derived
    from movements, so the two can never disagree."""

    id: str
    org_id: str
    account_id: str
    kind: CreditKind
    amount: Money
    granted_at: float
    expires_at: float | None = None
    source_ref: str = ""

    def is_active_at(self, ts: float) -> bool:
        return self.expires_at is None or ts < self.expires_at


@dataclass(frozen=True, slots=True)
class CreditMovement:
    """One append-only change to a credit balance. Consumption is negative; expiry closes
    out a remainder. There is no `set_balance` — only movements."""

    id: str
    org_id: str
    account_id: str
    grant_id: str
    amount: Money  # positive = granted, negative = consumed/expired
    occurred_at: float
    invoice_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CreditBalance:
    """A derived credit balance. `of()` is the only way to obtain one."""

    account_id: str
    currency: Currency
    available: Money
    movement_count: int

    @classmethod
    def of(
        cls, account_id: str, currency: Currency, movements: tuple[CreditMovement, ...]
    ) -> CreditBalance:
        mismatched = [m for m in movements if m.amount.currency is not currency]
        if mismatched:
            raise ValueError(
                f"credit movements in {mismatched[0].amount.currency}, expected {currency}"
            )
        total = sum(m.amount.minor for m in movements)
        return cls(account_id, currency, Money(total, currency), len(movements))

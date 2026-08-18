"""Charge lines, invoices, and credit notes (M9.13 domain).

ADR-0023 IN1–IN7. Two invariants are structural here, not procedural:

* **An issued invoice is immutable.** There is no method that alters an issued invoice,
  and no administrative path to edit one. Corrections are credit notes.
* **An invoice is self-contained evidence** — it carries the plan version, price versions,
  usage summary, discount trail, tax determination, currency, and period, so it can be
  explained years later without querying live state.

Numbering is gapless, sequential, and per legal entity/jurisdiction; the allocation
*mechanism* is a persistence concern (M9.4/M9.13) but the value object that carries it is
declared here so no layer invents its own format.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .domain import BillingPeriod, Quantity
from .money import Currency, Money


class InvoiceState(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"  # contract/enterprise only
    ISSUED = "issued"
    OVERDUE = "overdue"
    IN_COLLECTION = "in_collection"
    PAID = "paid"
    CREDITED = "credited"
    VOIDED = "voided"  # pre-issue only


class InvoiceTrigger(StrEnum):
    SUBMIT_FOR_APPROVAL = "submit_for_approval"
    APPROVE = "approve"
    ISSUE = "issue"
    VOID = "void"
    MARK_PAID = "mark_paid"
    MARK_OVERDUE = "mark_overdue"
    SEND_TO_COLLECTION = "send_to_collection"
    CREDIT = "credit"


#: Once issued, an invoice may never be edited or voided (ADR-0023 IN2/IN3).
IMMUTABLE_INVOICE_STATES: frozenset[InvoiceState] = frozenset(
    {
        InvoiceState.ISSUED,
        InvoiceState.OVERDUE,
        InvoiceState.IN_COLLECTION,
        InvoiceState.PAID,
        InvoiceState.CREDITED,
    }
)


class ChargeLineKind(StrEnum):
    RECURRING = "recurring"
    SEAT = "seat"
    USAGE = "usage"
    OVERAGE = "overage"
    ONE_OFF = "one_off"
    PRORATION = "proration"
    DISCOUNT = "discount"
    CREDIT = "credit"
    TAX = "tax"


@dataclass(frozen=True, slots=True)
class ChargeLine:
    """The immutable output of rating (M9.11): a priced line with full provenance.

    `price_code`/`price_version` and `meter_code` are what make a line explainable — a
    customer asking "why is this line ₹4,120?" is answered from the line itself plus the
    usage records it references."""

    id: str
    org_id: str
    account_id: str
    kind: ChargeLineKind
    description: str
    amount: Money
    period: BillingPeriod
    subscription_id: str = ""
    price_code: str = ""
    price_version: int = 0
    meter_code: str = ""
    quantity: Quantity | None = None
    unit_amount: Money | None = None
    usage_record_ids: tuple[str, ...] = ()
    discount_code: str = ""

    @property
    def is_negative(self) -> bool:
        """Discounts and credits are negative lines — never a second subtraction path."""
        return self.amount.is_negative


@dataclass(frozen=True, slots=True)
class InvoiceNumber:
    """A gapless, sequential number scoped to a legal entity and (where required) a
    jurisdiction. Allocated exactly once and never reused — including across retries,
    failures, and concurrent billing runs (ADR-0023 IN4)."""

    entity_code: str
    jurisdiction: str
    sequence: int
    formatted: str

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("invoice sequence must be >= 1")


@dataclass(frozen=True, slots=True)
class InvoiceTotals:
    """Derived totals. Computed from lines, never stored independently of them, so the
    header can never disagree with the body."""

    subtotal: Money
    discount_total: Money
    tax_total: Money
    credit_applied: Money
    total: Money
    amount_due: Money


@dataclass(frozen=True, slots=True)
class Invoice:
    """A billing document. Immutable once issued.

    `evidence` carries the self-containment requirement (IN5): plan/price versions, the
    usage summary, the discount trail, and the tax determination reference that produced
    these numbers."""

    id: str
    org_id: str
    account_id: str
    currency: Currency
    period: BillingPeriod
    state: InvoiceState = InvoiceState.DRAFT
    number: InvoiceNumber | None = None
    lines: tuple[ChargeLine, ...] = ()
    totals: InvoiceTotals | None = None
    subscription_id: str = ""
    tax_determination_id: str = ""
    issued_at: float | None = None
    due_at: float | None = None
    paid_at: float | None = None
    purchase_order_ref: str = ""  # enterprise procurement (Blueprint §16)
    payment_terms_days: int = 0  # net-N; 0 = due on issue
    evidence: tuple[str, ...] = ()  # ordered, human-readable provenance statements

    @property
    def is_issued(self) -> bool:
        return self.state in IMMUTABLE_INVOICE_STATES

    @property
    def is_immutable(self) -> bool:
        return self.is_issued

    @property
    def is_zero_amount(self) -> bool:
        """Zero-amount and fully credit-covered invoices are still issued (IN7)."""
        return self.totals is not None and self.totals.total.is_zero


@dataclass(frozen=True, slots=True)
class CreditNote:
    """The **only** correction path for an issued invoice. Itself immutable, and always
    references the original (ADR-0023 IN3)."""

    id: str
    org_id: str
    account_id: str
    invoice_id: str
    currency: Currency
    amount: Money
    reason: str
    issued_at: float
    number: InvoiceNumber | None = None
    refund_id: str = ""
    lines: tuple[ChargeLine, ...] = ()


class BillingRunStage(StrEnum):
    """The period-level lifecycle that produces money owed (Blueprint §10). Idempotent and
    resumable by construction: each stage appends and no stage mutates a previous stage's
    output, so an interrupted run is safely re-runnable."""

    PERIOD_OPEN = "period_open"
    PERIOD_CLOSING = "period_closing"
    USAGE_SEALED = "usage_sealed"
    RATED = "rated"
    ADJUSTED = "adjusted"
    TAXED = "taxed"
    INVOICE_ISSUED = "invoice_issued"
    PERIOD_CLOSED = "period_closed"


#: Stage order. Sealing is a one-way door: after USAGE_SEALED, correction has exactly one
#: path — an adjustment or a credit note (ADR-0023 M7).
BILLING_RUN_ORDER: tuple[BillingRunStage, ...] = (
    BillingRunStage.PERIOD_OPEN,
    BillingRunStage.PERIOD_CLOSING,
    BillingRunStage.USAGE_SEALED,
    BillingRunStage.RATED,
    BillingRunStage.ADJUSTED,
    BillingRunStage.TAXED,
    BillingRunStage.INVOICE_ISSUED,
    BillingRunStage.PERIOD_CLOSED,
)


@dataclass(frozen=True, slots=True)
class BillingRun:
    """One period's run for one account. Keyed by (account, period) so re-running is a
    no-op rather than a duplication."""

    id: str
    org_id: str
    account_id: str
    period: BillingPeriod
    stage: BillingRunStage = BillingRunStage.PERIOD_OPEN
    invoice_id: str = ""
    started_at: float = 0.0
    completed_at: float | None = None

    @property
    def is_complete(self) -> bool:
        return self.stage is BillingRunStage.PERIOD_CLOSED

    def can_advance_to(self, stage: BillingRunStage) -> bool:
        """Stages advance strictly forward, one step at a time — never skipped, never
        reversed. Re-entering the current stage is allowed (idempotent resume)."""
        here = BILLING_RUN_ORDER.index(self.stage)
        there = BILLING_RUN_ORDER.index(stage)
        return there in (here, here + 1)

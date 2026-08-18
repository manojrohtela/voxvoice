"""Credit & Revenue Ledger wire DTOs (M9.13) — Pydantic v2, additive to the earlier schemas.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views. Foundation enums are reused so validation and OpenAPI enums come from the domain
vocabulary, and **M9.5's `MoneyResponse` is reused rather than redeclared** — the lesson M9.12
recorded when a duplicated money type surfaced as two qualified schema names in the public document.

Money is always `{minor, currency}`: an integer in the currency's minor unit plus the currency,
never a formatted string and never a float (ADR-0023 RS5). In a ledger this matters more than
anywhere else — a float balance is a balance that disagrees with itself after enough additions.

Named distinctly from the frozen surfaces they sit beside: `JournalEntryResponse` rather than a
second `LedgerEntryResponse` (M9.5 owns that name for the M9.2 aggregate), `AccountBalanceResponse`
rather than a second `BalanceResponse`, and `AccountingPeriodBody` rather than a second
`PeriodBody`. `GrantCommercialCreditBody` likewise sits beside M9.5's `GrantCreditBody`, which
writes the M9.2 `CreditGrant` — a collision caught by `test_no_schema_name_is_qualified`, which
exists because two schema names for one concept in a public OpenAPI document is a defect a reader
can only discover by reading the generated document.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    AccountingPeriodState,
    CreditKind,
    CreditState,
    Currency,
    LedgerDirection,
    LedgerEntryType,
    LedgerSearchQuery,
    PostingSource,
)

from .schemas import MoneyResponse

# ── inbound ─────────────────────────────────────────────────────────────────────


class AccountingPeriodBody(BaseModel):
    """Named distinctly from M9.5's `PeriodBody` and M9.11's `UsagePeriodBody`. This one names the
    *account*, because an accounting period belongs to one."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": "bac_1",
                "period_start": 1735689600.0,
                "period_end": 1738368000.0,
                "currency": "INR",
            }
        }
    )
    account_id: str
    period_start: float = Field(gt=0)
    period_end: float = Field(gt=0)
    currency: Currency = Currency.INR


class PeriodKeyBody(BaseModel):
    account_id: str
    period_key: str


class PostRatingRunBody(BaseModel):
    """Record a **finalized** rating run's charges. A provisional run is refused: a figure an input
    can still move must not become a financial fact."""

    rating_run_id: str


class PostJournalEntryBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": "bac_1",
                "entry_type": "adjustment",
                "direction": "credit",
                "amount": {"minor": 25000, "currency": "INR"},
                "description": "Goodwill adjustment agreed with the customer",
            }
        }
    )
    account_id: str
    entry_type: LedgerEntryType = LedgerEntryType.ADJUSTMENT
    direction: LedgerDirection = LedgerDirection.DEBIT
    amount: MoneyResponse = Field(
        description="Never negative — direction carries the sign, so a report summing a column "
        "can never mis-read an amount"
    )
    reference: str = ""
    description: str = ""

    def currency(self) -> Currency:
        return Currency(self.amount.currency)


class ReasonBody(BaseModel):
    reason: str = ""


class AdjustEntryBody(BaseModel):
    """A signed delta: positive increases what the customer owes, negative reduces it."""

    delta_minor: int
    reason: str = ""


class GrantCommercialCreditBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": "bac_1",
                "code": "GOODWILL_2026_01",
                "kind": "goodwill",
                "amount": {"minor": 500000, "currency": "INR"},
                "expires_at": 1767225600.0,
                "reason": "Service credit for the January incident",
            }
        }
    )
    account_id: str
    code: str = Field(min_length=1, description="Unique per organization")
    kind: CreditKind = CreditKind.PROMOTIONAL
    amount: MoneyResponse = Field(description="Positive. Withdrawing a credit is a revocation")
    expires_at: float | None = None
    source_ref: str = ""
    reason: str = ""

    def currency(self) -> Currency:
        return Currency(self.amount.currency)


class ApplyCreditBody(BaseModel):
    """Name an amount, not a credit.

    Which credits are spent is the server's decision — earliest-expiring first — so two callers
    asking for the same thing get the same answer, and a customer's oldest credit cannot silently
    lapse while a newer one is consumed."""

    account_id: str
    amount: MoneyResponse
    journal_entry_id: str = ""
    reason: str = ""

    def currency(self) -> Currency:
        return Currency(self.amount.currency)


# ── responses ───────────────────────────────────────────────────────────────────


class EntryReferencesResponse(BaseModel):
    """The lineage that makes an entry explainable down to its cause. Every field is a *reference*
    to a fact another domain owns, never a copy of it."""

    subscription_id: str
    rating_run_id: str
    charge_id: str
    invoice_id: str
    payment_id: str
    credit_id: str
    usage_period_key: str
    is_traced: bool


class JournalEntryResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "jrn_a41c",
                "account_id": "bac_1",
                "period_key": "1735689600-1738368000",
                "sequence": 42,
                "entry_type": "charge",
                "direction": "debit",
                "amount": {"minor": 90000, "currency": "INR"},
                "source": "rating_run",
                "disposition": "in_period",
            }
        }
    )
    id: str
    account_id: str
    period_key: str
    sequence: int = Field(description="Position in this account's total order — a durable counter")
    entry_type: LedgerEntryType
    direction: LedgerDirection
    amount: MoneyResponse
    signed_minor: int = Field(
        description="Contribution to the balance; debits increase what is owed"
    )
    source: PostingSource
    source_id: str
    source_digest: str
    references: EntryReferencesResponse
    recognition: str
    reverses_entry_id: str
    adjusts_entry_id: str
    is_correction: bool
    disposition: str
    was_late: bool
    description: str
    posted_at: float
    posted_by: str
    digest: str


class AccountBalanceResponse(BaseModel):
    """**Derived on every read.** There is no stored balance to return instead."""

    account_id: str
    currency: str
    outstanding: MoneyResponse
    debits: MoneyResponse
    credits: MoneyResponse
    entry_count: int
    through_sequence: int
    is_settled: bool
    is_in_credit: bool = Field(description="The customer is owed money — a real state")
    digest: str


class AccountingPeriodResponse(BaseModel):
    id: str
    account_id: str
    period_key: str
    period_start: float
    period_end: float
    currency: str
    state: AccountingPeriodState
    entry_count: int
    debits: MoneyResponse
    credits: MoneyResponse
    closing_balance: MoneyResponse
    close_digest: str = Field(
        description="A deterministic hash of everything the period contained at close. A replay "
        "compares against this, so reconstruction is proven rather than asserted"
    )
    opened_at: float
    closing_at: float | None
    closed_at: float | None
    closed_by: str
    allowed_triggers: list[str] = Field(
        description="Which lifecycle moves the server will accept next. Empty once closed"
    )


class PostingReceiptResponse(BaseModel):
    """`outcome` is `posted`, `duplicate`, `deferred`, or `rejected` — and three of the four are
    successes. A duplicate means exactly-once posting worked."""

    outcome: str
    journal_entry_id: str
    period_key: str
    disposition: str
    reason: str
    entry: JournalEntryResponse | None


class RunPostingReceiptResponse(BaseModel):
    rating_run_id: str
    account_id: str
    charges_seen: int
    posted: int
    duplicates: int
    deferred: int
    skipped: int
    total: MoneyResponse
    receipts: list[PostingReceiptResponse]


class TrialBalanceLineResponse(BaseModel):
    entry_type: str
    debits: MoneyResponse
    credits: MoneyResponse
    net: MoneyResponse
    entry_count: int


class TrialBalanceResponse(BaseModel):
    """The subledger control report. Debits and credits are kept **separate** rather than netted,
    because an accountant reconciling against a general ledger needs both sides — a net figure
    cannot be mapped onto two GL accounts."""

    account_id: str
    period_key: str
    currency: str
    lines: list[TrialBalanceLineResponse]
    total_debits: MoneyResponse
    total_credits: MoneyResponse
    movement: MoneyResponse
    opening_balance: MoneyResponse | None
    closing_balance: MoneyResponse | None
    reconciles: bool = Field(description="opening + movement == closing")


class CommercialCreditResponse(BaseModel):
    id: str
    account_id: str
    code: str
    kind: CreditKind
    amount: MoneyResponse
    state: CreditState
    granted_at: float
    granted_by: str
    expires_at: float | None
    source_ref: str
    reason: str
    allowed_triggers: list[str]


class CreditPositionResponse(BaseModel):
    """Derived from the applications. The grant carries no balance, so the two cannot disagree."""

    credit_id: str
    currency: str
    granted: MoneyResponse
    applied: MoneyResponse
    remaining: MoneyResponse
    application_count: int
    state: str
    is_exhausted: bool


class CreditBalanceResponse(BaseModel):
    account_id: str
    currency: str
    available: MoneyResponse
    granted: MoneyResponse
    applied: MoneyResponse
    credit_count: int
    positions: list[CreditPositionResponse]


class CreditApplicationReceiptResponse(BaseModel):
    account_id: str
    requested: MoneyResponse
    covered: MoneyResponse
    shortfall: MoneyResponse
    is_fully_covered: bool
    applications: list[str]


class LedgerReplayResponse(BaseModel):
    """Reconstruct a period and compare against what it sealed. A pure read — replaying writes
    nothing, which is what makes it safe to run in production rather than only in a drill."""

    account_id: str
    period_key: str
    entries_replayed: int
    rebuilt_balance: MoneyResponse
    recorded_balance: MoneyResponse
    replay_digest: str
    close_digest: str
    matches_close_digest: bool
    balances_agree: bool
    is_clean: bool


class LedgerDiscrepancyResponse(BaseModel):
    kind: str
    entry_id: str
    charge_id: str
    expected_minor: int
    actual_minor: int
    detail: str


class LedgerReconciliationResponse(BaseModel):
    account_id: str
    period_key: str
    charges_examined: int
    entries_examined: int
    is_clean: bool
    unposted_count: int
    discrepancies: list[LedgerDiscrepancyResponse]


class EntryInspectionResponse(BaseModel):
    entry_id: str
    entry_type: str
    direction: str
    amount: MoneyResponse
    sequence: int
    is_traced: bool
    is_corrected: bool
    corrected_by: list[str]
    is_sound: bool
    notes: list[str]


class LedgerInspectionResponse(BaseModel):
    account_id: str
    period_key: str
    state: str
    balance: AccountBalanceResponse | None
    entries: list[EntryInspectionResponse]
    sequence_gaps: list[int] = Field(
        description="Allocated positions with no committed entry. A gap is unexplained rather "
        "than hidden — a silently reused number would be worse"
    )
    checkpoint_consistent: bool
    is_sound: bool
    untraced: list[str]
    notes: list[str]


class BalanceCheckpointResponse(BaseModel):
    """A cache with a proof, never a source of truth."""

    id: str
    account_id: str
    period_key: str
    currency: str
    outstanding: MoneyResponse
    entry_count: int
    through_sequence: int
    digest: str
    computed_at: float


class LedgerActivityResponse(BaseModel):
    id: str
    action: str
    actor_id: str
    at: float
    account_id: str
    period_key: str
    journal_entry_id: str
    credit_id: str
    amount: MoneyResponse | None
    reason: str
    detail: str


class LedgerDiagnosticResponse(BaseModel):
    path: str
    code: str
    severity: str
    message: str
    remediation: str


class LedgerDiagnosticsResponse(BaseModel):
    account_id: str
    period_key: str
    findings: list[LedgerDiagnosticResponse]
    error_count: int
    warning_count: int
    is_valid: bool


class LedgerSearchResponse(BaseModel):
    entries: list[JournalEntryResponse]
    total: int


# ── inbound query construction ──────────────────────────────────────────────────


def build_ledger_query(
    *,
    text: str = "",
    account_id: str = "",
    period_key: str = "",
    entry_types: list[LedgerEntryType] | None = None,
    directions: list[LedgerDirection] | None = None,
    sources: list[PostingSource] | None = None,
    corrections_only: bool = False,
    min_amount_minor: int | None = None,
    max_amount_minor: int | None = None,
    posted_from: float | None = None,
    posted_until: float | None = None,
    limit: int = 100,
    offset: int = 0,
) -> LedgerSearchQuery:
    """Transport → Foundation query object. No filtering happens here; `LedgerSearchService` owns
    every predicate, so the API and a unit test ask the same thing."""
    return LedgerSearchQuery(
        text=text,
        account_id=account_id,
        period_key=period_key,
        entry_types=tuple(entry_types or ()),
        directions=tuple(directions or ()),
        sources=tuple(sources or ()),
        corrections_only=corrections_only,
        min_amount_minor=min_amount_minor,
        max_amount_minor=max_amount_minor,
        posted_from=posted_from,
        posted_until=posted_until,
        limit=limit,
        offset=offset,
    )

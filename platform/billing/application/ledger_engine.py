"""Credit & Revenue Ledger application layer (M9.13) — commands, queries, use cases, mapping.

Additive to M9.3/M9.6–M9.12: the buses, Unit of Work, authorization orchestrator, application-event
vocabulary, and dispatch discipline are reused exactly as they are. This module adds 12 commands, 13
queries, their use cases, and the view DTOs — and nothing else.

The four beats are unchanged: **authorize → open a transaction → coordinate the Foundation → stage
an event.** Every ledger rule — whether a posting happens, where a late one lands, how a balance
folds, when a period may close, which credit is spent first, whether a replay agrees — is Foundation
behaviour these methods *call*. There is no arithmetic here, and a test greps this module for the
operators that would be tempting to inline.

## What this layer reads, and what it refuses

Posting a rating run is the only path by which commercial facts become financial ones, and it is
guarded on both sides:

* **The run must be `finalized`.** A provisional figure is one an input can still move; posting one
  would put a number on a statement that rating itself does not yet consider settled. This is the
  guard M9.12's own spec recorded as missing, implemented here — at the point where money is
  created, which is where it belongs.
* **The charges come from M9.12's charge repository, not from the run's payload.** The repository
  holds originals *and* the adjustment charges `RecordRatingAdjustment` appends; the payload holds
  only originals. Posting from the payload would silently omit every adjustment — a real gap, found
  while wiring this milestone and reported rather than worked around.

Everything else the Ledger touches is read-only: it calls `add`, `append`, `replace`, or `upsert` on
its own six seams and on nothing else, and a test walks this module's AST to prove it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from billing.foundation import (
    AccountingPeriod,
    AccountingPeriodTrigger,
    BillingPeriod,
    CommercialCredit,
    CreditKind,
    CreditTrigger,
    Currency,
    EntryReferences,
    JournalEntry,
    LedgerAction,
    LedgerDirection,
    LedgerEntryType,
    LedgerSearchQuery,
    Money,
    PostingOutcome,
    PostingRequest,
    PostingSource,
    RecognitionState,
    period_key_for,
)
from billing.foundation import BillingNotFound as _NotFound

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .mapping import MoneyView
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OpenAccountingPeriod(_Command):
    """Open the window in which this account's financial facts are recorded."""

    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0
    currency: Currency = Currency.INR


@dataclass(frozen=True, slots=True)
class BeginAccountingPeriodClose(_Command):
    account_id: str = ""
    period_key: str = ""


@dataclass(frozen=True, slots=True)
class CloseAccountingPeriod(_Command):
    """Seal a period and record the digest of exactly what it contained. **A one-way door** — after
    this, a late fact is recorded in the next open period, never retroactively."""

    account_id: str = ""
    period_key: str = ""


@dataclass(frozen=True, slots=True)
class PostRatingRun(_Command):
    """Record a **finalized** rating run's charges as financial facts.

    Idempotent by derived identity: posting the same run twice records nothing the second time."""

    rating_run_id: str = ""


@dataclass(frozen=True, slots=True)
class PostJournalEntry(_Command):
    """An operator posting. Always actor-attributed, because an entry nobody is named against is an
    entry nobody can be asked about.

    M9.16 (additive, recorded in DL-0126): `source`, `source_id`, and `payment_id` let a *domain*
    post through this one path instead of gaining a poster of its own. They default to exactly the
    previous behaviour — `MANUAL` with the reference as the cause — so every existing caller is
    unchanged. The Ledger still decides everything that matters: the entry id is derived from
    `(org, account, source, source_id, entry_type)`, so a payment replayed twice computes one id
    and the second posting is a recorded duplicate rather than a second financial fact."""

    account_id: str = ""
    entry_type: LedgerEntryType = LedgerEntryType.ADJUSTMENT
    direction: LedgerDirection = LedgerDirection.DEBIT
    amount_minor: int = 0
    currency: Currency = Currency.INR
    reference: str = ""
    description: str = ""
    source: PostingSource = PostingSource.MANUAL
    source_id: str = ""
    payment_id: str = ""
    # M9.18 (additive, recorded in DL-0129): the recognition posture the caused entry carries.
    # Defaults to exactly the previous behaviour, so every existing caller is unchanged. M9.2's
    # `RecognitionState` is reused rather than a second vocabulary being declared, which keeps
    # ADR-0023 RS6 true: recognition stays a **projection over this ledger**, and the projection is
    # a field on the entry rather than a second set of books somewhere else.
    recognition: RecognitionState = RecognitionState.NOT_APPLICABLE


@dataclass(frozen=True, slots=True)
class ReverseJournalEntry(_Command):
    """Withdraw an entry by posting its opposite. Never an edit, never a deletion."""

    journal_entry_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AdjustJournalEntry(_Command):
    """Move a recorded figure by a signed delta without withdrawing it."""

    journal_entry_id: str = ""
    delta_minor: int = 0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class GrantCommercialCredit(_Command):
    account_id: str = ""
    code: str = ""
    kind: CreditKind = CreditKind.PROMOTIONAL
    amount_minor: int = 0
    currency: Currency = Currency.INR
    expires_at: float | None = None
    source_ref: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ApplyCommercialCredit(_Command):
    """Spend credit against an outstanding balance.

    The caller names an amount, not a credit: which credits are spent is a Foundation decision
    (earliest-expiring first), so two callers asking for the same thing get the same answer."""

    account_id: str = ""
    amount_minor: int = 0
    currency: Currency = Currency.INR
    journal_entry_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ExpireCommercialCredit(_Command):
    credit_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RevokeCommercialCredit(_Command):
    """Withdraw a credit. Applications already made stay applied — revoking does not un-spend."""

    credit_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RebuildBalanceCheckpoint(_Command):
    """Recompute a derived checkpoint from the entries it summarises."""

    account_id: str = ""
    period_key: str = ""


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetAccountLedgerBalance(_Query):
    """Named distinctly from M9.3's `GetLedgerBalance`, which folds the M9.2 aggregate."""

    account_id: str = ""
    currency: Currency = Currency.INR


@dataclass(frozen=True, slots=True)
class GetJournalEntry(_Query):
    journal_entry_id: str = ""


@dataclass(frozen=True, slots=True)
class ListJournalEntries(_Query):
    account_id: str = ""
    period_key: str = ""
    limit: int = 200


@dataclass(frozen=True, slots=True)
class GetAccountingPeriod(_Query):
    account_id: str = ""
    period_key: str = ""


@dataclass(frozen=True, slots=True)
class ListAccountingPeriods(_Query):
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class GetTrialBalance(_Query):
    """The subledger control report an accountant reconciles a general ledger against."""

    account_id: str = ""
    period_key: str = ""
    currency: Currency = Currency.INR


@dataclass(frozen=True, slots=True)
class GetAccountCreditPosition(_Query):
    account_id: str = ""
    currency: Currency = Currency.INR


@dataclass(frozen=True, slots=True)
class ListCommercialCredits(_Query):
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class ReplayLedgerPeriod(_Query):
    """Reconstruct a period's financial state and compare it to what was closed."""

    account_id: str = ""
    period_key: str = ""
    currency: Currency = Currency.INR


@dataclass(frozen=True, slots=True)
class ReconcileLedgerPeriod(_Query):
    account_id: str = ""
    period_key: str = ""
    rating_run_id: str = ""


@dataclass(frozen=True, slots=True)
class InspectLedgerPeriod(_Query):
    account_id: str = ""
    period_key: str = ""
    currency: Currency = Currency.INR


@dataclass(frozen=True, slots=True)
class DiagnoseLedgerPeriod(_Query):
    account_id: str = ""
    period_key: str = ""


@dataclass(frozen=True, slots=True)
class SearchJournalEntries(_Query):
    query: LedgerSearchQuery = field(default_factory=LedgerSearchQuery)


@dataclass(frozen=True, slots=True)
class LedgerTimeline(_Query):
    account_id: str = ""
    limit: int = 100


#: The whole command and query surface, declared as tuples so "every message is registered on a bus
#: and every one is org-scoped" is checkable by iteration rather than by a reviewer's memory.
ALL_LEDGER_COMMANDS = (
    OpenAccountingPeriod,
    BeginAccountingPeriodClose,
    CloseAccountingPeriod,
    PostRatingRun,
    PostJournalEntry,
    ReverseJournalEntry,
    AdjustJournalEntry,
    GrantCommercialCredit,
    ApplyCommercialCredit,
    ExpireCommercialCredit,
    RevokeCommercialCredit,
    RebuildBalanceCheckpoint,
)

ALL_LEDGER_QUERIES = (
    GetAccountLedgerBalance,
    GetJournalEntry,
    ListJournalEntries,
    GetAccountingPeriod,
    ListAccountingPeriods,
    GetTrialBalance,
    GetAccountCreditPosition,
    ListCommercialCredits,
    ReplayLedgerPeriod,
    ReconcileLedgerPeriod,
    InspectLedgerPeriod,
    DiagnoseLedgerPeriod,
    SearchJournalEntries,
    LedgerTimeline,
)


# ── view DTOs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EntryReferencesView:
    subscription_id: str
    rating_run_id: str
    charge_id: str
    invoice_id: str
    payment_id: str
    credit_id: str
    usage_period_key: str
    is_traced: bool


@dataclass(frozen=True, slots=True)
class JournalEntryView:
    id: str
    account_id: str
    period_key: str
    sequence: int
    entry_type: str
    direction: str
    amount: MoneyView
    signed_minor: int
    source: str
    source_id: str
    source_digest: str
    references: EntryReferencesView
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


@dataclass(frozen=True, slots=True)
class AccountBalanceView:
    account_id: str
    currency: str
    outstanding: MoneyView
    debits: MoneyView
    credits: MoneyView
    entry_count: int
    through_sequence: int
    is_settled: bool
    is_in_credit: bool
    digest: str


@dataclass(frozen=True, slots=True)
class AccountingPeriodView:
    id: str
    account_id: str
    period_key: str
    period_start: float
    period_end: float
    currency: str
    state: str
    entry_count: int
    debits: MoneyView
    credits: MoneyView
    closing_balance: MoneyView
    close_digest: str
    opened_at: float
    closing_at: float | None
    closed_at: float | None
    closed_by: str
    allowed_triggers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PostingReceiptView:
    """What one posting did. `outcome` is `posted`, `duplicate`, `deferred`, or `rejected` — and
    three of the four are successes."""

    outcome: str
    journal_entry_id: str
    period_key: str
    disposition: str
    reason: str
    entry: JournalEntryView | None


@dataclass(frozen=True, slots=True)
class RunPostingReceiptView:
    rating_run_id: str
    account_id: str
    charges_seen: int
    posted: int
    duplicates: int
    deferred: int
    skipped: int
    total: MoneyView
    receipts: tuple[PostingReceiptView, ...]


@dataclass(frozen=True, slots=True)
class TrialBalanceLineView:
    entry_type: str
    debits: MoneyView
    credits: MoneyView
    net: MoneyView
    entry_count: int


@dataclass(frozen=True, slots=True)
class TrialBalanceView:
    account_id: str
    period_key: str
    currency: str
    lines: tuple[TrialBalanceLineView, ...]
    total_debits: MoneyView
    total_credits: MoneyView
    movement: MoneyView
    opening_balance: MoneyView | None
    closing_balance: MoneyView | None
    reconciles: bool


@dataclass(frozen=True, slots=True)
class CommercialCreditView:
    id: str
    account_id: str
    code: str
    kind: str
    amount: MoneyView
    state: str
    granted_at: float
    granted_by: str
    expires_at: float | None
    source_ref: str
    reason: str
    allowed_triggers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CreditPositionView:
    credit_id: str
    currency: str
    granted: MoneyView
    applied: MoneyView
    remaining: MoneyView
    application_count: int
    state: str
    is_exhausted: bool


@dataclass(frozen=True, slots=True)
class CreditBalanceOverview:
    account_id: str
    currency: str
    available: MoneyView
    granted: MoneyView
    applied: MoneyView
    credit_count: int
    positions: tuple[CreditPositionView, ...]


@dataclass(frozen=True, slots=True)
class CreditApplicationReceiptView:
    account_id: str
    requested: MoneyView
    covered: MoneyView
    shortfall: MoneyView
    is_fully_covered: bool
    applications: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LedgerReplayView:
    account_id: str
    period_key: str
    entries_replayed: int
    rebuilt_balance: MoneyView
    recorded_balance: MoneyView
    replay_digest: str
    close_digest: str
    matches_close_digest: bool
    balances_agree: bool
    is_clean: bool


@dataclass(frozen=True, slots=True)
class LedgerDiscrepancyView:
    kind: str
    entry_id: str
    charge_id: str
    expected_minor: int
    actual_minor: int
    detail: str


@dataclass(frozen=True, slots=True)
class LedgerReconciliationView:
    account_id: str
    period_key: str
    charges_examined: int
    entries_examined: int
    is_clean: bool
    unposted_count: int
    discrepancies: tuple[LedgerDiscrepancyView, ...]


@dataclass(frozen=True, slots=True)
class EntryInspectionView:
    entry_id: str
    entry_type: str
    direction: str
    amount: MoneyView
    sequence: int
    is_traced: bool
    is_corrected: bool
    corrected_by: tuple[str, ...]
    is_sound: bool
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LedgerInspectionView:
    account_id: str
    period_key: str
    state: str
    balance: AccountBalanceView | None
    entries: tuple[EntryInspectionView, ...]
    sequence_gaps: tuple[int, ...]
    checkpoint_consistent: bool
    is_sound: bool
    untraced: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BalanceCheckpointView:
    id: str
    account_id: str
    period_key: str
    currency: str
    outstanding: MoneyView
    entry_count: int
    through_sequence: int
    digest: str
    computed_at: float


@dataclass(frozen=True, slots=True)
class LedgerActivityView:
    id: str
    action: str
    actor_id: str
    at: float
    account_id: str
    period_key: str
    journal_entry_id: str
    credit_id: str
    amount: MoneyView | None
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class LedgerDiagnosticView:
    path: str
    code: str
    severity: str
    message: str
    remediation: str


@dataclass(frozen=True, slots=True)
class LedgerDiagnosticsView:
    account_id: str
    period_key: str
    findings: tuple[LedgerDiagnosticView, ...]
    error_count: int
    warning_count: int
    is_valid: bool


@dataclass(frozen=True, slots=True)
class LedgerSearchView:
    entries: tuple[JournalEntryView, ...]
    total: int


# ── mapping ─────────────────────────────────────────────────────────────────────


def _money(amount: Money) -> MoneyView:
    """Money on the wire is integer minor units plus a currency (ADR-0023 RS5)."""
    return MoneyView(minor=amount.minor, currency=amount.currency.value)


def _money_or_none(amount: Money | None) -> MoneyView | None:
    return None if amount is None else _money(amount)


def references_view(references: EntryReferences) -> EntryReferencesView:
    return EntryReferencesView(
        subscription_id=references.subscription_id,
        rating_run_id=references.rating_run_id,
        charge_id=references.charge_id,
        invoice_id=references.invoice_id,
        payment_id=references.payment_id,
        credit_id=references.credit_id,
        usage_period_key=references.usage_period_key,
        is_traced=references.is_traced,
    )


def entry_view(entry: JournalEntry) -> JournalEntryView:
    return JournalEntryView(
        id=entry.id,
        account_id=entry.account_id,
        period_key=entry.period_key,
        sequence=entry.sequence,
        entry_type=entry.entry_type.value,
        direction=entry.direction.value,
        amount=_money(entry.amount),
        signed_minor=entry.signed_minor,
        source=entry.source.value,
        source_id=entry.source_id,
        source_digest=entry.source_digest,
        references=references_view(entry.references),
        recognition=entry.recognition.value,
        reverses_entry_id=entry.reverses_entry_id,
        adjusts_entry_id=entry.adjusts_entry_id,
        is_correction=entry.is_correction,
        disposition=entry.disposition.value,
        was_late=entry.was_late,
        description=entry.description,
        posted_at=entry.posted_at,
        posted_by=entry.posted_by,
        digest=entry.digest,
    )


def balance_view(balance) -> AccountBalanceView:
    return AccountBalanceView(
        account_id=balance.account_id,
        currency=balance.currency.value,
        outstanding=_money(balance.outstanding),
        debits=_money(balance.debits),
        credits=_money(balance.credits),
        entry_count=balance.entry_count,
        through_sequence=balance.through_sequence,
        is_settled=balance.is_settled,
        is_in_credit=balance.is_in_credit,
        digest=balance.digest,
    )


def accounting_period_view(period: AccountingPeriod, allowed: tuple = ()) -> AccountingPeriodView:
    return AccountingPeriodView(
        id=period.id,
        account_id=period.account_id,
        period_key=period.period_key,
        period_start=period.period.start,
        period_end=period.period.end,
        currency=period.currency.value,
        state=period.state.value,
        entry_count=period.entry_count,
        debits=_money(period.debits),
        credits=_money(period.credits),
        closing_balance=_money(period.closing_balance),
        close_digest=period.close_digest,
        opened_at=period.opened_at,
        closing_at=period.closing_at,
        closed_at=period.closed_at,
        closed_by=period.closed_by,
        allowed_triggers=tuple(t.value for t in allowed),
    )


def receipt_view(decision, entry: JournalEntry | None) -> PostingReceiptView:
    return PostingReceiptView(
        outcome=decision.outcome.value,
        journal_entry_id=decision.entry_id,
        period_key=decision.landing_period_key,
        disposition=decision.disposition.value,
        reason=decision.reason,
        entry=entry_view(entry) if entry else None,
    )


def trial_balance_view(trial) -> TrialBalanceView:
    return TrialBalanceView(
        account_id=trial.account_id,
        period_key=trial.period_key,
        currency=trial.currency.value,
        lines=tuple(
            TrialBalanceLineView(
                entry_type=line.entry_type.value,
                debits=_money(line.debits),
                credits=_money(line.credits),
                net=_money(line.net),
                entry_count=line.entry_count,
            )
            for line in trial.lines
        ),
        total_debits=_money(trial.total_debits),
        total_credits=_money(trial.total_credits),
        movement=_money(trial.movement),
        opening_balance=_money_or_none(trial.opening_balance),
        closing_balance=_money_or_none(trial.closing_balance),
        reconciles=trial.reconciles,
    )


def credit_view(credit: CommercialCredit, allowed: tuple = ()) -> CommercialCreditView:
    return CommercialCreditView(
        id=credit.id,
        account_id=credit.account_id,
        code=credit.code,
        kind=credit.kind.value,
        amount=_money(credit.amount),
        state=credit.state.value,
        granted_at=credit.granted_at,
        granted_by=credit.granted_by,
        expires_at=credit.expires_at,
        source_ref=credit.source_ref,
        reason=credit.reason,
        allowed_triggers=tuple(t.value for t in allowed),
    )


def position_view(position) -> CreditPositionView:
    return CreditPositionView(
        credit_id=position.credit_id,
        currency=position.currency.value,
        granted=_money(position.granted),
        applied=_money(position.applied),
        remaining=_money(position.remaining),
        application_count=position.application_count,
        state=position.state.value,
        is_exhausted=position.is_exhausted,
    )


def credit_balance_view(overview) -> CreditBalanceOverview:
    return CreditBalanceOverview(
        account_id=overview.account_id,
        currency=overview.currency.value,
        available=_money(overview.available),
        granted=_money(overview.granted),
        applied=_money(overview.applied),
        credit_count=overview.credit_count,
        positions=tuple(position_view(p) for p in overview.positions),
    )


def replay_view(result) -> LedgerReplayView:
    return LedgerReplayView(
        account_id=result.account_id,
        period_key=result.period_key,
        entries_replayed=result.entries_replayed,
        rebuilt_balance=MoneyView(minor=result.rebuilt_balance_minor, currency=""),
        recorded_balance=MoneyView(minor=result.recorded_balance_minor, currency=""),
        replay_digest=result.replay_digest,
        close_digest=result.close_digest,
        matches_close_digest=result.matches_close_digest,
        balances_agree=result.balances_agree,
        is_clean=result.is_clean,
    )


def reconciliation_view(report) -> LedgerReconciliationView:
    return LedgerReconciliationView(
        account_id=report.account_id,
        period_key=report.period_key,
        charges_examined=report.charges_examined,
        entries_examined=report.entries_examined,
        is_clean=report.is_clean,
        unposted_count=report.unposted_count,
        discrepancies=tuple(
            LedgerDiscrepancyView(
                kind=d.kind.value,
                entry_id=d.entry_id,
                charge_id=d.charge_id,
                expected_minor=d.expected_minor,
                actual_minor=d.actual_minor,
                detail=d.detail,
            )
            for d in report.discrepancies
        ),
    )


def inspection_view(inspection) -> LedgerInspectionView:
    return LedgerInspectionView(
        account_id=inspection.account_id,
        period_key=inspection.period_key,
        state=inspection.state.value,
        balance=balance_view(inspection.balance) if inspection.balance else None,
        entries=tuple(
            EntryInspectionView(
                entry_id=e.entry_id,
                entry_type=e.entry_type.value,
                direction=e.direction.value,
                amount=_money(e.amount),
                sequence=e.sequence,
                is_traced=e.is_traced,
                is_corrected=e.is_corrected,
                corrected_by=e.corrected_by,
                is_sound=e.is_sound,
                notes=e.notes,
            )
            for e in inspection.entries
        ),
        sequence_gaps=inspection.sequence_gaps,
        checkpoint_consistent=inspection.checkpoint_consistent,
        is_sound=inspection.is_sound,
        untraced=inspection.untraced,
        notes=inspection.notes,
    )


def checkpoint_view(checkpoint) -> BalanceCheckpointView:
    return BalanceCheckpointView(
        id=checkpoint.id,
        account_id=checkpoint.account_id,
        period_key=checkpoint.period_key,
        currency=checkpoint.currency.value,
        outstanding=_money(checkpoint.outstanding),
        entry_count=checkpoint.entry_count,
        through_sequence=checkpoint.through_sequence,
        digest=checkpoint.digest,
        computed_at=checkpoint.computed_at,
    )


def activity_view(activity) -> LedgerActivityView:
    return LedgerActivityView(
        id=activity.id,
        action=activity.action.value,
        actor_id=activity.actor_id,
        at=activity.at,
        account_id=activity.account_id,
        period_key=activity.period_key,
        journal_entry_id=activity.journal_entry_id,
        credit_id=activity.credit_id,
        amount=(
            MoneyView(minor=activity.amount_minor, currency=activity.currency.value)
            if activity.currency
            else None
        ),
        reason=activity.reason,
        detail=activity.detail,
    )


def diagnostics_view(account_id: str, period_key: str, diagnostics) -> LedgerDiagnosticsView:
    return LedgerDiagnosticsView(
        account_id=account_id,
        period_key=period_key,
        findings=tuple(
            LedgerDiagnosticView(
                path=f.path,
                code=f.code.value,
                severity=f.severity.value,
                message=f.message,
                remediation=f.remediation,
            )
            for f in diagnostics.findings
        ),
        error_count=len(diagnostics.errors),
        warning_count=len(diagnostics.warnings),
        is_valid=diagnostics.is_valid,
    )


# ── use cases ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LedgerRepositories:
    """The ledger seams, injected. Interfaces only — implementations in persistence."""

    entries: object
    periods: object
    checkpoints: object
    credits: object
    applications: object
    activity: object


class LedgerCommandUseCases:
    """Orchestration for the Credit & Revenue Ledger. Coordinates; decides nothing.

    Not one method here folds a balance, decides where a late entry lands, computes a close digest,
    chooses which credit to spend, or performs arithmetic on money. Those are `BalanceService`,
    `PostingService`, `AccountingPeriodService`, and `CreditApplicationService` — and a test greps
    this module to keep it that way."""

    def __init__(
        self,
        *,
        context,
        ledger_repos: LedgerRepositories,
        factory,
        posting,
        periods,
        balances,
        reversals,
        adjustments,
        credits,
        credit_lifecycle,
        charge_postings,
        validator,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        self._ctx = context
        self._repos = ledger_repos
        self._factory = factory
        self._posting = posting
        self._periods = periods
        self._balances = balances
        self._reversals = reversals
        self._adjustments = adjustments
        self._credits = credits
        self._credit_lifecycle = credit_lifecycle
        self._charge_postings = charge_postings
        self._validator = validator
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── accounting periods ──────────────────────────────────────────────────────

    def open_period(self, command: OpenAccountingPeriod) -> AccountingPeriodView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            window = BillingPeriod(command.period_start, command.period_end)
            key = period_key_for(window)
            existing = self._repos.periods.by_key(command.org_id, command.account_id, key)
            if existing is not None:
                # Idempotent: opening a period twice is a retry, not a second period. Two periods
                # for one window would split an account's financial history across both.
                return accounting_period_view(existing, self._periods.allowed(existing.state))
            stored = self._repos.periods.add(
                self._factory.open_period(
                    org_id=command.org_id,
                    account_id=command.account_id,
                    period=window,
                    currency=command.currency,
                    actor_id=command.actor_id,
                )
            )
            self._record(
                command,
                LedgerAction.PERIOD_OPENED,
                account_id=stored.account_id,
                period_key=stored.period_key,
            )
            uow.stage(self._event(command, stored.id, period_key=stored.period_key))
            return accounting_period_view(stored, self._periods.allowed(stored.state))

    def begin_close(self, command: BeginAccountingPeriodClose) -> AccountingPeriodView:
        return self._period_transition(
            command, AccountingPeriodTrigger.BEGIN_CLOSE, LedgerAction.PERIOD_CLOSING
        )

    def close_period(self, command: CloseAccountingPeriod) -> AccountingPeriodView:
        """Seal the period, recording the digest of exactly what it contained.

        The digest is computed by the Foundation from the stored entries rather than passed in by a
        caller — which is what makes a later replay a *proof* rather than an assertion."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVOICE_ISSUE)
        with self._uow.new() as uow:
            period = self._period(command.org_id, command.account_id, command.period_key)
            entries = self._repos.entries.for_period(
                command.org_id, command.account_id, command.period_key
            )
            self._fail_on_errors(self._validator.validate_period_close(period, entries))
            moved = self._periods.apply(
                period,
                AccountingPeriodTrigger.CLOSE,
                at=self._clock.now(),
                actor_id=command.actor_id,
                entries=entries,
            )
            stored = self._repos.periods.replace(moved)
            self._repos.checkpoints.upsert(
                self._balances.checkpoint(
                    org_id=command.org_id,
                    account_id=stored.account_id,
                    period_key=stored.period_key,
                    currency=stored.currency,
                    entries=entries,
                    computed_at=self._clock.now(),
                )
            )
            self._factory.emit_period_transition(
                stored,
                event_type=_period_closed_event(),
                actor_id=command.actor_id,
                entries=stored.entry_count,
                digest=stored.close_digest,
            )
            self._record(
                command,
                LedgerAction.PERIOD_CLOSED,
                account_id=stored.account_id,
                period_key=stored.period_key,
                detail=stored.close_digest,
            )
            uow.stage(self._event(command, stored.id, digest=stored.close_digest))
            return accounting_period_view(stored, self._periods.allowed(stored.state))

    # ── posting ─────────────────────────────────────────────────────────────────

    def post_rating_run(self, command: PostRatingRun) -> RunPostingReceiptView:
        """The main path: a finalized rating run's charges become financial facts.

        Two guards, both refusing loudly rather than recording something provisional."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            run = self._rating_run(command.org_id, command.rating_run_id)
            self._guard_finalized(run)
            charges = self._rated_charges(command.org_id, run.id)
            requests = self._charge_postings.requests(
                charges,
                org_id=command.org_id,
                account_id=run.account_id,
                rating_run_id=run.id,
                period_key=period_key_for(run.period),
            )
            counts = {"posted": 0, "duplicate": 0, "deferred": 0, "rejected": 0}
            receipts = []
            total = Money.zero(run.currency)
            for request in requests:
                receipt, entry = self._post_one(command, request, uow=uow)
                counts[receipt.outcome] = counts.get(receipt.outcome, 0) + 1
                receipts.append(receipt)
                if entry is not None:
                    total = total + entry.amount
            self._record(
                command,
                LedgerAction.RUN_POSTED,
                account_id=run.account_id,
                detail=run.id,
                amount=total,
            )
            return RunPostingReceiptView(
                rating_run_id=run.id,
                account_id=run.account_id,
                charges_seen=len(charges),
                posted=counts["posted"],
                duplicates=counts["duplicate"],
                deferred=counts["deferred"],
                skipped=counts["rejected"],
                total=_money(total),
                receipts=tuple(receipts),
            )

    def post_entry(self, command: PostJournalEntry) -> PostingReceiptView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            request = PostingRequest(
                org_id=command.org_id,
                account_id=command.account_id,
                entry_type=command.entry_type,
                direction=command.direction,
                amount=Money(command.amount_minor, command.currency),
                source=command.source,
                source_id=command.source_id or command.reference or self._ids.new("man"),
                references=EntryReferences(
                    invoice_id=command.reference, payment_id=command.payment_id
                ),
                recognition=command.recognition,
                description=command.description,
            )
            receipt, _ = self._post_one(command, request, uow=uow)
            return receipt

    def reverse(self, command: ReverseJournalEntry) -> PostingReceiptView:
        """Withdraw an entry by posting its opposite. The original is read and left exactly as it
        is — there is no repository method that could amend it."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            entry = self._entry(command.org_id, command.journal_entry_id)
            self._reversals.guard_reversible(
                entry, self._repos.entries.reversals_of(command.org_id, entry.id)
            )
            request = self._reversals.request(
                entry, reason=command.reason, actor_id=command.actor_id
            )
            receipt, posted = self._post_one(command, request, uow=uow)
            if posted is not None:
                self._factory.emit_correction(
                    posted, event_type=_reversed_event(), actor_id=command.actor_id
                )
                self._record(
                    command,
                    LedgerAction.ENTRY_REVERSED,
                    account_id=entry.account_id,
                    journal_entry_id=posted.id,
                    amount=posted.amount,
                    reason=command.reason,
                    detail=entry.id,
                )
            return receipt

    def adjust(self, command: AdjustJournalEntry) -> PostingReceiptView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            entry = self._entry(command.org_id, command.journal_entry_id)
            request = self._adjustments.request(
                entry,
                delta=Money(command.delta_minor, entry.amount.currency),
                reason=command.reason,
            )
            receipt, posted = self._post_one(command, request, uow=uow)
            if posted is not None:
                self._factory.emit_correction(
                    posted, event_type=_adjusted_event(), actor_id=command.actor_id
                )
                self._record(
                    command,
                    LedgerAction.ENTRY_ADJUSTED,
                    account_id=entry.account_id,
                    journal_entry_id=posted.id,
                    amount=posted.amount,
                    reason=command.reason,
                    detail=entry.id,
                )
            return receipt

    def _post_one(self, command, request: PostingRequest, *, uow):
        """One request, one decision, one write at most.

        Every branch commits *something* — an entry, or an activity record of the duplicate —
        because "we saw this and did nothing at all" is the one outcome that would make the
        financial pipeline unauditable."""
        target = self._repos.periods.by_key(
            command.org_id, request.account_id, self._target_key(request)
        )
        open_period = self._repos.periods.open_for(command.org_id, request.account_id)
        decision = self._posting.decide(
            request,
            target_period=target or open_period,
            open_period=open_period,
            already_posted=self._repos.entries.exists(command.org_id, request.entry_id),
        )
        if not decision.should_write:
            if decision.outcome is PostingOutcome.DUPLICATE:
                self._factory.emit_posting(
                    decision, None, org_id=command.org_id, actor_id=command.actor_id
                )
                self._record(
                    command,
                    LedgerAction.ENTRY_DUPLICATE_IGNORED,
                    account_id=request.account_id,
                    journal_entry_id=decision.entry_id,
                )
                uow.stage(self._event(command, decision.entry_id, outcome="duplicate"))
                stored = self._repos.entries.get(command.org_id, decision.entry_id)
                return receipt_view(decision, stored), None
            self._fail_on_errors(decision.diagnostics)
            raise _NotFound(decision.reason or "the posting was rejected")

        entry = self._factory.compose_entry(
            request,
            decision,
            sequence=self._repos.entries.next_sequence(command.org_id, request.account_id),
            actor_id=command.actor_id,
        )
        stored = self._repos.entries.append(entry)
        self._factory.emit_posting(
            decision, stored, org_id=command.org_id, actor_id=command.actor_id
        )
        self._record(
            command,
            LedgerAction.ENTRY_POSTED,
            account_id=stored.account_id,
            period_key=stored.period_key,
            journal_entry_id=stored.id,
            amount=stored.amount,
        )
        uow.stage(
            self._event(
                command,
                stored.id,
                outcome=decision.outcome.value,
                amount_minor=stored.amount.minor,
            )
        )
        return receipt_view(decision, stored), stored

    # ── credits ─────────────────────────────────────────────────────────────────

    def grant_credit(self, command: GrantCommercialCredit) -> CommercialCreditView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            credit = self._repos.credits.add(
                self._factory.grant_credit(
                    org_id=command.org_id,
                    account_id=command.account_id,
                    code=command.code,
                    kind=command.kind,
                    amount=Money(command.amount_minor, command.currency),
                    expires_at=command.expires_at,
                    source_ref=command.source_ref,
                    reason=command.reason,
                    actor_id=command.actor_id,
                )
            )
            self._record(
                command,
                LedgerAction.CREDIT_GRANTED,
                account_id=credit.account_id,
                credit_id=credit.id,
                amount=credit.amount,
            )
            uow.stage(self._event(command, credit.id, code=credit.code))
            return credit_view(credit, self._credit_lifecycle.allowed(credit.state))

    def apply_credit(self, command: ApplyCommercialCredit) -> CreditApplicationReceiptView:
        """Spend credit against a balance. **Which** credits are spent is the Foundation's
        decision, so two callers asking for the same amount get the same answer."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            now = self._clock.now()
            amount = Money(command.amount_minor, command.currency)
            credits = self._repos.credits.for_account(command.org_id, command.account_id)
            applications = self._repos.applications.for_account(command.org_id, command.account_id)
            positions = {
                c.id: self._credits.position(c, [a for a in applications if a.credit_id == c.id])
                for c in credits
            }
            plan = self._credits.plan(amount, credits, positions, at=now)
            written: list[str] = []
            by_id = {c.id: c for c in credits}
            for allocation in plan.allocations:
                credit = by_id[allocation.credit_id]
                self._fail_on_errors(
                    self._validator.validate_application(
                        credit, positions[credit.id], allocation.amount, at=now
                    )
                )
                stored = self._repos.applications.append(
                    self._factory.apply_credit(
                        credit,
                        amount=allocation.amount,
                        journal_entry_id=command.journal_entry_id,
                        sequence=positions[credit.id].application_count + 1,
                        reason=command.reason,
                        actor_id=command.actor_id,
                    )
                )
                written.append(stored.id)
                self._record(
                    command,
                    LedgerAction.CREDIT_APPLIED,
                    account_id=command.account_id,
                    credit_id=credit.id,
                    amount=allocation.amount,
                )
            uow.stage(self._event(command, command.account_id, applied=len(written)))
            return CreditApplicationReceiptView(
                account_id=command.account_id,
                requested=_money(plan.requested),
                covered=_money(plan.covered),
                shortfall=_money(plan.shortfall),
                is_fully_covered=plan.is_fully_covered,
                applications=tuple(written),
            )

    def expire_credit(self, command: ExpireCommercialCredit) -> CommercialCreditView:
        return self._credit_transition(
            command, CreditTrigger.EXPIRE, LedgerAction.CREDIT_EXPIRED, _credit_expired_event()
        )

    def revoke_credit(self, command: RevokeCommercialCredit) -> CommercialCreditView:
        return self._credit_transition(
            command, CreditTrigger.REVOKE, LedgerAction.CREDIT_REVOKED, _credit_revoked_event()
        )

    # ── checkpoints ─────────────────────────────────────────────────────────────

    def rebuild_checkpoint(self, command: RebuildBalanceCheckpoint) -> BalanceCheckpointView:
        """Recompute a derived checkpoint. A rebuild **replaces** rather than accumulating beside
        the figure it supersedes, because a checkpoint is a cache and never a fact."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            period = self._period(command.org_id, command.account_id, command.period_key)
            entries = self._repos.entries.for_period(
                command.org_id, command.account_id, command.period_key
            )
            checkpoint = self._repos.checkpoints.upsert(
                self._balances.checkpoint(
                    org_id=command.org_id,
                    account_id=command.account_id,
                    period_key=command.period_key,
                    currency=period.currency,
                    entries=entries,
                    computed_at=self._clock.now(),
                )
            )
            self._factory.emit_checkpoint(checkpoint, actor_id=command.actor_id)
            self._record(
                command,
                LedgerAction.CHECKPOINT_REBUILT,
                account_id=command.account_id,
                period_key=command.period_key,
                detail=checkpoint.digest,
            )
            uow.stage(self._event(command, checkpoint.id, digest=checkpoint.digest))
            return checkpoint_view(checkpoint)

    # ── internals ───────────────────────────────────────────────────────────────

    def _period_transition(self, command, trigger, action) -> AccountingPeriodView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            period = self._period(command.org_id, command.account_id, command.period_key)
            moved = self._periods.apply(
                period, trigger, at=self._clock.now(), actor_id=command.actor_id
            )
            stored = self._repos.periods.replace(moved)
            self._factory.emit_period_transition(
                stored, event_type=_period_closing_event(), actor_id=command.actor_id
            )
            self._record(
                command,
                action,
                account_id=stored.account_id,
                period_key=stored.period_key,
            )
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return accounting_period_view(stored, self._periods.allowed(stored.state))

    def _credit_transition(self, command, trigger, action, event_type) -> CommercialCreditView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            credit = self._credit(command.org_id, command.credit_id)
            moved = self._credit_lifecycle.apply(
                credit,
                trigger,
                at=self._clock.now(),
                actor_id=command.actor_id,
                reason=command.reason,
            )
            stored = self._repos.credits.replace(moved)
            self._factory.emit_credit_transition(
                stored, event_type=event_type, actor_id=command.actor_id, reason=command.reason
            )
            self._record(
                command,
                action,
                account_id=stored.account_id,
                credit_id=stored.id,
                reason=command.reason,
            )
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return credit_view(stored, self._credit_lifecycle.allowed(stored.state))

    @staticmethod
    def _target_key(request: PostingRequest) -> str:
        """Which period a request *belongs* to — not necessarily where it lands.

        A rated charge belongs to its rating period; a correction belongs to the period of the entry
        it corrects. When that period has closed the Foundation defers the posting into the next
        open one and records the disposition on the entry, so the two months reconcile against each
        other rather than appearing to disagree."""
        return request.target_period_key

    def _guard_finalized(self, run) -> None:
        """A provisional figure must not become a financial fact.

        M9.12 finalizes a run when its arithmetic explains itself; until then an input can still
        move it. Posting one would put a number on a statement that rating does not yet consider
        settled — the guard M9.12's spec recorded as missing, placed where money is created."""
        if not getattr(run, "is_finalized", False):
            from billing.foundation import BillingConflict

            raise BillingConflict(
                f"rating run {run.id} is {run.state.value}, not finalized; a provisional figure is "
                "one an input can still move, and the ledger records settled facts only"
            )

    def _rating_run(self, org_id: str, run_id: str):
        runs = getattr(self._ctx.rating_engine, "runs", None)
        found = runs.get(org_id, run_id) if runs is not None else None
        if found is None:
            raise _NotFound(f"rating run {run_id!r} not found")
        return found

    def _rated_charges(self, org_id: str, run_id: str) -> tuple:
        """Charges from M9.12's **repository**, which holds originals and adjustments alike.

        The run's payload holds only originals; posting from it would silently omit every
        adjustment `RecordRatingAdjustment` appended."""
        charges = getattr(self._ctx.rating_engine, "charges", None)
        return charges.for_run(org_id, run_id) if charges is not None else ()

    def _entry(self, org_id: str, entry_id: str) -> JournalEntry:
        found = self._repos.entries.get(org_id, entry_id)
        if found is None:
            raise _NotFound(f"journal entry {entry_id!r} not found")
        return found

    def _period(self, org_id: str, account_id: str, period_key: str) -> AccountingPeriod:
        found = self._repos.periods.by_key(org_id, account_id, period_key)
        if found is None:
            raise _NotFound(f"accounting period {period_key!r} not found")
        return found

    def _credit(self, org_id: str, credit_id: str) -> CommercialCredit:
        found = self._repos.credits.get(org_id, credit_id)
        if found is None:
            raise _NotFound(f"credit {credit_id!r} not found")
        return found

    def _fail_on_errors(self, diagnostics) -> None:
        if diagnostics.has_errors:
            from billing.foundation import BillingValidationError

            raise BillingValidationError(diagnostics)

    def _record(
        self,
        command,
        action: LedgerAction,
        *,
        account_id: str = "",
        period_key: str = "",
        journal_entry_id: str = "",
        credit_id: str = "",
        amount: Money | None = None,
        reason: str = "",
        detail: str = "",
    ) -> None:
        """Append ledger history **inside the caller's transaction**, so the history and the fact it
        describes commit or vanish together."""
        self._repos.activity.append(
            self._factory.activity(
                org_id=command.org_id,
                action=action,
                actor_id=command.actor_id,
                account_id=account_id,
                period_key=period_key,
                journal_entry_id=journal_entry_id,
                credit_id=credit_id,
                amount=amount,
                reason=reason,
                detail=detail,
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.LEDGER_POSTED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


class LedgerQueryUseCases:
    """Reads. No transaction, no mutation, no arithmetic — every number comes from a Foundation
    service or a stored aggregate."""

    def __init__(
        self,
        *,
        context,
        ledger_repos: LedgerRepositories,
        balances,
        periods,
        credits,
        credit_lifecycle,
        trial_balances,
        replay,
        reconciliation,
        inspector,
        search,
        validator,
        authz: AuthorizationOrchestrator,
        clock,
    ) -> None:
        self._ctx = context
        self._repos = ledger_repos
        self._balances = balances
        self._periods = periods
        self._credits = credits
        self._credit_lifecycle = credit_lifecycle
        self._trial_balances = trial_balances
        self._replay = replay
        self._reconciliation = reconciliation
        self._inspector = inspector
        self._search = search
        self._validator = validator
        self._authz = authz
        self._clock = clock

    def balance(self, query: GetAccountLedgerBalance) -> AccountBalanceView:
        """Derived on every read. There is no stored balance to return instead."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        entries = self._repos.entries.for_account(query.org_id, query.account_id)
        return balance_view(
            self._balances.balance(
                query.account_id, query.currency, entries, as_of=self._clock.now()
            )
        )

    def entry(self, query: GetJournalEntry) -> JournalEntryView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.entries.get(query.org_id, query.journal_entry_id)
        if found is None:
            raise _NotFound(f"journal entry {query.journal_entry_id!r} not found")
        return entry_view(found)

    def entries(self, query: ListJournalEntries) -> tuple[JournalEntryView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        if query.period_key:
            found = self._repos.entries.for_period(query.org_id, query.account_id, query.period_key)
        elif query.account_id:
            found = self._repos.entries.for_account(query.org_id, query.account_id, query.limit)
        else:
            found = self._repos.entries.all(query.org_id, query.limit)
        return tuple(entry_view(e) for e in found)

    def period(self, query: GetAccountingPeriod) -> AccountingPeriodView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.periods.by_key(query.org_id, query.account_id, query.period_key)
        if found is None:
            raise _NotFound(f"accounting period {query.period_key!r} not found")
        return accounting_period_view(found, self._periods.allowed(found.state))

    def periods(self, query: ListAccountingPeriods) -> tuple[AccountingPeriodView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            accounting_period_view(p, self._periods.allowed(p.state))
            for p in self._repos.periods.for_account(query.org_id, query.account_id)
        )

    def trial_balance(self, query: GetTrialBalance) -> TrialBalanceView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        entries = self._repos.entries.for_period(query.org_id, query.account_id, query.period_key)
        return trial_balance_view(
            self._trial_balances.compute(
                entries,
                org_id=query.org_id,
                account_id=query.account_id,
                period_key=query.period_key,
                currency=query.currency,
                opening=Money.zero(query.currency),
                at=self._clock.now(),
            )
        )

    def credit_position(self, query: GetAccountCreditPosition) -> CreditBalanceOverview:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        credits = self._repos.credits.for_account(query.org_id, query.account_id)
        applications = self._repos.applications.for_account(query.org_id, query.account_id)
        return credit_balance_view(
            self._credits.balance(query.account_id, query.currency, credits, applications)
        )

    def credits(self, query: ListCommercialCredits) -> tuple[CommercialCreditView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = (
            self._repos.credits.for_account(query.org_id, query.account_id)
            if query.account_id
            else self._repos.credits.all(query.org_id)
        )
        return tuple(credit_view(c, self._credit_lifecycle.allowed(c.state)) for c in found)

    def replay(self, query: ReplayLedgerPeriod) -> LedgerReplayView:
        """Reconstruct the period and compare against what it sealed. A pure read: replaying
        writes nothing, which is what makes it safe to run in production."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        entries = self._repos.entries.for_period(query.org_id, query.account_id, query.period_key)
        period = self._repos.periods.by_key(query.org_id, query.account_id, query.period_key)
        return replay_view(
            self._replay.replay(
                entries,
                org_id=query.org_id,
                account_id=query.account_id,
                period=period,
                currency=query.currency,
                at=self._clock.now(),
            )
        )

    def reconcile(self, query: ReconcileLedgerPeriod) -> LedgerReconciliationView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        entries = self._repos.entries.for_period(query.org_id, query.account_id, query.period_key)
        expected = [
            (c.id, abs(c.amount.minor))
            for c in self._expected_charges(query.org_id, query.rating_run_id)
            if not c.amount.is_zero
        ]
        checkpoint = self._repos.checkpoints.get(query.org_id, query.account_id, query.period_key)
        return reconciliation_view(
            self._reconciliation.reconcile(
                org_id=query.org_id,
                account_id=query.account_id,
                period_key=query.period_key,
                expected=expected,
                entries=entries,
                checkpoint=checkpoint,
                at=self._clock.now(),
            )
        )

    def inspect(self, query: InspectLedgerPeriod) -> LedgerInspectionView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        entries = self._repos.entries.for_period(query.org_id, query.account_id, query.period_key)
        period = self._repos.periods.by_key(query.org_id, query.account_id, query.period_key)
        checkpoint = self._repos.checkpoints.get(query.org_id, query.account_id, query.period_key)
        return inspection_view(
            self._inspector.inspect(
                entries,
                org_id=query.org_id,
                account_id=query.account_id,
                period_key=query.period_key,
                state=period.state if period else _default_period_state(),
                currency=period.currency if period else query.currency,
                checkpoint=checkpoint,
            )
        )

    def diagnose(self, query: DiagnoseLedgerPeriod) -> LedgerDiagnosticsView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        entries = self._repos.entries.for_period(query.org_id, query.account_id, query.period_key)
        period = self._repos.periods.by_key(query.org_id, query.account_id, query.period_key)
        if period is None:
            raise _NotFound(f"accounting period {query.period_key!r} not found")
        return diagnostics_view(
            query.account_id,
            query.period_key,
            self._validator.validate_period_close(period, entries),
        )

    def search(self, query: SearchJournalEntries) -> LedgerSearchView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._search.search(self._repos.entries.all(query.org_id, 2000), query.query)
        return LedgerSearchView(
            entries=tuple(entry_view(e) for e in found.entries), total=found.total
        )

    def timeline(self, query: LedgerTimeline) -> tuple[LedgerActivityView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            activity_view(a)
            for a in self._repos.activity.history(query.org_id, query.account_id, query.limit)
        )

    def _expected_charges(self, org_id: str, rating_run_id: str) -> tuple:
        if not rating_run_id:
            return ()
        charges = getattr(self._ctx.rating_engine, "charges", None)
        return charges.for_run(org_id, rating_run_id) if charges is not None else ()


# ── event lookups ───────────────────────────────────────────────────────────────


def _default_period_state():
    from billing.foundation import AccountingPeriodState

    return AccountingPeriodState.OPEN


def _period_closing_event():
    from billing.foundation import BillingEventType as E

    return E.ACCOUNTING_PERIOD_CLOSING


def _period_closed_event():
    from billing.foundation import BillingEventType as E

    return E.ACCOUNTING_PERIOD_CLOSED


def _reversed_event():
    from billing.foundation import BillingEventType as E

    return E.JOURNAL_ENTRY_REVERSED


def _adjusted_event():
    from billing.foundation import BillingEventType as E

    return E.JOURNAL_ENTRY_ADJUSTED


def _credit_expired_event():
    from billing.foundation import BillingEventType as E

    return E.CREDIT_EXPIRED


def _credit_revoked_event():
    from billing.foundation import BillingEventType as E

    return E.COMMERCIAL_CREDIT_REVOKED


# ── wiring table ────────────────────────────────────────────────────────────────


def register_ledger_command_handlers(bus, uc: LedgerCommandUseCases) -> None:
    bus.register(OpenAccountingPeriod, uc.open_period)
    bus.register(BeginAccountingPeriodClose, uc.begin_close)
    bus.register(CloseAccountingPeriod, uc.close_period)
    bus.register(PostRatingRun, uc.post_rating_run)
    bus.register(PostJournalEntry, uc.post_entry)
    bus.register(ReverseJournalEntry, uc.reverse)
    bus.register(AdjustJournalEntry, uc.adjust)
    bus.register(GrantCommercialCredit, uc.grant_credit)
    bus.register(ApplyCommercialCredit, uc.apply_credit)
    bus.register(ExpireCommercialCredit, uc.expire_credit)
    bus.register(RevokeCommercialCredit, uc.revoke_credit)
    bus.register(RebuildBalanceCheckpoint, uc.rebuild_checkpoint)


def register_ledger_query_handlers(bus, uc: LedgerQueryUseCases) -> None:
    bus.register(GetAccountLedgerBalance, uc.balance)
    bus.register(GetJournalEntry, uc.entry)
    bus.register(ListJournalEntries, uc.entries)
    bus.register(GetAccountingPeriod, uc.period)
    bus.register(ListAccountingPeriods, uc.periods)
    bus.register(GetTrialBalance, uc.trial_balance)
    bus.register(GetAccountCreditPosition, uc.credit_position)
    bus.register(ListCommercialCredits, uc.credits)
    bus.register(ReplayLedgerPeriod, uc.replay)
    bus.register(ReconcileLedgerPeriod, uc.reconcile)
    bus.register(InspectLedgerPeriod, uc.inspect)
    bus.register(DiagnoseLedgerPeriod, uc.diagnose)
    bus.register(SearchJournalEntries, uc.search)
    bus.register(LedgerTimeline, uc.timeline)

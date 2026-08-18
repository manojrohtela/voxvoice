"""Credit & Revenue Ledger routes (M9.13) — transport only.

Additive to the M9.5–M9.12 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No accounting rule is
evaluated here — whether a posting happens, where a late one lands, how a balance folds, when a
period may close, which credit is spent first, and whether a replay agrees are all Foundation
behaviour reached through the Application.

Mounted at **`/accounting`**, not under M9.5's `/ledger` or `/credits`. Same reason every domain
since M9.8 took its own prefix: siblings under an existing wildcard would be shadowed depending on
router registration order, and the M9.2 aggregate's minimal surface still answers on the old paths.

**Five things no endpoint here does.** None computes an amount — every figure came from a Foundation
service. None edits a posted entry: the surface has no PUT and no PATCH on an entry, and there is no
repository method one could reach. None deletes anything, anywhere. None reopens a closed period —
the FSM has no edge out of `closed`, so the route does not exist to be called. And none posts a
provisional rating run: a figure an input can still move is refused with a 409 rather than recorded.

Posting returns **201** for a new entry and **200** for a duplicate, so a caller can tell "recorded"
from "already recorded" without either being an error.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from billing.application import (
    AdjustJournalEntry,
    ApplyCommercialCredit,
    BeginAccountingPeriodClose,
    BillingApplication,
    CloseAccountingPeriod,
    DiagnoseLedgerPeriod,
    ExpireCommercialCredit,
    GetAccountCreditPosition,
    GetAccountingPeriod,
    GetAccountLedgerBalance,
    GetJournalEntry,
    GetTrialBalance,
    GrantCommercialCredit,
    InspectLedgerPeriod,
    LedgerTimeline,
    ListAccountingPeriods,
    ListCommercialCredits,
    ListJournalEntries,
    OpenAccountingPeriod,
    PostJournalEntry,
    PostRatingRun,
    RebuildBalanceCheckpoint,
    ReconcileLedgerPeriod,
    ReplayLedgerPeriod,
    ReverseJournalEntry,
    RevokeCommercialCredit,
    SearchJournalEntries,
)
from billing.foundation import Currency, LedgerDirection, LedgerEntryType, PostingSource

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..ledger_schemas import (
    AccountBalanceResponse,
    AccountingPeriodBody,
    AccountingPeriodResponse,
    AdjustEntryBody,
    ApplyCreditBody,
    BalanceCheckpointResponse,
    CommercialCreditResponse,
    CreditApplicationReceiptResponse,
    CreditBalanceResponse,
    GrantCommercialCreditBody,
    JournalEntryResponse,
    LedgerActivityResponse,
    LedgerDiagnosticsResponse,
    LedgerInspectionResponse,
    LedgerReconciliationResponse,
    LedgerReplayResponse,
    LedgerSearchResponse,
    PeriodKeyBody,
    PostingReceiptResponse,
    PostJournalEntryBody,
    PostRatingRunBody,
    ReasonBody,
    RunPostingReceiptResponse,
    TrialBalanceResponse,
    build_ledger_query,
)
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/accounting", tags=["ledger"], responses=ERROR_RESPONSES)


# ── accounting periods ──────────────────────────────────────────────────────────


@router.post(
    "/periods",
    response_model=AccountingPeriodResponse,
    status_code=201,
    summary="Open an accounting period",
)
def open_period(
    body: AccountingPeriodBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AccountingPeriodResponse:
    """Idempotent: opening the same window twice returns the existing period. Two periods for one
    window would split an account's financial history across both."""
    return _period(
        app.commands.dispatch(
            OpenAccountingPeriod(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=body.account_id,
                period_start=body.period_start,
                period_end=body.period_end,
                currency=body.currency,
            )
        )
    )


@router.post(
    "/periods/close/begin",
    response_model=AccountingPeriodResponse,
    summary="Halt new postings ahead of a close",
)
def begin_close(
    body: PeriodKeyBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AccountingPeriodResponse:
    return _period(
        app.commands.dispatch(
            BeginAccountingPeriodClose(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=body.account_id,
                period_key=body.period_key,
            )
        )
    )


@router.post(
    "/periods/close",
    response_model=AccountingPeriodResponse,
    summary="Close a period and seal its digest — a one-way door",
)
def close_period(
    body: PeriodKeyBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AccountingPeriodResponse:
    """After this, a late fact is recorded in the next open period as an adjustment. There is no
    route that reopens a closed period, because the lifecycle has no edge out of it."""
    return _period(
        app.commands.dispatch(
            CloseAccountingPeriod(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=body.account_id,
                period_key=body.period_key,
            )
        )
    )


@router.get(
    "/periods", response_model=list[AccountingPeriodResponse], summary="List accounting periods"
)
def list_periods(
    account_id: str = Query(),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[AccountingPeriodResponse]:
    return [
        _period(p)
        for p in app.queries.dispatch(
            ListAccountingPeriods(
                actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id
            )
        )
    ]


@router.get(
    "/periods/{period_key}",
    response_model=AccountingPeriodResponse,
    summary="One accounting period",
)
def get_period(
    period_key: str,
    account_id: str = Query(),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AccountingPeriodResponse:
    return _period(
        app.queries.dispatch(
            GetAccountingPeriod(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                period_key=period_key,
            )
        )
    )


# ── posting ─────────────────────────────────────────────────────────────────────


@router.post(
    "/postings/rating-run",
    response_model=RunPostingReceiptResponse,
    status_code=201,
    summary="Record a finalized rating run's charges",
)
def post_rating_run(
    body: PostRatingRunBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RunPostingReceiptResponse:
    """The only path by which commercial facts become financial ones. A provisional run is refused
    with a 409, and re-posting a run records nothing the second time."""
    batch = app.commands.dispatch(
        PostRatingRun(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            rating_run_id=body.rating_run_id,
        )
    )
    return RunPostingReceiptResponse(
        rating_run_id=batch.rating_run_id,
        account_id=batch.account_id,
        charges_seen=batch.charges_seen,
        posted=batch.posted,
        duplicates=batch.duplicates,
        deferred=batch.deferred,
        skipped=batch.skipped,
        total=batch.total.__dict__,
        receipts=[_receipt(r) for r in batch.receipts],
    )


@router.post(
    "/postings",
    response_model=PostingReceiptResponse,
    summary="Record an operator posting",
)
def post_entry(
    body: PostJournalEntryBody,
    response: Response,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PostingReceiptResponse:
    """**201** for a new entry, **200** when the cause was already recorded. Both are successes."""
    receipt = app.commands.dispatch(
        PostJournalEntry(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            entry_type=body.entry_type,
            direction=body.direction,
            amount_minor=body.amount.minor,
            currency=Currency(body.amount.currency),
            reference=body.reference,
            description=body.description,
        )
    )
    response.status_code = 200 if receipt.outcome == "duplicate" else 201
    return _receipt(receipt)


@router.post(
    "/entries/{journal_entry_id}/reverse",
    response_model=PostingReceiptResponse,
    status_code=201,
    summary="Withdraw an entry by posting its opposite",
)
def reverse_entry(
    journal_entry_id: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PostingReceiptResponse:
    """Never an edit and never a deletion. Reversing a reversal, or reversing twice, is refused."""
    return _receipt(
        app.commands.dispatch(
            ReverseJournalEntry(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                journal_entry_id=journal_entry_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/entries/{journal_entry_id}/adjust",
    response_model=PostingReceiptResponse,
    status_code=201,
    summary="Move a recorded figure without withdrawing it",
)
def adjust_entry(
    journal_entry_id: str,
    body: AdjustEntryBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PostingReceiptResponse:
    """If the original's period has closed, the adjustment lands in the next open one and the
    entry records that it did."""
    return _receipt(
        app.commands.dispatch(
            AdjustJournalEntry(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                journal_entry_id=journal_entry_id,
                delta_minor=body.delta_minor,
                reason=body.reason,
            )
        )
    )


# ── credits ─────────────────────────────────────────────────────────────────────


@router.post(
    "/credits",
    response_model=CommercialCreditResponse,
    status_code=201,
    summary="Grant a credit",
)
def grant_credit(
    body: GrantCommercialCreditBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialCreditResponse:
    return _credit(
        app.commands.dispatch(
            GrantCommercialCredit(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=body.account_id,
                code=body.code,
                kind=body.kind,
                amount_minor=body.amount.minor,
                currency=Currency(body.amount.currency),
                expires_at=body.expires_at,
                source_ref=body.source_ref,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/credits/apply",
    response_model=CreditApplicationReceiptResponse,
    status_code=201,
    summary="Spend credit — earliest-expiring first",
)
def apply_credit(
    body: ApplyCreditBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CreditApplicationReceiptResponse:
    """The caller names an amount; the server decides which credits are spent, so two callers
    asking for the same thing get the same answer."""
    receipt = app.commands.dispatch(
        ApplyCommercialCredit(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            amount_minor=body.amount.minor,
            currency=Currency(body.amount.currency),
            journal_entry_id=body.journal_entry_id,
            reason=body.reason,
        )
    )
    return CreditApplicationReceiptResponse(
        account_id=receipt.account_id,
        requested=receipt.requested.__dict__,
        covered=receipt.covered.__dict__,
        shortfall=receipt.shortfall.__dict__,
        is_fully_covered=receipt.is_fully_covered,
        applications=list(receipt.applications),
    )


@router.post(
    "/credits/{credit_id}/expire",
    response_model=CommercialCreditResponse,
    summary="Close out a lapsed credit",
)
def expire_credit(
    credit_id: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialCreditResponse:
    return _credit(
        app.commands.dispatch(
            ExpireCommercialCredit(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                credit_id=credit_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/credits/{credit_id}/revoke",
    response_model=CommercialCreditResponse,
    summary="Withdraw a credit (applications already made stay applied)",
)
def revoke_credit(
    credit_id: str,
    body: ReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialCreditResponse:
    return _credit(
        app.commands.dispatch(
            RevokeCommercialCredit(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                credit_id=credit_id,
                reason=body.reason,
            )
        )
    )


@router.get("/credits", response_model=list[CommercialCreditResponse], summary="List credits")
def list_credits(
    account_id: str = Query(default=""),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CommercialCreditResponse]:
    return [
        _credit(c)
        for c in app.queries.dispatch(
            ListCommercialCredits(
                actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id
            )
        )
    ]


@router.get(
    "/credits/position",
    response_model=CreditBalanceResponse,
    summary="Available credit, derived from applications",
)
def credit_position(
    account_id: str = Query(),
    currency: Currency = Query(default=Currency.INR),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CreditBalanceResponse:
    overview = app.queries.dispatch(
        GetAccountCreditPosition(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            currency=currency,
        )
    )
    return CreditBalanceResponse(
        account_id=overview.account_id,
        currency=overview.currency,
        available=overview.available.__dict__,
        granted=overview.granted.__dict__,
        applied=overview.applied.__dict__,
        credit_count=overview.credit_count,
        positions=[
            {
                **p.__dict__,
                "granted": p.granted.__dict__,
                "applied": p.applied.__dict__,
                "remaining": p.remaining.__dict__,
            }
            for p in overview.positions
        ],
    )


# ── reads ───────────────────────────────────────────────────────────────────────


@router.get(
    "/balance",
    response_model=AccountBalanceResponse,
    summary="An account's balance, derived from its entries",
)
def balance(
    account_id: str = Query(),
    currency: Currency = Query(default=Currency.INR),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AccountBalanceResponse:
    """Folded on every read. There is no stored balance this could return instead."""
    view = app.queries.dispatch(
        GetAccountLedgerBalance(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            currency=currency,
        )
    )
    return AccountBalanceResponse(
        **{
            **view.__dict__,
            "outstanding": view.outstanding.__dict__,
            "debits": view.debits.__dict__,
            "credits": view.credits.__dict__,
        }
    )


@router.get("/entries", response_model=list[JournalEntryResponse], summary="List journal entries")
def list_entries(
    account_id: str = Query(default=""),
    period_key: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[JournalEntryResponse]:
    return [
        _entry(e)
        for e in app.queries.dispatch(
            ListJournalEntries(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                period_key=period_key,
                limit=limit,
            )
        )
    ]


@router.get(
    "/entries/{journal_entry_id}",
    response_model=JournalEntryResponse,
    summary="One journal entry with its full lineage",
)
def get_entry(
    journal_entry_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> JournalEntryResponse:
    return _entry(
        app.queries.dispatch(
            GetJournalEntry(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                journal_entry_id=journal_entry_id,
            )
        )
    )


@router.get(
    "/trial-balance",
    response_model=TrialBalanceResponse,
    summary="The subledger control report",
)
def trial_balance(
    account_id: str = Query(),
    period_key: str = Query(),
    currency: Currency = Query(default=Currency.INR),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TrialBalanceResponse:
    view = app.queries.dispatch(
        GetTrialBalance(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            period_key=period_key,
            currency=currency,
        )
    )
    return TrialBalanceResponse(
        account_id=view.account_id,
        period_key=view.period_key,
        currency=view.currency,
        lines=[
            {
                "entry_type": line.entry_type,
                "debits": line.debits.__dict__,
                "credits": line.credits.__dict__,
                "net": line.net.__dict__,
                "entry_count": line.entry_count,
            }
            for line in view.lines
        ],
        total_debits=view.total_debits.__dict__,
        total_credits=view.total_credits.__dict__,
        movement=view.movement.__dict__,
        opening_balance=view.opening_balance.__dict__ if view.opening_balance else None,
        closing_balance=view.closing_balance.__dict__ if view.closing_balance else None,
        reconciles=view.reconciles,
    )


@router.get(
    "/replay",
    response_model=LedgerReplayResponse,
    summary="Reconstruct a period and prove it matches what was sealed",
)
def replay(
    account_id: str = Query(),
    period_key: str = Query(),
    currency: Currency = Query(default=Currency.INR),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> LedgerReplayResponse:
    """A **query**: replaying writes nothing, which is what makes it safe to run in production."""
    view = app.queries.dispatch(
        ReplayLedgerPeriod(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            period_key=period_key,
            currency=currency,
        )
    )
    return LedgerReplayResponse(
        **{
            **view.__dict__,
            "rebuilt_balance": view.rebuilt_balance.__dict__,
            "recorded_balance": view.recorded_balance.__dict__,
        }
    )


@router.get(
    "/reconcile",
    response_model=LedgerReconciliationResponse,
    summary="Does the ledger say what the rating run says?",
)
def reconcile(
    account_id: str = Query(),
    period_key: str = Query(),
    rating_run_id: str = Query(default=""),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> LedgerReconciliationResponse:
    view = app.queries.dispatch(
        ReconcileLedgerPeriod(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            period_key=period_key,
            rating_run_id=rating_run_id,
        )
    )
    return LedgerReconciliationResponse(
        **{**view.__dict__, "discrepancies": [d.__dict__ for d in view.discrepancies]}
    )


@router.get(
    "/inspect",
    response_model=LedgerInspectionResponse,
    summary="Is every entry traced, sequenced, and accounted for?",
)
def inspect(
    account_id: str = Query(),
    period_key: str = Query(),
    currency: Currency = Query(default=Currency.INR),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> LedgerInspectionResponse:
    view = app.queries.dispatch(
        InspectLedgerPeriod(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            period_key=period_key,
            currency=currency,
        )
    )
    return LedgerInspectionResponse(
        account_id=view.account_id,
        period_key=view.period_key,
        state=view.state,
        balance=(
            {
                **view.balance.__dict__,
                "outstanding": view.balance.outstanding.__dict__,
                "debits": view.balance.debits.__dict__,
                "credits": view.balance.credits.__dict__,
            }
            if view.balance
            else None
        ),
        entries=[
            {
                **e.__dict__,
                "amount": e.amount.__dict__,
                "corrected_by": list(e.corrected_by),
                "notes": list(e.notes),
            }
            for e in view.entries
        ],
        sequence_gaps=list(view.sequence_gaps),
        checkpoint_consistent=view.checkpoint_consistent,
        is_sound=view.is_sound,
        untraced=list(view.untraced),
        notes=list(view.notes),
    )


@router.get(
    "/diagnostics",
    response_model=LedgerDiagnosticsResponse,
    summary="Structural findings for one period",
)
def diagnose(
    account_id: str = Query(),
    period_key: str = Query(),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> LedgerDiagnosticsResponse:
    view = app.queries.dispatch(
        DiagnoseLedgerPeriod(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            period_key=period_key,
        )
    )
    return LedgerDiagnosticsResponse(
        **{**view.__dict__, "findings": [f.__dict__ for f in view.findings]}
    )


@router.post(
    "/checkpoints/rebuild",
    response_model=BalanceCheckpointResponse,
    summary="Recompute a derived balance checkpoint",
)
def rebuild_checkpoint(
    body: PeriodKeyBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BalanceCheckpointResponse:
    """A checkpoint is a cache with a proof. A rebuild replaces it rather than accumulating beside
    the figure it supersedes."""
    view = app.commands.dispatch(
        RebuildBalanceCheckpoint(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            period_key=body.period_key,
        )
    )
    return BalanceCheckpointResponse(**{**view.__dict__, "outstanding": view.outstanding.__dict__})


@router.get("/search", response_model=LedgerSearchResponse, summary="Search journal entries")
def search(
    text: str = Query(default=""),
    account_id: str = Query(default=""),
    period_key: str = Query(default=""),
    entry_types: list[LedgerEntryType] = Query(default=[]),
    directions: list[LedgerDirection] = Query(default=[]),
    sources: list[PostingSource] = Query(default=[]),
    corrections_only: bool = Query(default=False),
    min_amount_minor: int | None = Query(default=None),
    max_amount_minor: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> LedgerSearchResponse:
    found = app.queries.dispatch(
        SearchJournalEntries(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            query=build_ledger_query(
                text=text,
                account_id=account_id,
                period_key=period_key,
                entry_types=entry_types,
                directions=directions,
                sources=sources,
                corrections_only=corrections_only,
                min_amount_minor=min_amount_minor,
                max_amount_minor=max_amount_minor,
                limit=limit,
                offset=offset,
            ),
        )
    )
    return LedgerSearchResponse(entries=[_entry(e) for e in found.entries], total=found.total)


@router.get(
    "/timeline",
    response_model=list[LedgerActivityResponse],
    summary="Ledger history, newest first",
)
def timeline(
    account_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[LedgerActivityResponse]:
    """Includes the postings that recorded nothing — a duplicate entry in this log is the evidence
    exactly-once posting worked."""
    return [
        LedgerActivityResponse(**{**a.__dict__, "amount": a.amount.__dict__ if a.amount else None})
        for a in app.queries.dispatch(
            LedgerTimeline(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                limit=limit,
            )
        )
    ]


# ── serialization helpers ───────────────────────────────────────────────────────


def _entry(view) -> JournalEntryResponse:
    return JournalEntryResponse(
        **{
            **view.__dict__,
            "amount": view.amount.__dict__,
            "references": view.references.__dict__,
        }
    )


def _period(view) -> AccountingPeriodResponse:
    return AccountingPeriodResponse(
        **{
            **view.__dict__,
            "debits": view.debits.__dict__,
            "credits": view.credits.__dict__,
            "closing_balance": view.closing_balance.__dict__,
            "allowed_triggers": list(view.allowed_triggers),
        }
    )


def _receipt(view) -> PostingReceiptResponse:
    return PostingReceiptResponse(
        outcome=view.outcome,
        journal_entry_id=view.journal_entry_id,
        period_key=view.period_key,
        disposition=view.disposition,
        reason=view.reason,
        entry=_entry(view.entry) if view.entry else None,
    )


def _credit(view) -> CommercialCreditResponse:
    return CommercialCreditResponse(
        **{
            **view.__dict__,
            "amount": view.amount.__dict__,
            "allowed_triggers": list(view.allowed_triggers),
        }
    )

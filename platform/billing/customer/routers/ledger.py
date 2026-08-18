"""Ledger routes (M9.5) — transport only.

The ledger is **append-only**: there is no update endpoint and no delete endpoint, because
there is no update or delete path anywhere beneath this layer either (ADR-0023 RS1). A
correction is a new, later entry that references the original.

The balance is *derived* from those entries on read — never a stored counter that could drift
from the entries that justify it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing.application import BillingApplication, GetLedgerBalance, PostLedgerEntry

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES, BalanceResponse, PostLedgerEntryBody

router = APIRouter(prefix="/ledger", tags=["ledger"], responses=ERROR_RESPONSES)


@router.post("/entries", status_code=201, summary="Append a ledger entry")
def post_entry(
    body: PostLedgerEntryBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> dict:
    entry = app.commands.dispatch(
        PostLedgerEntry(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            entry_type=body.entry_type,
            direction=body.direction,
            amount=body.amount.to_money(),
            invoice_id=body.invoice_id,
            payment_id=body.payment_id,
            subscription_id=body.subscription_id,
            description=body.description,
        )
    )
    return {
        "id": entry.id,
        "entry_type": entry.entry_type.value,
        "direction": entry.direction.value,
        "amount": {"minor": entry.amount.minor, "currency": entry.amount.currency.value},
        "recorded_at": entry.recorded_at,
    }


@router.get(
    "/balance/{account_id}",
    response_model=BalanceResponse,
    summary="Outstanding balance (derived from the entries)",
)
def ledger_balance(
    account_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BalanceResponse:
    view = app.queries.dispatch(
        GetLedgerBalance(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id)
    )
    return BalanceResponse.from_view(view)

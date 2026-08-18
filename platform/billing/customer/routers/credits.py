"""Credit routes (M9.5) — transport only."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing.application import BillingApplication, GetCreditBalance, GrantCredit

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES, BalanceResponse, GrantCreditBody

router = APIRouter(prefix="/credits", tags=["credits"], responses=ERROR_RESPONSES)


@router.post("", status_code=201, summary="Grant credit to an account")
def grant_credit(
    body: GrantCreditBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> dict:
    grant = app.commands.dispatch(
        GrantCredit(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            kind=body.kind,
            amount=body.amount.to_money(),
            expires_at=body.expires_at,
            source_ref=body.source_ref,
        )
    )
    return {
        "id": grant.id,
        "kind": grant.kind.value,
        "amount": {"minor": grant.amount.minor, "currency": grant.amount.currency.value},
        "expires_at": grant.expires_at,
    }


@router.get(
    "/balance/{account_id}",
    response_model=BalanceResponse,
    summary="Available credit (derived from grants and movements)",
)
def credit_balance(
    account_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BalanceResponse:
    view = app.queries.dispatch(
        GetCreditBalance(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id)
    )
    return BalanceResponse.from_view(view)

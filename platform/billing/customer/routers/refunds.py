"""Refund routes (M9.5) — transport only.

Requesting and approving a refund are **separate endpoints** because they are separate
permissions below (`BILLING_REFUND_REQUEST` vs `BILLING_REFUND_APPROVE`). The API does not
enforce that separation — it preserves it, by not collapsing two authorized acts into one
call that a single permission could cover.

`request_key` is the caller's idempotency key: re-requesting with the same key returns the
existing refund rather than issuing a second one against the same payment.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing.application import (
    ApproveRefund,
    BillingApplication,
    ListRefunds,
    RequestRefund,
)

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES, ApproveRefundBody, RefundResponse, RequestRefundBody

router = APIRouter(prefix="/refunds", tags=["refunds"], responses=ERROR_RESPONSES)


@router.post("", response_model=RefundResponse, status_code=201, summary="Request a refund")
def request_refund(
    body: RequestRefundBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RefundResponse:
    refund = app.commands.dispatch(
        RequestRefund(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            payment_id=body.payment_id,
            invoice_id=body.invoice_id,
            amount=body.amount.to_money(),
            reason=body.reason,
            request_key=body.request_key,
            captured=body.captured.to_money() if body.captured else None,
        )
    )
    return RefundResponse.from_aggregate(refund)


@router.post(
    "/{refund_id}/approve",
    response_model=RefundResponse,
    summary="Approve a refund (a distinct permission from requesting one)",
)
def approve_refund(
    refund_id: str,
    body: ApproveRefundBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RefundResponse:
    refund = app.commands.dispatch(
        ApproveRefund(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            refund_id=refund_id,
            note=body.note,
        )
    )
    return RefundResponse.from_aggregate(refund)


@router.get("", response_model=list[RefundResponse], summary="List refunds for a payment")
def list_refunds(
    payment_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RefundResponse]:
    views = app.queries.dispatch(
        ListRefunds(actor_id=ctx.actor(), org_id=ctx.organization(), payment_id=payment_id)
    )
    return [RefundResponse.from_view(v) for v in views]

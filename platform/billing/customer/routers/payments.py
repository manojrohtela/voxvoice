"""Payment routes (M9.5) — transport only.

**No payment is executed here.** This endpoint records Billing's intent to be paid; moving
money is a rented edge behind a provider adapter (M9.14, ADR-0023 PY-series / M9.0C Option
C). A controller that called a payment provider would have made the transport layer the
authority for money movement — the single most expensive mistake available in this package.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing.application import BillingApplication, ListPayments, RequestPayment

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES, PaymentResponse, RequestPaymentBody

router = APIRouter(prefix="/payments", tags=["payments"], responses=ERROR_RESPONSES)


@router.post(
    "",
    response_model=PaymentResponse,
    status_code=201,
    summary="Request payment for an invoice (records intent; executes nothing)",
)
def request_payment(
    body: RequestPaymentBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PaymentResponse:
    payment = app.commands.dispatch(
        RequestPayment(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            invoice_id=body.invoice_id,
            amount=body.amount.to_money(),
            method=body.method,
            instrument_id=body.instrument_id,
        )
    )
    return PaymentResponse.from_aggregate(payment)


@router.get("", response_model=list[PaymentResponse], summary="List payments for an invoice")
def list_payments(
    invoice_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[PaymentResponse]:
    views = app.queries.dispatch(
        ListPayments(actor_id=ctx.actor(), org_id=ctx.organization(), invoice_id=invoice_id)
    )
    return [PaymentResponse.from_view(v) for v in views]

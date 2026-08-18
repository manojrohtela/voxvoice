"""Invoice routes (M9.5) — transport only.

An **issued** invoice is a legal document, not a mutable record: there is no PATCH and no
DELETE on this resource, and attempting to replace one below this layer returns 409
(ADR-0023 IN2). Correcting an issued invoice is a credit note, which arrives with M9.13.

Note the resource shape: a draft is `PATCH`-able only while it is a draft, and issuing is a
separate `POST .../issue` rather than a state field a client can set. Issuance allocates a
gapless invoice number and is the one-way door.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing.application import (
    BillingApplication,
    DraftInvoice,
    GetInvoice,
    IssueInvoice,
    ListInvoices,
)
from billing.foundation import BillingNotFound

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES, DraftInvoiceBody, InvoiceResponse, IssueInvoiceBody

router = APIRouter(prefix="/invoices", tags=["invoices"], responses=ERROR_RESPONSES)


@router.post("", response_model=InvoiceResponse, status_code=201, summary="Draft an invoice")
def draft_invoice(
    body: DraftInvoiceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvoiceResponse:
    """Totals are computed by the Foundation's `InvoiceTotalsService` from the lines given —
    this endpoint sums nothing."""
    invoice = app.commands.dispatch(
        DraftInvoice(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            currency=body.currency,
            period=body.period.to_period(),
            lines=body.to_lines(ctx.organization()),
            subscription_id=body.subscription_id,
            payment_terms_days=body.payment_terms_days,
            purchase_order_ref=body.purchase_order_ref,
        )
    )
    return InvoiceResponse.from_aggregate(invoice)


@router.get("", response_model=list[InvoiceResponse], summary="List an account's invoices")
def list_invoices(
    account_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[InvoiceResponse]:
    views = app.queries.dispatch(
        ListInvoices(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id)
    )
    return [InvoiceResponse.from_view(v) for v in views]


@router.get("/{invoice_id}", response_model=InvoiceResponse, summary="Get an invoice")
def get_invoice(
    invoice_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvoiceResponse:
    view = app.queries.dispatch(
        GetInvoice(actor_id=ctx.actor(), org_id=ctx.organization(), invoice_id=invoice_id)
    )
    if view is None:
        raise BillingNotFound(f"invoice {invoice_id!r} not found")
    return InvoiceResponse.from_view(view)


@router.post(
    "/{invoice_id}/issue",
    response_model=InvoiceResponse,
    summary="Issue an invoice (allocates a gapless number; irreversible)",
)
def issue_invoice(
    invoice_id: str,
    body: IssueInvoiceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvoiceResponse:
    invoice = app.commands.dispatch(
        IssueInvoice(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            invoice_id=invoice_id,
            entity_code=body.entity_code,
            jurisdiction=body.jurisdiction,
        )
    )
    return InvoiceResponse.from_aggregate(invoice)

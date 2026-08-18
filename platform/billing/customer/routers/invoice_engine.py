"""Invoice Engine routes (M9.14) — transport only.

Additive to the M9.5–M9.13 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No document rule is evaluated
here — which lines appear, how a snapshot is sealed, how a number reads, what a correction contains,
and whether a document agrees with the ledger are all Foundation behaviour reached through the
Application.

Mounted at **`/documents`**, not under M9.5's `/invoices`. Same reason every domain since M9.8 took
its own prefix: siblings under an existing wildcard would be shadowed depending on router
registration order, and the M9.2 aggregate's minimal surface still answers on the old paths.

**Six things no endpoint here does.** None computes an amount — every figure came from a sealed
snapshot. None allocates a number: the sequence is drawn inside the Application's transaction, and
a controller that could allocate one could leak it on a failure. None edits an issued document: the
surface has no PUT and no PATCH, and the FSM has no edge back to `draft`. None deletes anything,
anywhere. None voids an issued document — the route does not exist, because a gapless series has no
hole to spare. And none renders: `/render` dispatches a command that calls the Foundation renderers.

Static paths are declared **before** `/{document_id}`, so `/documents/preview` is a preview and not
a document whose id happens to be "preview".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    AuditInvoiceNumberSeries,
    BillingApplication,
    DiagnoseCommercialInvoice,
    ExportInvoiceDocument,
    GetCommercialInvoice,
    GetInvoiceDocument,
    GetInvoiceRevisions,
    GetRenderedInvoice,
    InspectCommercialInvoice,
    InvoiceTimeline,
    IssueCommercialInvoice,
    IssueCreditNote,
    IssueDebitNote,
    ListCommercialInvoices,
    ListInvoiceRelations,
    MarkInvoiceOverdue,
    MarkInvoicePaid,
    PrepareInvoice,
    PreviewInvoice,
    ReconcileInvoiceWithLedger,
    RecordInvoiceAttachment,
    RecordInvoiceDelivery,
    RenderInvoiceDocument,
    ReviseInvoiceDraft,
    SearchCommercialInvoices,
    SendInvoiceToCollection,
    SubmitInvoiceForApproval,
    VoidInvoiceDraft,
)
from billing.foundation import DocumentKind, InvoiceState, RenderFormat

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..invoice_schemas import (
    CommercialInvoiceResponse,
    InvoiceActivityResponse,
    InvoiceDiagnosticsResponse,
    InvoiceDocumentResponse,
    InvoiceInspectionResponse,
    InvoiceReasonBody,
    InvoiceReconciliationResponse,
    InvoiceRevisionResponse,
    InvoiceSearchResponse,
    IssueCommercialInvoiceBody,
    IssueCreditNoteBody,
    IssueDebitNoteBody,
    MarkInvoicePaidBody,
    NumberSeriesAuditResponse,
    PrepareInvoiceBody,
    RecordAttachmentBody,
    RecordDeliveryBody,
    RenderedInvoiceResponse,
    RenderInvoiceBody,
    ReviseInvoiceBody,
    build_invoice_query,
)
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/documents", tags=["invoices"], responses=ERROR_RESPONSES)


def _doc(view) -> CommercialInvoiceResponse:
    return CommercialInvoiceResponse.of(view)


# ── static paths (declared first so an id can never shadow one) ─────────────────


@router.get(
    "/preview",
    response_model=InvoiceDocumentResponse,
    summary="Preview the document a closed period would produce",
)
def preview_invoice(
    account_id: str,
    accounting_period_key: str,
    credit_applied_minor: int = 0,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvoiceDocumentResponse:
    """A pure read: persists nothing and allocates no number, so an operator can look before a
    one-way door opens."""
    return InvoiceDocumentResponse.of(
        app.queries.dispatch(
            PreviewInvoice(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                accounting_period_key=accounting_period_key,
                credit_applied_minor=credit_applied_minor,
            )
        )
    )


@router.get(
    "/search", response_model=InvoiceSearchResponse, summary="Search documents deterministically"
)
def search_documents(
    text: str = "",
    account_id: str = "",
    kinds: list[DocumentKind] = Query(default=[]),
    states: list[InvoiceState] = Query(default=[]),
    accounting_period_key: str = "",
    number: str = "",
    entity_code: str = "",
    jurisdiction: str = "",
    overdue_only: bool = False,
    corrections_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvoiceSearchResponse:
    return InvoiceSearchResponse.of(
        app.queries.dispatch(
            SearchCommercialInvoices(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                query=build_invoice_query(
                    text=text,
                    account_id=account_id,
                    kinds=list(kinds),
                    states=list(states),
                    accounting_period_key=accounting_period_key,
                    number=number,
                    entity_code=entity_code,
                    jurisdiction=jurisdiction,
                    overdue_only=overdue_only,
                    corrections_only=corrections_only,
                    limit=limit,
                    offset=offset,
                ),
            )
        )
    )


@router.get(
    "/activity",
    response_model=list[InvoiceActivityResponse],
    summary="Append-only invoice history",
)
def invoice_activity(
    account_id: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[InvoiceActivityResponse]:
    return [
        InvoiceActivityResponse.of(a)
        for a in app.queries.dispatch(
            InvoiceTimeline(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                limit=limit,
            )
        )
    ]


@router.get(
    "/number-series/{series_key}",
    response_model=NumberSeriesAuditResponse,
    summary="Audit a number series for gaps and duplicates",
)
def audit_series(
    series_key: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> NumberSeriesAuditResponse:
    """Read from the allocation ledger, not from the numbers printed on documents — a sequence
    allocated to an issue that then failed is a real hole."""
    return NumberSeriesAuditResponse.of(
        app.queries.dispatch(
            AuditInvoiceNumberSeries(
                actor_id=ctx.actor(), org_id=ctx.organization(), series_key=series_key
            )
        )
    )


# ── documents ───────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=CommercialInvoiceResponse,
    status_code=201,
    summary="Prepare a document from a closed accounting period",
)
def prepare_invoice(
    body: PrepareInvoiceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    """Idempotent: preparing the same period twice returns the existing draft. An **open** period
    is refused with 409 — a document is a statement of a figure that has stopped moving."""
    return _doc(
        app.commands.dispatch(
            PrepareInvoice(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=body.account_id,
                accounting_period_key=body.accounting_period_key,
                entity_code=body.entity_code,
                jurisdiction=body.jurisdiction,
                supplier=body.supplier.to_party() if body.supplier else None,
                payment_instructions=(
                    body.payment_instructions.to_instructions()
                    if body.payment_instructions
                    else None
                ),
                presentation=(
                    body.presentation.to_presentation() if body.presentation else None
                ),
                tax_determination_id=body.tax_determination_id,
                purchase_order_ref=body.purchase_order_ref,
                payment_terms_days=body.payment_terms_days,
                credit_applied_minor=body.credit_applied_minor,
                notes=body.notes,
            )
        )
    )


@router.get("", response_model=list[CommercialInvoiceResponse], summary="List documents")
def list_documents(
    account_id: str = "",
    accounting_period_key: str = "",
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CommercialInvoiceResponse]:
    return [
        _doc(v)
        for v in app.queries.dispatch(
            ListCommercialInvoices(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                accounting_period_key=accounting_period_key,
                limit=limit,
            )
        )
    ]


@router.get(
    "/{document_id}", response_model=CommercialInvoiceResponse, summary="Get a document header"
)
def get_document(
    document_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    return _doc(
        app.queries.dispatch(
            GetCommercialInvoice(
                actor_id=ctx.actor(), org_id=ctx.organization(), document_id=document_id
            )
        )
    )


@router.post(
    "/{document_id}/revise",
    response_model=CommercialInvoiceResponse,
    summary="Re-prepare a draft (appends a revision; refused once issued)",
)
def revise_draft(
    document_id: str,
    body: ReviseInvoiceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    return _doc(
        app.commands.dispatch(
            ReviseInvoiceDraft(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                reason=body.reason,
                supplier=body.supplier.to_party() if body.supplier else None,
                payment_instructions=(
                    body.payment_instructions.to_instructions()
                    if body.payment_instructions
                    else None
                ),
                presentation=(
                    body.presentation.to_presentation() if body.presentation else None
                ),
                notes=body.notes,
            )
        )
    )


@router.post(
    "/{document_id}/submit",
    response_model=CommercialInvoiceResponse,
    summary="Submit a draft for approval",
)
def submit_for_approval(
    document_id: str,
    body: InvoiceReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    return _doc(
        app.commands.dispatch(
            SubmitInvoiceForApproval(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/{document_id}/issue",
    response_model=CommercialInvoiceResponse,
    summary="Issue a document (allocates a gapless number, seals a snapshot; irreversible)",
)
def issue_document(
    document_id: str,
    body: IssueCommercialInvoiceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    """The one-way door. The number is drawn inside the issuing transaction, so a crash takes it
    down with the issue rather than leaving a hole a gapless series can never legally fill."""
    return _doc(
        app.commands.dispatch(
            IssueCommercialInvoice(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                approve=body.approve,
                numbering_reset=body.numbering_reset,
                number_prefix=body.number_prefix,
                number_padding=body.number_padding,
                due_at=body.due_at,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/{document_id}/void",
    response_model=CommercialInvoiceResponse,
    summary="Void a draft (a draft carries no number; an issued document is never voided)",
)
def void_draft(
    document_id: str,
    body: InvoiceReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    return _doc(
        app.commands.dispatch(
            VoidInvoiceDraft(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/{document_id}/paid",
    response_model=CommercialInvoiceResponse,
    summary="Record that a payment fact settled this document (executes no payment)",
)
def mark_paid(
    document_id: str,
    body: MarkInvoicePaidBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    return _doc(
        app.commands.dispatch(
            MarkInvoicePaid(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                payment_id=body.payment_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/{document_id}/overdue",
    response_model=CommercialInvoiceResponse,
    summary="Mark an issued document overdue",
)
def mark_overdue(
    document_id: str,
    body: InvoiceReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    return _doc(
        app.commands.dispatch(
            MarkInvoiceOverdue(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/{document_id}/collection",
    response_model=CommercialInvoiceResponse,
    summary="Send an overdue document to collection",
)
def send_to_collection(
    document_id: str,
    body: InvoiceReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    return _doc(
        app.commands.dispatch(
            SendInvoiceToCollection(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                reason=body.reason,
            )
        )
    )


# ── corrections ─────────────────────────────────────────────────────────────────


@router.post(
    "/{document_id}/credit-notes",
    response_model=CommercialInvoiceResponse,
    status_code=201,
    summary="Issue a credit note — the only correction path for an issued invoice",
)
def issue_credit_note(
    document_id: str,
    body: IssueCreditNoteBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    return _doc(
        app.commands.dispatch(
            IssueCreditNote(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                lines=tuple(
                    line.to_line(index) for index, line in enumerate(body.lines, 1)
                ),
                cancel=body.cancel,
                reason=body.reason,
                numbering_reset=body.numbering_reset,
            )
        )
    )


@router.post(
    "/{document_id}/debit-notes",
    response_model=CommercialInvoiceResponse,
    status_code=201,
    summary="Issue a debit note — correct an under-charge by adding, never by re-issuing",
)
def issue_debit_note(
    document_id: str,
    body: IssueDebitNoteBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    return _doc(
        app.commands.dispatch(
            IssueDebitNote(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                lines=tuple(
                    line.to_line(index) for index, line in enumerate(body.lines, 1)
                ),
                reason=body.reason,
                numbering_reset=body.numbering_reset,
            )
        )
    )


@router.get(
    "/{document_id}/relations",
    response_model=list[CommercialInvoiceResponse],
    summary="Every document that corrects, credits, or replaces this one",
)
def document_relations(
    document_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CommercialInvoiceResponse]:
    return [
        _doc(v)
        for v in app.queries.dispatch(
            ListInvoiceRelations(
                actor_id=ctx.actor(), org_id=ctx.organization(), document_id=document_id
            )
        )
    ]


# ── document content, rendering, export ─────────────────────────────────────────


@router.get(
    "/{document_id}/document",
    response_model=InvoiceDocumentResponse,
    summary="The sealed document, resolved by the digest pinned at issue",
)
def get_invoice_document(
    document_id: str,
    revision: int = 0,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvoiceDocumentResponse:
    """Reproduces byte-identically forever: the content is addressed by digest, so a later revision
    cannot become the issued document by being newer."""
    return InvoiceDocumentResponse.of(
        app.queries.dispatch(
            GetInvoiceDocument(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                revision=revision,
            )
        )
    )


@router.get(
    "/{document_id}/export",
    response_model=InvoiceDocumentResponse,
    summary="The structured export a customer's AP system consumes",
)
def export_document(
    document_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvoiceDocumentResponse:
    return InvoiceDocumentResponse.of(
        app.queries.dispatch(
            ExportInvoiceDocument(
                actor_id=ctx.actor(), org_id=ctx.organization(), document_id=document_id
            )
        )
    )


@router.post(
    "/{document_id}/render",
    response_model=RenderedInvoiceResponse,
    summary="Render the sealed document (deterministic; re-rendering returns the same artefact)",
)
def render_document(
    document_id: str,
    body: RenderInvoiceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RenderedInvoiceResponse:
    return RenderedInvoiceResponse.of(
        app.commands.dispatch(
            RenderInvoiceDocument(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                format=body.format,
            )
        )
    )


@router.get(
    "/{document_id}/rendered",
    response_model=RenderedInvoiceResponse,
    summary="Read a rendering of the sealed document",
)
def get_rendered(
    document_id: str,
    format: RenderFormat = RenderFormat.HTML,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RenderedInvoiceResponse:
    return RenderedInvoiceResponse.of(
        app.queries.dispatch(
            GetRenderedInvoice(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                format=format,
            )
        )
    )


@router.get(
    "/{document_id}/revisions",
    response_model=list[InvoiceRevisionResponse],
    summary="Append-only revision history",
)
def document_revisions(
    document_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[InvoiceRevisionResponse]:
    return [
        InvoiceRevisionResponse.of(r)
        for r in app.queries.dispatch(
            GetInvoiceRevisions(
                actor_id=ctx.actor(), org_id=ctx.organization(), document_id=document_id
            )
        )
    ]


# ── inspection ──────────────────────────────────────────────────────────────────


@router.get(
    "/{document_id}/inspect",
    response_model=InvoiceInspectionResponse,
    summary="Is this document numbered, sealed, traced, and self-consistent?",
)
def inspect_document(
    document_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvoiceInspectionResponse:
    return InvoiceInspectionResponse.of(
        app.queries.dispatch(
            InspectCommercialInvoice(
                actor_id=ctx.actor(), org_id=ctx.organization(), document_id=document_id
            )
        )
    )


@router.get(
    "/{document_id}/diagnostics",
    response_model=InvoiceDiagnosticsResponse,
    summary="Structural findings for a document",
)
def diagnose_document(
    document_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvoiceDiagnosticsResponse:
    return InvoiceDiagnosticsResponse.of(
        app.queries.dispatch(
            DiagnoseCommercialInvoice(
                actor_id=ctx.actor(), org_id=ctx.organization(), document_id=document_id
            )
        )
    )


@router.get(
    "/{document_id}/reconciliation",
    response_model=InvoiceReconciliationResponse,
    summary="Does this document present what the ledger recorded?",
)
def reconcile_document(
    document_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> InvoiceReconciliationResponse:
    return InvoiceReconciliationResponse.of(
        app.queries.dispatch(
            ReconcileInvoiceWithLedger(
                actor_id=ctx.actor(), org_id=ctx.organization(), document_id=document_id
            )
        )
    )


# ── delivery + attachments (metadata only) ──────────────────────────────────────


@router.post(
    "/{document_id}/delivery",
    response_model=CommercialInvoiceResponse,
    summary="Record what a delivery agent reported (the Invoice Engine sends nothing)",
)
def record_delivery(
    document_id: str,
    body: RecordDeliveryBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    return _doc(
        app.commands.dispatch(
            RecordInvoiceDelivery(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                channel=body.channel,
                recipient=body.recipient,
                state=body.state,
                failure_reason=body.failure_reason,
            )
        )
    )


@router.post(
    "/{document_id}/attachments",
    response_model=CommercialInvoiceResponse,
    status_code=201,
    summary="Record attachment metadata (no bytes cross this boundary)",
)
def record_attachment(
    document_id: str,
    body: RecordAttachmentBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CommercialInvoiceResponse:
    return _doc(
        app.commands.dispatch(
            RecordInvoiceAttachment(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                filename=body.filename,
                media_type=body.media_type,
                purpose=body.purpose,
                byte_size=body.byte_size,
                content_digest=body.content_digest,
                storage_ref=body.storage_ref,
            )
        )
    )

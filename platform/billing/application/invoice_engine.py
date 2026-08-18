"""Invoice Engine application layer (M9.14) — commands, queries, use cases, mapping.

Additive to M9.3/M9.6–M9.13: the buses, Unit of Work, authorization orchestrator, application-event
vocabulary, and dispatch discipline are reused exactly as they are. This module adds 13 commands, 14
queries, their use cases, and the view DTOs — and nothing else.

The four beats are unchanged: **authorize → open a transaction → coordinate the Foundation → stage
an event.** Every document rule — which lines appear, how a snapshot is sealed, how a number reads,
what a correction contains, whether a document agrees with the ledger, how a snapshot renders — is
Foundation behaviour these methods *call*. There is no arithmetic here, and a test greps this module
for the operators that would be tempting to inline.

## What this layer reads, and what it refuses

Preparing an invoice is the only path by which financial facts become customer documents, and it is
guarded on both sides:

* **The accounting period must be `closed`.** An open period can still receive a posting; a document
  prepared from one would be a legal statement of a figure that has not stopped moving. This is the
  boundary the milestone brief states as non-negotiable, enforced at the one place it can be.
* **The lines come from M9.13's journal entries, never from M9.12's charges.** The ledger is the
  authority on what is owed; rating is the authority on what *should have been* charged. Reading the
  latter would let a document disagree with the balance a customer is asked to settle whenever a
  reversal or an adjustment sits between them.

Everything else the Invoice Engine touches is read-only: it calls `add`, `append`, `replace`, or
`allocate` on its own six seams and on nothing else, and a test walks this module's AST to prove it.

## Names

`IssueCommercialInvoice`, `GetCommercialInvoice`, and `ListCommercialInvoices` sit beside M9.3's
`IssueInvoice`, `GetInvoice`, and `ListInvoices`, which stay exactly as they are and still write the
M9.2 `Invoice`. The same call M9.13 made for `JournalEntry` beside `LedgerEntry` — one name, one
aggregate, and no message whose meaning depends on which milestone you have in mind.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from billing.foundation import (
    AttachmentPurpose,
    CommercialInvoice,
    DeliveryChannel,
    DeliveryState,
    DocumentKind,
    DocumentPresentation,
    DocumentRelation,
    InvoiceAction,
    InvoiceLine,
    InvoiceParty,
    InvoiceSearchQuery,
    InvoiceSnapshot,
    InvoiceState,
    InvoiceTrigger,
    LineSource,
    Money,
    NumberingPolicy,
    NumberingReset,
    PartyRegistration,
    PaymentInstructions,
    RenderFormat,
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
class PrepareInvoice(_Command):
    """Draft a document from a **closed** accounting period. Idempotent: the id is derived from
    (org, account, kind, period), so running month-end twice produces one draft."""

    account_id: str = ""
    accounting_period_key: str = ""
    entity_code: str = ""
    jurisdiction: str = ""
    supplier: InvoiceParty | None = None
    customer_override: InvoiceParty | None = None
    payment_instructions: PaymentInstructions | None = None
    presentation: DocumentPresentation | None = None
    tax_determination_id: str = ""
    purchase_order_ref: str = ""
    payment_terms_days: int = 0
    credit_applied_minor: int = 0
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ReviseInvoiceDraft(_Command):
    """Re-prepare a draft from the ledger, appending a **new revision**. Refused once issued."""

    document_id: str = ""
    reason: str = ""
    supplier: InvoiceParty | None = None
    presentation: DocumentPresentation | None = None
    payment_instructions: PaymentInstructions | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class SubmitInvoiceForApproval(_Command):
    document_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class IssueCommercialInvoice(_Command):
    """Allocate a gapless number, seal a snapshot, and pin its digest. **Irreversible.**

    `approve=True` routes through the approval trigger instead — one fact, reached two ways."""

    document_id: str = ""
    approve: bool = False
    numbering_reset: NumberingReset = NumberingReset.NEVER
    number_prefix: str = ""
    number_padding: int = 6
    due_at: float | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class VoidInvoiceDraft(_Command):
    """Void a draft, which carries no number. There is no path to void an issued document."""

    document_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class MarkInvoicePaid(_Command):
    """Record that a payment fact settled this document. **Executes no payment.**"""

    document_id: str = ""
    payment_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class MarkInvoiceOverdue(_Command):
    document_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SendInvoiceToCollection(_Command):
    document_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class IssueCreditNote(_Command):
    """The only correction path for an issued invoice. `cancel=True` credits it in full."""

    document_id: str = ""
    lines: tuple[InvoiceLine, ...] = ()
    cancel: bool = False
    reason: str = ""
    numbering_reset: NumberingReset = NumberingReset.NEVER


@dataclass(frozen=True, slots=True)
class IssueDebitNote(_Command):
    """Correct an under-charge by adding, never by re-issuing at a higher figure."""

    document_id: str = ""
    lines: tuple[InvoiceLine, ...] = ()
    reason: str = ""
    numbering_reset: NumberingReset = NumberingReset.NEVER


@dataclass(frozen=True, slots=True)
class RenderInvoiceDocument(_Command):
    """Render the issued snapshot. Deterministic, so re-rendering returns the stored artefact."""

    document_id: str = ""
    format: RenderFormat = RenderFormat.HTML


@dataclass(frozen=True, slots=True)
class RecordInvoiceDelivery(_Command):
    """Record what a delivery agent reported. **The Invoice Engine sends nothing.**"""

    document_id: str = ""
    channel: DeliveryChannel = DeliveryChannel.EMAIL
    recipient: str = ""
    state: DeliveryState = DeliveryState.SENT
    failure_reason: str = ""


@dataclass(frozen=True, slots=True)
class RecordInvoiceAttachment(_Command):
    """Record that an attachment exists. **Metadata only — no bytes cross this boundary.**"""

    document_id: str = ""
    filename: str = ""
    media_type: str = "application/pdf"
    purpose: AttachmentPurpose = AttachmentPurpose.OTHER
    byte_size: int = 0
    content_digest: str = ""
    storage_ref: str = ""


ALL_INVOICE_COMMANDS = (
    PrepareInvoice,
    ReviseInvoiceDraft,
    SubmitInvoiceForApproval,
    IssueCommercialInvoice,
    VoidInvoiceDraft,
    MarkInvoicePaid,
    MarkInvoiceOverdue,
    SendInvoiceToCollection,
    IssueCreditNote,
    IssueDebitNote,
    RenderInvoiceDocument,
    RecordInvoiceDelivery,
    RecordInvoiceAttachment,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetCommercialInvoice(_Query):
    document_id: str = ""


@dataclass(frozen=True, slots=True)
class ListCommercialInvoices(_Query):
    account_id: str = ""
    accounting_period_key: str = ""
    limit: int = 200


@dataclass(frozen=True, slots=True)
class GetInvoiceDocument(_Query):
    """The sealed snapshot, resolved **by the digest pinned at issue** rather than by recency."""

    document_id: str = ""
    revision: int = 0


@dataclass(frozen=True, slots=True)
class GetRenderedInvoice(_Query):
    document_id: str = ""
    format: RenderFormat = RenderFormat.HTML


@dataclass(frozen=True, slots=True)
class PreviewInvoice(_Query):
    """What a document *would* say. A read: previewing persists nothing and allocates no number."""

    account_id: str = ""
    accounting_period_key: str = ""
    supplier: InvoiceParty | None = None
    presentation: DocumentPresentation | None = None
    credit_applied_minor: int = 0


@dataclass(frozen=True, slots=True)
class ExportInvoiceDocument(_Query):
    """The document as structured data — the JSON export a customer's AP system consumes."""

    document_id: str = ""


@dataclass(frozen=True, slots=True)
class GetInvoiceRevisions(_Query):
    document_id: str = ""


@dataclass(frozen=True, slots=True)
class ListInvoiceRelations(_Query):
    document_id: str = ""


@dataclass(frozen=True, slots=True)
class InspectCommercialInvoice(_Query):
    document_id: str = ""


@dataclass(frozen=True, slots=True)
class DiagnoseCommercialInvoice(_Query):
    document_id: str = ""


@dataclass(frozen=True, slots=True)
class ReconcileInvoiceWithLedger(_Query):
    document_id: str = ""


@dataclass(frozen=True, slots=True)
class AuditInvoiceNumberSeries(_Query):
    series_key: str = ""


@dataclass(frozen=True, slots=True)
class SearchCommercialInvoices(_Query):
    query: InvoiceSearchQuery = field(default_factory=InvoiceSearchQuery)


@dataclass(frozen=True, slots=True)
class InvoiceTimeline(_Query):
    account_id: str = ""
    document_id: str = ""
    limit: int = 100


ALL_INVOICE_QUERIES = (
    GetCommercialInvoice,
    ListCommercialInvoices,
    GetInvoiceDocument,
    GetRenderedInvoice,
    PreviewInvoice,
    ExportInvoiceDocument,
    GetInvoiceRevisions,
    ListInvoiceRelations,
    InspectCommercialInvoice,
    DiagnoseCommercialInvoice,
    ReconcileInvoiceWithLedger,
    AuditInvoiceNumberSeries,
    SearchCommercialInvoices,
    InvoiceTimeline,
)


# ── view DTOs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PartyView:
    legal_name: str
    trade_name: str = ""
    line1: str = ""
    line2: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""
    email: str = ""
    phone: str = ""
    registrations: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class InvoiceLineView:
    id: str
    sequence: int
    kind: str
    description: str
    amount: MoneyView
    unit_amount: MoneyView | None = None
    tax_amount: MoneyView | None = None
    tax_rate_basis_points: int = 0
    period_start: float | None = None
    period_end: float | None = None
    journal_entry_id: str = ""
    charge_id: str = ""
    subscription_id: str = ""
    price_code: str = ""
    meter_code: str = ""
    hsn_sac_code: str = ""


@dataclass(frozen=True, slots=True)
class InvoiceTotalsView:
    subtotal: MoneyView
    discount_total: MoneyView
    tax_total: MoneyView
    credit_applied: MoneyView
    total: MoneyView
    amount_due: MoneyView


@dataclass(frozen=True, slots=True)
class TaxComponentView:
    name: str
    rate_basis_points: int
    amount: MoneyView


@dataclass(frozen=True, slots=True)
class RelationView:
    kind: str
    document_id: str
    document_number: str = ""
    reason: str = ""
    at: float = 0.0


@dataclass(frozen=True, slots=True)
class AttachmentView:
    id: str
    filename: str
    media_type: str
    purpose: str
    byte_size: int = 0
    content_digest: str = ""


@dataclass(frozen=True, slots=True)
class DeliveryView:
    channel: str
    recipient: str
    state: str
    attempts: int = 0
    requested_at: float | None = None
    delivered_at: float | None = None
    failure_reason: str = ""


@dataclass(frozen=True, slots=True)
class InvoiceDocumentView:
    """The sealed document, as served. Everything a reader sees, and nothing recomputed."""

    document_id: str
    revision: int
    kind: str
    currency: str
    digest: str
    supplier: PartyView
    customer: PartyView
    lines: tuple[InvoiceLineView, ...] = ()
    tax_components: tuple[TaxComponentView, ...] = ()
    tax_treatment: str = "standard"
    tax_determination_id: str = ""
    place_of_supply: str = ""
    totals: InvoiceTotalsView | None = None
    number: str = ""
    issue_date: float | None = None
    due_date: float | None = None
    period_start: float | None = None
    period_end: float | None = None
    accounting_period_key: str = ""
    purchase_order_ref: str = ""
    payment_terms: str = ""
    payment_method: str = ""
    payment_reference: str = ""
    payment_url: str = ""
    template_code: str = "standard"
    template_version: int = 1
    locale: str = "en-IN"
    attachments: tuple[AttachmentView, ...] = ()
    relations: tuple[RelationView, ...] = ()
    notes: str = ""


@dataclass(frozen=True, slots=True)
class CommercialInvoiceView:
    id: str
    account_id: str
    kind: str
    state: str
    currency: str
    number: str = ""
    revision: int = 0
    issued_revision: int = 0
    snapshot_digest: str = ""
    accounting_period_key: str = ""
    entity_code: str = ""
    jurisdiction: str = ""
    total: MoneyView | None = None
    amount_due: MoneyView | None = None
    issued_at: float | None = None
    due_at: float | None = None
    settled_at: float | None = None
    is_issued: bool = False
    is_editable: bool = True
    is_correction: bool = False
    relations: tuple[RelationView, ...] = ()
    delivery: DeliveryView | None = None
    allowed_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderedInvoiceView:
    document_id: str
    format: str
    snapshot_digest: str
    template_code: str
    template_version: int
    byte_size: int
    content: str
    rendered_at: float = 0.0


@dataclass(frozen=True, slots=True)
class InvoiceRevisionView:
    document_id: str
    revision: int
    snapshot_digest: str
    at: float
    actor_id: str = ""
    reason: str = ""
    issued: bool = False


@dataclass(frozen=True, slots=True)
class InvoiceInspectionView:
    document_id: str
    kind: str
    state: str
    number: str
    revision: int
    snapshot_digest: str
    is_sealed: bool
    is_traced: bool
    totals_agree: bool
    is_sound: bool
    lines: int = 0
    relations: tuple[RelationView, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InvoiceDiscrepancyView:
    kind: str
    document_id: str = ""
    line_id: str = ""
    journal_entry_id: str = ""
    expected_minor: int = 0
    actual_minor: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class InvoiceReconciliationView:
    account_id: str
    accounting_period_key: str
    entries_examined: int
    lines_examined: int
    is_clean: bool
    unbilled_count: int = 0
    discrepancies: tuple[InvoiceDiscrepancyView, ...] = ()
    at: float = 0.0


@dataclass(frozen=True, slots=True)
class NumberSeriesAuditView:
    series_key: str
    allocated: int
    lowest: int
    highest: int
    is_gapless: bool
    missing: tuple[int, ...] = ()
    duplicated: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class InvoiceDiagnosticView:
    path: str
    code: str
    severity: str
    message: str
    remediation: str = ""


@dataclass(frozen=True, slots=True)
class InvoiceDiagnosticsView:
    document_id: str
    is_valid: bool
    findings: tuple[InvoiceDiagnosticView, ...] = ()

    @property
    def error_count(self) -> int:
        return len([f for f in self.findings if f.severity == "error"])


@dataclass(frozen=True, slots=True)
class InvoiceActivityView:
    id: str
    action: str
    at: float
    actor_id: str = ""
    account_id: str = ""
    document_id: str = ""
    document_number: str = ""
    revision: int = 0
    amount_minor: int = 0
    currency: str = ""
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class InvoiceSearchView:
    documents: tuple[CommercialInvoiceView, ...] = ()
    total: int = 0


# ── mapping ─────────────────────────────────────────────────────────────────────


def _money(amount: Money) -> MoneyView:
    return MoneyView(minor=amount.minor, currency=amount.currency.value)


def _money_or_none(amount: Money | None) -> MoneyView | None:
    return _money(amount) if amount is not None else None


def party_view(party: InvoiceParty) -> PartyView:
    address = party.address
    return PartyView(
        legal_name=party.legal_name,
        trade_name=party.trade_name,
        line1=address.line1,
        line2=address.line2,
        city=address.city,
        region=address.region,
        postal_code=address.postal_code,
        country=address.country,
        email=party.email,
        phone=party.phone,
        registrations=tuple((r.scheme, r.number) for r in party.registrations),
    )


def line_view(line: InvoiceLine) -> InvoiceLineView:
    return InvoiceLineView(
        id=line.id,
        sequence=line.sequence,
        kind=line.kind.value,
        description=line.description,
        amount=_money(line.amount),
        unit_amount=_money_or_none(line.unit_amount),
        tax_amount=_money_or_none(line.tax_amount),
        tax_rate_basis_points=line.tax_rate_basis_points,
        period_start=line.period.start if line.period else None,
        period_end=line.period.end if line.period else None,
        journal_entry_id=line.journal_entry_id,
        charge_id=line.charge_id,
        subscription_id=line.subscription_id,
        price_code=line.price_code,
        meter_code=line.meter_code,
        hsn_sac_code=line.hsn_sac_code,
    )


def relation_view(relation: DocumentRelation) -> RelationView:
    return RelationView(
        kind=relation.kind.value,
        document_id=relation.document_id,
        document_number=relation.document_number,
        reason=relation.reason,
        at=relation.at,
    )


def document_view(snapshot: InvoiceSnapshot) -> InvoiceDocumentView:
    totals = snapshot.totals
    instructions = snapshot.payment_instructions
    presentation = snapshot.presentation
    return InvoiceDocumentView(
        document_id=snapshot.document_id,
        revision=snapshot.revision,
        kind=snapshot.kind.value,
        currency=snapshot.currency.value,
        digest=snapshot.digest,
        supplier=party_view(snapshot.supplier),
        customer=party_view(snapshot.customer),
        lines=tuple(line_view(line) for line in snapshot.lines),
        tax_components=tuple(
            TaxComponentView(
                name=c.name, rate_basis_points=c.rate_basis_points, amount=_money(c.amount)
            )
            for c in snapshot.tax_components
        ),
        tax_treatment=snapshot.tax_treatment.value,
        tax_determination_id=snapshot.tax_determination_id,
        place_of_supply=snapshot.place_of_supply,
        totals=(
            InvoiceTotalsView(
                subtotal=_money(totals.subtotal),
                discount_total=_money(totals.discount_total),
                tax_total=_money(totals.tax_total),
                credit_applied=_money(totals.credit_applied),
                total=_money(totals.total),
                amount_due=_money(totals.amount_due),
            )
            if totals is not None
            else None
        ),
        number=snapshot.number.formatted if snapshot.number else "",
        issue_date=snapshot.issue_date,
        due_date=snapshot.due_date,
        period_start=snapshot.period.start if snapshot.period else None,
        period_end=snapshot.period.end if snapshot.period else None,
        accounting_period_key=snapshot.accounting_period_key,
        purchase_order_ref=snapshot.purchase_order_ref,
        payment_terms=instructions.terms_label,
        payment_method=instructions.method_label,
        payment_reference=instructions.remittance_reference,
        payment_url=instructions.payment_url,
        template_code=presentation.template_code,
        template_version=presentation.template_version,
        locale=presentation.locale,
        attachments=tuple(
            AttachmentView(
                id=a.id,
                filename=a.filename,
                media_type=a.media_type,
                purpose=a.purpose.value,
                byte_size=a.byte_size,
                content_digest=a.content_digest,
            )
            for a in snapshot.attachments
        ),
        relations=tuple(relation_view(r) for r in snapshot.relations),
        notes=snapshot.notes,
    )


def invoice_view(document: CommercialInvoice, allowed: tuple = ()) -> CommercialInvoiceView:
    delivery = document.delivery
    return CommercialInvoiceView(
        id=document.id,
        account_id=document.account_id,
        kind=document.kind.value,
        state=document.state.value,
        currency=document.currency.value,
        number=document.formatted_number,
        revision=document.revision,
        issued_revision=document.issued_revision,
        snapshot_digest=document.issued_snapshot_digest,
        accounting_period_key=document.accounting_period_key,
        entity_code=document.entity_code,
        jurisdiction=document.jurisdiction,
        total=_money(document.total),
        amount_due=_money(document.amount_due),
        issued_at=document.issued_at,
        due_at=document.due_at,
        settled_at=document.settled_at,
        is_issued=document.is_issued,
        is_editable=document.is_editable,
        is_correction=document.is_correction,
        relations=tuple(relation_view(r) for r in document.relations),
        delivery=DeliveryView(
            channel=delivery.channel.value,
            recipient=delivery.recipient,
            state=delivery.state.value,
            attempts=delivery.attempts,
            requested_at=delivery.requested_at,
            delivered_at=delivery.delivered_at,
            failure_reason=delivery.failure_reason,
        ),
        allowed_triggers=tuple(t.value for t in allowed),
    )


def rendered_view(rendered) -> RenderedInvoiceView:
    return RenderedInvoiceView(
        document_id=rendered.document_id,
        format=rendered.format.value,
        snapshot_digest=rendered.snapshot_digest,
        template_code=rendered.template_code,
        template_version=rendered.template_version,
        byte_size=rendered.byte_size,
        content=rendered.content,
        rendered_at=rendered.rendered_at,
    )


def revision_view(revision) -> InvoiceRevisionView:
    return InvoiceRevisionView(
        document_id=revision.document_id,
        revision=revision.revision,
        snapshot_digest=revision.snapshot_digest,
        at=revision.at,
        actor_id=revision.actor_id,
        reason=revision.reason,
        issued=revision.issued,
    )


def inspection_view(inspection) -> InvoiceInspectionView:
    return InvoiceInspectionView(
        document_id=inspection.document_id,
        kind=inspection.kind.value,
        state=inspection.state.value,
        number=inspection.number,
        revision=inspection.revision,
        snapshot_digest=inspection.snapshot_digest,
        is_sealed=inspection.is_sealed,
        is_traced=inspection.is_traced,
        totals_agree=inspection.totals_agree,
        is_sound=inspection.is_sound,
        lines=inspection.lines,
        relations=tuple(relation_view(r) for r in inspection.relations),
        notes=inspection.notes,
    )


def reconciliation_view(report) -> InvoiceReconciliationView:
    return InvoiceReconciliationView(
        account_id=report.account_id,
        accounting_period_key=report.accounting_period_key,
        entries_examined=report.entries_examined,
        lines_examined=report.lines_examined,
        is_clean=report.is_clean,
        unbilled_count=report.unbilled_count,
        discrepancies=tuple(
            InvoiceDiscrepancyView(
                kind=d.kind.value,
                document_id=d.document_id,
                line_id=d.line_id,
                journal_entry_id=d.journal_entry_id,
                expected_minor=d.expected_minor,
                actual_minor=d.actual_minor,
                detail=d.detail,
            )
            for d in report.discrepancies
        ),
        at=report.at,
    )


def series_audit_view(audit) -> NumberSeriesAuditView:
    return NumberSeriesAuditView(
        series_key=audit.series_key,
        allocated=audit.allocated,
        lowest=audit.lowest,
        highest=audit.highest,
        is_gapless=audit.is_gapless,
        missing=audit.missing,
        duplicated=audit.duplicated,
    )


def activity_view(activity) -> InvoiceActivityView:
    return InvoiceActivityView(
        id=activity.id,
        action=activity.action.value,
        at=activity.at,
        actor_id=activity.actor_id,
        account_id=activity.account_id,
        document_id=activity.document_id,
        document_number=activity.document_number,
        revision=activity.revision,
        amount_minor=activity.amount_minor,
        currency=activity.currency.value if activity.currency else "",
        reason=activity.reason,
        detail=activity.detail,
    )


def diagnostics_view(document_id: str, diagnostics) -> InvoiceDiagnosticsView:
    return InvoiceDiagnosticsView(
        document_id=document_id,
        is_valid=not diagnostics.has_errors,
        findings=tuple(
            InvoiceDiagnosticView(
                path=f.path,
                code=f.code.value,
                severity=f.severity.value,
                message=f.message,
                remediation=f.remediation,
            )
            for f in diagnostics.findings
        ),
    )


# ── use cases ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InvoiceRepositories:
    """The invoice seams, injected. Interfaces only — implementations in persistence."""

    documents: object
    snapshots: object
    revisions: object
    numbers: object
    renderings: object
    activity: object


class _InvoiceSupport:
    """Shared read helpers for the command and query sides.

    Both sides need to answer *what would this document say?* — one to store it, one to preview it —
    and building the answer twice is how the preview and the issued document come to differ."""

    def __init__(self, *, context, factory, lines, assembler, integrity) -> None:
        self._ctx = context
        self._factory = factory
        self._lines = lines
        self._assembler = assembler
        self._integrity = integrity

    # ── ledger (read-only) ──────────────────────────────────────────────────────

    def closed_period(self, org_id: str, account_id: str, period_key: str):
        """The one input this milestone is allowed to build a document from.

        An **open** period can still receive a posting, so a document prepared from one states a
        figure that has not stopped moving. Refusing here rather than warning later is the whole
        reason the Ledger's close is a one-way door."""
        periods = getattr(self._ctx.ledger_engine, "periods", None)
        found = periods.by_key(org_id, account_id, period_key) if periods is not None else None
        if found is None:
            raise _NotFound(f"accounting period {period_key!r} not found")
        if not found.is_closed:
            from billing.foundation import BillingConflict

            raise BillingConflict(
                f"accounting period {period_key} is {found.state.value}, not closed; an open "
                "period can still receive a posting, and a document is a statement of a figure "
                "that has stopped moving"
            )
        return found

    def entries(self, org_id: str, account_id: str, period_key: str) -> tuple:
        """The journal entries the document presents. **Not M9.12's charges.**

        The ledger is the authority on what is owed; rating is the authority on what should have
        been charged. Reading the latter would let a document disagree with the balance the customer
        is asked to settle whenever a reversal or an adjustment sits between them."""
        entries = getattr(self._ctx.ledger_engine, "entries", None)
        if entries is None:
            return ()
        return entries.for_period(org_id, account_id, period_key)

    def sources(self, entries) -> tuple[LineSource, ...]:
        return tuple(
            LineSource(
                journal_entry_id=entry.id,
                entry_type=entry.entry_type,
                signed_minor=entry.signed_minor,
                currency=entry.amount.currency,
                description=entry.description,
                charge_id=entry.references.charge_id,
                subscription_id=entry.references.subscription_id,
                sequence=entry.sequence,
            )
            for entry in entries
        )

    # ── parties (read-only) ─────────────────────────────────────────────────────

    def customer_party(self, org_id: str, account_id: str) -> InvoiceParty:
        """The customer as they were, captured from M9.8's commercial account.

        Read-only, and copied rather than referenced: a document must print the name and address the
        customer had when they were billed, and pointing at a live record would let a later edit
        silently change a filed document."""
        account = self._commercial_account(org_id, account_id)
        if account is None:
            return InvoiceParty(legal_name=account_id, account_ref=account_id)
        entity = account.legal_entity
        billing_address = self._billing_address(account)
        contact = next(
            (c for c in account.contacts if c.is_primary and c.contact.email),
            next(iter(account.contacts), None),
        )
        registrations = (
            (PartyRegistration(scheme="tax", number=entity.tax_identifier),)
            if entity.tax_identifier
            else ()
        )
        if entity.registration_number:
            registrations = registrations + (
                PartyRegistration(scheme="registration", number=entity.registration_number),
            )
        return InvoiceParty(
            legal_name=entity.legal_name or account.display_name,
            trade_name=account.display_name,
            address=billing_address,
            registrations=registrations,
            email=contact.contact.email if contact else "",
            phone=contact.contact.phone if contact else "",
            account_ref=account.account_number or account.id,
        )

    def _commercial_account(self, org_id: str, account_id: str):
        accounts = getattr(self._ctx.commercial_accounts, "accounts", None)
        return accounts.get(org_id, account_id) if accounts is not None else None

    @staticmethod
    def _billing_address(account):
        from billing.foundation import AddressKind, BillingAddress

        entry = account.addresses.primary(AddressKind.BILLING)
        return entry.address if entry is not None else BillingAddress()

    # ── tax (read-only) ─────────────────────────────────────────────────────────

    def determination(self, org_id: str, document_id: str, determination_id: str):
        """A **published** tax determination, read whole. Nothing here determines tax.

        The determination is recorded by the Tax domain against a document; the Invoice presents its
        conclusion. Re-deriving it would produce a figure the authority was never told."""
        repository = getattr(self._ctx.repositories, "tax_determinations", None)
        if repository is None:
            return None
        found = repository.for_invoice(org_id, document_id)
        if found is None and determination_id:
            getter = getattr(repository, "get", None)
            found = getter(org_id, determination_id) if getter is not None else None
        return found

    # ── assembly ────────────────────────────────────────────────────────────────

    def build_snapshot(
        self,
        *,
        document: CommercialInvoice,
        revision: int,
        supplier: InvoiceParty,
        customer: InvoiceParty,
        entries,
        determination,
        credit_applied: Money | None,
        number=None,
        issue_date: float | None = None,
        due_date: float | None = None,
        payment_instructions: PaymentInstructions | None = None,
        presentation: DocumentPresentation | None = None,
        relations: tuple = (),
        purchase_order_ref: str = "",
        notes: str = "",
        prepared_by: str = "",
        lines: tuple[InvoiceLine, ...] | None = None,
    ) -> InvoiceSnapshot:
        built = (
            lines
            if lines is not None
            else self._lines.lines(self.sources(entries), document_id=document.id)
        )
        components = determination.components if determination is not None else ()
        if components:
            built = built + self._lines.tax_lines(
                components, document_id=document.id, offset=len(built)
            )
        return self._factory.snapshot(
            document=document,
            revision=revision,
            supplier=supplier,
            customer=customer,
            lines=built,
            tax_components=components,
            tax_treatment=(
                determination.treatment if determination is not None else _default_treatment()
            ),
            tax_determination_id=determination.id if determination is not None else "",
            place_of_supply=determination.place_of_supply if determination is not None else "",
            credit_applied=credit_applied,
            number=number,
            issue_date=issue_date,
            due_date=due_date,
            payment_instructions=payment_instructions,
            presentation=presentation,
            relations=relations,
            purchase_order_ref=purchase_order_ref,
            notes=notes,
            prepared_by=prepared_by,
        )

    def issued_snapshot(self, org_id: str, document: CommercialInvoice) -> InvoiceSnapshot | None:
        """Resolve the served content **by pinned digest**.

        This is the method that makes historical reproduction structural. A later revision cannot
        become the document by being newer, because the lookup is by content address rather than by
        recency."""
        if document.issued_snapshot_digest:
            return self._snapshots.by_digest(org_id, document.issued_snapshot_digest)
        return self._snapshots.latest(org_id, document.id)


def _default_treatment():
    from billing.foundation import TaxTreatment

    return TaxTreatment.STANDARD


class InvoiceCommandUseCases(_InvoiceSupport):
    """Orchestration for the Invoice Engine. Coordinates; decides nothing.

    Not one method here builds a line, seals a snapshot, formats a number, totals a document, or
    renders anything. Those are `LineBuilder`, `DocumentAssembler`, `NumberingService`,
    `InvoiceTotalsService`, and the renderers — and a test greps this module to keep it that way."""

    def __init__(
        self,
        *,
        context,
        invoice_repos: InvoiceRepositories,
        factory,
        lifecycle,
        lines,
        assembler,
        numbering,
        corrections,
        integrity,
        validator,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        super().__init__(
            context=context,
            factory=factory,
            lines=lines,
            assembler=assembler,
            integrity=integrity,
        )
        self._repos = invoice_repos
        self._snapshots = invoice_repos.snapshots
        self._lifecycle = lifecycle
        self._numbering = numbering
        self._corrections = corrections
        self._validator = validator
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── preparation ─────────────────────────────────────────────────────────────

    def prepare(self, command: PrepareInvoice) -> CommercialInvoiceView:
        """Draft a document from a closed period. Idempotent on the derived id."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            period = self.closed_period(
                command.org_id, command.account_id, command.accounting_period_key
            )
            existing = self._repos.documents.get(
                command.org_id, self._document_id(command, DocumentKind.INVOICE)
            )
            if existing is not None:
                # Idempotent: running month-end twice is a retry, not a second document. Two drafts
                # for one period would race for one legally allocated number.
                return invoice_view(existing, self._lifecycle.allowed(existing.state))
            document = self._repos.documents.add(
                self._factory.prepare(
                    org_id=command.org_id,
                    account_id=command.account_id,
                    kind=DocumentKind.INVOICE,
                    basis_id=command.accounting_period_key,
                    currency=period.currency,
                    accounting_period_key=command.accounting_period_key,
                    period=period.period,
                    entity_code=command.entity_code,
                    jurisdiction=command.jurisdiction,
                    actor_id=command.actor_id,
                )
            )
            stored = self._append_revision(command, document, revision=1, reason="prepared")
            self._record(
                command,
                InvoiceAction.PREPARED,
                account_id=document.account_id,
                document_id=document.id,
                revision=1,
            )
            uow.stage(self._event(command, document.id, revision=1))
            return invoice_view(stored, self._lifecycle.allowed(stored.state))

    def revise(self, command: ReviseInvoiceDraft) -> CommercialInvoiceView:
        """Re-prepare a draft. Refused once issued — by the guard *and* by the missing FSM edge."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            document = self._document(command.org_id, command.document_id)
            self._lifecycle.guard_editable(document)
            stored = self._append_revision(
                command,
                document,
                revision=document.revision + 1,
                reason=command.reason or "revised",
            )
            self._factory.emit_revision(
                self._snapshots.latest(command.org_id, document.id), actor_id=command.actor_id
            )
            self._record(
                command,
                InvoiceAction.REVISED,
                account_id=document.account_id,
                document_id=document.id,
                revision=stored.revision,
                reason=command.reason,
            )
            uow.stage(self._event(command, document.id, revision=stored.revision))
            return invoice_view(stored, self._lifecycle.allowed(stored.state))

    def _append_revision(
        self, command, document: CommercialInvoice, *, revision: int, reason: str
    ) -> CommercialInvoice:
        """Append one revision, **inheriting everything the command does not override.**

        A revision restates the whole document, so anything not carried forward is silently
        dropped: a redraft that only changed a note would otherwise lose the supplier identity, the
        template, and the payment instructions the draft was prepared with — and then be issued
        that way. The lines are the deliberate exception: they are always re-read from the ledger,
        because re-preparing is how a correction posted since the last draft reaches the document.
        """
        previous = self._previous(command.org_id, document)
        snapshot = self.build_snapshot(
            document=document,
            revision=revision,
            supplier=(
                getattr(command, "supplier", None)
                or (previous.supplier if previous else None)
                or InvoiceParty(legal_name="")
            ),
            customer=(
                getattr(command, "customer_override", None)
                or self.customer_party(command.org_id, document.account_id)
            ),
            entries=self.entries(
                command.org_id, document.account_id, document.accounting_period_key
            ),
            determination=self.determination(
                command.org_id, document.id, getattr(command, "tax_determination_id", "")
            ),
            credit_applied=self._credit(command, document)
            or (self._last_credit(command.org_id, document) if previous else None),
            due_date=self._due_date(command, document),
            payment_instructions=(
                getattr(command, "payment_instructions", None)
                or (previous.payment_instructions if previous else None)
            ),
            presentation=(
                getattr(command, "presentation", None)
                or (previous.presentation if previous else None)
            ),
            purchase_order_ref=(
                getattr(command, "purchase_order_ref", "")
                or (previous.purchase_order_ref if previous else "")
            ),
            notes=getattr(command, "notes", "") or (previous.notes if previous else ""),
            prepared_by=command.actor_id,
        )
        self._snapshots.append(snapshot)
        self._repos.revisions.append(
            self._factory.revision(snapshot, actor_id=command.actor_id, reason=reason)
        )
        totals = snapshot.totals
        return self._repos.documents.replace(
            replace(
                document,
                revision=revision,
                total_minor=totals.total.minor if totals else 0,
                amount_due_minor=totals.amount_due.minor if totals else 0,
                due_at=snapshot.due_date,
                updated_at=self._clock.now(),
            )
        )

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def submit(self, command: SubmitInvoiceForApproval) -> CommercialInvoiceView:
        return self._transition(
            command, InvoiceTrigger.SUBMIT_FOR_APPROVAL, InvoiceAction.SUBMITTED
        )

    def void(self, command: VoidInvoiceDraft) -> CommercialInvoiceView:
        """Void a draft. There is no path to void an issued document: a number has been allocated,
        and a gapless series has no hole to spare."""
        return self._transition(command, InvoiceTrigger.VOID, InvoiceAction.VOIDED)

    def mark_paid(self, command: MarkInvoicePaid) -> CommercialInvoiceView:
        """Record a settlement a payment fact reported. **Moves no money.**"""
        return self._transition(command, InvoiceTrigger.MARK_PAID, InvoiceAction.MARKED_PAID)

    def mark_overdue(self, command: MarkInvoiceOverdue) -> CommercialInvoiceView:
        return self._transition(command, InvoiceTrigger.MARK_OVERDUE, InvoiceAction.MARKED_OVERDUE)

    def send_to_collection(self, command: SendInvoiceToCollection) -> CommercialInvoiceView:
        return self._transition(
            command, InvoiceTrigger.SEND_TO_COLLECTION, InvoiceAction.SENT_TO_COLLECTION
        )

    def _transition(self, command, trigger, action) -> CommercialInvoiceView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            document = self._document(command.org_id, command.document_id)
            moved = self._lifecycle.apply(
                document, trigger, at=self._clock.now(), actor_id=command.actor_id
            )
            stored = self._repos.documents.replace(moved)
            self._factory.emit_transition(stored, trigger, actor_id=command.actor_id)
            self._record(
                command,
                action,
                account_id=stored.account_id,
                document_id=stored.id,
                document_number=stored.formatted_number,
                reason=getattr(command, "reason", ""),
            )
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return invoice_view(stored, self._lifecycle.allowed(stored.state))

    # ── issuing ─────────────────────────────────────────────────────────────────

    def issue(self, command: IssueCommercialInvoice) -> CommercialInvoiceView:
        """Allocate a number, seal a snapshot, pin its digest. The one-way door.

        The number is allocated **inside this transaction**, so a crash between allocation and
        commit takes the number down with the issue rather than leaving a hole a gapless series can
        never legally fill."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVOICE_ISSUE)
        with self._uow.new() as uow:
            document = self._document(command.org_id, command.document_id)
            trigger = InvoiceTrigger.APPROVE if command.approve else InvoiceTrigger.ISSUE
            stored, snapshot = self._seal(command, document, trigger, uow=uow)
            return invoice_view(stored, self._lifecycle.allowed(stored.state))

    def _seal(
        self,
        command,
        document: CommercialInvoice,
        trigger,
        *,
        uow,
        relations=(),
        lines: tuple[InvoiceLine, ...] | None = None,
        supplier: InvoiceParty | None = None,
        customer: InvoiceParty | None = None,
    ):
        policy = NumberingPolicy(
            entity_code=document.entity_code,
            jurisdiction=document.jurisdiction,
            kind=document.kind,
            reset=command.numbering_reset,
            prefix=getattr(command, "number_prefix", ""),
            padding=getattr(command, "number_padding", 6),
        )
        now = self._clock.now()
        series_key = self._numbering.series_key(policy, at=now)
        sequence = self._repos.numbers.allocate(command.org_id, series_key)
        allocation = self._factory.number(
            policy, sequence, org_id=command.org_id, actor_id=command.actor_id
        )
        snapshot = self.build_snapshot(
            document=document,
            revision=document.revision + 1,
            supplier=supplier or self._last_supplier(command.org_id, document),
            customer=customer or self._last_customer(command.org_id, document),
            entries=self.entries(
                command.org_id, document.account_id, document.accounting_period_key
            ),
            determination=self.determination(command.org_id, document.id, ""),
            credit_applied=self._last_credit(command.org_id, document),
            number=allocation.number,
            issue_date=now,
            due_date=getattr(command, "due_at", None) or document.due_at,
            payment_instructions=self._last_instructions(command.org_id, document),
            presentation=self._last_presentation(command.org_id, document),
            relations=relations,
            purchase_order_ref=self._last_po(command.org_id, document),
            notes=self._last_notes(command.org_id, document),
            prepared_by=command.actor_id,
            lines=lines if lines is not None else self._last_lines(command.org_id, document),
        )
        self._fail_on_errors(self._validator.validate_issue(document, snapshot))
        self._snapshots.append(snapshot)
        self._repos.revisions.append(
            self._factory.revision(
                snapshot, actor_id=command.actor_id, reason="issued", issued=True
            )
        )
        moved = self._lifecycle.apply(
            document,
            trigger,
            at=now,
            actor_id=command.actor_id,
            number=allocation.number,
            snapshot=snapshot,
            due_at=getattr(command, "due_at", None),
        )
        stored = self._repos.documents.replace(replace(moved, revision=snapshot.revision))
        self._factory.emit_transition(stored, trigger, actor_id=command.actor_id)
        self._record(
            command,
            _issue_action(document.kind),
            account_id=stored.account_id,
            document_id=stored.id,
            document_number=stored.formatted_number,
            revision=snapshot.revision,
            amount=stored.total,
        )
        uow.stage(self._event(command, stored.id, number=stored.formatted_number))
        return stored, snapshot

    # ── corrections ─────────────────────────────────────────────────────────────

    def credit_note(self, command: IssueCreditNote) -> CommercialInvoiceView:
        """The only correction path for an issued invoice."""
        return self._correction(command, DocumentKind.CREDIT_NOTE)

    def debit_note(self, command: IssueDebitNote) -> CommercialInvoiceView:
        return self._correction(command, DocumentKind.DEBIT_NOTE)

    def _correction(self, command, kind: DocumentKind) -> CommercialInvoiceView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVOICE_ISSUE)
        with self._uow.new() as uow:
            original = self._document(command.org_id, command.document_id)
            snapshot = self.issued_snapshot(command.org_id, original)
            if snapshot is None:
                raise _NotFound(f"invoice {original.id!r} has no sealed document to correct")
            correction_id = _correction_id(original, kind)
            request = (
                self._corrections.credit_note(
                    original,
                    snapshot,
                    document_id=correction_id,
                    lines=tuple(command.lines) or None,
                    reason=command.reason,
                    cancels=getattr(command, "cancel", False),
                )
                if kind is DocumentKind.CREDIT_NOTE
                else self._corrections.debit_note(
                    original,
                    document_id=correction_id,
                    lines=tuple(command.lines),
                    reason=command.reason,
                )
            )
            existing = self._repos.documents.get(command.org_id, correction_id)
            if existing is not None and existing.is_issued:
                return invoice_view(existing, self._lifecycle.allowed(existing.state))
            document = existing or self._repos.documents.add(
                self._factory.prepare(
                    org_id=command.org_id,
                    account_id=original.account_id,
                    kind=kind,
                    basis_id=original.id,
                    currency=original.currency,
                    accounting_period_key=original.accounting_period_key,
                    period=original.period,
                    entity_code=original.entity_code,
                    jurisdiction=original.jurisdiction,
                    relations=(
                        DocumentRelation(
                            kind=request.relation,
                            document_id=original.id,
                            document_number=original.formatted_number,
                            reason=command.reason,
                            at=self._clock.now(),
                        ),
                    ),
                    actor_id=command.actor_id,
                )
            )
            stored, _ = self._seal(
                command,
                document,
                InvoiceTrigger.ISSUE,
                uow=uow,
                relations=document.relations,
                # The correction's lines were decided by `CorrectionService` — mirrored from the
                # original or supplied by an operator. They are never re-read from the ledger,
                # because a credit note states what *that invoice* said, not what the ledger says
                # now.
                lines=request.lines,
                supplier=snapshot.supplier,
                customer=snapshot.customer,
            )
            self._back_reference(command, original, stored)
            return invoice_view(stored, self._lifecycle.allowed(stored.state))

    def _back_reference(self, command, original: CommercialInvoice, correction) -> None:
        """Record the edge on the **original** too, so a reader who starts at the invoice finds the
        correction without having to search for one. Appending a relation is not editing content:
        the sealed snapshot is untouched, and its digest is unchanged."""
        edge = self._corrections.back_reference(
            correction, at=self._clock.now(), reason=command.reason
        )
        self._repos.documents.replace(replace(original, relations=original.relations + (edge,)))

    # ── rendering, delivery, attachments ────────────────────────────────────────

    def render(self, command: RenderInvoiceDocument) -> RenderedInvoiceView:
        """Render the sealed snapshot. Deterministic, so a re-render returns the stored artefact
        rather than a second one that might differ."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_READ)
        with self._uow.new() as uow:
            document = self._document(command.org_id, command.document_id)
            snapshot = self.issued_snapshot(command.org_id, document)
            if snapshot is None:
                raise _NotFound(f"invoice {document.id!r} has no sealed document to render")
            cached = self._repos.renderings.get(
                command.org_id, document.id, snapshot.digest, command.format
            )
            if cached is not None:
                return rendered_view(cached)
            rendered = self._repos.renderings.append(
                self._factory.render(
                    snapshot,
                    command.format,
                    org_id=command.org_id,
                    actor_id=command.actor_id,
                )
            )
            self._record(
                command,
                InvoiceAction.RENDERED,
                account_id=document.account_id,
                document_id=document.id,
                detail=command.format.value,
            )
            uow.stage(self._event(command, document.id, format=command.format.value))
            return rendered_view(rendered)

    def record_delivery(self, command: RecordInvoiceDelivery) -> CommercialInvoiceView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            document = self._document(command.org_id, command.document_id)
            delivery = self._factory.delivery(
                channel=command.channel,
                recipient=command.recipient,
                state=command.state,
                failure_reason=command.failure_reason,
                previous=document.delivery,
            )
            stored = self._repos.documents.replace(replace(document, delivery=delivery))
            self._factory.emit_delivery(stored, delivery, actor_id=command.actor_id)
            self._record(
                command,
                InvoiceAction.DELIVERY_RECORDED,
                account_id=stored.account_id,
                document_id=stored.id,
                detail=f"{delivery.channel.value}:{delivery.state.value}",
            )
            uow.stage(self._event(command, stored.id, state=delivery.state.value))
            return invoice_view(stored, self._lifecycle.allowed(stored.state))

    def record_attachment(self, command: RecordInvoiceAttachment) -> CommercialInvoiceView:
        """Record that an attachment exists. **No bytes cross this boundary.**"""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            document = self._document(command.org_id, command.document_id)
            attachment = self._factory.attachment(
                filename=command.filename,
                media_type=command.media_type,
                purpose=command.purpose,
                byte_size=command.byte_size,
                content_digest=command.content_digest,
                storage_ref=command.storage_ref,
            )
            self._factory.emit_attachment(document, attachment, actor_id=command.actor_id)
            self._record(
                command,
                InvoiceAction.ATTACHMENT_RECORDED,
                account_id=document.account_id,
                document_id=document.id,
                detail=attachment.filename,
            )
            uow.stage(self._event(command, document.id, filename=attachment.filename))
            return invoice_view(document, self._lifecycle.allowed(document.state))

    # ── internals ───────────────────────────────────────────────────────────────

    @staticmethod
    def _document_id(command: PrepareInvoice, kind: DocumentKind) -> str:
        from billing.foundation import derive_document_id

        return derive_document_id(
            command.org_id, command.account_id, kind.value, command.accounting_period_key
        )

    def _document(self, org_id: str, document_id: str) -> CommercialInvoice:
        found = self._repos.documents.get(org_id, document_id)
        if found is None:
            raise _NotFound(f"invoice {document_id!r} not found")
        return found

    def _credit(self, command, document: CommercialInvoice) -> Money | None:
        minor = getattr(command, "credit_applied_minor", 0)
        return Money(minor, document.currency) if minor else None

    def _due_date(self, command, document: CommercialInvoice) -> float | None:
        days = getattr(command, "payment_terms_days", 0)
        if not days:
            return document.due_at
        return self._clock.now() + float(days) * 86_400.0

    # Re-preparation reads the previous revision so an issue does not silently discard the
    # supplier, presentation, or payment instructions a draft was prepared with.
    def _previous(self, org_id: str, document: CommercialInvoice) -> InvoiceSnapshot | None:
        return self._snapshots.latest(org_id, document.id)

    def _last_supplier(self, org_id: str, document) -> InvoiceParty:
        previous = self._previous(org_id, document)
        return previous.supplier if previous else InvoiceParty(legal_name="")

    def _last_customer(self, org_id: str, document) -> InvoiceParty:
        previous = self._previous(org_id, document)
        return previous.customer if previous else self.customer_party(org_id, document.account_id)

    def _last_instructions(self, org_id: str, document) -> PaymentInstructions | None:
        previous = self._previous(org_id, document)
        return previous.payment_instructions if previous else None

    def _last_presentation(self, org_id: str, document) -> DocumentPresentation | None:
        previous = self._previous(org_id, document)
        return previous.presentation if previous else None

    def _last_credit(self, org_id: str, document) -> Money | None:
        previous = self._previous(org_id, document)
        if previous is None or previous.totals is None:
            return None
        applied = previous.totals.credit_applied
        return applied if not applied.is_zero else None

    def _last_po(self, org_id: str, document) -> str:
        previous = self._previous(org_id, document)
        return previous.purchase_order_ref if previous else ""

    def _last_notes(self, org_id: str, document) -> str:
        previous = self._previous(org_id, document)
        return previous.notes if previous else ""

    def _last_lines(self, org_id: str, document) -> tuple[InvoiceLine, ...] | None:
        """A correction's lines were decided when it was drafted; an invoice's are re-read from the
        ledger so a late correction posted before issue is presented."""
        if document.kind is DocumentKind.INVOICE:
            return None
        previous = self._previous(org_id, document)
        return previous.lines if previous else ()

    def _fail_on_errors(self, diagnostics) -> None:
        if diagnostics.has_errors:
            from billing.foundation import BillingValidationError

            raise BillingValidationError(diagnostics)

    def _record(
        self,
        command,
        action: InvoiceAction,
        *,
        account_id: str = "",
        document_id: str = "",
        document_number: str = "",
        revision: int = 0,
        amount: Money | None = None,
        reason: str = "",
        detail: str = "",
    ) -> None:
        """Append invoice history **inside the caller's transaction**, so the history and the fact
        it describes commit or vanish together."""
        self._repos.activity.append(
            self._factory.activity(
                org_id=command.org_id,
                action=action,
                actor_id=command.actor_id,
                account_id=account_id,
                document_id=document_id,
                document_number=document_number,
                revision=revision,
                amount_minor=amount.minor if amount else 0,
                currency=amount.currency if amount else None,
                reason=reason,
                detail=detail,
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.INVOICE_FINALIZED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


def _issue_action(kind: DocumentKind) -> InvoiceAction:
    return {
        DocumentKind.INVOICE: InvoiceAction.ISSUED,
        DocumentKind.CREDIT_NOTE: InvoiceAction.CREDIT_NOTE_ISSUED,
        DocumentKind.DEBIT_NOTE: InvoiceAction.DEBIT_NOTE_ISSUED,
    }[kind]


def _correction_id(original: CommercialInvoice, kind: DocumentKind) -> str:
    from billing.foundation import derive_document_id

    return derive_document_id(original.org_id, original.account_id, kind.value, original.id)


class InvoiceQueryUseCases(_InvoiceSupport):
    """Reads. No transaction, no mutation, no arithmetic — every figure comes from a sealed
    snapshot or a Foundation service."""

    def __init__(
        self,
        *,
        context,
        invoice_repos: InvoiceRepositories,
        factory,
        lifecycle,
        lines,
        assembler,
        numbering,
        integrity,
        inspector,
        reconciliation,
        auditor,
        search,
        validator,
        authz: AuthorizationOrchestrator,
        clock,
    ) -> None:
        super().__init__(
            context=context,
            factory=factory,
            lines=lines,
            assembler=assembler,
            integrity=integrity,
        )
        self._repos = invoice_repos
        self._snapshots = invoice_repos.snapshots
        self._lifecycle = lifecycle
        self._numbering = numbering
        self._inspector = inspector
        self._reconciliation = reconciliation
        self._auditor = auditor
        self._search = search
        self._validator = validator
        self._authz = authz
        self._clock = clock

    def get(self, query: GetCommercialInvoice) -> CommercialInvoiceView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        document = self._document(query.org_id, query.document_id)
        return invoice_view(document, self._lifecycle.allowed(document.state))

    def list(self, query: ListCommercialInvoices) -> tuple[CommercialInvoiceView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        if query.accounting_period_key:
            found = self._repos.documents.for_period(
                query.org_id, query.account_id, query.accounting_period_key
            )
        elif query.account_id:
            found = self._repos.documents.for_account(query.org_id, query.account_id, query.limit)
        else:
            found = self._repos.documents.all(query.org_id, query.limit)
        return tuple(invoice_view(d, self._lifecycle.allowed(d.state)) for d in found)

    def document(self, query: GetInvoiceDocument) -> InvoiceDocumentView:
        """The sealed document. Resolved by pinned digest unless a specific revision is asked for —
        which is what makes a historical read reproduce byte-identically rather than approximately.
        """
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        document = self._document(query.org_id, query.document_id)
        snapshot = (
            self._snapshots.get(query.org_id, query.document_id, query.revision)
            if query.revision
            else self.issued_snapshot(query.org_id, document)
        )
        if snapshot is None:
            raise _NotFound(f"invoice {query.document_id!r} has no such document revision")
        if document.is_issued and not query.revision:
            self._integrity.guard_pinned(document, snapshot)
        return document_view(snapshot)

    def rendered(self, query: GetRenderedInvoice) -> RenderedInvoiceView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        document = self._document(query.org_id, query.document_id)
        snapshot = self.issued_snapshot(query.org_id, document)
        if snapshot is None:
            raise _NotFound(f"invoice {query.document_id!r} has no sealed document")
        stored = self._repos.renderings.get(
            query.org_id, document.id, snapshot.digest, query.format
        )
        # Rendering is deterministic, so a read may produce it without storing it: the artefact a
        # later render stores will be byte-identical to this one.
        return rendered_view(
            stored if stored is not None else self._factory.render(snapshot, query.format)
        )

    def preview(self, query: PreviewInvoice) -> InvoiceDocumentView:
        """What a document *would* say. Persists nothing and allocates no number — so an operator
        can look before a one-way door opens."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        period = self.closed_period(query.org_id, query.account_id, query.accounting_period_key)
        from billing.foundation import derive_document_id

        draft = CommercialInvoice(
            id=derive_document_id(
                query.org_id,
                query.account_id,
                DocumentKind.INVOICE.value,
                query.accounting_period_key,
            ),
            org_id=query.org_id,
            account_id=query.account_id,
            kind=DocumentKind.INVOICE,
            currency=period.currency,
            state=InvoiceState.DRAFT,
            accounting_period_key=query.accounting_period_key,
            period=period.period,
        )
        credit = (
            Money(query.credit_applied_minor, period.currency)
            if query.credit_applied_minor
            else None
        )
        return document_view(
            self.build_snapshot(
                document=draft,
                revision=0,
                supplier=query.supplier or InvoiceParty(legal_name=""),
                customer=self.customer_party(query.org_id, query.account_id),
                entries=self.entries(query.org_id, query.account_id, query.accounting_period_key),
                determination=self.determination(query.org_id, draft.id, ""),
                credit_applied=credit,
                presentation=query.presentation,
            )
        )

    def export(self, query: ExportInvoiceDocument) -> InvoiceDocumentView:
        """The structured export. Identical content to `document` — deliberately, because an export
        that differed from the document would be a second answer to what the invoice says."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        document = self._document(query.org_id, query.document_id)
        snapshot = self.issued_snapshot(query.org_id, document)
        if snapshot is None:
            raise _NotFound(f"invoice {query.document_id!r} has no sealed document")
        return document_view(snapshot)

    def revisions(self, query: GetInvoiceRevisions) -> tuple[InvoiceRevisionView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            revision_view(r) for r in self._repos.revisions.history(query.org_id, query.document_id)
        )

    def relations(self, query: ListInvoiceRelations) -> tuple[CommercialInvoiceView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            invoice_view(d, self._lifecycle.allowed(d.state))
            for d in self._repos.documents.related_to(query.org_id, query.document_id)
        )

    def inspect(self, query: InspectCommercialInvoice) -> InvoiceInspectionView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        document = self._document(query.org_id, query.document_id)
        return inspection_view(
            self._inspector.inspect(document, self.issued_snapshot(query.org_id, document))
        )

    def diagnose(self, query: DiagnoseCommercialInvoice) -> InvoiceDiagnosticsView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        document = self._document(query.org_id, query.document_id)
        snapshot = self.issued_snapshot(query.org_id, document)
        findings = self._validator.validate_document(document)
        if snapshot is not None:
            findings = findings.merged(self._validator.validate_snapshot(snapshot))
        return diagnostics_view(document.id, findings)

    def reconcile(self, query: ReconcileInvoiceWithLedger) -> InvoiceReconciliationView:
        """Does the document present what the ledger recorded? A pure read."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        document = self._document(query.org_id, query.document_id)
        entries = self.entries(query.org_id, document.account_id, document.accounting_period_key)
        return reconciliation_view(
            self._reconciliation.reconcile(
                org_id=query.org_id,
                account_id=document.account_id,
                accounting_period_key=document.accounting_period_key,
                entries=tuple((e.id, e.signed_minor) for e in entries),
                snapshot=self.issued_snapshot(query.org_id, document),
                at=self._clock.now(),
            )
        )

    def audit_series(self, query: AuditInvoiceNumberSeries) -> NumberSeriesAuditView:
        """The compliance question: is this series gapless?

        Read from the **allocation ledger**, not from the numbers printed on documents. A sequence
        allocated to an issue that then failed is a real hole in the series, and auditing the
        documents alone would hide precisely the case this query exists to find."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return series_audit_view(
            self._auditor.audit(
                query.series_key,
                self._repos.numbers.allocated(query.org_id, query.series_key),
            )
        )

    def search(self, query: SearchCommercialInvoices) -> InvoiceSearchView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._search.search(self._repos.documents.all(query.org_id, 2000), query.query)
        return InvoiceSearchView(
            documents=tuple(
                invoice_view(d, self._lifecycle.allowed(d.state)) for d in found.documents
            ),
            total=found.total,
        )

    def timeline(self, query: InvoiceTimeline) -> tuple[InvoiceActivityView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = (
            self._repos.activity.for_document(query.org_id, query.document_id)
            if query.document_id
            else self._repos.activity.history(query.org_id, query.account_id, query.limit)
        )
        return tuple(activity_view(a) for a in found)

    def _document(self, org_id: str, document_id: str) -> CommercialInvoice:
        found = self._repos.documents.get(org_id, document_id)
        if found is None:
            raise _NotFound(f"invoice {document_id!r} not found")
        return found


# ── wiring table ────────────────────────────────────────────────────────────────


def register_invoice_command_handlers(bus, uc: InvoiceCommandUseCases) -> None:
    bus.register(PrepareInvoice, uc.prepare)
    bus.register(ReviseInvoiceDraft, uc.revise)
    bus.register(SubmitInvoiceForApproval, uc.submit)
    bus.register(IssueCommercialInvoice, uc.issue)
    bus.register(VoidInvoiceDraft, uc.void)
    bus.register(MarkInvoicePaid, uc.mark_paid)
    bus.register(MarkInvoiceOverdue, uc.mark_overdue)
    bus.register(SendInvoiceToCollection, uc.send_to_collection)
    bus.register(IssueCreditNote, uc.credit_note)
    bus.register(IssueDebitNote, uc.debit_note)
    bus.register(RenderInvoiceDocument, uc.render)
    bus.register(RecordInvoiceDelivery, uc.record_delivery)
    bus.register(RecordInvoiceAttachment, uc.record_attachment)


def register_invoice_query_handlers(bus, uc: InvoiceQueryUseCases) -> None:
    bus.register(GetCommercialInvoice, uc.get)
    bus.register(ListCommercialInvoices, uc.list)
    bus.register(GetInvoiceDocument, uc.document)
    bus.register(GetRenderedInvoice, uc.rendered)
    bus.register(PreviewInvoice, uc.preview)
    bus.register(ExportInvoiceDocument, uc.export)
    bus.register(GetInvoiceRevisions, uc.revisions)
    bus.register(ListInvoiceRelations, uc.relations)
    bus.register(InspectCommercialInvoice, uc.inspect)
    bus.register(DiagnoseCommercialInvoice, uc.diagnose)
    bus.register(ReconcileInvoiceWithLedger, uc.reconcile)
    bus.register(AuditInvoiceNumberSeries, uc.audit_series)
    bus.register(SearchCommercialInvoices, uc.search)
    bus.register(InvoiceTimeline, uc.timeline)


__all__ = [
    "ALL_INVOICE_COMMANDS",
    "ALL_INVOICE_QUERIES",
    "AttachmentView",
    "AuditInvoiceNumberSeries",
    "CommercialInvoiceView",
    "DeliveryView",
    "DiagnoseCommercialInvoice",
    "ExportInvoiceDocument",
    "GetCommercialInvoice",
    "GetInvoiceDocument",
    "GetInvoiceRevisions",
    "GetRenderedInvoice",
    "InspectCommercialInvoice",
    "InvoiceActivityView",
    "InvoiceCommandUseCases",
    "InvoiceDiagnosticView",
    "InvoiceDiagnosticsView",
    "InvoiceDiscrepancyView",
    "InvoiceDocumentView",
    "InvoiceInspectionView",
    "InvoiceLineView",
    "InvoiceQueryUseCases",
    "InvoiceReconciliationView",
    "InvoiceRepositories",
    "InvoiceRevisionView",
    "InvoiceSearchView",
    "InvoiceTimeline",
    "InvoiceTotalsView",
    "IssueCommercialInvoice",
    "IssueCreditNote",
    "IssueDebitNote",
    "ListCommercialInvoices",
    "ListInvoiceRelations",
    "MarkInvoiceOverdue",
    "MarkInvoicePaid",
    "NumberSeriesAuditView",
    "PartyView",
    "PrepareInvoice",
    "PreviewInvoice",
    "ReconcileInvoiceWithLedger",
    "RecordInvoiceAttachment",
    "RecordInvoiceDelivery",
    "RelationView",
    "RenderInvoiceDocument",
    "RenderedInvoiceView",
    "ReviseInvoiceDraft",
    "SearchCommercialInvoices",
    "SendInvoiceToCollection",
    "SubmitInvoiceForApproval",
    "TaxComponentView",
    "VoidInvoiceDraft",
    "register_invoice_command_handlers",
    "register_invoice_query_handlers",
]

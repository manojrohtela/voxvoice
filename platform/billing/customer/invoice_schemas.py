"""Invoice Engine wire DTOs (M9.14) — Pydantic v2, additive to the earlier schemas.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views. Foundation enums are reused so validation and OpenAPI enums come from the domain
vocabulary, and **M9.5's `MoneyResponse` is reused rather than redeclared** — the lesson M9.12
recorded when a duplicated money type surfaced as two qualified schema names in the public document.

Money is always `{minor, currency}`: an integer in the currency's minor unit plus the currency,
never a formatted string and never a float (ADR-0023 RS5). On an invoice this matters for a reason
beyond arithmetic — a float total is a total that can print differently from the one the ledger
recorded, and the printed figure is the one a customer pays.

Named distinctly from the frozen surfaces they sit beside: `CommercialInvoiceResponse` rather than a
second `InvoiceResponse` (M9.5 owns that name for the M9.2 aggregate), `PrepareInvoiceBody` rather
than a second `DraftInvoiceBody`, and `IssueCommercialInvoiceBody` rather than a second
`IssueInvoiceBody` — the collision class `test_no_schema_name_is_qualified` exists to catch, because
two schema names for one concept in a public OpenAPI document is a defect a reader can only discover
by reading the generated document.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    AttachmentPurpose,
    BillingAddress,
    ChargeLineKind,
    Currency,
    DeliveryChannel,
    DeliveryState,
    DocumentKind,
    DocumentPresentation,
    InvoiceParty,
    InvoiceSearchQuery,
    InvoiceState,
    Money,
    NumberingReset,
    PartyRegistration,
    PaymentInstructions,
    RenderFormat,
)
from billing.foundation import InvoiceLine as DomainInvoiceLine

from .schemas import MoneyResponse

# ── shared inbound value objects ────────────────────────────────────────────────


class PartyRegistrationBody(BaseModel):
    scheme: str = Field(description='e.g. "gstin", "vat", "cin"')
    number: str
    country: str = ""


class PostalAddressBody(BaseModel):
    line1: str = ""
    line2: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""

    def to_address(self) -> BillingAddress:
        return BillingAddress(
            line1=self.line1,
            line2=self.line2,
            city=self.city,
            region=self.region,
            postal_code=self.postal_code,
            country=self.country,
        )


class InvoicePartyBody(BaseModel):
    """A party as it should be **printed**, captured at issue.

    The supplier arrives on the request rather than from a registry, and deliberately: who VoxVoice
    contracts as is published organization information, which Billing references and never owns
    (ADR-0023 TX3). A supplier registry inside this plane would be Billing holding a fact about the
    organization it bills *for*."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "legal_name": "VoxVoice Technologies Private Limited",
                "address": {"line1": "12 MG Road", "city": "Bengaluru", "country": "IN"},
                "registrations": [{"scheme": "gstin", "number": "29AABCV1234F1Z5"}],
            }
        }
    )
    legal_name: str = Field(min_length=1)
    trade_name: str = ""
    address: PostalAddressBody = Field(default_factory=PostalAddressBody)
    registrations: list[PartyRegistrationBody] = Field(default_factory=list)
    email: str = ""
    phone: str = ""

    def to_party(self) -> InvoiceParty:
        return InvoiceParty(
            legal_name=self.legal_name,
            trade_name=self.trade_name,
            address=self.address.to_address(),
            registrations=tuple(
                PartyRegistration(scheme=r.scheme, number=r.number, country=r.country)
                for r in self.registrations
            ),
            email=self.email,
            phone=self.phone,
        )


class PaymentInstructionsBody(BaseModel):
    """**How to pay** — printed guidance. No gateway, no token, no capture."""

    terms_label: str = ""
    method_label: str = ""
    bank_account_name: str = ""
    bank_account_ref: str = ""
    bank_ifsc_or_swift: str = ""
    remittance_reference: str = ""
    payment_url: str = ""
    notes: str = ""

    def to_instructions(self) -> PaymentInstructions:
        return PaymentInstructions(
            terms_label=self.terms_label,
            method_label=self.method_label,
            bank_account_name=self.bank_account_name,
            bank_account_ref=self.bank_account_ref,
            bank_ifsc_or_swift=self.bank_ifsc_or_swift,
            remittance_reference=self.remittance_reference,
            payment_url=self.payment_url,
            notes=self.notes,
        )


class PresentationBody(BaseModel):
    """Presentation choices, pinned into the snapshot at issue so a template revised next year does
    not alter last year's document."""

    template_code: str = "standard"
    template_version: int = Field(default=1, ge=1)
    locale: str = "en-IN"
    paper_size: str = "A4"
    show_usage_detail: bool = True
    show_period_column: bool = True
    logo_ref: str = ""
    footer_note: str = ""

    def to_presentation(self) -> DocumentPresentation:
        return DocumentPresentation(
            template_code=self.template_code,
            template_version=self.template_version,
            locale=self.locale,
            paper_size=self.paper_size,
            show_usage_detail=self.show_usage_detail,
            show_period_column=self.show_period_column,
            logo_ref=self.logo_ref,
            footer_note=self.footer_note,
        )


class CorrectionLineBody(BaseModel):
    """One line of a partial credit or debit note. The amount is supplied, never recomputed — a
    correction states what *that document* said, not what pricing would say today."""

    description: str = Field(min_length=1)
    amount: MoneyResponse
    kind: ChargeLineKind = ChargeLineKind.ONE_OFF
    journal_entry_id: str = ""

    def to_line(self, sequence: int) -> DomainInvoiceLine:
        return DomainInvoiceLine(
            id="",
            sequence=sequence,
            kind=self.kind,
            description=self.description,
            amount=Money(self.amount.minor, Currency(self.amount.currency)),
            journal_entry_id=self.journal_entry_id,
        )


# ── inbound ─────────────────────────────────────────────────────────────────────


class PrepareInvoiceBody(BaseModel):
    """Draft a document from a **closed** accounting period. An open one is refused with 409."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": "bac_1",
                "accounting_period_key": "1735689600-1738368000",
                "entity_code": "VOX",
                "jurisdiction": "KA",
                "supplier": {"legal_name": "VoxVoice Technologies Private Limited"},
                "payment_terms_days": 30,
            }
        }
    )
    account_id: str
    accounting_period_key: str = Field(min_length=1)
    entity_code: str = Field(min_length=1, description="The legal entity a number is drawn for")
    jurisdiction: str = ""
    supplier: InvoicePartyBody | None = None
    payment_instructions: PaymentInstructionsBody | None = None
    presentation: PresentationBody | None = None
    tax_determination_id: str = ""
    purchase_order_ref: str = ""
    payment_terms_days: int = Field(default=0, ge=0)
    credit_applied_minor: int = Field(default=0, ge=0)
    notes: str = ""


class ReviseInvoiceBody(BaseModel):
    reason: str = ""
    supplier: InvoicePartyBody | None = None
    payment_instructions: PaymentInstructionsBody | None = None
    presentation: PresentationBody | None = None
    notes: str = ""


class IssueCommercialInvoiceBody(BaseModel):
    """Allocate a gapless number and seal a snapshot. **Irreversible.**"""

    approve: bool = Field(
        default=False, description="Route through approval rather than direct issue"
    )
    numbering_reset: NumberingReset = NumberingReset.NEVER
    number_prefix: str = ""
    number_padding: int = Field(default=6, ge=1, le=12)
    due_at: float | None = None
    reason: str = ""


class InvoiceReasonBody(BaseModel):
    reason: str = ""


class MarkInvoicePaidBody(BaseModel):
    """Record that a payment fact settled this document. **Executes no payment.**"""

    payment_id: str = ""
    reason: str = ""


class IssueCreditNoteBody(BaseModel):
    """The only correction path for an issued invoice."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"cancel": True, "reason": "Service credit for the January incident"}
        }
    )
    cancel: bool = Field(
        default=False, description="Credit the invoice in full — the legal form of cancellation"
    )
    lines: list[CorrectionLineBody] = Field(default_factory=list)
    reason: str = ""
    numbering_reset: NumberingReset = NumberingReset.NEVER


class IssueDebitNoteBody(BaseModel):
    """Correct an under-charge by adding, never by re-issuing at a higher figure."""

    lines: list[CorrectionLineBody] = Field(min_length=1)
    reason: str = ""
    numbering_reset: NumberingReset = NumberingReset.NEVER


class RenderInvoiceBody(BaseModel):
    format: RenderFormat = RenderFormat.HTML


class RecordDeliveryBody(BaseModel):
    """Record what a delivery agent reported. **The Invoice Engine sends nothing.**"""

    channel: DeliveryChannel = DeliveryChannel.EMAIL
    recipient: str = ""
    state: DeliveryState = DeliveryState.SENT
    failure_reason: str = ""


class RecordAttachmentBody(BaseModel):
    """**Metadata only** — no bytes cross this boundary."""

    filename: str = Field(min_length=1)
    media_type: str = "application/pdf"
    purpose: AttachmentPurpose = AttachmentPurpose.OTHER
    byte_size: int = Field(default=0, ge=0)
    content_digest: str = ""
    storage_ref: str = ""


# ── outbound ────────────────────────────────────────────────────────────────────


class InvoicePartyResponse(BaseModel):
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
    registrations: list[tuple[str, str]] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> InvoicePartyResponse:
        return cls(
            legal_name=view.legal_name,
            trade_name=view.trade_name,
            line1=view.line1,
            line2=view.line2,
            city=view.city,
            region=view.region,
            postal_code=view.postal_code,
            country=view.country,
            email=view.email,
            phone=view.phone,
            registrations=[list(r) for r in view.registrations],  # type: ignore[misc]
        )


class InvoiceLineResponse(BaseModel):
    id: str
    sequence: int
    kind: str
    description: str
    amount: MoneyResponse
    tax_amount: MoneyResponse | None = None
    tax_rate_basis_points: int = 0
    period_start: float | None = None
    period_end: float | None = None
    journal_entry_id: str = ""
    charge_id: str = ""
    subscription_id: str = ""
    price_code: str = ""
    meter_code: str = ""
    hsn_sac_code: str = ""

    @classmethod
    def of(cls, view) -> InvoiceLineResponse:
        return cls(
            id=view.id,
            sequence=view.sequence,
            kind=view.kind,
            description=view.description,
            amount=_money(view.amount),
            tax_amount=_money_or_none(view.tax_amount),
            tax_rate_basis_points=view.tax_rate_basis_points,
            period_start=view.period_start,
            period_end=view.period_end,
            journal_entry_id=view.journal_entry_id,
            charge_id=view.charge_id,
            subscription_id=view.subscription_id,
            price_code=view.price_code,
            meter_code=view.meter_code,
            hsn_sac_code=view.hsn_sac_code,
        )


class InvoiceTotalsResponse(BaseModel):
    subtotal: MoneyResponse
    discount_total: MoneyResponse
    tax_total: MoneyResponse
    credit_applied: MoneyResponse
    total: MoneyResponse
    amount_due: MoneyResponse


class InvoiceTaxComponentResponse(BaseModel):
    name: str
    rate_basis_points: int
    amount: MoneyResponse


class DocumentRelationResponse(BaseModel):
    kind: str
    document_id: str
    document_number: str = ""
    reason: str = ""
    at: float = 0.0


class InvoiceAttachmentResponse(BaseModel):
    id: str
    filename: str
    media_type: str
    purpose: str
    byte_size: int = 0
    content_digest: str = ""


class InvoiceDeliveryResponse(BaseModel):
    channel: str
    recipient: str
    state: str
    attempts: int = 0
    requested_at: float | None = None
    delivered_at: float | None = None
    failure_reason: str = ""


class InvoiceDocumentResponse(BaseModel):
    """The sealed document, as served. Every figure came from the snapshot; none is recomputed."""

    document_id: str
    revision: int
    kind: str
    currency: str
    digest: str = Field(
        description="The content address of this document — the same string an issued invoice "
        "pins, so 'is this what we issued?' is a comparison rather than an argument"
    )
    supplier: InvoicePartyResponse
    customer: InvoicePartyResponse
    lines: list[InvoiceLineResponse] = Field(default_factory=list)
    tax_components: list[InvoiceTaxComponentResponse] = Field(default_factory=list)
    tax_treatment: str = "standard"
    tax_determination_id: str = ""
    place_of_supply: str = ""
    totals: InvoiceTotalsResponse | None = None
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
    attachments: list[InvoiceAttachmentResponse] = Field(default_factory=list)
    relations: list[DocumentRelationResponse] = Field(default_factory=list)
    notes: str = ""

    @classmethod
    def of(cls, view) -> InvoiceDocumentResponse:
        totals = view.totals
        return cls(
            document_id=view.document_id,
            revision=view.revision,
            kind=view.kind,
            currency=view.currency,
            digest=view.digest,
            supplier=InvoicePartyResponse.of(view.supplier),
            customer=InvoicePartyResponse.of(view.customer),
            lines=[InvoiceLineResponse.of(line) for line in view.lines],
            tax_components=[
                InvoiceTaxComponentResponse(
                    name=c.name, rate_basis_points=c.rate_basis_points, amount=_money(c.amount)
                )
                for c in view.tax_components
            ],
            tax_treatment=view.tax_treatment,
            tax_determination_id=view.tax_determination_id,
            place_of_supply=view.place_of_supply,
            totals=(
                InvoiceTotalsResponse(
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
            number=view.number,
            issue_date=view.issue_date,
            due_date=view.due_date,
            period_start=view.period_start,
            period_end=view.period_end,
            accounting_period_key=view.accounting_period_key,
            purchase_order_ref=view.purchase_order_ref,
            payment_terms=view.payment_terms,
            payment_method=view.payment_method,
            payment_reference=view.payment_reference,
            payment_url=view.payment_url,
            template_code=view.template_code,
            template_version=view.template_version,
            locale=view.locale,
            attachments=[
                InvoiceAttachmentResponse(
                    id=a.id,
                    filename=a.filename,
                    media_type=a.media_type,
                    purpose=a.purpose,
                    byte_size=a.byte_size,
                    content_digest=a.content_digest,
                )
                for a in view.attachments
            ],
            relations=[DocumentRelationResponse(**vars(r)) for r in view.relations],
            notes=view.notes,
        )


class CommercialInvoiceResponse(BaseModel):
    """The document header. `allowed_triggers` is the server's answer to what may happen next —
    a client never derives it, so a UI can never offer a transition the FSM would refuse."""

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
    total: MoneyResponse | None = None
    amount_due: MoneyResponse | None = None
    issued_at: float | None = None
    due_at: float | None = None
    settled_at: float | None = None
    is_issued: bool = False
    is_editable: bool = True
    is_correction: bool = False
    relations: list[DocumentRelationResponse] = Field(default_factory=list)
    delivery: InvoiceDeliveryResponse | None = None
    allowed_triggers: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> CommercialInvoiceResponse:
        return cls(
            id=view.id,
            account_id=view.account_id,
            kind=view.kind,
            state=view.state,
            currency=view.currency,
            number=view.number,
            revision=view.revision,
            issued_revision=view.issued_revision,
            snapshot_digest=view.snapshot_digest,
            accounting_period_key=view.accounting_period_key,
            entity_code=view.entity_code,
            jurisdiction=view.jurisdiction,
            total=_money_or_none(view.total),
            amount_due=_money_or_none(view.amount_due),
            issued_at=view.issued_at,
            due_at=view.due_at,
            settled_at=view.settled_at,
            is_issued=view.is_issued,
            is_editable=view.is_editable,
            is_correction=view.is_correction,
            relations=[DocumentRelationResponse(**vars(r)) for r in view.relations],
            delivery=(
                InvoiceDeliveryResponse(**vars(view.delivery))
                if view.delivery is not None
                else None
            ),
            allowed_triggers=list(view.allowed_triggers),
        )


class RenderedInvoiceResponse(BaseModel):
    document_id: str
    format: str
    snapshot_digest: str
    template_code: str
    template_version: int
    byte_size: int
    content: str
    rendered_at: float = 0.0

    @classmethod
    def of(cls, view) -> RenderedInvoiceResponse:
        return cls(**vars(view))


class InvoiceRevisionResponse(BaseModel):
    document_id: str
    revision: int
    snapshot_digest: str
    at: float
    actor_id: str = ""
    reason: str = ""
    issued: bool = False

    @classmethod
    def of(cls, view) -> InvoiceRevisionResponse:
        return cls(**vars(view))


class InvoiceInspectionResponse(BaseModel):
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
    relations: list[DocumentRelationResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> InvoiceInspectionResponse:
        return cls(
            document_id=view.document_id,
            kind=view.kind,
            state=view.state,
            number=view.number,
            revision=view.revision,
            snapshot_digest=view.snapshot_digest,
            is_sealed=view.is_sealed,
            is_traced=view.is_traced,
            totals_agree=view.totals_agree,
            is_sound=view.is_sound,
            lines=view.lines,
            relations=[DocumentRelationResponse(**vars(r)) for r in view.relations],
            notes=list(view.notes),
        )


class InvoiceDiscrepancyResponse(BaseModel):
    kind: str
    document_id: str = ""
    line_id: str = ""
    journal_entry_id: str = ""
    expected_minor: int = 0
    actual_minor: int = 0
    detail: str = ""


class InvoiceReconciliationResponse(BaseModel):
    account_id: str
    accounting_period_key: str
    entries_examined: int
    lines_examined: int
    is_clean: bool
    unbilled_count: int = 0
    discrepancies: list[InvoiceDiscrepancyResponse] = Field(default_factory=list)
    at: float = 0.0

    @classmethod
    def of(cls, view) -> InvoiceReconciliationResponse:
        return cls(
            account_id=view.account_id,
            accounting_period_key=view.accounting_period_key,
            entries_examined=view.entries_examined,
            lines_examined=view.lines_examined,
            is_clean=view.is_clean,
            unbilled_count=view.unbilled_count,
            discrepancies=[InvoiceDiscrepancyResponse(**vars(d)) for d in view.discrepancies],
            at=view.at,
        )


class NumberSeriesAuditResponse(BaseModel):
    """The compliance answer: is this number series gapless?"""

    series_key: str
    allocated: int
    lowest: int
    highest: int
    is_gapless: bool
    missing: list[int] = Field(default_factory=list)
    duplicated: list[int] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> NumberSeriesAuditResponse:
        return cls(
            series_key=view.series_key,
            allocated=view.allocated,
            lowest=view.lowest,
            highest=view.highest,
            is_gapless=view.is_gapless,
            missing=list(view.missing),
            duplicated=list(view.duplicated),
        )


class InvoiceDiagnosticResponse(BaseModel):
    path: str
    code: str
    severity: str
    message: str
    remediation: str = ""


class InvoiceDiagnosticsResponse(BaseModel):
    document_id: str
    is_valid: bool
    findings: list[InvoiceDiagnosticResponse] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> InvoiceDiagnosticsResponse:
        return cls(
            document_id=view.document_id,
            is_valid=view.is_valid,
            findings=[InvoiceDiagnosticResponse(**vars(f)) for f in view.findings],
        )


class InvoiceActivityResponse(BaseModel):
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

    @classmethod
    def of(cls, view) -> InvoiceActivityResponse:
        return cls(**vars(view))


class InvoiceSearchResponse(BaseModel):
    documents: list[CommercialInvoiceResponse] = Field(default_factory=list)
    total: int = 0

    @classmethod
    def of(cls, view) -> InvoiceSearchResponse:
        return cls(
            documents=[CommercialInvoiceResponse.of(d) for d in view.documents],
            total=view.total,
        )


# ── query construction ──────────────────────────────────────────────────────────


def build_invoice_query(
    *,
    text: str = "",
    account_id: str = "",
    kinds: list[DocumentKind] | None = None,
    states: list[InvoiceState] | None = None,
    accounting_period_key: str = "",
    number: str = "",
    entity_code: str = "",
    jurisdiction: str = "",
    overdue_only: bool = False,
    corrections_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> InvoiceSearchQuery:
    """Transport → the Foundation's deterministic filter. No filtering happens here."""
    return InvoiceSearchQuery(
        text=text,
        account_id=account_id,
        kinds=tuple(kinds or ()),
        states=tuple(states or ()),
        accounting_period_key=accounting_period_key,
        number=number,
        entity_code=entity_code,
        jurisdiction=jurisdiction,
        overdue_only=overdue_only,
        corrections_only=corrections_only,
        limit=limit,
        offset=offset,
    )


def _money(view) -> MoneyResponse:
    return MoneyResponse(minor=view.minor, currency=view.currency)


def _money_or_none(view) -> MoneyResponse | None:
    return _money(view) if view is not None else None

"""Invoice Engine domain (M9.14) — customer documents, and nothing else.

Eight milestones produced facts. M9.6 said **what is sold**, M9.7 **what it costs**, M9.8 **who we
bill**, M9.9 **what was agreed**, M9.10 **what may be used**, M9.11 **what was consumed**, M9.12
**what should be charged**, M9.13 **what is owed**. None of them produced anything a customer, a
court, or a tax authority could read. This one does.

**The Invoice Engine owns** invoices, credit notes, debit notes, their numbering, their immutable
snapshots, their lines, their presentation, their rendering models, their document relationships,
their delivery metadata, their attachments metadata, their lifecycle, their history, and their
search.

**It owns nothing else.** It calculates no price, resolves no pricing, meters no usage, rates no
charge, posts no ledger entry, computes no balance, evaluates no entitlement, authorizes no request,
executes no payment, and determines no tax. It **consumes** a closed accounting period, the journal
entries it contains, the account, a published tax determination, and published customer and
organization information. A test walks these modules' ASTs to prove it names none of those domains'
operations.

## The distinction the whole milestone rests on

> **The Ledger owns financial truth. The Invoice owns presentation of it.**

A document is not an accounting record; it is the *evidence* an accounting record produces. That is
why a line here carries a `journal_entry_id` rather than a recomputed amount, why no service in this
package folds a balance, and why rendering is downstream of a snapshot that was already sealed.

## Four properties that are structural rather than conventional

* **An issued document's content is addressed by digest, never by mutation.** Content lives in an
  append-only snapshot store keyed by `(document, revision)`. Issuance pins one revision's digest
  onto the document, and every read of an issued document resolves *that digest*. Even if something
  appended a later revision, the issued document would be unchanged — which is what makes
  "reproduces byte-identically forever" a property of addressing rather than of discipline.
* **Corrections are new documents.** A credit note and a debit note are themselves numbered,
  issued, immutable documents that carry a `DocumentRelation` to what they correct. There is no
  edit path, and no `VOID` edge out of any issued state — a number has been allocated, and voiding
  it would put a hole in a sequence a jurisdiction requires to be gapless.
* **Numbering is gapless because allocation is transactional, not because a job repairs it.** The
  number is drawn from a durable per-series counter **inside the issuing transaction**, so a crash
  rolls back the number along with the issue. A pre-allocated pool cannot offer this: a crash
  between allocation and use leaves a hole that no later process can legally fill.
* **Rendering consumes a prepared model and computes nothing.** `PdfDocumentModel` and
  `HtmlDocumentModel` carry *strings that were already formatted*, so a template has nothing to add
  up. A renderer that could compute a total is a renderer that can disagree with the ledger.

M9.2's `InvoiceState`, `InvoiceTrigger`, `IMMUTABLE_INVOICE_STATES`, `ChargeLineKind`,
`InvoiceNumber`, and `InvoiceTotals` are **reused unchanged**, not restated — as are `TaxComponent`
and `TaxTreatment` from the tax domain and `BillingAddress` from accounts. Two answers to "what
state is this invoice in?" is exactly the drift §A2.5 prevents.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .accounts import BillingAddress
from .domain import BillingPeriod, Quantity
from .invoicing import (
    IMMUTABLE_INVOICE_STATES,
    ChargeLineKind,
    InvoiceNumber,
    InvoiceState,
    InvoiceTotals,
    InvoiceTrigger,
)
from .money import Currency, Money
from .tax import TaxComponent, TaxTreatment

# ── identity ────────────────────────────────────────────────────────────────────


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def derive_document_id(org_id: str, account_id: str, kind: str, basis_id: str) -> str:
    """The deterministic identity of one customer document.

    Derived from **what it documents** rather than minted, so preparing the same document twice is
    one document. `basis_id` is the closed accounting period key for an invoice, and the corrected
    document's id for a credit or debit note.

    The corollary is what matters legally: a correction derives a *different* id by construction, so
    there is no code path by which a credit note could overwrite the invoice it credits."""
    return "inv_" + _digest(org_id, account_id, kind, basis_id)[:32]


def derive_line_id(document_id: str, sequence: int, source_ref: str) -> str:
    """A line's identity is its position plus what it presents, so a re-prepared draft produces the
    same lines rather than a second set beside them."""
    return "inl_" + _digest(document_id, str(sequence), source_ref)[:32]


def derive_snapshot_id(document_id: str, revision: int) -> str:
    """The identity of one stored revision of a document's content.

    Declared here rather than computed in persistence, and the boundary is the point: an identity
    is a *domain* fact, and a storage layer that minted one would be a second place the answer to
    "which row is this?" lives. Caught by `test_persistence_imports_only_its_allowed_dependencies`,
    which refuses `hashlib` inside the persistence package for exactly this reason."""
    return _digest(document_id, str(revision))[:32]


def derive_revision_id(document_id: str, revision: int, snapshot_digest: str) -> str:
    """A revision-history entry's identity. Includes the digest, so re-appending the same history
    entry is one row rather than two."""
    return _digest(document_id, str(revision), snapshot_digest)[:32]


def derive_rendering_id(
    document_id: str, snapshot_digest: str, fmt: str, template_version: int
) -> str:
    """A rendering's identity: what was rendered, in which format, under which template.

    The template version is part of it deliberately — a template revision produces a different
    artefact from the same content, and both must remain readable."""
    return _digest(document_id, snapshot_digest, fmt, str(template_version))[:32]


class NumberingReset(StrEnum):
    """When a number series returns to 1.

    Jurisdictional, not stylistic: India's GST rules and several EU VAT regimes require a series
    that restarts each financial year, and a platform that only ever counted upward would be
    unable to issue a compliant invoice there."""

    NEVER = "never"
    ANNUAL = "annual"
    MONTHLY = "monthly"


def series_key_for(
    *, entity_code: str, jurisdiction: str, kind: str, reset: NumberingReset, bucket: str = ""
) -> str:
    """The name of one gapless counter.

    Separate series per legal entity, per jurisdiction, per document kind, and per reset bucket —
    because "gapless" is a property *of a series*, and merging two jurisdictions into one counter
    makes each of them look gapped to the authority that reads it."""
    parts = [entity_code.upper(), jurisdiction.upper(), kind]
    if reset is not NumberingReset.NEVER:
        parts.append(bucket)
    return ":".join(p for p in parts if p)


# ── documents ───────────────────────────────────────────────────────────────────


class DocumentKind(StrEnum):
    """What a document *is*. Three kinds, one lifecycle, one numbering mechanism.

    A credit note is not a flag on an invoice: it is a numbered, issued, immutable document in its
    own right, which is what every jurisdiction that forbids editing an issued invoice requires."""

    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"


# The document lifecycle, over M9.2's states and triggers. Four absences, each a decision:
#
#   * **no edge back to DRAFT from any issued state.** An issued document's content is fixed. A
#     mistake is corrected by a credit or debit note that references it, which is what leaves both
#     the original figure and the correction separately readable;
#   * **no VOID out of any issued state.** Voiding would put a hole in a sequence a jurisdiction
#     requires to be gapless. Pre-issue a draft carries no number, so voiding one costs nothing;
#     post-issue, cancellation is a full credit note carrying `DocumentRelationKind.CANCELS`;
#   * **no edge out of PAID, CREDITED, or VOIDED.** A settled or cancelled document is a historical
#     fact, and re-opening one would make "was this ever paid?" a question with two answers;
#   * **no deletion state anywhere** (ADR-0023 IN2/RS1).
#
# `APPROVE` issues directly rather than returning to `DRAFT`: in an enterprise approval flow the
# approver's decision *is* the act of issuing, and a state that meant "approved but not yet issued"
# would be a window in which an approved document could still be edited.
DOCUMENT_LIFECYCLE: StateMachineDefinition = define(
    [
        (InvoiceState.DRAFT, InvoiceTrigger.SUBMIT_FOR_APPROVAL, InvoiceState.PENDING_APPROVAL),
        (InvoiceState.DRAFT, InvoiceTrigger.ISSUE, InvoiceState.ISSUED),
        (InvoiceState.DRAFT, InvoiceTrigger.VOID, InvoiceState.VOIDED),
        (InvoiceState.PENDING_APPROVAL, InvoiceTrigger.APPROVE, InvoiceState.ISSUED),
        (InvoiceState.PENDING_APPROVAL, InvoiceTrigger.VOID, InvoiceState.VOIDED),
        (InvoiceState.ISSUED, InvoiceTrigger.MARK_OVERDUE, InvoiceState.OVERDUE),
        (InvoiceState.ISSUED, InvoiceTrigger.MARK_PAID, InvoiceState.PAID),
        (InvoiceState.ISSUED, InvoiceTrigger.CREDIT, InvoiceState.CREDITED),
        (InvoiceState.OVERDUE, InvoiceTrigger.SEND_TO_COLLECTION, InvoiceState.IN_COLLECTION),
        (InvoiceState.OVERDUE, InvoiceTrigger.MARK_PAID, InvoiceState.PAID),
        (InvoiceState.OVERDUE, InvoiceTrigger.CREDIT, InvoiceState.CREDITED),
        (InvoiceState.IN_COLLECTION, InvoiceTrigger.MARK_PAID, InvoiceState.PAID),
        (InvoiceState.IN_COLLECTION, InvoiceTrigger.CREDIT, InvoiceState.CREDITED),
    ]
)

#: The only states in which a draft's content may still be re-prepared.
EDITABLE_DOCUMENT_STATES: frozenset[InvoiceState] = frozenset(
    {InvoiceState.DRAFT, InvoiceState.PENDING_APPROVAL}
)

#: The triggers that allocate a number and seal a snapshot. Both are one-way doors.
ISSUING_TRIGGERS: frozenset[InvoiceTrigger] = frozenset(
    {InvoiceTrigger.ISSUE, InvoiceTrigger.APPROVE}
)


class DocumentRelationKind(StrEnum):
    """How two documents relate. A closed vocabulary, because "why does this document exist?" is
    the first question of every dispute and free text would let the interesting answers hide."""

    CREDITS = "credits"  # this credit note reduces that invoice
    DEBITS = "debits"  # this debit note increases that invoice
    CANCELS = "cancels"  # a full-value credit note: the legal form of cancellation post-issue
    REPLACES = "replaces"  # a corrected re-issue, where the jurisdiction permits one
    CORRECTED_BY = "corrected_by"  # the back-reference recorded on the original
    REPLACED_BY = "replaced_by"


@dataclass(frozen=True, slots=True)
class DocumentRelation:
    """One directed edge between two documents. Append-only: a relation is never removed, because
    the history of what corrected what is the audit trail."""

    kind: DocumentRelationKind
    document_id: str
    document_number: str = ""
    reason: str = ""
    at: float = 0.0


# ── parties ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PartyRegistration:
    """A registration identifier as **printed on the document**: GSTIN, VAT ID, CIN, or equivalent.

    Deliberately a captured value rather than a reference to the live `TaxRegistration`. An invoice
    must show what was true on its issue date; if it pointed at the tax profile, editing that
    profile years later would silently change a filed document."""

    scheme: str  # "gstin", "vat", "cin", "abn", …
    number: str
    country: str = ""


@dataclass(frozen=True, slots=True)
class InvoiceParty:
    """A supplier or a customer, captured at issue.

    Every field is a *copy taken at issue*, not a reference — the one place in the Billing plane
    where copying is the correct answer rather than the drift §A2.5 warns about. A document that
    re-read the customer's current name would print a name the customer did not have when they were
    billed, and that is precisely what a legal document may not do.

    `BillingAddress` is M9.2's, reused unchanged."""

    legal_name: str
    address: BillingAddress = field(default_factory=BillingAddress)
    trade_name: str = ""
    registrations: tuple[PartyRegistration, ...] = ()
    email: str = ""
    phone: str = ""
    account_ref: str = ""  # the billing account or legal entity code this was taken from

    def registration(self, scheme: str) -> PartyRegistration | None:
        return next((r for r in self.registrations if r.scheme == scheme), None)


# ── lines ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InvoiceLine:
    """One presented line.

    `journal_entry_id` is the load-bearing field: every monetary line on a VoxVoice invoice points
    at the financial fact that produced it, so "why is this line ₹4,120?" is answered by walking to
    the ledger entry, then to the charge, then to the usage. The amount here is a **copy of what was
    posted**, never a recomputation of it — which is why nothing in this package prices, rates, or
    folds anything.

    `ChargeLineKind` is M9.2's, reused unchanged."""

    id: str
    sequence: int
    kind: ChargeLineKind
    description: str
    amount: Money
    quantity: Quantity | None = None
    unit_amount: Money | None = None
    tax_amount: Money | None = None
    tax_rate_basis_points: int = 0
    period: BillingPeriod | None = None
    journal_entry_id: str = ""
    charge_id: str = ""
    subscription_id: str = ""
    price_code: str = ""
    meter_code: str = ""
    hsn_sac_code: str = ""  # India GST classification, printed where the regime requires it

    @property
    def is_negative(self) -> bool:
        return self.amount.is_negative

    @property
    def digest(self) -> str:
        return _digest(
            str(self.sequence),
            self.kind.value,
            self.description,
            str(self.amount.minor),
            self.amount.currency.value,
            str(self.tax_amount.minor if self.tax_amount else 0),
            str(self.tax_rate_basis_points),
            self.journal_entry_id,
        )


# ── presentation, payment, delivery, attachments ────────────────────────────────


@dataclass(frozen=True, slots=True)
class PaymentInstructions:
    """**How to pay** — printed guidance, never an operation.

    There is no gateway reference, no token, no capture, and no amount arithmetic here. The Invoice
    tells a customer where to send money; moving it is the Payment domain's, and a document that
    could initiate a charge would be a document that has left its boundary."""

    terms_label: str = ""  # "Net 30", "Due on receipt"
    due_at: float | None = None
    method_label: str = ""  # "Bank transfer (NEFT/RTGS)", "Card on file"
    bank_account_name: str = ""
    bank_account_ref: str = ""
    bank_ifsc_or_swift: str = ""
    remittance_reference: str = ""
    payment_url: str = ""
    notes: str = ""


class RenderFormat(StrEnum):
    """The document formats this milestone prepares models for. `PDF` and `HTML` are *models*, not
    bytes: producing the bytes is a template's job, and templates live outside the domain."""

    HTML = "html"
    PDF = "pdf"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class DocumentPresentation:
    """How a document should be presented — pinned at issue.

    `template_code` and `template_version` are in the snapshot and in its digest, so a template
    revised next year does not alter last year's document: the historical invoice re-renders through
    the template it was issued with. Presentation choices never touch a figure."""

    template_code: str = "standard"
    template_version: int = 1
    locale: str = "en-IN"
    date_format: str = "%d %b %Y"
    paper_size: str = "A4"
    show_usage_detail: bool = True
    show_period_column: bool = True
    logo_ref: str = ""
    footer_note: str = ""
    accent_colour: str = ""


class AttachmentPurpose(StrEnum):
    USAGE_DETAIL = "usage_detail"
    PURCHASE_ORDER = "purchase_order"
    TAX_CERTIFICATE = "tax_certificate"
    CONTRACT = "contract"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class AttachmentMetadata:
    """**Metadata only.** No bytes are stored, read, or served by this domain.

    The Invoice records that an attachment exists, what it is, how large it is, and its content
    digest; storing the object is a storage concern outside this plane, and pulling bytes through a
    domain module is how a pure package acquires I/O."""

    id: str
    filename: str
    media_type: str
    purpose: AttachmentPurpose = AttachmentPurpose.OTHER
    byte_size: int = 0
    content_digest: str = ""
    storage_ref: str = ""
    attached_at: float = 0.0


class DeliveryChannel(StrEnum):
    EMAIL = "email"
    PORTAL = "portal"
    API = "api"
    POST = "post"
    NONE = "none"


class DeliveryState(StrEnum):
    """What is known about delivery. Recorded, never performed.

    `UNKNOWN` is a real member and the default: a system that has not been told anything must say so
    rather than claim `PENDING`, which reads as "we are working on it"."""

    UNKNOWN = "unknown"
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True, slots=True)
class DeliveryMetadata:
    """What is known about how a document reached its customer.

    **The Invoice Engine sends nothing.** It has no mailer, no queue, and no transport; a delivery
    agent outside this boundary reports what happened and this records it. Keeping the record here
    and the sending elsewhere is what stops an invoice domain growing an SMTP client."""

    channel: DeliveryChannel = DeliveryChannel.NONE
    recipient: str = ""
    state: DeliveryState = DeliveryState.UNKNOWN
    requested_at: float | None = None
    last_attempt_at: float | None = None
    delivered_at: float | None = None
    attempts: int = 0
    failure_reason: str = ""

    @property
    def is_delivered(self) -> bool:
        return self.state is DeliveryState.DELIVERED


# ── the snapshot ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InvoiceSnapshot:
    """**The document.** Immutable, self-contained, and addressed by digest.

    Self-containment is the requirement (ADR-0023 IN5): everything needed to reproduce this document
    is *inside* it — both parties as they were, every line, the tax determination's conclusion, the
    totals, the payment instructions, and the template it was issued with. Nothing here is a lookup.
    An invoice that had to query live state to render would be an invoice that changes when that
    state does, and a document that changes is not evidence.

    `revision` counts preparations. Only one revision is ever *issued*, and the document pins that
    revision's digest — so a later revision, if one somehow existed, could not become the issued
    document. That is why "reproduces byte-identically forever" is a property of addressing rather
    than of care.

    `InvoiceTotals` is M9.2's and is computed by M9.2's `InvoiceTotalsService`. This domain declares
    no second way to total an invoice."""

    document_id: str
    revision: int
    kind: DocumentKind
    org_id: str
    account_id: str
    currency: Currency
    supplier: InvoiceParty
    customer: InvoiceParty
    lines: tuple[InvoiceLine, ...] = ()
    tax_components: tuple[TaxComponent, ...] = ()
    tax_treatment: TaxTreatment = TaxTreatment.STANDARD
    tax_determination_id: str = ""
    place_of_supply: str = ""
    totals: InvoiceTotals | None = None
    number: InvoiceNumber | None = None
    period: BillingPeriod | None = None
    issue_date: float | None = None
    due_date: float | None = None
    payment_instructions: PaymentInstructions = field(default_factory=PaymentInstructions)
    presentation: DocumentPresentation = field(default_factory=DocumentPresentation)
    attachments: tuple[AttachmentMetadata, ...] = ()
    relations: tuple[DocumentRelation, ...] = ()
    accounting_period_key: str = ""
    purchase_order_ref: str = ""
    notes: str = ""
    prepared_at: float = 0.0
    prepared_by: str = ""

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def is_zero_amount(self) -> bool:
        """A zero-amount document is still issued (ADR-0023 IN7) — suppressing it would leave a
        gap in a sequence and a period an auditor cannot account for."""
        return self.totals is not None and self.totals.total.is_zero

    @property
    def digest(self) -> str:
        """A deterministic fingerprint of the document's **content**.

        Excludes `prepared_at` and `prepared_by`, which legitimately differ between a preparation
        and a re-preparation of identical content, and includes everything a reader would see. Two
        preparations with equal digests produced the same document, which is checkable by comparing
        two strings rather than by trusting that nothing moved.

        Includes the template code and version: a document issued under one template is a different
        document from the same figures issued under another, and pinning it is what stops a
        redesign altering history."""
        totals = self.totals
        return _digest(
            self.document_id,
            str(self.revision),
            self.kind.value,
            self.org_id,
            self.account_id,
            self.currency.value,
            self.number.formatted if self.number else "",
            self.supplier.legal_name,
            self.customer.legal_name,
            *(line.digest for line in self.lines),
            *(f"{c.name}:{c.rate_basis_points}:{c.amount.minor}" for c in self.tax_components),
            self.tax_treatment.value,
            self.tax_determination_id,
            str(totals.subtotal.minor if totals else 0),
            str(totals.discount_total.minor if totals else 0),
            str(totals.tax_total.minor if totals else 0),
            str(totals.credit_applied.minor if totals else 0),
            str(totals.total.minor if totals else 0),
            str(totals.amount_due.minor if totals else 0),
            str(int(self.issue_date or 0)),
            str(int(self.due_date or 0)),
            self.presentation.template_code,
            str(self.presentation.template_version),
            self.presentation.locale,
            self.accounting_period_key,
        )


@dataclass(frozen=True, slots=True)
class InvoiceRevision:
    """One entry in a document's revision history. Append-only, and it survives issuance: knowing
    that a draft was prepared four times before it was issued is exactly what a dispute needs."""

    document_id: str
    revision: int
    snapshot_digest: str
    at: float = 0.0
    actor_id: str = ""
    reason: str = ""
    issued: bool = False


@dataclass(frozen=True, slots=True)
class CommercialInvoice:
    """A customer document: its identity, its state, its number, and the digest of its content.

    Deliberately **carries no lines and no totals**. The content is the snapshot, addressed by
    `issued_snapshot_digest` once issued — the same reason `AccountBalance` is derived in M9.13. A
    header that carried its own totals is a second copy of a fact the snapshot already holds, and
    the two eventually disagree in a way nobody notices until a customer does.

    Named `CommercialInvoice` beside M9.2's `Invoice`, which stays exactly as it is and which the
    minimal M9.5 surface still writes."""

    id: str
    org_id: str
    account_id: str
    kind: DocumentKind
    currency: Currency
    state: InvoiceState = InvoiceState.DRAFT
    number: InvoiceNumber | None = None
    revision: int = 0
    issued_revision: int = 0
    issued_snapshot_digest: str = ""
    accounting_period_key: str = ""
    period: BillingPeriod | None = None
    relations: tuple[DocumentRelation, ...] = ()
    delivery: DeliveryMetadata = field(default_factory=DeliveryMetadata)
    entity_code: str = ""
    jurisdiction: str = ""
    total_minor: int = 0  # a projection of the snapshot's total, for listing and search only
    amount_due_minor: int = 0
    prepared_at: float = 0.0
    issued_at: float | None = None
    due_at: float | None = None
    settled_at: float | None = None
    settled_by: str = ""
    updated_at: float = 0.0

    @property
    def is_issued(self) -> bool:
        return self.state in IMMUTABLE_INVOICE_STATES

    @property
    def is_immutable(self) -> bool:
        """Issued is immutable. There is no administrative override, because an override is exactly
        what an issued document must not have."""
        return self.is_issued

    @property
    def is_editable(self) -> bool:
        return self.state in EDITABLE_DOCUMENT_STATES

    @property
    def is_correction(self) -> bool:
        return self.kind is not DocumentKind.INVOICE

    @property
    def total(self) -> Money:
        return Money(self.total_minor, self.currency)

    @property
    def amount_due(self) -> Money:
        return Money(self.amount_due_minor, self.currency)

    @property
    def formatted_number(self) -> str:
        return self.number.formatted if self.number else ""

    def related(self, kind: DocumentRelationKind) -> tuple[DocumentRelation, ...]:
        return tuple(r for r in self.relations if r.kind is kind)


# ── numbering ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NumberingPolicy:
    """The declared numbering rule for one legal entity and jurisdiction.

    `gapless` is not decoration. Where a jurisdiction requires it, a missing number is a compliance
    failure rather than a cosmetic one, and the mechanism differs: a gapless series must be
    allocated inside the issuing transaction so a crash takes the number down with the issue. Where
    it is *not* required, the same mechanism is used anyway — there is no benefit to being loose."""

    entity_code: str
    jurisdiction: str = ""
    kind: DocumentKind = DocumentKind.INVOICE
    reset: NumberingReset = NumberingReset.NEVER
    gapless: bool = True
    prefix: str = ""
    padding: int = 6

    def series_key(self, bucket: str = "") -> str:
        return series_key_for(
            entity_code=self.entity_code,
            jurisdiction=self.jurisdiction,
            kind=self.kind.value,
            reset=self.reset,
            bucket=bucket,
        )


@dataclass(frozen=True, slots=True)
class NumberAllocation:
    """One allocated number, and the series it came from. Allocated exactly once, never reused —
    including across retries, failures, and concurrent billing runs (ADR-0023 IN4)."""

    series_key: str
    sequence: int
    number: InvoiceNumber
    allocated_at: float = 0.0


# ── rendering models ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DocumentField:
    """One labelled value, **already formatted as a string**.

    The type is the point: a template receives text, so there is nothing for it to add up. A
    renderer that could compute a total is a renderer that can disagree with the ledger, and the
    cheapest way to make that impossible is to hand it no numbers."""

    label: str
    value: str
    emphasis: bool = False


@dataclass(frozen=True, slots=True)
class DocumentTable:
    """A prepared table: headers and rows of strings, plus prepared footer totals."""

    caption: str = ""
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    footer: tuple[DocumentField, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentSection:
    """One block of a prepared document."""

    key: str
    title: str = ""
    fields: tuple[DocumentField, ...] = ()
    table: DocumentTable | None = None
    text: str = ""


@dataclass(frozen=True, slots=True)
class DocumentModel:
    """The prepared, format-neutral model a template consumes.

    One model serves both PDF and HTML. Two models would be two chances for the formats to diverge,
    and a customer holding a PDF that disagrees with the portal is a support case that cannot be
    closed."""

    document_id: str
    kind: DocumentKind
    title: str
    number: str = ""
    snapshot_digest: str = ""
    template_code: str = "standard"
    template_version: int = 1
    locale: str = "en-IN"
    sections: tuple[DocumentSection, ...] = ()

    def section(self, key: str) -> DocumentSection | None:
        return next((s for s in self.sections if s.key == key), None)


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    """A rendering of a snapshot in one format.

    `snapshot_digest` ties the output to the exact content it came from, so "is this PDF the invoice
    we issued?" is answered by comparing two strings. `is_faithful` is the check itself."""

    document_id: str
    format: RenderFormat
    snapshot_digest: str
    content: str = ""
    model: DocumentModel | None = None
    template_code: str = "standard"
    template_version: int = 1
    rendered_at: float = 0.0
    byte_size: int = 0

    def is_faithful(self, snapshot: InvoiceSnapshot) -> bool:
        """Whether this rendering came from that document. A `False` here means a rendered artefact
        is circulating that no issued document produced."""
        return self.snapshot_digest == snapshot.digest


# ── inspection, validation, history, search ─────────────────────────────────────


class InvoiceDiscrepancyKind(StrEnum):
    """How a document and its sources can disagree. Each is a distinct failure with a distinct
    remedy, which is why they are separate members rather than one `MISMATCH`."""

    UNBILLED_ENTRY = "unbilled_entry"  # the ledger recorded it; no document presents it
    ORPHAN_LINE = "orphan_line"  # the document presents it; no ledger entry produced it
    AMOUNT_MISMATCH = "amount_mismatch"
    TOTALS_MISMATCH = "totals_mismatch"  # the header disagrees with its own lines
    CURRENCY_MISMATCH = "currency_mismatch"
    NUMBER_GAP = "number_gap"
    DUPLICATE_NUMBER = "duplicate_number"
    SNAPSHOT_DRIFT = "snapshot_drift"  # the pinned digest no longer matches the stored snapshot
    MISSING_TAX_DETERMINATION = "missing_tax_determination"


@dataclass(frozen=True, slots=True)
class InvoiceDiscrepancy:
    kind: InvoiceDiscrepancyKind
    document_id: str = ""
    line_id: str = ""
    journal_entry_id: str = ""
    expected_minor: int = 0
    actual_minor: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class InvoiceReconciliation:
    """Does the document say what the ledger says? Asked as a read, because a document that
    disagrees with the ledger has usually already been sent."""

    org_id: str
    account_id: str
    accounting_period_key: str
    entries_examined: int = 0
    lines_examined: int = 0
    discrepancies: tuple[InvoiceDiscrepancy, ...] = ()
    at: float = 0.0

    @property
    def is_clean(self) -> bool:
        return not self.discrepancies

    @property
    def unbilled_count(self) -> int:
        return len(
            [d for d in self.discrepancies if d.kind is InvoiceDiscrepancyKind.UNBILLED_ENTRY]
        )


@dataclass(frozen=True, slots=True)
class NumberSeriesAudit:
    """A gapless series, audited. `is_gapless` is the compliance question stated as a property, so
    it can be answered by a screen rather than by a spreadsheet."""

    series_key: str
    allocated: int = 0
    lowest: int = 0
    highest: int = 0
    missing: tuple[int, ...] = ()
    duplicated: tuple[int, ...] = ()

    @property
    def is_gapless(self) -> bool:
        return not self.missing and not self.duplicated


@dataclass(frozen=True, slots=True)
class InvoiceInspection:
    """One document, inspected structurally: is it numbered, sealed, traced, and self-consistent?"""

    document_id: str
    kind: DocumentKind
    state: InvoiceState
    number: str = ""
    revision: int = 0
    snapshot_digest: str = ""
    is_sealed: bool = False
    is_traced: bool = True
    totals_agree: bool = True
    lines: int = 0
    relations: tuple[DocumentRelation, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_sound(self) -> bool:
        """What issuing requires: a document whose lines cannot be traced to a financial fact, or
        whose header disagrees with its body, must not become evidence."""
        return self.is_traced and self.totals_agree


class InvoiceAction(StrEnum):
    """What happened to a document. The activity log's closed vocabulary."""

    PREPARED = "document.prepared"
    REVISED = "document.revised"
    SUBMITTED = "document.submitted"
    ISSUED = "document.issued"
    VOIDED = "document.voided"
    MARKED_PAID = "document.marked_paid"
    MARKED_OVERDUE = "document.marked_overdue"
    SENT_TO_COLLECTION = "document.sent_to_collection"
    CREDITED = "document.credited"
    CREDIT_NOTE_ISSUED = "credit_note.issued"
    DEBIT_NOTE_ISSUED = "debit_note.issued"
    RENDERED = "document.rendered"
    DELIVERY_RECORDED = "document.delivery_recorded"
    ATTACHMENT_RECORDED = "document.attachment_recorded"
    NUMBER_ALLOCATED = "number.allocated"


@dataclass(frozen=True, slots=True)
class InvoiceActivity:
    """One append-only entry in the Invoice Engine's own history."""

    id: str
    org_id: str
    action: InvoiceAction
    actor_id: str = ""
    at: float = 0.0
    account_id: str = ""
    document_id: str = ""
    document_number: str = ""
    revision: int = 0
    amount_minor: int = 0
    currency: Currency | None = None
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class InvoiceSearchQuery:
    """A deterministic filter. No relevance scoring and no index — the same documents and the same
    query always come back in the same order, which is what a finance screen and a reproducible test
    both need."""

    text: str = ""
    account_id: str = ""
    kinds: tuple[DocumentKind, ...] = ()
    states: tuple[InvoiceState, ...] = ()
    accounting_period_key: str = ""
    number: str = ""
    entity_code: str = ""
    jurisdiction: str = ""
    issued_from: float | None = None
    issued_until: float | None = None
    min_total_minor: int | None = None
    max_total_minor: int | None = None
    overdue_only: bool = False
    corrections_only: bool = False
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class InvoiceSearchResult:
    documents: tuple[CommercialInvoice, ...] = ()
    total: int = 0


__all__ = [
    "DOCUMENT_LIFECYCLE",
    "EDITABLE_DOCUMENT_STATES",
    "ISSUING_TRIGGERS",
    "AttachmentMetadata",
    "AttachmentPurpose",
    "CommercialInvoice",
    "DeliveryChannel",
    "DeliveryMetadata",
    "DeliveryState",
    "DocumentField",
    "DocumentKind",
    "DocumentModel",
    "DocumentPresentation",
    "DocumentRelation",
    "DocumentRelationKind",
    "DocumentSection",
    "DocumentTable",
    "InvoiceActivity",
    "InvoiceAction",
    "InvoiceDiscrepancy",
    "InvoiceDiscrepancyKind",
    "InvoiceInspection",
    "InvoiceLine",
    "InvoiceParty",
    "InvoiceReconciliation",
    "InvoiceRevision",
    "InvoiceSearchQuery",
    "InvoiceSearchResult",
    "InvoiceSnapshot",
    "NumberAllocation",
    "NumberSeriesAudit",
    "NumberingPolicy",
    "NumberingReset",
    "PartyRegistration",
    "PaymentInstructions",
    "RenderFormat",
    "RenderedDocument",
    "derive_document_id",
    "derive_line_id",
    "derive_rendering_id",
    "derive_revision_id",
    "derive_snapshot_id",
    "series_key_for",
]

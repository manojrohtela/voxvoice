"""Invoice factories + repository contracts (M9.14).

`InvoiceFactory` is the one place a document, a snapshot, a revision, a rendering, an attachment
record, or an activity entry is constructed, following the M9.2 factory discipline exactly: validate
structurally, **fail fast** on error-severity findings (`BillingValidationError`, §A2.6), compose an
immutable aggregate from the injected id and clock seams, and emit the corresponding `billing.*`
fact through the injected emitter.

**Exactly one event per business fact.** A rejected command emits nothing at all. Note what the
lifecycle table makes visible: `ISSUE` and `APPROVE` both land on `issued`, and both publish
`billing.invoice.issued` — one fact, reached two ways, not two facts.

No I/O, no persistence, no networking, no rendering of bytes. Emission is the only effect and it is
injected.

---

## Why document ids are derived rather than minted

A document id is a hash of **what it documents**: for an invoice, the closed accounting period it
bills; for a credit or debit note, the document it corrects. Three consequences, all of them the
reason:

* **Re-preparing is idempotent.** Running the month-end job twice produces one draft, not two
  drafts racing for one number. Every other domain could de-duplicate on a read; here a second draft
  that got issued would be a second *legally numbered* document for one period.
* **A correction cannot overwrite what it corrects.** A credit note's basis is the invoice's id, so
  it derives a different id by construction. There is no code path that could edit the original,
  because the id it would need is never the id it computes.
* **Replay writes nothing new.** Re-preparing a period recomputes the same ids and the same snapshot
  digest, which is what makes a reproduction drill safe to run in production.

## Why numbering is not in this module

Formatting is here (through M9.2's `InvoiceNumberFormatter`, via `NumberingService`). *Allocation*
is behind `NumberSeriesAllocator`, a persistence seam, because gapless-under-concurrency and
durable-across-a-crash are properties of a transaction, and a pure function that claimed them would
be a claim no test could check where it matters.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .commercial_invoicing import (
    AttachmentMetadata,
    AttachmentPurpose,
    CommercialInvoice,
    DeliveryChannel,
    DeliveryMetadata,
    DeliveryState,
    DocumentKind,
    DocumentModel,
    DocumentRelation,
    InvoiceAction,
    InvoiceActivity,
    InvoiceRevision,
    InvoiceSnapshot,
    NumberAllocation,
    NumberingPolicy,
    RenderedDocument,
    RenderFormat,
    derive_document_id,
)
from .domain import BillingPeriod
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .invoice_rules import (
    DocumentAssembler,
    DocumentModelBuilder,
    HtmlRenderer,
    InvoiceValidator,
    NumberingService,
    PdfRenderer,
)
from .invoicing import InvoiceState, InvoiceTrigger
from .money import Currency
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

PREFIX_INVOICE_ATTACHMENT = "iat"
PREFIX_INVOICE_ACTIVITY = "iac"

#: Which `billing.*` fact each lifecycle trigger publishes. A table so "exactly one event per
#: business fact" is checkable by iteration rather than asserted in prose. `ISSUE` and `APPROVE`
#: deliberately share one fact: they are two routes to the same event in the world.
LIFECYCLE_EVENTS: dict[InvoiceTrigger, BillingEventType] = {
    InvoiceTrigger.SUBMIT_FOR_APPROVAL: BillingEventType.INVOICE_SUBMITTED_FOR_APPROVAL,
    InvoiceTrigger.APPROVE: BillingEventType.INVOICE_ISSUED,
    InvoiceTrigger.ISSUE: BillingEventType.INVOICE_ISSUED,
    InvoiceTrigger.VOID: BillingEventType.INVOICE_VOIDED,
    InvoiceTrigger.MARK_PAID: BillingEventType.INVOICE_PAID,
    InvoiceTrigger.MARK_OVERDUE: BillingEventType.INVOICE_OVERDUE,
    InvoiceTrigger.SEND_TO_COLLECTION: BillingEventType.INVOICE_SENT_TO_COLLECTION,
    InvoiceTrigger.CREDIT: BillingEventType.INVOICE_CREDITED,
}

#: Which fact issuing a correction document publishes. An invoice, a credit note, and a debit note
#: are the same aggregate with different legal meanings, and the vocabulary keeps them distinct.
ISSUE_EVENTS: dict[DocumentKind, BillingEventType] = {
    DocumentKind.INVOICE: BillingEventType.INVOICE_ISSUED,
    DocumentKind.CREDIT_NOTE: BillingEventType.CREDIT_NOTE_ISSUED,
    DocumentKind.DEBIT_NOTE: BillingEventType.DEBIT_NOTE_ISSUED,
}


@dataclass(frozen=True, slots=True)
class InvoiceFactory:
    """Constructs invoice aggregates from injected seams. Stateless; holds references only."""

    ids: IdGenerator = SequentialIdGenerator()
    clock: Clock = SystemClock()
    emitter: EventEmitter = NullEventEmitter()
    validator: InvoiceValidator = InvoiceValidator()
    assembler: DocumentAssembler = DocumentAssembler()
    numbering: NumberingService = NumberingService()
    models: DocumentModelBuilder = DocumentModelBuilder()
    html: HtmlRenderer = HtmlRenderer()
    pdf: PdfRenderer = PdfRenderer()

    # ── documents ───────────────────────────────────────────────────────────────

    def prepare(
        self,
        *,
        org_id: str,
        account_id: str,
        kind: DocumentKind,
        basis_id: str,
        currency: Currency,
        accounting_period_key: str = "",
        period: BillingPeriod | None = None,
        entity_code: str = "",
        jurisdiction: str = "",
        relations: tuple[DocumentRelation, ...] = (),
        actor_id: str = "",
    ) -> CommercialInvoice:
        """A new draft. Carries no content — content is a snapshot, appended separately."""
        now = self.clock.now()
        document = CommercialInvoice(
            id=derive_document_id(org_id, account_id, kind.value, basis_id),
            org_id=org_id,
            account_id=account_id,
            kind=kind,
            currency=currency,
            state=InvoiceState.DRAFT,
            accounting_period_key=accounting_period_key,
            period=period,
            relations=relations,
            entity_code=entity_code,
            jurisdiction=jurisdiction,
            prepared_at=now,
            updated_at=now,
        )
        self._fail_fast(self.validator.validate_document(document))
        self._emit(
            BillingEventType.INVOICE_PREPARED,
            org_id,
            document.id,
            actor_id,
            account_id=account_id,
            kind=kind.value,
            accounting_period_key=accounting_period_key,
        )
        return document

    def snapshot(self, **kwargs) -> InvoiceSnapshot:
        """Seal one revision's content. Delegates wholly to `DocumentAssembler` — totals come from
        M9.2's `InvoiceTotalsService` and nothing here adds anything up."""
        built = self.assembler.snapshot(
            prepared_at=self.clock.now(),
            **kwargs,
        )
        self._fail_fast(self.validator.validate_snapshot(built))
        return built

    def revision(
        self,
        snapshot: InvoiceSnapshot,
        *,
        actor_id: str = "",
        reason: str = "",
        issued: bool = False,
    ) -> InvoiceRevision:
        """One append-only entry in a document's revision history.

        It survives issuance deliberately: knowing that a draft was prepared four times before it
        was issued is exactly what a dispute needs, and it costs one row."""
        return InvoiceRevision(
            document_id=snapshot.document_id,
            revision=snapshot.revision,
            snapshot_digest=snapshot.digest,
            at=self.clock.now(),
            actor_id=actor_id,
            reason=reason,
            issued=issued,
        )

    def emit_revision(self, snapshot: InvoiceSnapshot, *, actor_id: str = "") -> None:
        self._emit(
            BillingEventType.INVOICE_REVISED,
            snapshot.org_id,
            snapshot.document_id,
            actor_id,
            account_id=snapshot.account_id,
            revision=snapshot.revision,
            digest=snapshot.digest,
        )

    def emit_transition(
        self,
        document: CommercialInvoice,
        trigger: InvoiceTrigger,
        *,
        actor_id: str = "",
        **data,
    ) -> None:
        """Emit exactly the one fact this move represents.

        Issuing a credit note publishes `credit_note.issued` rather than `invoice.issued`: they are
        different facts to every consumer, and collapsing them would make a revenue report that
        counts issuances silently wrong."""
        event_type = LIFECYCLE_EVENTS[trigger]
        if document.state is InvoiceState.ISSUED:
            event_type = ISSUE_EVENTS.get(document.kind, event_type)
        self._emit(
            event_type,
            document.org_id,
            document.id,
            actor_id,
            account_id=document.account_id,
            kind=document.kind.value,
            state=document.state.value,
            number=document.formatted_number,
            total_minor=document.total_minor,
            currency=document.currency.value,
            **data,
        )

    # ── numbering ───────────────────────────────────────────────────────────────

    def number(
        self, policy: NumberingPolicy, sequence: int, *, org_id: str, actor_id: str = ""
    ) -> NumberAllocation:
        """Format an already-allocated sequence and record that it was allocated.

        The *allocation* happened in persistence, inside the issuing transaction. Recording it as a
        fact here means a series audit can be answered from the event stream as well as from the
        rows — two independent readings of a compliance property that must never disagree."""
        now = self.clock.now()
        allocation = self.numbering.allocation(policy, sequence, at=now)
        self._emit(
            BillingEventType.INVOICE_NUMBER_ALLOCATED,
            org_id,
            allocation.number.formatted,
            actor_id,
            series_key=allocation.series_key,
            sequence=sequence,
        )
        return allocation

    # ── rendering ───────────────────────────────────────────────────────────────

    def render(
        self,
        snapshot: InvoiceSnapshot,
        fmt: RenderFormat,
        *,
        org_id: str = "",
        actor_id: str = "",
    ) -> RenderedDocument:
        """Render a sealed snapshot. Deterministic, and it computes nothing.

        The rendering carries the snapshot's digest, so "is this the document we issued?" is a
        string comparison rather than a re-derivation."""
        model = self.models.build(snapshot)
        rendered = self._renderer(fmt).render(model, at=self.clock.now())
        self._emit(
            BillingEventType.INVOICE_RENDERED,
            org_id or snapshot.org_id,
            snapshot.document_id,
            actor_id,
            account_id=snapshot.account_id,
            format=fmt.value,
            digest=snapshot.digest,
            byte_size=rendered.byte_size,
        )
        return rendered

    def model(self, snapshot: InvoiceSnapshot) -> DocumentModel:
        return self.models.build(snapshot)

    def _renderer(self, fmt: RenderFormat):
        if fmt is RenderFormat.PDF:
            return self.pdf
        return self.html

    # ── delivery + attachments (metadata only) ──────────────────────────────────

    def delivery(
        self,
        *,
        channel: DeliveryChannel,
        recipient: str,
        state: DeliveryState,
        attempts: int = 0,
        failure_reason: str = "",
        previous: DeliveryMetadata | None = None,
    ) -> DeliveryMetadata:
        """Record what a delivery agent reported. **Sends nothing.**"""
        now = self.clock.now()
        return DeliveryMetadata(
            channel=channel,
            recipient=recipient,
            state=state,
            requested_at=(previous.requested_at if previous else None) or now,
            last_attempt_at=now,
            delivered_at=now if state is DeliveryState.DELIVERED else None,
            attempts=attempts or ((previous.attempts if previous else 0) + 1),
            failure_reason=failure_reason,
        )

    def emit_delivery(
        self, document: CommercialInvoice, delivery: DeliveryMetadata, *, actor_id: str = ""
    ) -> None:
        self._emit(
            BillingEventType.INVOICE_DELIVERY_RECORDED,
            document.org_id,
            document.id,
            actor_id,
            account_id=document.account_id,
            channel=delivery.channel.value,
            state=delivery.state.value,
            attempts=delivery.attempts,
        )

    def attachment(
        self,
        *,
        filename: str,
        media_type: str,
        purpose: AttachmentPurpose = AttachmentPurpose.OTHER,
        byte_size: int = 0,
        content_digest: str = "",
        storage_ref: str = "",
    ) -> AttachmentMetadata:
        """Metadata only. No bytes are read, stored, or served by this domain."""
        return AttachmentMetadata(
            id=self.ids.new(PREFIX_INVOICE_ATTACHMENT),
            filename=filename,
            media_type=media_type,
            purpose=purpose,
            byte_size=byte_size,
            content_digest=content_digest,
            storage_ref=storage_ref,
            attached_at=self.clock.now(),
        )

    def emit_attachment(
        self, document: CommercialInvoice, attachment: AttachmentMetadata, *, actor_id: str = ""
    ) -> None:
        self._emit(
            BillingEventType.INVOICE_ATTACHMENT_RECORDED,
            document.org_id,
            document.id,
            actor_id,
            account_id=document.account_id,
            filename=attachment.filename,
            purpose=attachment.purpose.value,
        )

    # ── history ─────────────────────────────────────────────────────────────────

    def activity(
        self,
        *,
        org_id: str,
        action: InvoiceAction,
        actor_id: str = "",
        account_id: str = "",
        document_id: str = "",
        document_number: str = "",
        revision: int = 0,
        amount_minor: int = 0,
        currency: Currency | None = None,
        reason: str = "",
        detail: str = "",
    ) -> InvoiceActivity:
        return InvoiceActivity(
            id=self.ids.new(PREFIX_INVOICE_ACTIVITY),
            org_id=org_id,
            action=action,
            actor_id=actor_id,
            at=self.clock.now(),
            account_id=account_id,
            document_id=document_id,
            document_number=document_number,
            revision=revision,
            amount_minor=amount_minor,
            currency=currency,
            reason=reason,
            detail=detail,
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _fail_fast(self, diagnostics) -> None:
        if diagnostics.has_errors:
            raise BillingValidationError(diagnostics)

    def _emit(
        self, event_type: BillingEventType, org_id: str, subject_id: str, actor_id: str, **data
    ) -> None:
        self.emitter.emit(
            BillingEvent(
                id=self.ids.new("bev"),
                type=event_type,
                ts=self.clock.now(),
                org_id=org_id,
                subject_id=subject_id,
                actor_id=actor_id,
                data=dict(data),
            )
        )


# ── repository contracts (additive to the M9.2 seam) ────────────────────────────


class CommercialInvoiceRepository(ABC):
    """Document headers. Org-scoped by construction (§A11.2).

    `replace` moves *state*, a number, and a pinned digest — never content, because there is no
    content on a header to move. That is what makes "an issued document is immutable" survive the
    existence of a `replace` method at all: the only mutable thing about a document is where it is
    in its lifecycle, and the lifecycle has no edge back."""

    @abstractmethod
    def add(self, document: CommercialInvoice) -> CommercialInvoice: ...
    @abstractmethod
    def get(self, org_id: str, document_id: str) -> CommercialInvoice | None: ...
    @abstractmethod
    def by_number(self, org_id: str, formatted: str) -> CommercialInvoice | None: ...
    @abstractmethod
    def for_account(
        self, org_id: str, account_id: str, limit: int = 200
    ) -> tuple[CommercialInvoice, ...]: ...
    @abstractmethod
    def for_period(
        self, org_id: str, account_id: str, accounting_period_key: str
    ) -> tuple[CommercialInvoice, ...]: ...
    @abstractmethod
    def related_to(self, org_id: str, document_id: str) -> tuple[CommercialInvoice, ...]:
        """Every credit note, debit note, or replacement that references this document."""

    @abstractmethod
    def all(self, org_id: str, limit: int = 500) -> tuple[CommercialInvoice, ...]: ...
    @abstractmethod
    def replace(self, document: CommercialInvoice) -> CommercialInvoice:
        """Lifecycle state, number, pinned digest, delivery, relations. Never content."""


class InvoiceSnapshotRepository(ABC):
    """**Append-only document content.**

    There is no update method and no delete method — not by convention, but because none exists to
    call. A revision is written once and read forever; `by_digest` is how an issued document is
    resolved, so the *content served* is addressed by the digest that was pinned at issue rather
    than by whichever revision happens to be newest."""

    @abstractmethod
    def append(self, snapshot: InvoiceSnapshot) -> InvoiceSnapshot:
        """Store, or return what is already stored for this (document, revision). Never raises on a
        duplicate; never writes a second row for one revision."""

    @abstractmethod
    def get(self, org_id: str, document_id: str, revision: int) -> InvoiceSnapshot | None: ...
    @abstractmethod
    def by_digest(self, org_id: str, digest: str) -> InvoiceSnapshot | None:
        """The resolution path for an issued document. This is the method that makes byte-identical
        historical reproduction structural rather than careful."""

    @abstractmethod
    def revisions(self, org_id: str, document_id: str) -> tuple[InvoiceSnapshot, ...]:
        """Every revision, oldest first."""

    @abstractmethod
    def latest(self, org_id: str, document_id: str) -> InvoiceSnapshot | None: ...


class InvoiceRevisionRepository(ABC):
    """Append-only revision history — who prepared what, when, and why."""

    @abstractmethod
    def append(self, revision: InvoiceRevision) -> InvoiceRevision: ...
    @abstractmethod
    def history(self, org_id: str, document_id: str) -> tuple[InvoiceRevision, ...]: ...


class NumberSeriesAllocator(ABC):
    """**Gapless, exactly-once, multi-worker-safe, crash-safe** number allocation.

    Declared beside M9.2's `InvoiceNumberAllocator`, which stays exactly as it is. The difference is
    real rather than cosmetic: this one is org-scoped by construction (§A11.2) and keyed by *series*
    rather than by entity alone, because a jurisdiction that resets annually needs a counter per
    year and one that does not needs a counter that never resets — and merging them makes each look
    gapped to the authority reading it.

    The contract implementations must honour: **allocate inside the caller's transaction.** A number
    handed out before the issue commits is a number that a crash turns into a permanent hole, and no
    later process may legally fill it."""

    @abstractmethod
    def allocate(self, org_id: str, series_key: str) -> int:
        """The next sequence in this series. Never returns a value twice, never skips on success."""

    @abstractmethod
    def peek(self, org_id: str, series_key: str) -> int:
        """The last allocated value, without allocating. `0` when the series is untouched."""

    @abstractmethod
    def series(self, org_id: str) -> tuple[str, ...]:
        """Every series this org has allocated from — what a compliance audit enumerates."""

    @abstractmethod
    def allocated(self, org_id: str, series_key: str) -> tuple[int, ...]:
        """Every sequence this series has handed out.

        The **authoritative** input to a gapless audit, and deliberately not "the numbers on the
        documents". A number allocated to an issue that then failed is a real hole, and inferring
        the series from documents alone would hide exactly the case the audit exists to find."""


class RenderedDocumentRepository(ABC):
    """Append-only renderings, keyed by (document, digest, format, template version).

    Stored rather than only recomputed so that "the PDF the customer holds" is an artefact the
    platform can produce again identically, not a claim about a function. Append-only for the same
    reason the snapshot is: a rendering that could be replaced is a rendering that can stop matching
    the document it came from."""

    @abstractmethod
    def append(self, rendered: RenderedDocument) -> RenderedDocument: ...
    @abstractmethod
    def get(
        self, org_id: str, document_id: str, digest: str, fmt: RenderFormat
    ) -> RenderedDocument | None: ...
    @abstractmethod
    def for_document(self, org_id: str, document_id: str) -> tuple[RenderedDocument, ...]: ...


class InvoiceActivityRepository(ABC):
    """Append-only invoice history."""

    @abstractmethod
    def append(self, activity: InvoiceActivity) -> InvoiceActivity: ...
    @abstractmethod
    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[InvoiceActivity, ...]:
        """Newest first. `account_id=""` spans the whole organization."""

    @abstractmethod
    def for_document(self, org_id: str, document_id: str) -> tuple[InvoiceActivity, ...]: ...


__all__ = [
    "ISSUE_EVENTS",
    "LIFECYCLE_EVENTS",
    "PREFIX_INVOICE_ACTIVITY",
    "PREFIX_INVOICE_ATTACHMENT",
    "CommercialInvoiceRepository",
    "InvoiceActivityRepository",
    "InvoiceFactory",
    "InvoiceRevisionRepository",
    "InvoiceSnapshotRepository",
    "NumberSeriesAllocator",
    "RenderedDocumentRepository",
]

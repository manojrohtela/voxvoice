"""Invoice Engine rules (M9.14) — stateless document services.

Every decision this milestone makes lives here: which lines a document presents, how a snapshot is
assembled and sealed, how a number is formatted, what a correction looks like, whether a document
agrees with the ledger, whether a series is gapless, and how a snapshot becomes a renderable model.

**No I/O, no clock, no id minting, no persistence.** Every service is a pure function of what it is
given, which is what makes "the same snapshot renders byte-identically forever" testable rather than
merely intended.

## What is deliberately *not* here

* **No price, no rate, no meter, no balance.** A line's amount is a copy of what M9.13 posted, read
  from the journal entry's own `signed_minor`. Nothing in this module multiplies a quantity by a
  rate, folds a balance, or resolves a price — a test walks the AST to prove it.
* **No second way to total an invoice.** `DocumentAssembler` delegates to M9.2's
  `InvoiceTotalsService`, which declares the order discount → credit → tax is applied in. Two
  totalling implementations is exactly the drift §A2.5 prevents, and in a legal document it is the
  drift that produces a header disagreeing with its own body.
* **No second way to format a number.** `NumberingService` delegates to M9.2's
  `InvoiceNumberFormatter`. Allocation — the gapless, exactly-once part — is a persistence concern
  behind a seam, because a counter that is safe under concurrency cannot be pure.
* **No arithmetic in the renderers.** `DocumentModelBuilder` hands templates *strings*. A renderer
  that could compute a total is a renderer that can disagree with the ledger, and handing it no
  numbers is cheaper than trusting it not to.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from core.fsm import StateMachineDefinition

from .commercial_invoicing import (
    DOCUMENT_LIFECYCLE,
    EDITABLE_DOCUMENT_STATES,
    CommercialInvoice,
    DocumentField,
    DocumentKind,
    DocumentModel,
    DocumentPresentation,
    DocumentRelation,
    DocumentRelationKind,
    DocumentSection,
    DocumentTable,
    InvoiceDiscrepancy,
    InvoiceDiscrepancyKind,
    InvoiceInspection,
    InvoiceLine,
    InvoiceParty,
    InvoiceReconciliation,
    InvoiceSearchQuery,
    InvoiceSearchResult,
    InvoiceSnapshot,
    NumberAllocation,
    NumberingPolicy,
    NumberingReset,
    NumberSeriesAudit,
    PaymentInstructions,
    RenderedDocument,
    RenderFormat,
    derive_line_id,
)
from .diagnostics import BillingDiagnosticCode, BillingDiagnostics, Diagnostic, Severity
from .domain import BillingPeriod
from .errors import BillingConflict, InvalidBillingTransition
from .invoicing import (
    ChargeLine,
    ChargeLineKind,
    InvoiceNumber,
    InvoiceState,
    InvoiceTotals,
    InvoiceTrigger,
)
from .ledger import LedgerEntryType
from .money import Currency, Money
from .services import InvoiceNumberFormatter, InvoiceTotalsService
from .tax import TaxComponent, TaxTreatment

#: How a posted ledger entry is presented on a document. A declared table rather than a chain of
#: `if`s, so "what does a customer see when the ledger records an adjustment?" is answerable by
#: reading one mapping instead of tracing a branch.
ENTRY_TYPE_TO_LINE_KIND: dict[LedgerEntryType, ChargeLineKind] = {
    LedgerEntryType.CHARGE: ChargeLineKind.USAGE,
    LedgerEntryType.DISCOUNT: ChargeLineKind.DISCOUNT,
    LedgerEntryType.CREDIT_APPLIED: ChargeLineKind.CREDIT,
    LedgerEntryType.ADJUSTMENT: ChargeLineKind.ONE_OFF,
    LedgerEntryType.TAX: ChargeLineKind.TAX,
    LedgerEntryType.WRITE_OFF: ChargeLineKind.ONE_OFF,
    LedgerEntryType.PAYMENT: ChargeLineKind.CREDIT,
    LedgerEntryType.REFUND: ChargeLineKind.ONE_OFF,
    LedgerEntryType.CHARGEBACK: ChargeLineKind.ONE_OFF,
    LedgerEntryType.PAYABLE: ChargeLineKind.ONE_OFF,
}

#: The kinds whose amounts are *presented* as reductions. Used only for ordering and labelling —
#: the sign itself always comes from the ledger entry.
REDUCING_LINE_KINDS: frozenset[ChargeLineKind] = frozenset(
    {ChargeLineKind.DISCOUNT, ChargeLineKind.CREDIT}
)

#: A placeholder window for lines that carry none — a tax line covers the document, not a period.
#: `BillingPeriod` refuses a zero-length window (§A2.6), so the placeholder is a real one-second
#: rather than a sentinel, and it exists only to satisfy the M9.2 `ChargeLine` projection the
#: totalling service reads. Nothing financial depends on it: `InvoiceTotalsService` reads kind and
#: amount only.
_NO_PERIOD = BillingPeriod(0.0, 1.0)


# ── lifecycle ───────────────────────────────────────────────────────────────────


class DocumentLifecycleService:
    """Applies document lifecycle triggers through the shared FSM.

    Illegal edges are rejected by the table, not by an `if` — which is what makes "an issued
    document never becomes editable" a property of the machine rather than of whoever wrote the last
    branch. There is no edge back to `draft` and no `void` out of any issued state, so neither has a
    code path that could be reached by mistake."""

    def __init__(self, definition: StateMachineDefinition = DOCUMENT_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: InvoiceState, trigger: InvoiceTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: InvoiceState, trigger: InvoiceTrigger) -> InvoiceState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"an invoice cannot {trigger.value} from {current.value}; legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: InvoiceState) -> tuple[InvoiceTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self,
        document: CommercialInvoice,
        trigger: InvoiceTrigger,
        *,
        at: float,
        actor_id: str = "",
        number: InvoiceNumber | None = None,
        snapshot: InvoiceSnapshot | None = None,
        due_at: float | None = None,
    ) -> CommercialInvoice:
        """Move a document. Issuing is the only transition that changes content, and it does so by
        *pinning* a snapshot digest rather than by writing content onto the document."""
        target = self.next_state(document.state, trigger)
        changes: dict = {"state": target, "updated_at": at}
        if target is InvoiceState.ISSUED:
            if number is None or snapshot is None:
                raise BillingConflict(
                    "issuing requires an allocated number and a sealed snapshot; a document "
                    "without both is not evidence"
                )
            totals = snapshot.totals
            changes.update(
                number=number,
                issued_at=at,
                due_at=due_at if due_at is not None else snapshot.due_date,
                issued_revision=snapshot.revision,
                issued_snapshot_digest=snapshot.digest,
                total_minor=totals.total.minor if totals else 0,
                amount_due_minor=totals.amount_due.minor if totals else 0,
            )
        if target in (InvoiceState.PAID, InvoiceState.CREDITED, InvoiceState.VOIDED):
            changes.update(settled_at=at, settled_by=actor_id)
        return replace(document, **changes)

    def guard_editable(self, document: CommercialInvoice) -> None:
        """Fail fast when something would re-prepare a document that has already been issued.

        Belt and braces beside the FSM: the machine has no edge, and this refuses the *intent* with
        the message a caller trying to fix an issued invoice actually needs to read."""
        if document.state not in EDITABLE_DOCUMENT_STATES:
            raise BillingConflict(
                f"invoice {document.id} is {document.state.value}; an issued document is never "
                "edited — correct it with a credit note or a debit note"
            )


# ── line building ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LineSource:
    """One ledger fact, ready to be presented.

    A deliberately narrow projection of an M9.13 `JournalEntry`: this module never receives the
    entry itself, so it cannot be tempted to fold a balance out of a collection of them."""

    journal_entry_id: str
    entry_type: LedgerEntryType
    signed_minor: int
    currency: Currency
    description: str = ""
    charge_id: str = ""
    subscription_id: str = ""
    price_code: str = ""
    meter_code: str = ""
    hsn_sac_code: str = ""
    period: BillingPeriod | None = None
    sequence: int = 0


class LineBuilder:
    """Turns ledger facts into presented lines. **Copies amounts; never computes one.**

    The amount comes from the entry's own `signed_minor` — M9.13's derived contribution to the
    balance — so a line and the ledger cannot disagree about direction. Presenting a debit as a
    positive line and a credit as a negative one is the ledger's convention, read rather than
    re-derived."""

    def lines(self, sources: Sequence[LineSource], *, document_id: str) -> tuple[InvoiceLine, ...]:
        built: list[InvoiceLine] = []
        for index, source in enumerate(sorted(sources, key=lambda s: (s.sequence, s.charge_id)), 1):
            kind = ENTRY_TYPE_TO_LINE_KIND.get(source.entry_type, ChargeLineKind.ONE_OFF)
            built.append(
                InvoiceLine(
                    id=derive_line_id(document_id, index, source.journal_entry_id),
                    sequence=index,
                    kind=kind,
                    description=source.description or _default_description(kind),
                    amount=Money(source.signed_minor, source.currency),
                    period=source.period,
                    journal_entry_id=source.journal_entry_id,
                    charge_id=source.charge_id,
                    subscription_id=source.subscription_id,
                    price_code=source.price_code,
                    meter_code=source.meter_code,
                    hsn_sac_code=source.hsn_sac_code,
                )
            )
        return tuple(built)

    def tax_lines(
        self, components: Sequence[TaxComponent], *, document_id: str, offset: int
    ) -> tuple[InvoiceLine, ...]:
        """Tax is presented as lines because a customer reads a document, not a determination.

        The amounts are the determination's, copied. Nothing here applies a rate to a base — that
        happened in the tax domain, and reapplying it is how a document comes to show a figure the
        authority was never told."""
        return tuple(
            InvoiceLine(
                id=derive_line_id(document_id, offset + index, component.name),
                sequence=offset + index,
                kind=ChargeLineKind.TAX,
                description=component.name,
                amount=component.amount,
                tax_amount=component.amount,
                tax_rate_basis_points=component.rate_basis_points,
            )
            for index, component in enumerate(components, 1)
        )


def _default_description(kind: ChargeLineKind) -> str:
    return {
        ChargeLineKind.USAGE: "Usage charges",
        ChargeLineKind.DISCOUNT: "Discount",
        ChargeLineKind.CREDIT: "Credit applied",
        ChargeLineKind.TAX: "Tax",
        ChargeLineKind.ONE_OFF: "Adjustment",
    }.get(kind, kind.value.replace("_", " ").title())


# ── assembly ────────────────────────────────────────────────────────────────────


class DocumentAssembler:
    """Assembles an immutable snapshot from parts that were all decided elsewhere.

    Totals come from **M9.2's `InvoiceTotalsService`**, which declares the order in which discount,
    credit, and tax are applied. That order is financially load-bearing, so there is exactly one
    place it lives and this is not a second one."""

    def __init__(self, totals: InvoiceTotalsService | None = None) -> None:
        self._totals = totals or InvoiceTotalsService()

    def totals(
        self,
        lines: Sequence[InvoiceLine],
        *,
        currency: Currency,
        credit_applied: Money | None = None,
    ) -> InvoiceTotals:
        """Delegate to the one declared totalling service.

        Presented lines are projected onto M9.2 `ChargeLine`s because that is what the service
        reads. The projection carries only kind and amount — the two fields the order depends on —
        so nothing about presentation can influence a total."""
        projected = tuple(
            ChargeLine(
                id=line.id,
                org_id="",
                account_id="",
                kind=line.kind,
                description=line.description,
                amount=line.amount,
                period=line.period or _NO_PERIOD,
            )
            for line in lines
        )
        return self._totals.totals(projected, currency=currency, credit_applied=credit_applied)

    def snapshot(
        self,
        *,
        document: CommercialInvoice,
        revision: int,
        supplier: InvoiceParty,
        customer: InvoiceParty,
        lines: Sequence[InvoiceLine],
        tax_components: Sequence[TaxComponent] = (),
        tax_treatment: TaxTreatment = TaxTreatment.STANDARD,
        tax_determination_id: str = "",
        place_of_supply: str = "",
        credit_applied: Money | None = None,
        number: InvoiceNumber | None = None,
        issue_date: float | None = None,
        due_date: float | None = None,
        payment_instructions: PaymentInstructions | None = None,
        presentation: DocumentPresentation | None = None,
        attachments: Sequence = (),
        relations: Sequence[DocumentRelation] = (),
        purchase_order_ref: str = "",
        notes: str = "",
        prepared_at: float = 0.0,
        prepared_by: str = "",
    ) -> InvoiceSnapshot:
        ordered = tuple(lines)
        return InvoiceSnapshot(
            document_id=document.id,
            revision=revision,
            kind=document.kind,
            org_id=document.org_id,
            account_id=document.account_id,
            currency=document.currency,
            supplier=supplier,
            customer=customer,
            lines=ordered,
            tax_components=tuple(tax_components),
            tax_treatment=tax_treatment,
            tax_determination_id=tax_determination_id,
            place_of_supply=place_of_supply,
            totals=self.totals(ordered, currency=document.currency, credit_applied=credit_applied),
            number=number,
            period=document.period,
            issue_date=issue_date,
            due_date=due_date,
            payment_instructions=payment_instructions or PaymentInstructions(),
            presentation=presentation or DocumentPresentation(),
            attachments=tuple(attachments),
            relations=tuple(relations),
            accounting_period_key=document.accounting_period_key,
            purchase_order_ref=purchase_order_ref,
            notes=notes,
            prepared_at=prepared_at,
            prepared_by=prepared_by,
        )


class SnapshotIntegrityService:
    """Proves that the document being served is the document that was issued.

    The check is a string comparison against the digest pinned at issue. That is the whole
    mechanism, and its cheapness is the point: an integrity check nobody can afford to run on every
    read is an integrity check that runs never."""

    def is_pinned(self, document: CommercialInvoice, snapshot: InvoiceSnapshot) -> bool:
        return bool(document.issued_snapshot_digest) and (
            snapshot.digest == document.issued_snapshot_digest
        )

    def guard_pinned(self, document: CommercialInvoice, snapshot: InvoiceSnapshot) -> None:
        if not self.is_pinned(document, snapshot):
            raise BillingConflict(
                f"invoice {document.id} pins snapshot digest "
                f"{document.issued_snapshot_digest[:12]}… but the stored revision hashes to "
                f"{snapshot.digest[:12]}… — an issued document has changed underneath its pin"
            )

    def select_issued(
        self, document: CommercialInvoice, revisions: Sequence[InvoiceSnapshot]
    ) -> InvoiceSnapshot | None:
        """Resolve the issued revision **by digest**, not by recency.

        This is what makes historical reproduction structural. If a later revision somehow existed,
        selecting by digest would still return the issued one — the newest revision cannot become
        the document by being newest."""
        if not document.issued_snapshot_digest:
            return revisions[-1] if revisions else None
        return next(
            (r for r in revisions if r.digest == document.issued_snapshot_digest),
            None,
        )


# ── numbering ───────────────────────────────────────────────────────────────────


class NumberingService:
    """Decides *which series* a document numbers in and *how* the number reads.

    Deliberately does not allocate. Allocation must be atomic under concurrency and durable across a
    crash, which is a persistence property; a pure service that pretended to allocate would be a
    service whose correctness could not be tested where it matters."""

    #: The bucket length, in seconds, an annual or monthly reset uses. Derived from the issue
    #: instant so a series key is a pure function of the document rather than of a calendar library.
    _YEAR = 365 * 24 * 60 * 60
    _MONTH = 30 * 24 * 60 * 60

    def __init__(self, formatter: InvoiceNumberFormatter | None = None) -> None:
        self._formatter = formatter or InvoiceNumberFormatter()

    def bucket(self, policy: NumberingPolicy, *, at: float) -> str:
        """Which reset window an issue instant falls in.

        Computed from the instant rather than from a locale calendar, because a key that depended on
        a timezone would let the same document land in two series depending on which region issued
        it — and a series that two regions can both write is a series that is not gapless."""
        if policy.reset is NumberingReset.NEVER:
            return ""
        span = self._YEAR if policy.reset is NumberingReset.ANNUAL else self._MONTH
        return f"{int(at) // span}"

    def series_key(self, policy: NumberingPolicy, *, at: float) -> str:
        return policy.series_key(self.bucket(policy, at=at))

    #: The token that distinguishes a correction document's number from an invoice's.
    #:
    #: Load-bearing rather than decorative. Each document kind counts in its **own** gapless series,
    #: so an invoice and a credit note both legitimately reach sequence 1 — and without a token in
    #: the printed number they would carry the same identifier while stating opposite things. Found
    #: by the end-to-end smoke, which issued all three kinds and got one number back three times.
    #:
    #: An invoice's token is empty, so `VOX-KA-000001` is exactly what M9.2's
    #: `InvoiceNumberFormatter` has always produced. Only the new kinds add anything.
    KIND_TOKENS: dict[DocumentKind, str] = {
        DocumentKind.INVOICE: "",
        DocumentKind.CREDIT_NOTE: "CN",
        DocumentKind.DEBIT_NOTE: "DN",
    }

    def format(self, policy: NumberingPolicy, sequence: int) -> InvoiceNumber:
        """Format an allocated sequence. Delegates to M9.2's formatter for the plain invoice case so
        no layer invents a second invoice-number format; `prefix`, `padding`, and the document-kind
        token are the policy's presentation on top."""
        token = self.KIND_TOKENS.get(policy.kind, "")
        if not policy.prefix and policy.padding == 6 and not token:
            return self._formatter.format(
                entity_code=policy.entity_code,
                jurisdiction=policy.jurisdiction,
                sequence=sequence,
            )
        parts = [policy.prefix.upper()] if policy.prefix else []
        parts.append(policy.entity_code.upper())
        if policy.jurisdiction:
            parts.append(policy.jurisdiction.upper())
        if token:
            parts.append(token)
        parts.append(str(sequence).zfill(policy.padding))
        return InvoiceNumber(
            entity_code=policy.entity_code,
            jurisdiction=policy.jurisdiction,
            sequence=sequence,
            formatted="-".join(parts),
        )

    def allocation(self, policy: NumberingPolicy, sequence: int, *, at: float) -> NumberAllocation:
        return NumberAllocation(
            series_key=self.series_key(policy, at=at),
            sequence=sequence,
            number=self.format(policy, sequence),
            allocated_at=at,
        )


class NumberSeriesAuditor:
    """Answers the compliance question — *is this series gapless?* — as a property rather than as a
    spreadsheet exercise."""

    def audit(self, series_key: str, sequences: Sequence[int]) -> NumberSeriesAudit:
        if not sequences:
            return NumberSeriesAudit(series_key=series_key)
        ordered = sorted(sequences)
        seen: set[int] = set()
        duplicated: list[int] = []
        for value in ordered:
            if value in seen:
                duplicated.append(value)
            seen.add(value)
        missing = tuple(n for n in range(ordered[0], ordered[-1] + 1) if n not in seen)
        return NumberSeriesAudit(
            series_key=series_key,
            allocated=len(ordered),
            lowest=ordered[0],
            highest=ordered[-1],
            missing=missing,
            duplicated=tuple(sorted(set(duplicated))),
        )


# ── corrections ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CorrectionRequest:
    """What a credit or debit note will contain. A *plan*, so a caller can show an operator exactly
    what would be issued before anything is."""

    kind: DocumentKind
    original_id: str
    original_number: str
    relation: DocumentRelationKind
    lines: tuple[InvoiceLine, ...] = ()
    reason: str = ""
    is_full: bool = False


class CorrectionService:
    """Builds credit notes and debit notes. **The only correction path for an issued document.**

    Amounts here are *mirrored*, never recomputed: a full credit note negates the original's lines,
    and a partial one carries amounts an operator supplied. Recomputing them would mean asking
    pricing and rating what the invoice should have said, which is precisely the reach-back this
    milestone forbids — and it would produce a credit note that disagrees with the invoice it
    credits whenever a price has since changed."""

    def credit_note(
        self,
        original: CommercialInvoice,
        snapshot: InvoiceSnapshot,
        *,
        document_id: str,
        lines: Sequence[InvoiceLine] | None = None,
        reason: str = "",
        cancels: bool = False,
    ) -> CorrectionRequest:
        self.guard_correctable(original)
        mirrored = (
            tuple(lines)
            if lines is not None
            else self._mirror(snapshot.lines, document_id=document_id)
        )
        return CorrectionRequest(
            kind=DocumentKind.CREDIT_NOTE,
            original_id=original.id,
            original_number=original.formatted_number,
            relation=(DocumentRelationKind.CANCELS if cancels else DocumentRelationKind.CREDITS),
            lines=mirrored,
            reason=reason,
            is_full=lines is None,
        )

    def debit_note(
        self,
        original: CommercialInvoice,
        *,
        document_id: str,
        lines: Sequence[InvoiceLine],
        reason: str = "",
    ) -> CorrectionRequest:
        """An under-charge is corrected by adding, never by re-issuing at a higher figure. The
        original stays exactly as the customer received it."""
        self.guard_correctable(original)
        return CorrectionRequest(
            kind=DocumentKind.DEBIT_NOTE,
            original_id=original.id,
            original_number=original.formatted_number,
            relation=DocumentRelationKind.DEBITS,
            lines=tuple(lines),
            reason=reason,
        )

    def guard_correctable(self, original: CommercialInvoice) -> None:
        """A draft is not corrected; it is re-prepared. Issuing a credit note against one would
        allocate a number for a document that corrects nothing."""
        if not original.is_issued:
            raise BillingConflict(
                f"invoice {original.id} is {original.state.value}; a correction document applies "
                "only to an issued invoice — edit the draft instead"
            )

    def back_reference(
        self, correction: CommercialInvoice, *, at: float, reason: str = ""
    ) -> DocumentRelation:
        """The edge recorded on the **original**, so a reader who starts at the invoice finds the
        credit note without having to search for one."""
        return DocumentRelation(
            kind=DocumentRelationKind.CORRECTED_BY,
            document_id=correction.id,
            document_number=correction.formatted_number,
            reason=reason,
            at=at,
        )

    @staticmethod
    def _mirror(lines: Sequence[InvoiceLine], *, document_id: str) -> tuple[InvoiceLine, ...]:
        return tuple(
            replace(
                line,
                id=derive_line_id(document_id, line.sequence, line.journal_entry_id or line.id),
                amount=-line.amount,
                tax_amount=-line.tax_amount if line.tax_amount is not None else None,
            )
            for line in lines
        )


# ── rendering ───────────────────────────────────────────────────────────────────


class DocumentModelBuilder:
    """Prepares a format-neutral model from a sealed snapshot.

    **Everything it emits is a string.** A template receives text, so there is nothing for it to add
    up — which is how "rendering never influences totals" becomes a property of the type rather than
    a rule someone must remember. `str(Money)` is M9.2's presentation of an integer minor amount;
    this module declares no formatting of its own and performs no arithmetic at all."""

    def build(self, snapshot: InvoiceSnapshot) -> DocumentModel:
        presentation = snapshot.presentation
        return DocumentModel(
            document_id=snapshot.document_id,
            kind=snapshot.kind,
            title=_document_title(snapshot.kind),
            number=snapshot.number.formatted if snapshot.number else "",
            snapshot_digest=snapshot.digest,
            template_code=presentation.template_code,
            template_version=presentation.template_version,
            locale=presentation.locale,
            sections=(
                self._header(snapshot),
                self._party("supplier", "From", snapshot.supplier),
                self._party("customer", "Bill to", snapshot.customer),
                self._lines(snapshot),
                self._totals(snapshot),
                self._tax(snapshot),
                self._payment(snapshot),
                self._references(snapshot),
            ),
        )

    def _header(self, snapshot: InvoiceSnapshot) -> DocumentSection:
        fields = [
            DocumentField("Document", _document_title(snapshot.kind), emphasis=True),
            DocumentField("Number", snapshot.number.formatted if snapshot.number else "DRAFT"),
            DocumentField("Issue date", _date(snapshot.issue_date, snapshot.presentation)),
            DocumentField("Due date", _date(snapshot.due_date, snapshot.presentation)),
            DocumentField("Currency", snapshot.currency.value),
        ]
        if snapshot.period is not None:
            fields.append(
                DocumentField(
                    "Billing period",
                    f"{_date(snapshot.period.start, snapshot.presentation)} — "
                    f"{_date(snapshot.period.end, snapshot.presentation)}",
                )
            )
        return DocumentSection(key="header", title="", fields=tuple(fields))

    def _party(self, key: str, title: str, party: InvoiceParty) -> DocumentSection:
        address = party.address
        fields = [DocumentField("Name", party.legal_name, emphasis=True)]
        if party.trade_name:
            fields.append(DocumentField("Trading as", party.trade_name))
        for label, value in (
            ("Address", ", ".join(p for p in (address.line1, address.line2) if p)),
            ("City", ", ".join(p for p in (address.city, address.region) if p)),
            ("Postal code", address.postal_code),
            ("Country", address.country),
            ("Email", party.email),
        ):
            if value:
                fields.append(DocumentField(label, value))
        for registration in party.registrations:
            fields.append(DocumentField(registration.scheme.upper(), registration.number))
        return DocumentSection(key=key, title=title, fields=tuple(fields))

    def _lines(self, snapshot: InvoiceSnapshot) -> DocumentSection:
        show_period = snapshot.presentation.show_period_column
        headers = ["#", "Description"]
        if show_period:
            headers.append("Period")
        headers.extend(["Tax rate", "Amount"])
        rows = []
        for line in snapshot.lines:
            row = [str(line.sequence), line.description]
            if show_period:
                row.append(
                    f"{_date(line.period.start, snapshot.presentation)} — "
                    f"{_date(line.period.end, snapshot.presentation)}"
                    if line.period is not None
                    else ""
                )
            row.extend(
                [
                    _rate(line.tax_rate_basis_points),
                    str(line.amount),
                ]
            )
            rows.append(tuple(row))
        return DocumentSection(
            key="lines",
            title="Details",
            table=DocumentTable(headers=tuple(headers), rows=tuple(rows)),
        )

    def _totals(self, snapshot: InvoiceSnapshot) -> DocumentSection:
        totals = snapshot.totals
        if totals is None:
            return DocumentSection(key="totals", title="Totals")
        return DocumentSection(
            key="totals",
            title="Totals",
            fields=(
                DocumentField("Subtotal", str(totals.subtotal)),
                DocumentField("Discounts", str(totals.discount_total)),
                DocumentField("Tax", str(totals.tax_total)),
                DocumentField("Credit applied", str(totals.credit_applied)),
                DocumentField("Total", str(totals.total), emphasis=True),
                DocumentField("Amount due", str(totals.amount_due), emphasis=True),
            ),
        )

    def _tax(self, snapshot: InvoiceSnapshot) -> DocumentSection:
        fields = [DocumentField("Treatment", snapshot.tax_treatment.value.replace("_", " "))]
        if snapshot.place_of_supply:
            fields.append(DocumentField("Place of supply", snapshot.place_of_supply))
        for component in snapshot.tax_components:
            fields.append(
                DocumentField(
                    f"{component.name} @ {_rate(component.rate_basis_points)}",
                    str(component.amount),
                )
            )
        return DocumentSection(key="tax", title="Tax", fields=tuple(fields))

    def _payment(self, snapshot: InvoiceSnapshot) -> DocumentSection:
        instructions = snapshot.payment_instructions
        fields = [
            DocumentField(label, value)
            for label, value in (
                ("Terms", instructions.terms_label),
                ("Method", instructions.method_label),
                ("Account name", instructions.bank_account_name),
                ("Account", instructions.bank_account_ref),
                ("IFSC / SWIFT", instructions.bank_ifsc_or_swift),
                ("Reference", instructions.remittance_reference),
                ("Pay online", instructions.payment_url),
            )
            if value
        ]
        return DocumentSection(
            key="payment", title="How to pay", fields=tuple(fields), text=instructions.notes
        )

    def _references(self, snapshot: InvoiceSnapshot) -> DocumentSection:
        fields = [
            DocumentField(label, value)
            for label, value in (
                ("Purchase order", snapshot.purchase_order_ref),
                ("Accounting period", snapshot.accounting_period_key),
                ("Tax determination", snapshot.tax_determination_id),
            )
            if value
        ]
        for relation in snapshot.relations:
            fields.append(
                DocumentField(
                    relation.kind.value.replace("_", " ").title(),
                    relation.document_number or relation.document_id,
                )
            )
        for attachment in snapshot.attachments:
            fields.append(DocumentField("Attachment", attachment.filename))
        return DocumentSection(
            key="references", title="References", fields=tuple(fields), text=snapshot.notes
        )


class HtmlRenderer:
    """Renders a prepared model to HTML. Deterministic, self-contained, and arithmetic-free.

    It never sees a `Money`, never sees a snapshot, and has no way to reach one: its only input is a
    `DocumentModel` of strings. That is the structural form of "presentation cannot influence
    financial truth"."""

    def render(self, model: DocumentModel, *, at: float = 0.0) -> RenderedDocument:
        parts = [
            "<!doctype html>",
            '<html lang="' + _escape(model.locale) + '"><head><meta charset="utf-8">',
            f"<title>{_escape(model.title)} {_escape(model.number)}</title>",
            "<style>body{font:13px/1.5 system-ui,sans-serif;margin:2rem;color:#111}"
            "table{width:100%;border-collapse:collapse;margin:.5rem 0}"
            "th,td{border-bottom:1px solid #ddd;padding:.4rem;text-align:left}"
            "h1{font-size:1.2rem}h2{font-size:.95rem;margin:1.2rem 0 .3rem}"
            ".e{font-weight:700}.d{color:#777;font-size:11px}</style></head><body>",
            f"<h1>{_escape(model.title)} {_escape(model.number)}</h1>",
        ]
        for section in model.sections:
            parts.extend(self._section(section))
        parts.append(f'<p class="d">Document digest: {_escape(model.snapshot_digest)}</p>')
        parts.append("</body></html>")
        content = "\n".join(parts)
        return RenderedDocument(
            document_id=model.document_id,
            format=RenderFormat.HTML,
            snapshot_digest=model.snapshot_digest,
            content=content,
            model=model,
            template_code=model.template_code,
            template_version=model.template_version,
            rendered_at=at,
            byte_size=len(content.encode("utf-8")),
        )

    def _section(self, section: DocumentSection) -> list[str]:
        out: list[str] = []
        if section.title:
            out.append(f"<h2>{_escape(section.title)}</h2>")
        if section.fields:
            out.append("<table>")
            for f in section.fields:
                cls = ' class="e"' if f.emphasis else ""
                out.append(f"<tr><td>{_escape(f.label)}</td><td{cls}>{_escape(f.value)}</td></tr>")
            out.append("</table>")
        if section.table is not None:
            out.append("<table><thead><tr>")
            out.extend(f"<th>{_escape(h)}</th>" for h in section.table.headers)
            out.append("</tr></thead><tbody>")
            for row in section.table.rows:
                out.append("<tr>" + "".join(f"<td>{_escape(c)}</td>" for c in row) + "</tr>")
            out.append("</tbody></table>")
        if section.text:
            out.append(f"<p>{_escape(section.text)}</p>")
        return out


class PdfRenderer:
    """Prepares the **PDF document model** — a deterministic, paginated text layout.

    Producing PDF bytes needs a rendering library, which is I/O-adjacent vendor code and does not
    belong in a pure domain (ADR-0031 IR1). What belongs here is everything that decides *what the
    PDF says*: the ordering, the pagination, and the exact lines. A byte generator downstream
    consumes this and adds nothing."""

    def __init__(self, lines_per_page: int = 40) -> None:
        self._lines_per_page = lines_per_page

    def render(self, model: DocumentModel, *, at: float = 0.0) -> RenderedDocument:
        rows: list[str] = [f"{model.title} {model.number}".strip(), ""]
        for section in model.sections:
            if section.title:
                rows.extend([section.title.upper(), ""])
            for f in section.fields:
                rows.append(f"{f.label}: {f.value}")
            if section.table is not None:
                rows.append(" | ".join(section.table.headers))
                rows.extend(" | ".join(row) for row in section.table.rows)
            if section.text:
                rows.append(section.text)
            rows.append("")
        pages = [
            rows[i : i + self._lines_per_page] for i in range(0, len(rows), self._lines_per_page)
        ] or [[]]
        content = "\n\f\n".join("\n".join(page) for page in pages)
        return RenderedDocument(
            document_id=model.document_id,
            format=RenderFormat.PDF,
            snapshot_digest=model.snapshot_digest,
            content=content,
            model=model,
            template_code=model.template_code,
            template_version=model.template_version,
            rendered_at=at,
            byte_size=len(content.encode("utf-8")),
        )


def _document_title(kind: DocumentKind) -> str:
    return {
        DocumentKind.INVOICE: "Tax Invoice",
        DocumentKind.CREDIT_NOTE: "Credit Note",
        DocumentKind.DEBIT_NOTE: "Debit Note",
    }[kind]


def _rate(basis_points: int) -> str:
    """Basis points to a printed percentage, without touching a money value.

    Integer-only, and it formats rather than computes: a rate the tax domain determined is being
    written down, not applied to anything."""
    whole, fraction = divmod(basis_points, 100)
    return f"{whole}%" if fraction == 0 else f"{whole}.{fraction:02d}%"


def _date(value: float | None, presentation: DocumentPresentation) -> str:
    """A stable, locale-independent date string.

    Deliberately not `datetime`: the Foundation imports the standard library's pure parts only
    (ADR-0031 IR1) and a timezone-sensitive format would make the same document render differently
    in two regions — which for a legal document is a defect, not a nicety."""
    if not value:
        return ""
    days = int(value) // 86_400
    year, day_of_year = _civil(days)
    return f"{year}-{day_of_year:03d}" if presentation.date_format else str(int(value))


def _civil(days: int) -> tuple[int, int]:
    """Days since the epoch to (year, day-of-year). Integer arithmetic on a calendar, not on
    money — and it keeps this module free of `datetime`."""
    year = 1970
    while True:
        length = 366 if (year % 4 == 0 and year % 100 != 0) or year % 400 == 0 else 365
        if days < length:
            return year, days + 1
        days -= length
        year += 1


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


# ── reconciliation, inspection, validation ──────────────────────────────────────


class InvoiceReconciliationService:
    """Does the document present what the ledger recorded?

    Compares line by line against the entries the period contains. A document is evidence, and
    evidence that disagrees with the record it evidences has usually already been sent."""

    def reconcile(
        self,
        *,
        org_id: str,
        account_id: str,
        accounting_period_key: str,
        entries: Sequence[tuple[str, int]],
        snapshot: InvoiceSnapshot | None,
        at: float = 0.0,
    ) -> InvoiceReconciliation:
        found: list[InvoiceDiscrepancy] = []
        lines = snapshot.lines if snapshot is not None else ()
        presented = {line.journal_entry_id: line for line in lines if line.journal_entry_id}
        for entry_id, signed_minor in entries:
            line = presented.get(entry_id)
            if line is None:
                found.append(
                    InvoiceDiscrepancy(
                        kind=InvoiceDiscrepancyKind.UNBILLED_ENTRY,
                        journal_entry_id=entry_id,
                        expected_minor=signed_minor,
                        detail="the ledger recorded this; no document line presents it",
                    )
                )
            elif line.amount.minor != signed_minor:
                found.append(
                    InvoiceDiscrepancy(
                        kind=InvoiceDiscrepancyKind.AMOUNT_MISMATCH,
                        journal_entry_id=entry_id,
                        line_id=line.id,
                        expected_minor=signed_minor,
                        actual_minor=line.amount.minor,
                    )
                )
        known = {entry_id for entry_id, _ in entries}
        for line in lines:
            if line.journal_entry_id and line.journal_entry_id not in known:
                found.append(
                    InvoiceDiscrepancy(
                        kind=InvoiceDiscrepancyKind.ORPHAN_LINE,
                        line_id=line.id,
                        journal_entry_id=line.journal_entry_id,
                        actual_minor=line.amount.minor,
                        detail="the document presents this; no ledger entry produced it",
                    )
                )
        if snapshot is not None and snapshot.totals is not None:
            found.extend(self._totals_check(snapshot))
        return InvoiceReconciliation(
            org_id=org_id,
            account_id=account_id,
            accounting_period_key=accounting_period_key,
            entries_examined=len(entries),
            lines_examined=len(lines),
            discrepancies=tuple(found),
            at=at,
        )

    @staticmethod
    def _totals_check(snapshot: InvoiceSnapshot) -> list[InvoiceDiscrepancy]:
        """The header must agree with the body. Checked by re-running the *same* declared totalling
        service over the same lines, so a disagreement means the stored header was written by
        something other than that service."""
        recomputed = DocumentAssembler().totals(
            snapshot.lines,
            currency=snapshot.currency,
            credit_applied=snapshot.totals.credit_applied if snapshot.totals else None,
        )
        if snapshot.totals is not None and recomputed.total == snapshot.totals.total:
            return []
        return [
            InvoiceDiscrepancy(
                kind=InvoiceDiscrepancyKind.TOTALS_MISMATCH,
                document_id=snapshot.document_id,
                expected_minor=recomputed.total.minor,
                actual_minor=snapshot.totals.total.minor if snapshot.totals else 0,
                detail="the stored header disagrees with its own lines",
            )
        ]


class InvoiceInspector:
    """A structural read of one document: numbered, sealed, traced, self-consistent."""

    def __init__(self, integrity: SnapshotIntegrityService | None = None) -> None:
        self._integrity = integrity or SnapshotIntegrityService()

    def inspect(
        self, document: CommercialInvoice, snapshot: InvoiceSnapshot | None
    ) -> InvoiceInspection:
        notes: list[str] = []
        traced = True
        totals_agree = True
        if snapshot is not None:
            monetary = [
                line
                for line in snapshot.lines
                if line.kind is not ChargeLineKind.TAX and not line.amount.is_zero
            ]
            traced = all(line.journal_entry_id for line in monetary)
            if not traced:
                notes.append("one or more monetary lines cannot be traced to a ledger entry")
            totals_agree = not InvoiceReconciliationService._totals_check(snapshot)
            if not totals_agree:
                notes.append("the header disagrees with its own lines")
        sealed = document.is_issued and bool(document.issued_snapshot_digest)
        if (
            document.is_issued
            and snapshot is not None
            and not self._integrity.is_pinned(document, snapshot)
        ):
            sealed = False
            notes.append("the stored revision does not match the digest pinned at issue")
        return InvoiceInspection(
            document_id=document.id,
            kind=document.kind,
            state=document.state,
            number=document.formatted_number,
            revision=document.revision,
            snapshot_digest=document.issued_snapshot_digest,
            is_sealed=sealed,
            is_traced=traced,
            totals_agree=totals_agree,
            lines=len(snapshot.lines) if snapshot else 0,
            relations=document.relations,
            notes=tuple(notes),
        )


class InvoiceValidator:
    """Structural validation. **Never raises** (§A2.6) — it reports, and a factory decides."""

    def validate_document(self, document: CommercialInvoice) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not document.account_id:
            found.append(
                _d(
                    "account_id",
                    BillingDiagnosticCode.MISSING_REQUIRED_FIELD,
                    "a document must name the account it bills",
                )
            )
        if document.is_issued and not document.number:
            found.append(
                _d(
                    "number",
                    BillingDiagnosticCode.INVOICE_NOT_NUMBERED,
                    "an issued document must carry an allocated number",
                    remediation="issue through the issuing use case, which allocates one",
                )
            )
        if document.is_issued and not document.issued_snapshot_digest:
            found.append(
                _d(
                    "issued_snapshot_digest",
                    BillingDiagnosticCode.UNSEALED_INVOICE,
                    "an issued document must pin the digest of the snapshot it sealed",
                )
            )
        return BillingDiagnostics.of(found)

    def validate_snapshot(self, snapshot: InvoiceSnapshot) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not snapshot.lines:
            found.append(
                _d(
                    "lines",
                    BillingDiagnosticCode.EMPTY_INVOICE,
                    "a document with no lines documents nothing",
                    Severity.WARNING,
                )
            )
        for line in snapshot.lines:
            if line.amount.currency is not snapshot.currency:
                found.append(
                    _d(
                        f"lines[{line.sequence}].amount",
                        BillingDiagnosticCode.INVOICE_CURRENCY_MISMATCH,
                        f"line is in {line.amount.currency}, the document is in "
                        f"{snapshot.currency}",
                    )
                )
        if not snapshot.supplier.legal_name:
            found.append(
                _d(
                    "supplier.legal_name",
                    BillingDiagnosticCode.MISSING_SUPPLIER_IDENTITY,
                    "a legal document must name its supplier",
                )
            )
        if not snapshot.customer.legal_name:
            found.append(
                _d(
                    "customer.legal_name",
                    BillingDiagnosticCode.MISSING_CUSTOMER_IDENTITY,
                    "a legal document must name its customer",
                )
            )
        if snapshot.tax_components and not snapshot.tax_determination_id:
            found.append(
                _d(
                    "tax_determination_id",
                    BillingDiagnosticCode.MISSING_TAX_EVIDENCE,
                    "tax is presented but no determination is referenced; the invoice would not "
                    "be explainable after rates change",
                )
            )
        return BillingDiagnostics.of(found)

    def validate_issue(
        self, document: CommercialInvoice, snapshot: InvoiceSnapshot
    ) -> BillingDiagnostics:
        """Issuing is a one-way door, so everything checkable is checked before it opens."""
        found = list(self.validate_snapshot(snapshot).findings)
        if not document.entity_code:
            found.append(
                _d(
                    "entity_code",
                    BillingDiagnosticCode.MISSING_LEGAL_ENTITY,
                    "a number is allocated per legal entity; the document names none",
                )
            )
        if snapshot.document_id != document.id:
            found.append(
                _d(
                    "snapshot.document_id",
                    BillingDiagnosticCode.SNAPSHOT_DOCUMENT_MISMATCH,
                    "the snapshot being sealed belongs to a different document",
                )
            )
        return BillingDiagnostics.of(found)

    def validate_series(self, audit: NumberSeriesAudit) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if audit.missing:
            found.append(
                _d(
                    "series",
                    BillingDiagnosticCode.INVOICE_NUMBER_GAP,
                    f"series {audit.series_key} is missing {list(audit.missing)[:5]}",
                    remediation="a gap is a compliance failure where the jurisdiction requires "
                    "gapless numbering; investigate rather than backfill",
                )
            )
        if audit.duplicated:
            found.append(
                _d(
                    "series",
                    BillingDiagnosticCode.DUPLICATE_INVOICE_NUMBER,
                    f"series {audit.series_key} allocated {list(audit.duplicated)[:5]} twice",
                )
            )
        return BillingDiagnostics.of(found)


class InvoiceSearchService:
    """Deterministic filtering over a bounded set. No index, no scoring."""

    def search(
        self, documents: Sequence[CommercialInvoice], query: InvoiceSearchQuery
    ) -> InvoiceSearchResult:
        text = query.text.lower()
        found = [d for d in documents if self._matches(d, query, text)]
        found.sort(key=lambda d: (-(d.issued_at or 0.0), d.id))
        return InvoiceSearchResult(
            documents=tuple(found[query.offset : query.offset + query.limit]),
            total=len(found),
        )

    @staticmethod
    def _matches(document: CommercialInvoice, query: InvoiceSearchQuery, text: str) -> bool:
        if text and text not in (document.formatted_number + " " + document.id).lower():
            return False
        if query.account_id and document.account_id != query.account_id:
            return False
        if query.kinds and document.kind not in query.kinds:
            return False
        if query.states and document.state not in query.states:
            return False
        if (
            query.accounting_period_key
            and document.accounting_period_key != query.accounting_period_key
        ):
            return False
        if query.number and query.number.lower() not in document.formatted_number.lower():
            return False
        if query.entity_code and document.entity_code != query.entity_code:
            return False
        if query.jurisdiction and document.jurisdiction != query.jurisdiction:
            return False
        if query.issued_from is not None and (document.issued_at or 0.0) < query.issued_from:
            return False
        if query.issued_until is not None and (document.issued_at or 0.0) > query.issued_until:
            return False
        if query.min_total_minor is not None and document.total_minor < query.min_total_minor:
            return False
        if query.max_total_minor is not None and document.total_minor > query.max_total_minor:
            return False
        if query.overdue_only and document.state is not InvoiceState.OVERDUE:
            return False
        if query.corrections_only and not document.is_correction:
            return False
        return True


def _d(
    path: str,
    code: BillingDiagnosticCode,
    message: str,
    severity: Severity = Severity.ERROR,
    *,
    remediation: str = "",
) -> Diagnostic:
    return Diagnostic(
        path=path, code=code, severity=severity, message=message, remediation=remediation
    )


__all__ = [
    "ENTRY_TYPE_TO_LINE_KIND",
    "REDUCING_LINE_KINDS",
    "CorrectionRequest",
    "CorrectionService",
    "DocumentAssembler",
    "DocumentLifecycleService",
    "DocumentModelBuilder",
    "HtmlRenderer",
    "InvoiceInspector",
    "InvoiceReconciliationService",
    "InvoiceSearchService",
    "InvoiceValidator",
    "LineBuilder",
    "LineSource",
    "NumberSeriesAuditor",
    "NumberingService",
    "PdfRenderer",
    "SnapshotIntegrityService",
]

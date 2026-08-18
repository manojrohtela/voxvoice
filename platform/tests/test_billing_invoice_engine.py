"""M9.14 — Invoice Engine.

The Invoice is where financial facts become customer documents, and it is the first Billing domain
whose output leaves the building: an issued invoice reaches a customer, an auditor, and a tax
authority, and none of them can be asked to accept a corrected copy.

So the suite is organised so that every legal invariant is demonstrated **structurally** — by the
absence of a method, the absence of an FSM edge, a primary key that makes a duplicate impossible, or
a digest comparison — rather than by a narrative that a passing integration test is assumed to
imply. §13 is a numbered list of them.
"""

from __future__ import annotations

import ast
import inspect as _inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from billing.application import (
    ALL_INVOICE_COMMANDS,
    ALL_INVOICE_QUERIES,
    ActivateBillingAccount,
    ActivateCommercialMeter,
    ActivateSubscription,
    AllowAllAuthorizer,
    AuditInvoiceNumberSeries,
    CloseAccountingPeriod,
    CreateBillingAccount,
    CreatePrice,
    CreatePriceBook,
    CreateProduct,
    CreateSubscription,
    DefineCommercialMeter,
    DiagnoseCommercialInvoice,
    ExportInvoiceDocument,
    FinalizeRatingRun,
    GetCommercialInvoice,
    GetInvoiceDocument,
    GetInvoiceRevisions,
    GetRenderedInvoice,
    IngestMetricSignal,
    InspectCommercialInvoice,
    InvoiceTimeline,
    IssueCommercialInvoice,
    IssueCreditNote,
    IssueDebitNote,
    ListCommercialInvoices,
    ListInvoiceRelations,
    MarkInvoiceOverdue,
    MarkInvoicePaid,
    OpenAccountingPeriod,
    OpenUsagePeriod,
    PostRatingRun,
    PrepareInvoice,
    PreviewInvoice,
    PublishCatalog,
    PublishPricing,
    PublishPricingBook,
    PublishProduct,
    RateSubscriptionPeriod,
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
from billing.customer.app import create_billing_app
from billing.customer.config import BillingApiSettings
from billing.customer.container import BillingContainer
from billing.foundation import (
    EDITABLE_DOCUMENT_STATES,
    IMMUTABLE_INVOICE_STATES,
    AgreementItem,
    AttachmentPurpose,
    BillingAddress,
    BillingConflict,
    BillingDiagnosticCode,
    BillingInterval,
    BillingNotFound,
    BillingPeriod,
    ChargeLineKind,
    CommercialInvoice,
    CorrectionService,
    Currency,
    DeliveryChannel,
    DeliveryState,
    DocumentAssembler,
    DocumentKind,
    DocumentLifecycleService,
    DocumentModelBuilder,
    DocumentPresentation,
    DocumentRelationKind,
    FixedClock,
    HtmlRenderer,
    InvalidBillingTransition,
    InvoiceInspector,
    InvoiceLine,
    InvoiceParty,
    InvoiceReconciliationService,
    InvoiceSearchQuery,
    InvoiceSearchService,
    InvoiceState,
    InvoiceTrigger,
    InvoiceValidator,
    ItemKind,
    LedgerEntryType,
    LineBuilder,
    LineSource,
    MeterSource,
    MetricSignal,
    Money,
    Normalization,
    NumberingPolicy,
    NumberingReset,
    NumberingService,
    NumberSeriesAuditor,
    PartyRegistration,
    PaymentInstructions,
    PdfRenderer,
    PriceShape,
    PriceTier,
    RenderFormat,
    Scope,
    SequentialIdGenerator,
    SnapshotIntegrityService,
    TaxComponent,
    derive_document_id,
    period_key_for,
    series_key_for,
)
from billing.persistence import build_persistent_billing_application

PLATFORM = Path(__file__).resolve().parents[1]
BILLING = PLATFORM / "billing"
STUDIO = BILLING / "customer" / "studio" / "invoices.html"

INR = Currency.INR
USD = Currency.USD
ORG, ACTOR, ACC = "org_1", "usr_1", "bac_1"
P0, P1 = 1_000.0, 2_000.0
PERIOD = BillingPeriod(P0, P1)
KEY = period_key_for(PERIOD)

lifecycle = DocumentLifecycleService()
lines = LineBuilder()
assembler = DocumentAssembler()
numbering = NumberingService()
integrity = SnapshotIntegrityService()
corrections = CorrectionService()


# ── helpers ─────────────────────────────────────────────────────────────────────


def _class_code(module: str, name: str) -> str:
    """One class's source with **docstrings stripped**.

    The M9.6 lesson, applied at class granularity: these scans are about what the code *does*, and
    prose explaining that a correction never recomputes must not itself read as recomputation."""
    tree = ast.parse((BILLING / "foundation" / module).read_text())
    found = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == name
    )
    for node in ast.walk(found):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(found)


def a_document(state=InvoiceState.DRAFT, kind=DocumentKind.INVOICE, **kw) -> CommercialInvoice:
    base = dict(
        id="inv_1",
        org_id=ORG,
        account_id=ACC,
        kind=kind,
        currency=INR,
        state=state,
        accounting_period_key=KEY,
        period=PERIOD,
        entity_code="VOX",
        jurisdiction="KA",
    )
    base.update(kw)
    return CommercialInvoice(**base)  # type: ignore[arg-type]


def a_source(minor: int = 500_00, **kw) -> LineSource:
    base = dict(
        journal_entry_id="jrn_1",
        entry_type=LedgerEntryType.CHARGE,
        signed_minor=minor,
        currency=INR,
        description="Platform fee",
        charge_id="chg_1",
        sequence=1,
    )
    base.update(kw)
    return LineSource(**base)  # type: ignore[arg-type]


def a_snapshot(document=None, sources=None, revision=1, **kw):
    document = document or a_document()
    built = lines.lines(sources or [a_source()], document_id=document.id)
    base = dict(
        document=document,
        revision=revision,
        supplier=InvoiceParty(legal_name="VoxVoice Technologies Private Limited"),
        customer=InvoiceParty(legal_name="Acme Technologies Private Limited"),
        lines=built,
    )
    base.update(kw)
    return assembler.snapshot(**base)  # type: ignore[arg-type]


# ── 1. identity + idempotent preparation ────────────────────────────────────────


def test_a_document_id_is_derived_from_what_it_documents():
    """The idempotency story in one assertion: same basis, same id, no generator consulted."""
    first = derive_document_id(ORG, ACC, "invoice", KEY)
    assert first == derive_document_id(ORG, ACC, "invoice", KEY)
    assert first.startswith("inv_")


def test_a_correction_derives_a_different_id_from_what_it_corrects():
    """Structural: a credit note cannot overwrite the invoice it credits, because the id it would
    need is never the id it computes."""
    invoice = derive_document_id(ORG, ACC, "invoice", KEY)
    note = derive_document_id(ORG, ACC, "credit_note", invoice)
    debit = derive_document_id(ORG, ACC, "debit_note", invoice)
    assert len({invoice, note, debit}) == 3


def test_a_different_period_is_a_different_document():
    assert derive_document_id(ORG, ACC, "invoice", KEY) != derive_document_id(
        ORG, ACC, "invoice", "9999-9999"
    )


def test_a_different_account_is_a_different_document():
    assert derive_document_id(ORG, ACC, "invoice", KEY) != derive_document_id(
        ORG, "bac_2", "invoice", KEY
    )


# ── 2. the document lifecycle ───────────────────────────────────────────────────


def test_an_issued_document_never_becomes_editable_again():
    """The absence that matters most: there is no edge back to `draft` from any issued state."""
    for state in IMMUTABLE_INVOICE_STATES:
        triggers = lifecycle.allowed(state)
        for trigger in triggers:
            assert lifecycle.next_state(state, trigger) is not InvoiceState.DRAFT


def test_no_issued_state_can_be_voided():
    """A number has been allocated; voiding would put a hole in a gapless series."""
    for state in IMMUTABLE_INVOICE_STATES:
        assert not lifecycle.can(state, InvoiceTrigger.VOID)


def test_a_draft_can_be_voided_because_it_carries_no_number():
    assert lifecycle.can(InvoiceState.DRAFT, InvoiceTrigger.VOID)
    assert lifecycle.can(InvoiceState.PENDING_APPROVAL, InvoiceTrigger.VOID)


def test_paid_credited_and_voided_are_terminal():
    for state in (InvoiceState.PAID, InvoiceState.CREDITED, InvoiceState.VOIDED):
        assert lifecycle.allowed(state) == ()


def test_the_lifecycle_has_no_deletion_state():
    states = {s.value for s in InvoiceState}
    assert not {s for s in states if "delete" in s or "remove" in s or "purge" in s}


def test_an_illegal_transition_names_the_legal_ones():
    with pytest.raises(InvalidBillingTransition) as excinfo:
        lifecycle.next_state(InvoiceState.PAID, InvoiceTrigger.ISSUE)
    assert "legal triggers: []" in str(excinfo.value)


def test_issuing_requires_both_a_number_and_a_sealed_snapshot():
    with pytest.raises(BillingConflict):
        lifecycle.apply(a_document(), InvoiceTrigger.ISSUE, at=1.0)


def test_guard_editable_refuses_an_issued_document_with_a_usable_message():
    with pytest.raises(BillingConflict) as excinfo:
        lifecycle.guard_editable(a_document(InvoiceState.ISSUED))
    assert "credit note" in str(excinfo.value)


def test_approve_and_issue_reach_the_same_state():
    """Two routes, one fact. A state meaning "approved but not yet issued" would be a window in
    which an approved document could still be edited."""
    assert lifecycle.next_state(InvoiceState.DRAFT, InvoiceTrigger.ISSUE) is InvoiceState.ISSUED
    assert (
        lifecycle.next_state(InvoiceState.PENDING_APPROVAL, InvoiceTrigger.APPROVE)
        is InvoiceState.ISSUED
    )


def test_only_drafts_are_editable():
    assert EDITABLE_DOCUMENT_STATES == frozenset(
        {InvoiceState.DRAFT, InvoiceState.PENDING_APPROVAL}
    )
    assert not (EDITABLE_DOCUMENT_STATES & IMMUTABLE_INVOICE_STATES)


# ── 3. line building copies, never computes ─────────────────────────────────────


def test_a_line_amount_is_the_ledger_entrys_own_signed_figure():
    built = lines.lines([a_source(123_45)], document_id="inv_1")
    assert built[0].amount == Money(123_45, INR)
    assert built[0].journal_entry_id == "jrn_1"


def test_a_credit_entry_presents_as_a_negative_line():
    built = lines.lines(
        [a_source(-50_00, entry_type=LedgerEntryType.CREDIT_APPLIED)], document_id="inv_1"
    )
    assert built[0].kind is ChargeLineKind.CREDIT
    assert built[0].is_negative


def test_lines_are_ordered_deterministically():
    sources = [
        a_source(1, sequence=3, journal_entry_id="c", charge_id="c"),
        a_source(2, sequence=1, journal_entry_id="a", charge_id="a"),
        a_source(3, sequence=2, journal_entry_id="b", charge_id="b"),
    ]
    assert [line.journal_entry_id for line in lines.lines(sources, document_id="x")] == [
        "a",
        "b",
        "c",
    ]
    assert [line.sequence for line in lines.lines(sources, document_id="x")] == [1, 2, 3]


def test_tax_lines_copy_the_determinations_amounts():
    built = lines.tax_lines(
        [TaxComponent(name="IGST", rate_basis_points=1800, amount=Money(9_000, INR))],
        document_id="inv_1",
        offset=1,
    )
    assert built[0].amount == Money(9_000, INR)
    assert built[0].tax_rate_basis_points == 1800
    assert built[0].kind is ChargeLineKind.TAX


def test_every_ledger_entry_type_has_a_declared_presentation():
    from billing.foundation import ENTRY_TYPE_TO_LINE_KIND

    assert set(ENTRY_TYPE_TO_LINE_KIND) == set(LedgerEntryType)


# ── 4. totals come from one declared service ────────────────────────────────────


def test_totals_delegate_to_the_m9_2_service():
    """One place applies the order discount → credit → tax. Two implementations is the drift
    §A2.5 prevents, and on a document it produces a header disagreeing with its own body."""
    source = _class_code("invoice_rules.py", "DocumentAssembler")
    assert "self._totals.totals(" in source
    assert "def totals" in source
    assert "+" not in source  # no arithmetic of its own


def test_a_tax_line_lands_in_the_tax_total_not_the_subtotal():
    snapshot = a_snapshot(
        lines=lines.lines([a_source(500_00)], document_id="inv_1")
        + lines.tax_lines(
            [TaxComponent(name="IGST", rate_basis_points=1800, amount=Money(90_00, INR))],
            document_id="inv_1",
            offset=1,
        )
    )
    assert snapshot.totals.subtotal == Money(500_00, INR)
    assert snapshot.totals.tax_total == Money(90_00, INR)
    assert snapshot.totals.total == Money(590_00, INR)


def test_a_credit_reduces_the_amount_due_not_the_total():
    snapshot = a_snapshot(credit_applied=Money(100_00, INR))
    assert snapshot.totals.total == Money(500_00, INR)
    assert snapshot.totals.amount_due == Money(400_00, INR)


def test_a_document_covered_entirely_by_credit_is_still_issued():
    """ADR-0023 IN7: suppressing it would leave a hole in a sequence and a period an auditor
    cannot account for."""
    snapshot = a_snapshot(credit_applied=Money(500_00, INR))
    assert snapshot.totals.amount_due.is_zero
    assert not snapshot.is_zero_amount  # the total is not zero; only what is left to pay


# ── 5. the snapshot digest ──────────────────────────────────────────────────────


def test_two_identical_preparations_produce_one_digest():
    assert a_snapshot().digest == a_snapshot().digest


def test_the_digest_excludes_when_and_who_prepared_it():
    """Deliberate: those legitimately differ between a preparation and a re-preparation of
    identical content, and a digest that moved with them could never prove anything."""
    assert a_snapshot(prepared_at=1.0, prepared_by="a").digest == (
        a_snapshot(prepared_at=999.0, prepared_by="b").digest
    )


def test_the_digest_moves_when_any_figure_moves():
    assert a_snapshot().digest != a_snapshot(sources=[a_source(500_01)]).digest


def test_the_digest_moves_when_the_template_changes():
    """A document issued under one template is a different document from the same figures issued
    under another — and pinning it is what stops a redesign altering history."""
    other = a_snapshot(presentation=DocumentPresentation(template_code="minimal"))
    assert a_snapshot().digest != other.digest


def test_the_digest_moves_when_a_party_changes():
    assert (
        a_snapshot().digest
        != a_snapshot(customer=InvoiceParty(legal_name="Someone Else Ltd")).digest
    )


def test_the_digest_covers_the_number():
    numbered = a_snapshot(number=numbering.format(NumberingPolicy(entity_code="VOX"), 1))
    assert numbered.digest != a_snapshot().digest


# ── 6. immutability by content addressing ───────────────────────────────────────


def test_an_issued_document_pins_a_digest():
    snapshot = a_snapshot()
    issued = lifecycle.apply(
        a_document(),
        InvoiceTrigger.ISSUE,
        at=1.0,
        number=numbering.format(NumberingPolicy(entity_code="VOX"), 1),
        snapshot=snapshot,
    )
    assert issued.issued_snapshot_digest == snapshot.digest
    assert issued.is_immutable


def test_the_issued_revision_is_selected_by_digest_not_by_recency():
    """The structural form of "reproduces byte-identically forever": a later revision cannot become
    the served document by being newer."""
    sealed = a_snapshot(revision=1)
    later = a_snapshot(revision=2, sources=[a_source(999_99)])
    document = a_document(
        InvoiceState.ISSUED, issued_revision=1, issued_snapshot_digest=sealed.digest
    )
    assert integrity.select_issued(document, (sealed, later)) == sealed


def test_a_pin_that_no_longer_matches_is_an_incident_not_a_warning():
    document = a_document(InvoiceState.ISSUED, issued_snapshot_digest="deadbeef")
    assert not integrity.is_pinned(document, a_snapshot())
    with pytest.raises(BillingConflict) as excinfo:
        integrity.guard_pinned(document, a_snapshot())
    assert "changed underneath its pin" in str(excinfo.value)


def test_the_document_header_carries_no_lines_and_no_totals():
    """A header that carried its own totals is a second copy of a fact the snapshot already
    holds, and the two eventually disagree in a way nobody notices until a customer does."""
    fields = set(CommercialInvoice.__dataclass_fields__)
    assert "lines" not in fields
    assert "totals" not in fields
    assert "issued_snapshot_digest" in fields


# ── 7. numbering ────────────────────────────────────────────────────────────────


def test_a_series_is_per_entity_per_jurisdiction_per_kind():
    """ "Gapless" is a property *of a series*; merging two jurisdictions into one counter makes each
    look gapped to the authority reading it."""
    keys = {
        series_key_for(entity_code="VOX", jurisdiction=j, kind=k, reset=NumberingReset.NEVER)
        for j in ("KA", "MH")
        for k in ("invoice", "credit_note")
    }
    assert len(keys) == 4


def test_an_annual_reset_buckets_by_the_issue_instant():
    policy = NumberingPolicy(entity_code="VOX", reset=NumberingReset.ANNUAL)
    assert numbering.series_key(policy, at=0.0) != numbering.series_key(policy, at=40_000_000.0)


def test_a_never_reset_series_ignores_the_instant():
    policy = NumberingPolicy(entity_code="VOX")
    assert numbering.series_key(policy, at=0.0) == numbering.series_key(policy, at=1e9)


def test_an_invoice_number_is_exactly_the_m9_2_format():
    assert (
        numbering.format(NumberingPolicy(entity_code="VOX", jurisdiction="KA"), 7).formatted
        == "VOX-KA-000007"
    )


def test_a_correction_number_carries_a_kind_token():
    """Each kind counts in its own series, so an invoice and a credit note both legitimately reach
    sequence 1. Without a token they would print the same identifier while stating opposite things —
    found by the end-to-end smoke, which issued all three kinds and got one number back."""
    base = dict(entity_code="VOX", jurisdiction="KA")
    invoice = numbering.format(NumberingPolicy(**base, kind=DocumentKind.INVOICE), 1)
    credit = numbering.format(NumberingPolicy(**base, kind=DocumentKind.CREDIT_NOTE), 1)
    debit = numbering.format(NumberingPolicy(**base, kind=DocumentKind.DEBIT_NOTE), 1)
    assert len({invoice.formatted, credit.formatted, debit.formatted}) == 3
    assert credit.formatted == "VOX-KA-CN-000001"
    assert debit.formatted == "VOX-KA-DN-000001"


def test_a_sequence_below_one_is_refused():
    from billing.foundation import InvoiceNumber

    with pytest.raises(ValueError, match="sequence"):
        InvoiceNumber("VOX", "KA", 0, "VOX-KA-000000")


def test_a_gapless_series_is_a_property_not_an_opinion():
    auditor = NumberSeriesAuditor()
    assert auditor.audit("s", [1, 2, 3]).is_gapless
    assert not auditor.audit("s", [1, 2, 4]).is_gapless
    assert auditor.audit("s", [1, 2, 4]).missing == (3,)


def test_a_duplicated_sequence_is_reported_separately_from_a_gap():
    audit = NumberSeriesAuditor().audit("s", [1, 2, 2, 4])
    assert audit.duplicated == (2,)
    assert audit.missing == (3,)


def test_an_untouched_series_is_gapless():
    assert NumberSeriesAuditor().audit("s", []).is_gapless


def test_the_validator_flags_a_gap_with_the_right_remediation():
    findings = InvoiceValidator().validate_series(NumberSeriesAuditor().audit("s", [1, 3]))
    assert findings.has_errors
    codes = {f.code for f in findings.findings}
    assert BillingDiagnosticCode.INVOICE_NUMBER_GAP in codes
    assert "investigate rather than backfill" in findings.findings[0].remediation


# ── 8. corrections ──────────────────────────────────────────────────────────────


def test_a_full_credit_note_mirrors_the_original():
    snapshot = a_snapshot()
    request = corrections.credit_note(
        a_document(InvoiceState.ISSUED), snapshot, document_id="inv_2", cancels=True
    )
    assert request.kind is DocumentKind.CREDIT_NOTE
    assert request.relation is DocumentRelationKind.CANCELS
    assert request.lines[0].amount == -snapshot.lines[0].amount
    assert request.is_full


def test_a_partial_credit_note_carries_what_an_operator_supplied():
    supplied = (
        InvoiceLine(
            id="",
            sequence=1,
            kind=ChargeLineKind.ONE_OFF,
            description="goodwill",
            amount=Money(-10_00, INR),
        ),
    )
    request = corrections.credit_note(
        a_document(InvoiceState.ISSUED), a_snapshot(), document_id="inv_2", lines=supplied
    )
    assert request.lines == supplied
    assert not request.is_full
    assert request.relation is DocumentRelationKind.CREDITS


def test_a_correction_never_asks_pricing_what_the_invoice_should_have_said():
    """Recomputing would produce a credit note that disagrees with the invoice it credits whenever
    a price has since changed."""
    source = _class_code("invoice_rules.py", "CorrectionService")
    for forbidden in ("PriceResolver", "RatingEngine", "resolve", "recompute", "Rating"):
        assert forbidden not in source, forbidden


def test_a_draft_cannot_be_corrected():
    with pytest.raises(BillingConflict) as excinfo:
        corrections.credit_note(a_document(), a_snapshot(), document_id="inv_2")
    assert "edit the draft instead" in str(excinfo.value)


def test_a_debit_note_adds_rather_than_re_issuing():
    request = corrections.debit_note(
        a_document(InvoiceState.ISSUED),
        document_id="inv_3",
        lines=(
            InvoiceLine(
                id="",
                sequence=1,
                kind=ChargeLineKind.ONE_OFF,
                description="missed overage",
                amount=Money(1_000, INR),
            ),
        ),
    )
    assert request.kind is DocumentKind.DEBIT_NOTE
    assert request.relation is DocumentRelationKind.DEBITS


def test_the_original_records_a_back_reference():
    correction = a_document(InvoiceState.ISSUED, id="inv_2", kind=DocumentKind.CREDIT_NOTE)
    edge = corrections.back_reference(correction, at=5.0, reason="outage")
    assert edge.kind is DocumentRelationKind.CORRECTED_BY
    assert edge.document_id == "inv_2"


# ── 9. rendering computes nothing ───────────────────────────────────────────────


RENDER_CLASSES = (DocumentModelBuilder, HtmlRenderer, PdfRenderer)


def test_no_renderer_performs_arithmetic_on_money():
    """The structural form of "presentation cannot influence financial truth". A model of strings
    gives a template nothing to add up, and that is cheaper than trusting it not to."""
    for cls in RENDER_CLASSES:
        source = _class_code("invoice_rules.py", cls.__name__)
        for forbidden in ("Money(", ".times(", ".scaled(", ".allocate(", "sum(", "minor"):
            assert forbidden not in source, (cls.__name__, forbidden)


def test_a_renderer_never_sees_a_snapshot():
    for cls in (HtmlRenderer, PdfRenderer):
        assert "InvoiceSnapshot" not in _class_code("invoice_rules.py", cls.__name__), cls.__name__


def test_a_document_model_is_entirely_strings():
    model = DocumentModelBuilder().build(a_snapshot())
    for section in model.sections:
        for entry in section.fields:
            assert isinstance(entry.value, str)
        if section.table is not None:
            for row in section.table.rows:
                assert all(isinstance(cell, str) for cell in row)


def test_rendering_is_deterministic():
    model = DocumentModelBuilder().build(a_snapshot())
    assert HtmlRenderer().render(model).content == HtmlRenderer().render(model).content
    assert PdfRenderer().render(model).content == PdfRenderer().render(model).content


def test_a_rendering_carries_the_digest_it_came_from():
    snapshot = a_snapshot()
    rendered = HtmlRenderer().render(DocumentModelBuilder().build(snapshot))
    assert rendered.is_faithful(snapshot)
    assert not rendered.is_faithful(a_snapshot(sources=[a_source(1)]))


def test_html_escapes_what_a_party_supplied():
    snapshot = a_snapshot(customer=InvoiceParty(legal_name="<script>alert(1)</script>"))
    content = HtmlRenderer().render(DocumentModelBuilder().build(snapshot)).content
    assert "<script>" not in content
    assert "&lt;script&gt;" in content


def test_the_pdf_model_paginates_deterministically():
    model = DocumentModelBuilder().build(a_snapshot())
    assert PdfRenderer(lines_per_page=5).render(model).content.count("\f") >= 1


def test_a_document_title_names_what_it_is():
    for kind, title in (
        (DocumentKind.INVOICE, "Tax Invoice"),
        (DocumentKind.CREDIT_NOTE, "Credit Note"),
        (DocumentKind.DEBIT_NOTE, "Debit Note"),
    ):
        model = DocumentModelBuilder().build(a_snapshot(document=a_document(kind=kind)))
        assert model.title == title


# ── 10. validation ──────────────────────────────────────────────────────────────


def test_the_validator_never_raises():
    """§A2.6: it reports; a factory decides."""
    assert InvoiceValidator().validate_document(a_document(account_id="")).has_errors


def test_an_issued_document_must_carry_a_number_and_a_pin():
    findings = InvoiceValidator().validate_document(a_document(InvoiceState.ISSUED))
    codes = {f.code for f in findings.findings}
    assert BillingDiagnosticCode.INVOICE_NOT_NUMBERED in codes
    assert BillingDiagnosticCode.UNSEALED_INVOICE in codes


def test_a_legal_document_must_name_both_parties():
    findings = InvoiceValidator().validate_snapshot(
        a_snapshot(supplier=InvoiceParty(legal_name=""), customer=InvoiceParty(legal_name=""))
    )
    codes = {f.code for f in findings.findings}
    assert BillingDiagnosticCode.MISSING_SUPPLIER_IDENTITY in codes
    assert BillingDiagnosticCode.MISSING_CUSTOMER_IDENTITY in codes


def test_a_mixed_currency_line_is_refused_twice():
    """Two defences, and the earlier one is louder. M9.2's totalling service *raises* on a mixed
    fold rather than producing a meaningless number, so the assembler never builds such a snapshot;
    the validator's finding covers a snapshot that reached storage some other way."""
    from billing.foundation import InvoiceSnapshot

    with pytest.raises(ValueError, match="charge line in"):
        a_snapshot(sources=[a_source(1, currency=USD)])
    hand_built = InvoiceSnapshot(
        document_id="inv_1",
        revision=1,
        kind=DocumentKind.INVOICE,
        org_id=ORG,
        account_id=ACC,
        currency=INR,
        supplier=InvoiceParty(legal_name="VoxVoice"),
        customer=InvoiceParty(legal_name="Acme"),
        lines=lines.lines([a_source(1, currency=USD)], document_id="inv_1"),
    )
    codes = {f.code for f in InvoiceValidator().validate_snapshot(hand_built).findings}
    assert BillingDiagnosticCode.INVOICE_CURRENCY_MISMATCH in codes


def test_tax_without_a_determination_is_an_error():
    snapshot = a_snapshot(
        tax_components=[TaxComponent(name="IGST", rate_basis_points=1800, amount=Money(1, INR))]
    )
    codes = {f.code for f in InvoiceValidator().validate_snapshot(snapshot).findings}
    assert BillingDiagnosticCode.MISSING_TAX_EVIDENCE in codes


def test_issuing_requires_a_legal_entity():
    document = a_document(entity_code="")
    codes = {
        f.code for f in InvoiceValidator().validate_issue(document, a_snapshot(document)).findings
    }
    assert BillingDiagnosticCode.MISSING_LEGAL_ENTITY in codes


def test_sealing_a_snapshot_that_belongs_elsewhere_is_refused():
    other = a_snapshot(document=a_document(id="inv_other"))
    codes = {f.code for f in InvoiceValidator().validate_issue(a_document(), other).findings}
    assert BillingDiagnosticCode.SNAPSHOT_DOCUMENT_MISMATCH in codes


# ── 11. reconciliation + inspection ─────────────────────────────────────────────


def test_a_document_that_presents_every_entry_reconciles():
    snapshot = a_snapshot()
    report = InvoiceReconciliationService().reconcile(
        org_id=ORG,
        account_id=ACC,
        accounting_period_key=KEY,
        entries=(("jrn_1", 500_00),),
        snapshot=snapshot,
    )
    assert report.is_clean


def test_an_unbilled_ledger_entry_is_detected():
    report = InvoiceReconciliationService().reconcile(
        org_id=ORG,
        account_id=ACC,
        accounting_period_key=KEY,
        entries=(("jrn_1", 500_00), ("jrn_2", 10_00)),
        snapshot=a_snapshot(),
    )
    assert report.unbilled_count == 1


def test_an_orphan_line_is_detected():
    report = InvoiceReconciliationService().reconcile(
        org_id=ORG, account_id=ACC, accounting_period_key=KEY, entries=(), snapshot=a_snapshot()
    )
    from billing.foundation import InvoiceDiscrepancyKind

    assert any(d.kind is InvoiceDiscrepancyKind.ORPHAN_LINE for d in report.discrepancies)


def test_an_amount_that_drifted_from_the_ledger_is_detected():
    report = InvoiceReconciliationService().reconcile(
        org_id=ORG,
        account_id=ACC,
        accounting_period_key=KEY,
        entries=(("jrn_1", 499_00),),
        snapshot=a_snapshot(),
    )
    from billing.foundation import InvoiceDiscrepancyKind

    mismatch = [d for d in report.discrepancies if d.kind is InvoiceDiscrepancyKind.AMOUNT_MISMATCH]
    assert mismatch and mismatch[0].expected_minor == 499_00


def test_inspection_reports_an_untraced_monetary_line():
    snapshot = a_snapshot(sources=[a_source(journal_entry_id="")])
    inspection = InvoiceInspector().inspect(a_document(), snapshot)
    assert not inspection.is_traced
    assert not inspection.is_sound


def test_a_tax_line_needs_no_ledger_entry_to_be_traced():
    """Tax is the determination's conclusion presented on a document; it is not a posting."""
    snapshot = a_snapshot(
        lines=lines.tax_lines(
            [TaxComponent(name="IGST", rate_basis_points=1800, amount=Money(9_000, INR))],
            document_id="inv_1",
            offset=0,
        )
    )
    assert InvoiceInspector().inspect(a_document(), snapshot).is_traced


def test_inspection_notices_a_pin_that_no_longer_matches():
    document = a_document(InvoiceState.ISSUED, issued_snapshot_digest="nope")
    inspection = InvoiceInspector().inspect(document, a_snapshot())
    assert not inspection.is_sealed
    assert any("pinned at issue" in note for note in inspection.notes)


# ── 12. search ──────────────────────────────────────────────────────────────────


def test_search_is_deterministic_and_filters_by_kind():
    documents = (
        a_document(InvoiceState.ISSUED, id="a", issued_at=1.0),
        a_document(InvoiceState.ISSUED, id="b", kind=DocumentKind.CREDIT_NOTE, issued_at=2.0),
    )
    service = InvoiceSearchService()
    assert service.search(documents, InvoiceSearchQuery()).total == 2
    assert (
        service.search(documents, InvoiceSearchQuery(kinds=(DocumentKind.CREDIT_NOTE,))).total == 1
    )
    assert service.search(documents, InvoiceSearchQuery(corrections_only=True)).total == 1
    first = service.search(documents, InvoiceSearchQuery()).documents
    assert first == service.search(documents, InvoiceSearchQuery()).documents


def test_search_filters_by_state_and_period():
    documents = (a_document(InvoiceState.OVERDUE, id="a"), a_document(id="b"))
    service = InvoiceSearchService()
    assert service.search(documents, InvoiceSearchQuery(overdue_only=True)).total == 1
    assert service.search(documents, InvoiceSearchQuery(accounting_period_key=KEY)).total == 2
    assert service.search(documents, InvoiceSearchQuery(accounting_period_key="x")).total == 0


# ── 13. wiring ──────────────────────────────────────────────────────────────────


SUPPLIER = InvoiceParty(
    legal_name="VoxVoice Technologies Private Limited",
    address=BillingAddress(line1="12 MG Road", city="Bengaluru", region="KA", country="IN"),
    registrations=(PartyRegistration(scheme="gstin", number="29AABCV1234F1Z5"),),
)
KW = {"actor_id": ACTOR, "org_id": ORG}
EXPECTED = 500_00 + (100 * 200 + 200 * 100)


@pytest.fixture
def wired():
    """A real database with a posted, **closed** accounting period ready to be documented."""
    persistent = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(), ids=SequentialIdGenerator(), clock=FixedClock(1_500.0)
    )
    app = persistent.application
    d = app.commands.dispatch
    account = d(CreateBillingAccount(**KW, display_name="Acme", currency=INR))
    d(ActivateBillingAccount(**KW, account_id=account.id))
    d(CreateProduct(**KW, code="voice", display_name="Voice"))
    d(PublishProduct(**KW, code="voice"))
    catalog = d(PublishCatalog(**KW))
    d(CreatePriceBook(**KW, code="std", display_name="Standard", currency=INR))
    d(
        CreatePrice(
            **KW,
            book_code="std",
            code="platform_fee",
            shape=PriceShape.FLAT_RECURRING,
            amount=Money(500_00, INR),
            interval=BillingInterval.MONTHLY,
        )
    )
    d(
        CreatePrice(
            **KW,
            book_code="std",
            code="voice_minutes",
            shape=PriceShape.TIERED_GRADUATED,
            meter_code="voice_minutes",
            tiers=(
                PriceTier(up_to=100, unit_price=Money(200, INR)),
                PriceTier(up_to=None, unit_price=Money(100, INR)),
            ),
        )
    )
    d(PublishPricingBook(**KW, code="std"))
    pricing = d(PublishPricing(**KW, notes="v1"))
    d(
        DefineCommercialMeter(
            **KW,
            code="voice_minutes",
            unit="minute",
            source=MeterSource.RUNTIME_SESSION,
            normalization=Normalization(1, 1),
            accepted_event_types=("metric_session_minutes",),
        )
    )
    d(ActivateCommercialMeter(**KW, code="voice_minutes"))
    d(OpenUsagePeriod(**KW, account_id=account.id, period_start=P0, period_end=P1))
    subscription = d(
        CreateSubscription(
            **KW,
            account_id=account.id,
            catalog_version=catalog.version,
            pricing_version=pricing.version,
            currency=INR,
            plan_code="pro",
            items=(
                AgreementItem(id="itm_flat", price_code="platform_fee", kind=ItemKind.FLAT),
                AgreementItem(
                    id="itm_use",
                    price_code="voice_minutes",
                    kind=ItemKind.USAGE,
                    meter_code="voice_minutes",
                ),
            ),
        )
    )
    d(ActivateSubscription(**KW, subscription_id=subscription.id))
    d(
        IngestMetricSignal(
            **KW,
            period_start=P0,
            period_end=P1,
            signal=MetricSignal(
                event_id="evt_1",
                event_type="metric_session_minutes",
                source=MeterSource.RUNTIME_SESSION,
                observed_at=1_200.0,
                raw_units=300,
                raw_unit="minute",
                scope=Scope(org_id=ORG),
                received_at=1_200.0,
            ),
        )
    )
    rated = d(
        RateSubscriptionPeriod(
            **KW, subscription_id=subscription.id, period_start=P0, period_end=P1
        )
    )
    d(
        OpenAccountingPeriod(
            **KW, account_id=account.id, period_start=P0, period_end=P1, currency=INR
        )
    )
    d(FinalizeRatingRun(**KW, rating_run_id=rated.rating_run_id))
    d(PostRatingRun(**KW, rating_run_id=rated.rating_run_id))
    yield persistent, account
    persistent.database.close()


def _closed(app, account):
    app.commands.dispatch(CloseAccountingPeriod(**KW, account_id=account.id, period_key=KEY))


def _prepare(app, account, **kw):
    body = dict(
        account_id=account.id,
        accounting_period_key=KEY,
        entity_code="VOX",
        jurisdiction="KA",
        supplier=SUPPLIER,
        payment_instructions=PaymentInstructions(terms_label="Net 30"),
        payment_terms_days=30,
    )
    body.update(kw)
    return app.commands.dispatch(PrepareInvoice(**KW, **body))


def _issue(app, account, **kw):
    document = _prepare(app, account, **kw)
    return app.commands.dispatch(IssueCommercialInvoice(**KW, document_id=document.id))


# ── 14. THE DOCUMENT DEMONSTRATIONS ─────────────────────────────────────────────
#
# Each corresponds to one invariant the milestone claims. Separated deliberately: an invariant
# nobody can point at is one that decays.


def test_demo_1_an_open_accounting_period_cannot_become_a_document(wired):
    """The non-negotiable boundary: a document states a figure that has stopped moving."""
    persistent, account = wired
    with pytest.raises(BillingConflict) as excinfo:
        _prepare(persistent.application, account)
    assert "not closed" in str(excinfo.value)


def test_demo_2_a_closed_period_produces_a_document_that_matches_the_ledger(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    document = _prepare(app, account)
    assert document.total.minor == EXPECTED
    assert document.state == "draft"


def test_demo_3_preparing_twice_produces_one_document(wired):
    """Running month-end twice is a retry, not a second document racing for one legal number."""
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    first = _prepare(app, account)
    second = _prepare(app, account)
    assert first.id == second.id and second.revision == 1
    assert len(app.queries.dispatch(ListCommercialInvoices(**KW, account_id=account.id))) == 1


def test_demo_4_a_preview_persists_nothing_and_allocates_no_number(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    preview = app.queries.dispatch(
        PreviewInvoice(**KW, account_id=account.id, accounting_period_key=KEY, supplier=SUPPLIER)
    )
    assert preview.totals.total.minor == EXPECTED
    assert preview.number == ""
    assert app.queries.dispatch(ListCommercialInvoices(**KW, account_id=account.id)) == ()


def test_demo_5_a_revision_inherits_everything_it_does_not_override(wired):
    """A redraft restates the whole document, so anything not carried forward is silently dropped —
    and would then be issued that way. Found by the end-to-end smoke."""
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    document = _prepare(app, account)
    app.commands.dispatch(ReviseInvoiceDraft(**KW, document_id=document.id, reason="added PO"))
    revised = app.queries.dispatch(GetInvoiceDocument(**KW, document_id=document.id))
    assert revised.supplier.legal_name == SUPPLIER.legal_name
    assert revised.payment_terms == "Net 30"
    assert revised.revision == 2


def test_demo_6_issuing_allocates_a_gapless_number_and_seals_a_digest(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    assert issued.number == "VOX-KA-000001"
    assert issued.state == "issued"
    assert issued.snapshot_digest
    assert issued.is_issued and not issued.is_editable


def test_demo_7_an_issued_document_can_never_be_revised(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    with pytest.raises(BillingConflict):
        app.commands.dispatch(ReviseInvoiceDraft(**KW, document_id=issued.id, reason="oops"))


def test_demo_8_an_issued_document_can_never_be_voided(wired):
    """A number has been allocated, and a gapless series has no hole to spare."""
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    with pytest.raises(InvalidBillingTransition):
        app.commands.dispatch(VoidInvoiceDraft(**KW, document_id=issued.id, reason="mistake"))


def test_demo_9_the_document_reads_byte_identically_every_time(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    first = app.queries.dispatch(GetInvoiceDocument(**KW, document_id=issued.id))
    assert first == app.queries.dispatch(GetInvoiceDocument(**KW, document_id=issued.id))
    assert first.digest == issued.snapshot_digest


def test_demo_10_the_document_survives_a_restart_with_new_seams(wired):
    """Reproduction is a property of storage plus addressing, not of a warm cache."""
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    before = app.queries.dispatch(GetInvoiceDocument(**KW, document_id=issued.id))
    second = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(9_000.0),
        database=persistent.database,
        create_all=False,
    )
    assert (
        second.application.queries.dispatch(GetInvoiceDocument(**KW, document_id=issued.id))
        == before
    )


def test_demo_11_rendering_is_deterministic_and_carries_the_document_digest(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    document = app.queries.dispatch(GetInvoiceDocument(**KW, document_id=issued.id))
    first = app.commands.dispatch(RenderInvoiceDocument(**KW, document_id=issued.id))
    second = app.commands.dispatch(RenderInvoiceDocument(**KW, document_id=issued.id))
    assert first.content == second.content
    assert first.snapshot_digest == document.digest
    pdf = app.commands.dispatch(
        RenderInvoiceDocument(**KW, document_id=issued.id, format=RenderFormat.PDF)
    )
    assert pdf.snapshot_digest == document.digest and pdf.byte_size > 0


def test_demo_12_a_credit_note_leaves_the_original_exactly_as_it_was(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    before = app.queries.dispatch(GetInvoiceDocument(**KW, document_id=issued.id))
    note = app.commands.dispatch(
        IssueCreditNote(**KW, document_id=issued.id, cancel=True, reason="outage")
    )
    assert note.kind == "credit_note"
    assert note.total.minor == -EXPECTED
    assert app.queries.dispatch(GetInvoiceDocument(**KW, document_id=issued.id)) == before


def test_demo_13_a_correction_is_numbered_in_its_own_series(wired):
    """An invoice and a credit note both reach sequence 1; without a kind token they would print
    the same identifier while stating opposite things."""
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    note = app.commands.dispatch(IssueCreditNote(**KW, document_id=issued.id, cancel=True))
    debit = app.commands.dispatch(
        IssueDebitNote(
            **KW,
            document_id=issued.id,
            reason="under-charged",
            lines=(
                InvoiceLine(
                    id="",
                    sequence=1,
                    kind=ChargeLineKind.ONE_OFF,
                    description="Missed overage",
                    amount=Money(1_000, INR),
                ),
            ),
        )
    )
    assert {issued.number, note.number, debit.number} == {
        "VOX-KA-000001",
        "VOX-KA-CN-000001",
        "VOX-KA-DN-000001",
    }


def test_demo_14_a_correction_is_discoverable_from_the_original(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    note = app.commands.dispatch(IssueCreditNote(**KW, document_id=issued.id, cancel=True))
    related = app.queries.dispatch(ListInvoiceRelations(**KW, document_id=issued.id))
    assert [r.id for r in related] == [note.id]
    header = app.queries.dispatch(GetCommercialInvoice(**KW, document_id=issued.id))
    assert [r.kind for r in header.relations] == ["corrected_by"]


def test_demo_15_the_number_series_is_gapless_and_auditable(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    _issue(app, account)
    audit = app.queries.dispatch(AuditInvoiceNumberSeries(**KW, series_key="VOX:KA:invoice"))
    assert audit.is_gapless and audit.allocated == 1 and audit.missing == ()


def test_demo_16_the_document_reconciles_against_the_ledger(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    report = app.queries.dispatch(ReconcileInvoiceWithLedger(**KW, document_id=issued.id))
    assert report.is_clean and report.entries_examined == 2 and report.lines_examined == 2


def test_demo_17_every_revision_is_kept_and_exactly_one_is_issued(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    document = _prepare(app, account)
    app.commands.dispatch(ReviseInvoiceDraft(**KW, document_id=document.id, reason="a"))
    app.commands.dispatch(IssueCommercialInvoice(**KW, document_id=document.id))
    revisions = app.queries.dispatch(GetInvoiceRevisions(**KW, document_id=document.id))
    assert [r.revision for r in revisions] == [1, 2, 3]
    assert [r.issued for r in revisions] == [False, False, True]


def test_demo_18_the_settled_document_accepts_no_further_move(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    paid = app.commands.dispatch(MarkInvoicePaid(**KW, document_id=issued.id, payment_id="pay_1"))
    assert paid.state == "paid" and paid.allowed_triggers == ()
    with pytest.raises(InvalidBillingTransition):
        app.commands.dispatch(MarkInvoiceOverdue(**KW, document_id=issued.id))


def test_demo_19_the_invoice_writes_to_no_other_domain(wired):
    """Read ledger, rating, metering, pricing and subscriptions before and after issuing: none
    moved."""
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    ctx = app.context
    before_entries = ctx.ledger_engine.entries.for_period(ORG, account.id, KEY)
    before_periods = ctx.ledger_engine.periods.by_key(ORG, account.id, KEY)
    before_usage = ctx.metering_engine.usage.for_period(ORG, account.id, P0, P1)
    before_pricing = ctx.pricing.versions.latest(ORG)
    before_accounts = ctx.commercial_accounts.accounts.get(ORG, account.id)
    issued = _issue(app, account)
    app.commands.dispatch(IssueCreditNote(**KW, document_id=issued.id, cancel=True))
    assert ctx.ledger_engine.entries.for_period(ORG, account.id, KEY) == before_entries
    assert ctx.ledger_engine.periods.by_key(ORG, account.id, KEY) == before_periods
    assert ctx.metering_engine.usage.for_period(ORG, account.id, P0, P1) == before_usage
    assert ctx.pricing.versions.latest(ORG) == before_pricing
    assert ctx.commercial_accounts.accounts.get(ORG, account.id) == before_accounts


# ── 15. more wiring ─────────────────────────────────────────────────────────────


def test_the_customer_identity_is_read_from_the_billing_account(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    document = app.queries.dispatch(GetInvoiceDocument(**KW, document_id=issued.id))
    assert document.customer.legal_name
    assert document.customer.trade_name == "Acme"


def test_the_export_is_the_same_content_as_the_document(wired):
    """An export that differed from the document would be a second answer to what the invoice
    says."""
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    assert app.queries.dispatch(
        ExportInvoiceDocument(**KW, document_id=issued.id)
    ) == app.queries.dispatch(GetInvoiceDocument(**KW, document_id=issued.id))


def test_a_rendering_can_be_read_without_having_been_stored(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    rendered = app.queries.dispatch(
        GetRenderedInvoice(**KW, document_id=issued.id, format=RenderFormat.PDF)
    )
    assert rendered.byte_size > 0


def test_approval_reaches_the_same_state_as_a_direct_issue(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    document = _prepare(app, account)
    app.commands.dispatch(SubmitInvoiceForApproval(**KW, document_id=document.id))
    approved = app.commands.dispatch(
        IssueCommercialInvoice(**KW, document_id=document.id, approve=True)
    )
    assert approved.state == "issued" and approved.number


def test_the_overdue_and_collection_path_works(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    overdue = app.commands.dispatch(MarkInvoiceOverdue(**KW, document_id=issued.id))
    assert overdue.state == "overdue"
    assert (
        app.commands.dispatch(SendInvoiceToCollection(**KW, document_id=issued.id)).state
        == "in_collection"
    )


def test_delivery_and_attachment_metadata_are_recorded(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    delivered = app.commands.dispatch(
        RecordInvoiceDelivery(
            **KW,
            document_id=issued.id,
            channel=DeliveryChannel.EMAIL,
            recipient="ap@acme.example",
            state=DeliveryState.DELIVERED,
        )
    )
    assert delivered.delivery.state == "delivered" and delivered.delivery.attempts == 1
    app.commands.dispatch(
        RecordInvoiceAttachment(
            **KW,
            document_id=issued.id,
            filename="usage.csv",
            media_type="text/csv",
            purpose=AttachmentPurpose.USAGE_DETAIL,
        )
    )
    actions = {a.action for a in app.queries.dispatch(InvoiceTimeline(**KW, document_id=issued.id))}
    assert "document.delivery_recorded" in actions
    assert "document.attachment_recorded" in actions


def test_inspection_and_diagnostics_are_reachable(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    assert app.queries.dispatch(InspectCommercialInvoice(**KW, document_id=issued.id)).is_sound
    assert app.queries.dispatch(DiagnoseCommercialInvoice(**KW, document_id=issued.id)).is_valid


def test_search_and_history_over_the_bus(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    app.commands.dispatch(IssueCreditNote(**KW, document_id=issued.id, cancel=True))
    assert app.queries.dispatch(SearchCommercialInvoices(**KW)).total == 2
    assert (
        app.queries.dispatch(
            SearchCommercialInvoices(**KW, query=InvoiceSearchQuery(corrections_only=True))
        ).total
        == 1
    )
    assert app.queries.dispatch(InvoiceTimeline(**KW, account_id=account.id))


def test_the_invoice_engine_is_org_scoped(wired):
    persistent, account = wired
    app = persistent.application
    _closed(app, account)
    issued = _issue(app, account)
    assert (
        app.queries.dispatch(
            ListCommercialInvoices(actor_id=ACTOR, org_id="org_other", account_id=account.id)
        )
        == ()
    )
    with pytest.raises(BillingNotFound):
        app.queries.dispatch(
            GetCommercialInvoice(actor_id=ACTOR, org_id="org_other", document_id=issued.id)
        )


def test_an_unknown_document_is_a_typed_not_found(wired):
    persistent, _ = wired
    with pytest.raises(BillingNotFound):
        persistent.application.queries.dispatch(GetCommercialInvoice(**KW, document_id="nope"))


# ── 16. HTTP ────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    settings = BillingApiSettings.load(environment="testing")
    container = BillingContainer.build(
        settings,
        authorizer=AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(1_500.0),
    )
    app = create_billing_app(settings=settings, container=container)
    with TestClient(app) as test_client:
        yield test_client


PREFIX = "/v1/billing"


def test_invoice_routes_require_identity_and_an_org(client):
    assert client.get(f"{PREFIX}/documents").status_code == 401


def test_the_organization_always_comes_from_the_token(client):
    source = (BILLING / "customer" / "routers" / "invoice_engine.py").read_text()
    assert "ctx.organization()" in source
    assert "org_id: str" not in source  # never a body or query parameter


def test_the_surface_has_no_delete_put_or_patch(client):
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        if not path.startswith(f"{PREFIX}/documents"):
            continue
        assert set(operations) <= {"get", "post"}, (path, sorted(operations))


def test_no_route_voids_an_issued_document(client):
    spec = client.get("/openapi.json").json()
    paths = [p for p in spec["paths"] if p.startswith(f"{PREFIX}/documents")]
    assert f"{PREFIX}/documents/{{document_id}}/void" in paths  # drafts only
    assert not [p for p in paths if p.endswith("/unissue") or p.endswith("/reopen")]


def test_every_invoice_path_is_documented_and_tagged(client):
    """Scoped to the invoice surface by prefix, not substring: M9.15 mounts a *tax view* of a
    document at `/taxation/documents/...`, which a substring scan would read as an invoice route."""
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        if not path.startswith(f"{PREFIX}/documents"):
            continue
        for method, operation in operations.items():
            assert operation.get("summary"), (method, path)
            assert operation["tags"] == ["invoices"], (method, path)


def test_the_invoice_surface_is_the_declared_size(client):
    spec = client.get("/openapi.json").json()
    paths = [p for p in spec["paths"] if p.startswith(f"{PREFIX}/documents")]
    assert len(paths) == 26


def test_no_invoice_schema_name_is_qualified(client):
    """A duplicated schema name surfaces in the public document as
    `module__ClassName`. M9.13 caught one this way; this keeps M9.14's own surface clean."""
    spec = client.get("/openapi.json").json()
    qualified = [
        name for name in spec["components"]["schemas"] if "__" in name and "invoice" in name.lower()
    ]
    assert qualified == []


def test_money_is_still_declared_exactly_once(client):
    spec = client.get("/openapi.json").json()
    assert len([n for n in spec["components"]["schemas"] if n.endswith("MoneyResponse")]) == 1


# ── 17. Studio ──────────────────────────────────────────────────────────────────


def _studio_script() -> str:
    """The Studio's executable body — everything between `<script>` and `</script>`.

    Scanned instead of the whole file because the header comment deliberately *names* what the
    Studio does not do, and a scan that could not tell prose from code would flag the explanation
    as the violation."""
    source = STUDIO.read_text()
    return source[source.index("<script>") : source.rindex("</script>")]


def test_invoice_studio_is_served(client):
    response = client.get("/studio/invoices.html")
    assert response.status_code == 200
    assert "VoxVoice · Invoices" in response.text


def test_invoice_studio_is_self_contained():
    source = STUDIO.read_text()
    for forbidden in ("http://", "https://", "cdn.", "<script src", "@import"):
        assert forbidden not in source, forbidden


def test_invoice_studio_has_a_screen_for_every_required_area():
    source = STUDIO.read_text()
    for screen in (
        "documentsScreen",
        "prepareScreen",
        "documentScreen",
        "renderingScreen",
        "correctionsScreen",
        "relationsScreen",
        "revisionsScreen",
        "numberingScreen",
        "reconcileScreen",
        "deliveryScreen",
        "activityScreen",
    ):
        assert screen in source, screen
    assert source.count('["documents", "Documents"]') == 1


def test_invoice_studio_never_computes_a_total():
    """A client that adds up its own column on an invoice will eventually print a figure the
    customer is asked to pay and the ledger has never seen."""
    body = _studio_script()
    for forbidden in (
        "reduce(",
        "+=",
        "toFixed(",
        ".minor +",
        ".minor -",
        ".minor *",
        ".minor /",
        "parseFloat",
        "total +",
        "amount +",
    ):
        assert forbidden not in body, forbidden
    # The one place a monetary value is touched at all formats it as a string and derives nothing.
    assert body.count(".minor") == 2  # a sign test and a digit string, both inside `amount()`


def test_invoice_studio_owns_no_state():
    """Scanned over the script body only — the header comment names these to explain their
    absence, and prose about a rule must not read as a breach of it."""
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in _studio_script(), forbidden


def test_invoice_studio_takes_its_lifecycle_buttons_from_the_server():
    source = _studio_script()
    assert "allowed_triggers" in source
    # No hard-coded decision about which transitions are legal from which state.
    assert 'state === "issued" ?' not in source


def test_invoice_studio_renders_nothing_itself():
    """The markup shown to a viewer is the server's artefact, handed straight to a sandboxed
    frame — so what the browser shows is what was issued."""
    source = STUDIO.read_text()
    assert 'sandbox: ""' in source
    assert "srcdoc" in source


# ── 18. architecture compliance ─────────────────────────────────────────────────

INVOICE_MODULES = (
    BILLING / "foundation" / "commercial_invoicing.py",
    BILLING / "foundation" / "invoice_rules.py",
    BILLING / "foundation" / "invoice_factories.py",
    BILLING / "application" / "invoice_engine.py",
    BILLING / "persistence" / "invoice_models.py",
    BILLING / "persistence" / "invoice_repositories.py",
    BILLING / "customer" / "invoice_schemas.py",
    BILLING / "customer" / "routers" / "invoice_engine.py",
)


def _code_only(path: Path) -> str:
    """Source with docstrings stripped. These scans are about what the domain *does*, and prose
    explaining that the invoice prices nothing must not read as pricing."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            tops.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            tops.update(a.name.split(".")[0] for a in node.names)
    return tops


def test_the_invoice_calculates_no_price():
    for path in INVOICE_MODULES:
        code = _code_only(path)
        for forbidden in (
            "PriceResolver",
            "PriceResolutionRequest",
            "select_book",
            "DiscountEngine",
            "ResolvedPrice",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_invoice_rates_nothing():
    """It reads M9.13's postings and presents them; it never recomputes a charge."""
    for path in INVOICE_MODULES:
        code = _code_only(path)
        for forbidden in (
            "RatingEngine",
            "ChargeAssembler",
            "ChargeCalculator",
            "DiscountRebasingPolicy",
            "RatingBasis",
            "RatedCharge",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_invoice_meters_nothing():
    for path in INVOICE_MODULES:
        code = _code_only(path)
        for forbidden in (
            "UsageIngestionService",
            "UsageAggregator",
            "MetricSignal",
            "MetricStreamPort",
            "normalize(",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_invoice_posts_no_ledger_entry_and_computes_no_balance():
    for path in INVOICE_MODULES:
        code = _code_only(path)
        for forbidden in (
            "PostingService",
            "JournalEntry(",
            "AccountBalance",
            "BalanceService",
            "TrialBalance",
            "CreditApplicationService",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_invoice_evaluates_no_entitlement():
    for path in INVOICE_MODULES:
        code = _code_only(path)
        for forbidden in (
            "EntitlementSet",
            "ResolvedEntitlement",
            "EntitlementGate",
            "is_enabled",
            "allowance(",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_invoice_authorizes_nothing():
    for path in INVOICE_MODULES[:3]:
        code = _code_only(path)
        for forbidden in ("principal", "Permission", "has_role", "authorize", "RBAC"):
            assert forbidden not in code, (path.name, forbidden)


def test_the_invoice_moves_no_money_and_determines_no_tax():
    for path in INVOICE_MODULES:
        code = _code_only(path)
        for forbidden in (
            "PaymentGateway",
            "capture(",
            "authorize_payment",
            "refund(",
            "determine_tax",
            "TaxRate",
            "rate_for",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_invoice_imports_no_frozen_epic():
    forbidden = {
        "api",
        "runtime",
        "product",
        "actuator",
        "providers",
        "provider",
        "agent",
        "tool",
        "workflow",
        "mcp",
        "knowledge",
        "observability",
        "gateway",
        "telemetry",
        "tenant",
        "deployment",
    }
    for path in INVOICE_MODULES:
        assert not (_imports(path) & forbidden), (path.name, _imports(path) & forbidden)


def test_the_invoice_foundation_stays_pure():
    forbidden = {
        "sqlalchemy",
        "sqlmodel",
        "fastapi",
        "starlette",
        "pydantic",
        "os",
        "random",
        "datetime",
        "socket",
        "httpx",
        "requests",
        "asyncio",
        "reportlab",
        "weasyprint",
        "jinja2",
    }
    for path in INVOICE_MODULES[:3]:
        assert not (_imports(path) & forbidden), (path.name, _imports(path) & forbidden)


def test_the_invoice_rules_read_no_clock():
    """Document assembly is a pure function of what it is given — the only way byte-identical
    reproduction can be proven."""
    code = _code_only(BILLING / "foundation" / "invoice_rules.py")
    for forbidden in ("Clock", "now()", "time("):
        assert forbidden not in code, forbidden


def test_invoice_use_cases_contain_no_arithmetic():
    """Every document rule belongs to the Foundation."""
    code = _code_only(BILLING / "application" / "invoice_engine.py")
    for forbidden in (
        ".times(",
        ".scaled(",
        "Money.allocate",
        "def calculate",
        "sum(",
        "Rounding.",
    ):
        assert forbidden not in code, forbidden
    # `.allocate(` is deliberately absent from that list: the one call bearing that name draws an
    # invoice number from the persistence seam, which is a write, not arithmetic. Scanned by
    # receiver so the distinction is checked rather than assumed.
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "allocate":
                assert ast.unparse(node.func.value) == "self._repos.numbers"


def test_the_invoice_writes_only_to_its_own_repositories():
    """The structural form of "the Invoice takes ownership of nothing": a write verb on an injected
    collaborator must target `self._repos.*`."""
    code = _code_only(BILLING / "application" / "invoice_engine.py")
    writes = {"add", "append", "upsert", "replace", "allocate", "publish", "seal"}
    for node in ast.walk(ast.parse(code)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in writes:
            continue
        target = ast.unparse(node.func.value)
        if not target.startswith("self._"):
            continue  # a local list's `.append` is not a repository write
        assert target.startswith(("self._repos.", "self._snapshots")), (target, node.func.attr)


def test_invoice_repositories_decide_nothing():
    code = _code_only(BILLING / "persistence" / "invoice_repositories.py")
    for forbidden in ("snapshot(", ".format(", "totals(", "InvoiceTotalsService", "render("):
        assert forbidden not in code, forbidden


def test_invoice_controllers_stay_transport_only():
    code = _code_only(BILLING / "customer" / "routers" / "invoice_engine.py")
    for forbidden in ("repositories", "session.", ".commit()", "select(", "Money(", "allocate("):
        assert forbidden not in code, forbidden


def test_the_snapshot_repository_has_no_update_path():
    """Append-only by construction: not by convention, but because no method exists to call."""
    from billing.foundation import InvoiceSnapshotRepository

    methods = {m for m in dir(InvoiceSnapshotRepository) if not m.startswith("_")}
    assert methods == {"append", "get", "by_digest", "revisions", "latest"}


def test_the_rendering_and_revision_repositories_have_no_update_path():
    from billing.foundation import InvoiceRevisionRepository, RenderedDocumentRepository

    assert {m for m in dir(InvoiceRevisionRepository) if not m.startswith("_")} == {
        "append",
        "history",
    }
    assert {m for m in dir(RenderedDocumentRepository) if not m.startswith("_")} == {
        "append",
        "get",
        "for_document",
    }


def test_the_activity_repository_is_append_only():
    from billing.foundation import InvoiceActivityRepository

    assert {m for m in dir(InvoiceActivityRepository) if not m.startswith("_")} == {
        "append",
        "history",
        "for_document",
    }


def test_m9_2_invoicing_vocabulary_is_reused_not_duplicated():
    """`InvoiceState`, `InvoiceTrigger`, `ChargeLineKind`, `InvoiceNumber` and `InvoiceTotals` are
    M9.2's. Two answers to "what state is this invoice in?" is the drift §A2.5 prevents."""
    m92 = (BILLING / "foundation" / "invoicing.py").read_text()
    assert "class InvoiceState" in m92 and "class InvoiceTrigger" in m92
    m914 = (BILLING / "foundation" / "commercial_invoicing.py").read_text()
    for duplicated in (
        "class InvoiceState",
        "class InvoiceTrigger",
        "class ChargeLineKind",
        "class InvoiceNumber",
        "class InvoiceTotals",
    ):
        assert duplicated not in m914, duplicated
    assert "class CommercialInvoice" in m914 and "class InvoiceSnapshot" in m914


def test_the_m9_2_aggregates_are_untouched():
    m92 = (BILLING / "foundation" / "invoicing.py").read_text()
    for kept in ("class Invoice", "class CreditNote", "class ChargeLine", "class BillingRun"):
        assert kept in m92, kept


def test_there_is_still_exactly_one_totalling_service():
    """M9.2's `InvoiceTotalsService` remains the only place the order discount → credit → tax is
    applied."""
    foundation = BILLING / "foundation"
    declarations = [
        path.name
        for path in foundation.glob("*.py")
        if "class InvoiceTotalsService" in path.read_text()
    ]
    assert declarations == ["services.py"]


def test_there_is_still_exactly_one_invoice_number_formatter():
    foundation = BILLING / "foundation"
    declarations = [
        path.name
        for path in foundation.glob("*.py")
        if "class InvoiceNumberFormatter" in path.read_text()
    ]
    assert declarations == ["services.py"]
    assert "self._formatter.format(" in (foundation / "invoice_rules.py").read_text()


def test_every_event_value_is_unique():
    from billing.foundation import BillingEventType

    values = [e.value for e in BillingEventType]
    assert len(values) == len(set(values))


def test_every_diagnostic_code_is_unique():
    from billing.foundation import BillingDiagnosticCode as D

    values = [d.value for d in D]
    assert len(values) == len(set(values))


def test_exactly_one_event_per_lifecycle_trigger():
    from billing.foundation import ISSUE_EVENTS, LIFECYCLE_EVENTS

    assert set(LIFECYCLE_EVENTS) == set(InvoiceTrigger)
    # ISSUE and APPROVE deliberately share one fact: two routes to the same event in the world.
    assert LIFECYCLE_EVENTS[InvoiceTrigger.ISSUE] is LIFECYCLE_EVENTS[InvoiceTrigger.APPROVE]
    assert set(ISSUE_EVENTS) == set(DocumentKind)
    assert len(set(ISSUE_EVENTS.values())) == 3


def test_command_and_query_coverage_is_complete():
    from billing.application.invoice_engine import (
        register_invoice_command_handlers,
        register_invoice_query_handlers,
    )

    registered = _inspect.getsource(register_invoice_command_handlers) + _inspect.getsource(
        register_invoice_query_handlers
    )
    for message in (*ALL_INVOICE_COMMANDS, *ALL_INVOICE_QUERIES):
        assert f"bus.register({message.__name__}," in registered, message.__name__
        fields = set(message.__dataclass_fields__)
        assert {"actor_id", "org_id"} <= fields, message.__name__
    assert len(ALL_INVOICE_COMMANDS) == 13
    assert len(ALL_INVOICE_QUERIES) == 14


def test_the_invoice_engine_is_additive_and_optional():
    from billing.application.context import BillingContext

    assert "invoice_engine" in BillingContext.__dataclass_fields__
    assert BillingContext.__dataclass_fields__["invoice_engine"].default is None


def test_invoice_tables_are_created_by_the_migration_chain():
    from sqlalchemy import inspect as sa_inspect

    from billing.persistence.database import BillingDatabase
    from billing.persistence.invoice_models import INVOICE_TABLE_NAMES
    from billing.persistence.migrations import MIGRATIONS, upgrade_all

    database = BillingDatabase("sqlite://")
    applied = upgrade_all(database)
    assert "0024" in applied  # pinned by presence: M9.15 appended 0025 after it
    assert [m.VERSION for m in MIGRATIONS] == sorted(m.VERSION for m in MIGRATIONS)
    tables = set(sa_inspect(database.engine).get_table_names())
    for name in INVOICE_TABLE_NAMES:
        assert name in tables
    # The M9.2 invoicing tables survive untouched.
    for name in ("cp_billing_invoice", "cp_billing_credit_note", "cp_billing_run"):
        assert name in tables, name
    database.close()


def test_the_invoice_migration_alters_nothing_and_is_idempotent():
    from billing.persistence.database import BillingDatabase
    from billing.persistence.migrations import upgrade_all, v0024_billing_invoicing

    code = _code_only(Path(v0024_billing_invoicing.__file__))
    for forbidden in ("ALTER", "DROP", "drop(", "delete("):
        assert forbidden not in code
    database = BillingDatabase("sqlite://")
    upgrade_all(database)
    v0024_billing_invoicing.upgrade(database)  # twice is a no-op
    assert v0024_billing_invoicing.downgrade(database) is None
    database.close()


def test_a_duplicate_invoice_number_is_an_impossible_row():
    """`(org, series, sequence)` is the primary key of the allocation ledger, so a duplicate number
    is not a finding a validator might report — it is a row the database refuses."""
    from billing.persistence.invoice_models import BillingInvoiceNumberRow

    primary = [c.name for c in BillingInvoiceNumberRow.__table__.primary_key]
    assert primary == ["org_id", "series_key", "sequence"]


def test_one_revision_per_document_is_a_constraint():
    from billing.persistence.invoice_models import BillingInvoiceSnapshotRow

    constraints = {
        c.name for c in BillingInvoiceSnapshotRow.__table__.constraints if getattr(c, "name", None)
    }
    assert "uq_cp_billing_invoice_snapshot_revision" in constraints


def test_the_snapshot_table_indexes_the_digest_it_is_resolved_by():
    from billing.persistence.invoice_models import BillingInvoiceSnapshotRow

    indexed = {c.name for c in BillingInvoiceSnapshotRow.__table__.columns if c.index}
    assert "digest" in indexed


def test_number_allocation_is_gapless_and_crash_safe_by_transaction():
    """The number is drawn inside the caller's transaction, so a rollback un-consumes it. That is
    the whole crash-safety mechanism, and a pre-allocated pool cannot offer it."""
    from billing.persistence.composition import build_invoice_repositories
    from billing.persistence.database import BillingDatabase

    database = BillingDatabase("sqlite://")
    database.create_all()
    allocator = build_invoice_repositories(database).numbers
    assert allocator.allocate(ORG, "VOX::invoice") == 1
    assert allocator.allocate(ORG, "VOX::invoice") == 2
    database.session.commit()

    # A worker dies between allocating and committing the issue. Because the allocation lives in
    # the same transaction, the rollback un-consumes it — which is the entire crash-safety
    # mechanism, and the one a pre-allocated pool cannot offer.
    allocator.allocate(ORG, "VOX::invoice")
    database.session.rollback()

    assert allocator.allocate(ORG, "VOX::invoice") == 3
    database.session.commit()
    assert allocator.allocated(ORG, "VOX::invoice") == (1, 2, 3)
    assert NumberSeriesAuditor().audit("VOX::invoice", (1, 2, 3)).is_gapless
    database.close()


def test_two_series_never_share_a_counter():
    from billing.persistence.composition import build_invoice_repositories
    from billing.persistence.database import BillingDatabase

    database = BillingDatabase("sqlite://")
    database.create_all()
    allocator = build_invoice_repositories(database).numbers
    assert allocator.allocate(ORG, "VOX:KA:invoice") == 1
    assert allocator.allocate(ORG, "VOX:MH:invoice") == 1
    assert allocator.allocate(ORG, "VOX:KA:credit_note") == 1
    database.session.commit()
    assert sorted(allocator.series(ORG)) == [
        "VOX:KA:credit_note",
        "VOX:KA:invoice",
        "VOX:MH:invoice",
    ]
    database.close()


def test_a_series_is_org_scoped():
    from billing.persistence.composition import build_invoice_repositories
    from billing.persistence.database import BillingDatabase

    database = BillingDatabase("sqlite://")
    database.create_all()
    allocator = build_invoice_repositories(database).numbers
    assert allocator.allocate("org_a", "VOX::invoice") == 1
    assert allocator.allocate("org_b", "VOX::invoice") == 1
    database.session.commit()
    assert allocator.peek("org_a", "VOX::invoice") == 1
    assert allocator.peek("org_c", "VOX::invoice") == 0
    database.close()


def test_a_snapshot_round_trips_through_persistence():
    """`decode(type(a), encode(a)) == a` for the deepest aggregate in the plane — parties,
    addresses, lines, tax components, totals, instructions, presentation, relations."""
    from billing.persistence.codec import decode, encode

    snapshot = a_snapshot(
        supplier=SUPPLIER,
        tax_components=[TaxComponent(name="IGST", rate_basis_points=1800, amount=Money(1, INR))],
        tax_determination_id="tax_1",
        payment_instructions=PaymentInstructions(terms_label="Net 30"),
        presentation=DocumentPresentation(template_code="minimal", template_version=3),
        number=numbering.format(NumberingPolicy(entity_code="VOX"), 1),
    )
    from billing.foundation import InvoiceSnapshot

    assert decode(InvoiceSnapshot, encode(snapshot)) == snapshot


def test_the_billing_package_layout_grew_by_addition():
    import pkgutil

    modules = {m.name for m in pkgutil.iter_modules([str(BILLING / "foundation")])}
    for name in ("commercial_invoicing", "invoice_rules", "invoice_factories"):
        assert name in modules, name
    assert "invoicing" in modules  # the M9.2 module, unreshaped


def test_the_earlier_domain_boundaries_still_hold():
    """Metering and Subscription still carry no money after the milestone that documents it."""
    for module in ("commercial_subscriptions", "commercial_metering"):
        code = _code_only(BILLING / "foundation" / f"{module}.py")
        assert "Money" not in code, module


def test_the_ledger_still_creates_no_invoice():
    """M9.13's boundary survives the milestone that consumes it."""
    for module in ("commercial_ledger", "ledger_rules", "ledger_factories"):
        code = _code_only(BILLING / "foundation" / f"{module}.py")
        for forbidden in ("InvoiceSnapshot", "CommercialInvoice", "NumberingService"):
            assert forbidden not in code, (module, forbidden)


def test_billing_is_still_absent_from_the_runtime_composition_root():
    """ADR-0031 CR2 survives M9.14."""
    assert "billing" not in (PLATFORM / "bootstrap.py").read_text().lower()

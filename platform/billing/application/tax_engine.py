"""Tax Determination application layer (M9.15) — commands, queries, use cases, mapping.

Additive to M9.3/M9.6–M9.14: the buses, Unit of Work, authorization orchestrator, application-event
vocabulary, and dispatch discipline are reused exactly as they are. This module adds 13 commands, 16
queries, their use cases, and the view DTOs — and nothing else.

The four beats are unchanged: **authorize → open a transaction → coordinate the Foundation → stage
an event.** Every tax rule — where a supply happens, whether there is an obligation, what treatment
applies, which rates are in force, how the tax is computed and rounded — is Foundation behaviour
these methods *call*. There is no arithmetic here and no rate anywhere, and a test greps this module
for both.

## What this layer reads, and what it refuses

Assessing a document is the only path by which a financial fact acquires a tax treatment, and it
reads **forward-only**:

* the **document snapshot** (M9.14) for the lines and their bases — never the ledger, never
  rating, never pricing. Tax determines the treatment of an already-determined amount; going back
  to recompute the amount would make Tax a second source of a figure another domain owns.
* the **billing account** (M9.8) for the customer's residency and registration;
* the **configuration** — jurisdictions, classifications, nexus, exemptions, declared rates — which
  is Tax's own.

The supplier's origin arrives on the command, for the same reason M9.14's supplier party does: who
VoxVoice contracts as is published organization information, which Billing references and never owns
(ADR-0023 TX3).

## The one write into another milestone's repository, and why it is not a leak

`FinalizeTaxAssessment` writes an M9.2 `TaxDetermination` through `TaxDeterminationRepository`.
That repository is **Tax's own seam** — M9.2 declared the contract and left it unimplemented for
this milestone — and the record written is produced by `TaxAssessment.to_determination()`, the
single derivation. So the thinner record M9.5's `/taxes` surface and M9.14's document assembly
already read is a *projection* of one truth rather than a second copy of it (§A2.5), and M9.14
needs no change to start presenting tax: it re-reads the determination on its next revision.

## Names

`TaxAssessment` and its commands sit beside M9.2's `UpsertTaxProfile`, `GetTaxProfile`, and
`GetTaxDetermination`, which stay exactly as they are. The same call M9.13 made for `JournalEntry`
beside `LedgerEntry` and M9.14 for `CommercialInvoice` beside `Invoice`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from billing.foundation import (
    AssessmentTrigger,
    Currency,
    DeclaredRateTaxProvider,
    DeterminationRequest,
    ExemptionKind,
    JurisdictionLevel,
    Money,
    NexusKind,
    Rounding,
    RoundingScope,
    SubjectKind,
    TaxAction,
    TaxAssessment,
    TaxBasisInputs,
    TaxpayerKind,
    TaxRegime,
    TaxRegistration,
    TaxResidency,
    TaxRoundingPolicy,
    TaxSearchQuery,
    TaxTreatment,
    jurisdiction_key,
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
class DeclareTaxJurisdiction(_Command):
    """Declare a place with taxing authority — **never its percentage.**"""

    country: str = ""
    region: str = ""
    display_name: str = ""
    level: JurisdictionLevel = JurisdictionLevel.COUNTRY
    regime: TaxRegime = TaxRegime.NONE
    parent_code: str = ""
    notes: str = ""


@dataclass(frozen=True, slots=True)
class RetireTaxJurisdiction(_Command):
    jurisdiction_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class DeclareTaxClassification(_Command):
    code: str = ""
    display_name: str = ""
    default_treatment: TaxTreatment = TaxTreatment.STANDARD
    codes: tuple[tuple[str, str], ...] = ()
    is_digital_service: bool = False
    notes: str = ""


@dataclass(frozen=True, slots=True)
class RegisterTaxNexus(_Command):
    """Record the supplier's obligation in a jurisdiction, and the registration discharging it."""

    entity_code: str = ""
    jurisdiction_code: str = ""
    kind: NexusKind = NexusKind.REGISTRATION
    registration_number: str = ""
    registration_regime: TaxRegime = TaxRegime.NONE
    registration_country: str = ""
    effective_from: float | None = None
    effective_until: float | None = None
    established_reason: str = ""


@dataclass(frozen=True, slots=True)
class EndTaxNexus(_Command):
    nexus_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RecordTaxExemption(_Command):
    account_id: str = ""
    jurisdiction_code: str = ""
    kind: ExemptionKind = ExemptionKind.CERTIFICATE
    certificate_ref: str = ""
    reason: str = ""
    effective_from: float | None = None
    effective_until: float | None = None
    evidence_ref: str = ""


@dataclass(frozen=True, slots=True)
class RevokeTaxExemption(_Command):
    exemption_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class DeclareTaxRate(_Command):
    """Declare an effective-dated rate as **Billing evidence**, with the authority that set it."""

    jurisdiction_code: str = ""
    tax_type: str = ""
    name: str = ""
    rate_basis_points: int = 0
    classification_code: str = ""
    treatment: TaxTreatment = TaxTreatment.STANDARD
    effective_from: float | None = None
    effective_until: float | None = None
    reference: str = ""
    source_version: str = ""


@dataclass(frozen=True, slots=True)
class RetireTaxRate(_Command):
    rate_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AssessDocumentTax(_Command):
    """Determine the tax treatment of a document's already-determined amounts.

    Idempotent on the basis digest: assessing the same facts twice is one assessment."""

    document_id: str = ""
    entity_code: str = ""
    origin_country: str = ""
    origin_region: str = ""
    supplier_registration: str = ""
    default_classification_code: str = ""
    line_classifications: tuple[tuple[str, str], ...] = ()
    taxpayer_kind: TaxpayerKind = TaxpayerKind.BUSINESS
    regime: TaxRegime = TaxRegime.NONE
    prices_include_tax: bool = False
    rounding_mode: Rounding = Rounding.HALF_UP
    rounding_scope: RoundingScope = RoundingScope.PER_LINE
    effective_at: float | None = None


@dataclass(frozen=True, slots=True)
class FinalizeTaxAssessment(_Command):
    """Seal a determination and publish its projection onto M9.2's `TaxDetermination`."""

    assessment_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OverrideTaxTreatment(_Command):
    """A human decision to depart from what determination concluded. Always attributed."""

    assessment_id: str = ""
    treatment: TaxTreatment = TaxTreatment.STANDARD
    reason: str = ""
    reference: str = ""


@dataclass(frozen=True, slots=True)
class DiscardTaxAssessment(_Command):
    assessment_id: str = ""
    reason: str = ""


ALL_TAX_COMMANDS = (
    DeclareTaxJurisdiction,
    RetireTaxJurisdiction,
    DeclareTaxClassification,
    RegisterTaxNexus,
    EndTaxNexus,
    RecordTaxExemption,
    RevokeTaxExemption,
    DeclareTaxRate,
    RetireTaxRate,
    AssessDocumentTax,
    FinalizeTaxAssessment,
    OverrideTaxTreatment,
    DiscardTaxAssessment,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetTaxAssessment(_Query):
    assessment_id: str = ""


@dataclass(frozen=True, slots=True)
class GetSubjectTaxAssessment(_Query):
    """The determination that stands for a subject: the finalized one, or the newest provisional."""

    subject_id: str = ""


@dataclass(frozen=True, slots=True)
class ListTaxAssessments(_Query):
    account_id: str = ""
    subject_id: str = ""
    limit: int = 200


@dataclass(frozen=True, slots=True)
class ExplainTaxAssessment(_Query):
    """ "Why was this tax amount applied?" — answered from the assessment alone."""

    assessment_id: str = ""


@dataclass(frozen=True, slots=True)
class PreviewTaxAssessment(_Query):
    """What a determination *would* conclude. A read: persists nothing, finalizes nothing."""

    document_id: str = ""
    entity_code: str = ""
    origin_country: str = ""
    origin_region: str = ""
    default_classification_code: str = ""
    regime: TaxRegime = TaxRegime.NONE
    prices_include_tax: bool = False


@dataclass(frozen=True, slots=True)
class ResolveTaxPlaceOfSupply(_Query):
    """Where a supply would be taxed, without determining anything else."""

    origin_country: str = ""
    origin_region: str = ""
    destination_country: str = ""
    destination_region: str = ""
    regime: TaxRegime = TaxRegime.NONE
    customer_registration: str = ""
    taxpayer_kind: TaxpayerKind = TaxpayerKind.BUSINESS


@dataclass(frozen=True, slots=True)
class ListTaxJurisdictions(_Query):
    include_retired: bool = False


@dataclass(frozen=True, slots=True)
class ListTaxClassifications(_Query):
    pass


@dataclass(frozen=True, slots=True)
class ListTaxNexus(_Query):
    entity_code: str = ""


@dataclass(frozen=True, slots=True)
class ListTaxExemptions(_Query):
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class ListDeclaredTaxRates(_Query):
    """Every rate ever declared for a jurisdiction, live or not — what an audit reads."""

    jurisdiction_code: str = ""
    limit: int = 500


@dataclass(frozen=True, slots=True)
class ReconcileTaxAssessment(_Query):
    assessment_id: str = ""


@dataclass(frozen=True, slots=True)
class InspectTaxAssessment(_Query):
    assessment_id: str = ""


@dataclass(frozen=True, slots=True)
class DiagnoseTaxAssessment(_Query):
    assessment_id: str = ""


@dataclass(frozen=True, slots=True)
class SearchTaxAssessments(_Query):
    query: TaxSearchQuery = field(default_factory=TaxSearchQuery)


@dataclass(frozen=True, slots=True)
class TaxTimeline(_Query):
    account_id: str = ""
    subject_id: str = ""
    limit: int = 100


ALL_TAX_QUERIES = (
    GetTaxAssessment,
    GetSubjectTaxAssessment,
    ListTaxAssessments,
    ExplainTaxAssessment,
    PreviewTaxAssessment,
    ResolveTaxPlaceOfSupply,
    ListTaxJurisdictions,
    ListTaxClassifications,
    ListTaxNexus,
    ListTaxExemptions,
    ListDeclaredTaxRates,
    ReconcileTaxAssessment,
    InspectTaxAssessment,
    DiagnoseTaxAssessment,
    SearchTaxAssessments,
    TaxTimeline,
)


# ── view DTOs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TaxJurisdictionView:
    id: str
    code: str
    display_name: str
    country: str
    region: str
    level: str
    regime: str
    parent_code: str = ""
    is_active: bool = True
    notes: str = ""


@dataclass(frozen=True, slots=True)
class TaxClassificationView:
    id: str
    code: str
    display_name: str
    default_treatment: str
    codes: tuple[tuple[str, str], ...] = ()
    is_digital_service: bool = False
    notes: str = ""


@dataclass(frozen=True, slots=True)
class TaxNexusView:
    id: str
    entity_code: str
    jurisdiction_code: str
    kind: str
    registration_number: str = ""
    registration_regime: str = ""
    effective_from: float = 0.0
    effective_until: float | None = None
    established_reason: str = ""


@dataclass(frozen=True, slots=True)
class TaxExemptionView:
    id: str
    account_id: str
    jurisdiction_code: str
    kind: str
    certificate_ref: str = ""
    reason: str = ""
    effective_from: float = 0.0
    effective_until: float | None = None
    revoked_at: float | None = None
    evidence_ref: str = ""


@dataclass(frozen=True, slots=True)
class DeclaredTaxRateView:
    id: str
    jurisdiction_code: str
    tax_type: str
    name: str
    rate_basis_points: int
    classification_code: str = ""
    treatment: str = "standard"
    effective_from: float = 0.0
    effective_until: float | None = None
    reference: str = ""
    source_version: str = ""
    retired_at: float | None = None


@dataclass(frozen=True, slots=True)
class RateProvenanceView:
    source: str
    source_code: str = ""
    source_version: str = ""
    reference: str = ""
    effective_from: float = 0.0
    effective_until: float | None = None
    retrieved_at: float = 0.0
    is_sourced: bool = False


@dataclass(frozen=True, slots=True)
class AssessedComponentView:
    name: str
    tax_type: str
    jurisdiction_code: str
    rate_basis_points: int
    taxable_base: MoneyView
    amount: MoneyView
    basis: str
    provenance: RateProvenanceView


@dataclass(frozen=True, slots=True)
class AssessedLineView:
    subject_line_id: str
    classification_code: str
    taxable_base: MoneyView
    tax_total: MoneyView
    treatment: str
    exemption_reason: str = ""
    components: tuple[AssessedComponentView, ...] = ()


@dataclass(frozen=True, slots=True)
class PlaceOfSupplyView:
    origin_code: str
    destination_code: str
    resolved_code: str
    direction: str
    gst_split: str
    rule: str
    is_cross_border: bool = False
    is_digital_service: bool = False
    customer_registered: bool = False


@dataclass(frozen=True, slots=True)
class TaxOverrideView:
    treatment: str
    reason: str
    actor_id: str
    at: float = 0.0
    supersedes_treatment: str = ""
    reference: str = ""


@dataclass(frozen=True, slots=True)
class TaxAssessmentView:
    """A determination and its evidence. Everything a reader needs to answer "why?"."""

    id: str
    account_id: str
    subject_id: str
    subject_kind: str
    state: str
    treatment: str
    regime: str
    currency: str
    version: int
    digest: str
    taxable_base: MoneyView
    tax_total: MoneyView
    place_of_supply: PlaceOfSupplyView
    lines: tuple[AssessedLineView, ...] = ()
    rounding_mode: str = "half_up"
    rounding_scope: str = "per_line"
    nexus_id: str = ""
    nexus_kind: str = "none"
    registration_used: str = ""
    exemption_id: str = ""
    exemption_reason: str = ""
    override: TaxOverrideView | None = None
    withholding_minor: int = 0
    supersedes_assessment_id: str = ""
    is_final: bool = False
    is_sourced: bool = True
    is_explainable: bool = True
    notes: tuple[str, ...] = ()
    assessed_at: float = 0.0
    finalized_at: float | None = None
    allowed_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaxExplanationStepView:
    stage: str
    conclusion: str
    evidence: str = ""
    reference: str = ""


@dataclass(frozen=True, slots=True)
class TaxExplanationView:
    assessment_id: str
    subject_id: str
    answered_without_provider: bool
    steps: tuple[TaxExplanationStepView, ...] = ()


@dataclass(frozen=True, slots=True)
class TaxDiscrepancyView:
    kind: str
    assessment_id: str = ""
    line_id: str = ""
    expected_minor: int = 0
    actual_minor: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TaxReconciliationView:
    subject_id: str
    assessment_id: str
    lines_examined: int
    components_examined: int
    is_clean: bool
    discrepancies: tuple[TaxDiscrepancyView, ...] = ()
    at: float = 0.0


@dataclass(frozen=True, slots=True)
class TaxInspectionView:
    assessment_id: str
    subject_id: str
    state: str
    treatment: str
    version: int
    is_sourced: bool
    is_explainable: bool
    rates_in_window: bool
    has_nexus: bool
    is_overridden: bool
    is_sound: bool
    line_count: int = 0
    component_count: int = 0
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaxDiagnosticView:
    path: str
    code: str
    severity: str
    message: str
    remediation: str = ""


@dataclass(frozen=True, slots=True)
class TaxDiagnosticsView:
    assessment_id: str
    is_valid: bool
    findings: tuple[TaxDiagnosticView, ...] = ()


@dataclass(frozen=True, slots=True)
class TaxActivityView:
    id: str
    action: str
    at: float
    actor_id: str = ""
    account_id: str = ""
    subject_id: str = ""
    assessment_id: str = ""
    jurisdiction_code: str = ""
    amount_minor: int = 0
    currency: str = ""
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TaxSearchView:
    assessments: tuple[TaxAssessmentView, ...] = ()
    total: int = 0


# ── mapping ─────────────────────────────────────────────────────────────────────


def _money(amount: Money) -> MoneyView:
    return MoneyView(minor=amount.minor, currency=amount.currency.value)


def jurisdiction_view(j) -> TaxJurisdictionView:
    return TaxJurisdictionView(
        id=j.id,
        code=j.code,
        display_name=j.display_name,
        country=j.country,
        region=j.region,
        level=j.level.value,
        regime=j.regime.value,
        parent_code=j.parent_code,
        is_active=j.is_active,
        notes=j.notes,
    )


def classification_view(c) -> TaxClassificationView:
    return TaxClassificationView(
        id=c.id,
        code=c.code,
        display_name=c.display_name,
        default_treatment=c.default_treatment.value,
        codes=c.codes,
        is_digital_service=c.is_digital_service,
        notes=c.notes,
    )


def nexus_view(n) -> TaxNexusView:
    return TaxNexusView(
        id=n.id,
        entity_code=n.entity_code,
        jurisdiction_code=n.jurisdiction_code,
        kind=n.kind.value,
        registration_number=n.registration.number if n.registration else "",
        registration_regime=n.registration.regime.value if n.registration else "",
        effective_from=n.effective_from,
        effective_until=n.effective_until,
        established_reason=n.established_reason,
    )


def exemption_view(e) -> TaxExemptionView:
    return TaxExemptionView(
        id=e.id,
        account_id=e.account_id,
        jurisdiction_code=e.jurisdiction_code,
        kind=e.kind.value,
        certificate_ref=e.certificate_ref,
        reason=e.reason,
        effective_from=e.effective_from,
        effective_until=e.effective_until,
        revoked_at=e.revoked_at,
        evidence_ref=e.evidence_ref,
    )


def rate_view(r) -> DeclaredTaxRateView:
    return DeclaredTaxRateView(
        id=r.id,
        jurisdiction_code=r.jurisdiction_code,
        tax_type=r.tax_type,
        name=r.name,
        rate_basis_points=r.rate_basis_points,
        classification_code=r.classification_code,
        treatment=r.treatment.value,
        effective_from=r.effective_from,
        effective_until=r.effective_until,
        reference=r.reference,
        source_version=r.source_version,
        retired_at=r.retired_at,
    )


def place_view(p) -> PlaceOfSupplyView:
    return PlaceOfSupplyView(
        origin_code=p.origin_code,
        destination_code=p.destination_code,
        resolved_code=p.resolved_code,
        direction=p.direction.value,
        gst_split=p.gst_split.value,
        rule=p.rule,
        is_cross_border=p.is_cross_border,
        is_digital_service=p.is_digital_service,
        customer_registered=p.customer_registered,
    )


def _provenance_view(p) -> RateProvenanceView:
    return RateProvenanceView(
        source=p.source.value,
        source_code=p.source_code,
        source_version=p.source_version,
        reference=p.reference,
        effective_from=p.effective_from,
        effective_until=p.effective_until,
        retrieved_at=p.retrieved_at,
        is_sourced=p.is_sourced,
    )


def assessment_view(a: TaxAssessment, allowed: tuple = ()) -> TaxAssessmentView:
    return TaxAssessmentView(
        id=a.id,
        account_id=a.account_id,
        subject_id=a.subject_id,
        subject_kind=a.subject_kind.value,
        state=a.state.value,
        treatment=a.treatment.value,
        regime=a.regime.value,
        currency=a.currency.value,
        version=a.version,
        digest=a.digest,
        taxable_base=_money(a.taxable_base),
        tax_total=_money(a.tax_total),
        place_of_supply=place_view(a.place_of_supply),
        lines=tuple(
            AssessedLineView(
                subject_line_id=line.subject_line_id,
                classification_code=line.classification_code,
                taxable_base=_money(line.taxable_base),
                tax_total=_money(line.tax_total),
                treatment=line.treatment.value,
                exemption_reason=line.exemption_reason,
                components=tuple(
                    AssessedComponentView(
                        name=c.name,
                        tax_type=c.tax_type,
                        jurisdiction_code=c.jurisdiction_code,
                        rate_basis_points=c.rate_basis_points,
                        taxable_base=_money(c.taxable_base),
                        amount=_money(c.amount),
                        basis=c.basis.value,
                        provenance=_provenance_view(c.provenance),
                    )
                    for c in line.components
                ),
            )
            for line in a.lines
        ),
        rounding_mode=a.rounding.mode.value,
        rounding_scope=a.rounding.scope.value,
        nexus_id=a.nexus_id,
        nexus_kind=a.nexus_kind.value,
        registration_used=a.registration_used,
        exemption_id=a.exemption_id,
        exemption_reason=a.exemption_reason,
        override=(
            TaxOverrideView(
                treatment=a.override.treatment.value,
                reason=a.override.reason,
                actor_id=a.override.actor_id,
                at=a.override.at,
                supersedes_treatment=(
                    a.override.supersedes_treatment.value if a.override.supersedes_treatment else ""
                ),
                reference=a.override.reference,
            )
            if a.override is not None
            else None
        ),
        withholding_minor=a.withholding_minor,
        supersedes_assessment_id=a.supersedes_assessment_id,
        is_final=a.is_final,
        is_sourced=a.is_sourced,
        is_explainable=a.is_explainable,
        notes=a.notes,
        assessed_at=a.assessed_at,
        finalized_at=a.finalized_at,
        allowed_triggers=tuple(t.value for t in allowed),
    )


def tax_explanation_view(e) -> TaxExplanationView:
    return TaxExplanationView(
        assessment_id=e.assessment_id,
        subject_id=e.subject_id,
        answered_without_provider=e.answered_without_provider,
        steps=tuple(
            TaxExplanationStepView(
                stage=s.stage, conclusion=s.conclusion, evidence=s.evidence, reference=s.reference
            )
            for s in e.steps
        ),
    )


def reconciliation_view(r) -> TaxReconciliationView:
    return TaxReconciliationView(
        subject_id=r.subject_id,
        assessment_id=r.assessment_id,
        lines_examined=r.lines_examined,
        components_examined=r.components_examined,
        is_clean=r.is_clean,
        discrepancies=tuple(
            TaxDiscrepancyView(
                kind=d.kind.value,
                assessment_id=d.assessment_id,
                line_id=d.line_id,
                expected_minor=d.expected_minor,
                actual_minor=d.actual_minor,
                detail=d.detail,
            )
            for d in r.discrepancies
        ),
        at=r.at,
    )


def inspection_view(i) -> TaxInspectionView:
    return TaxInspectionView(
        assessment_id=i.assessment_id,
        subject_id=i.subject_id,
        state=i.state.value,
        treatment=i.treatment.value,
        version=i.version,
        is_sourced=i.is_sourced,
        is_explainable=i.is_explainable,
        rates_in_window=i.rates_in_window,
        has_nexus=i.has_nexus,
        is_overridden=i.is_overridden,
        is_sound=i.is_sound,
        line_count=i.line_count,
        component_count=i.component_count,
        notes=i.notes,
    )


def activity_view(a) -> TaxActivityView:
    return TaxActivityView(
        id=a.id,
        action=a.action.value,
        at=a.at,
        actor_id=a.actor_id,
        account_id=a.account_id,
        subject_id=a.subject_id,
        assessment_id=a.assessment_id,
        jurisdiction_code=a.jurisdiction_code,
        amount_minor=a.amount_minor,
        currency=a.currency.value if a.currency else "",
        reason=a.reason,
        detail=a.detail,
    )


def diagnostics_view(assessment_id: str, diagnostics) -> TaxDiagnosticsView:
    return TaxDiagnosticsView(
        assessment_id=assessment_id,
        is_valid=not diagnostics.has_errors,
        findings=tuple(
            TaxDiagnosticView(
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
class TaxRepositories:
    """The tax seams, injected. Interfaces only — implementations in persistence.

    `determinations` is M9.2's `TaxDeterminationRepository`, wired here because it is **Tax's own
    seam**: M9.2 declared the contract and left it for this milestone. Holding it here rather than
    reaching through the shared context is what lets the "writes only to its own repositories"
    assertion stay a simple structural check."""

    jurisdictions: object
    classifications: object
    nexus: object
    exemptions: object
    rates: object
    assessments: object
    activity: object
    determinations: object


class _TaxSupport:
    """Shared read helpers for the command and query sides.

    Both sides need to build the same determination request — one to record it, one to preview it —
    and assembling it twice is how a preview and a determination come to disagree."""

    def __init__(self, *, context, tax_repos: TaxRepositories, determination, rounding) -> None:
        self._ctx = context
        self._repos = tax_repos
        self._determination = determination
        self._rounding_service = rounding

    # ── the document being taxed (read-only) ────────────────────────────────────

    def document_snapshot(self, org_id: str, document_id: str):
        """The M9.14 document whose amounts are being taxed.

        Read forward-only: the lines and their amounts came from the Ledger through the Invoice, and
        Tax reads them as given. Going back to the ledger — let alone to rating or pricing — would
        make Tax a second source of a figure another domain owns."""
        engine = getattr(self._ctx, "invoice_engine", None)
        documents = getattr(engine, "documents", None)
        snapshots = getattr(engine, "snapshots", None)
        if documents is None or snapshots is None:
            raise _NotFound("the invoice engine is not wired; there is no document to assess")
        document = documents.get(org_id, document_id)
        if document is None:
            raise _NotFound(f"document {document_id!r} not found")
        snapshot = (
            snapshots.by_digest(org_id, document.issued_snapshot_digest)
            if document.issued_snapshot_digest
            else snapshots.latest(org_id, document.id)
        )
        if snapshot is None:
            raise _NotFound(f"document {document_id!r} has no content to assess")
        return document, snapshot

    @staticmethod
    def taxable_lines(snapshot, default_code: str, overrides: tuple) -> tuple:
        """The lines a determination taxes.

        **Tax lines are excluded**: a document's existing tax lines are the *output* of a previous
        determination, and taxing them would compound tax on tax on every re-assessment. Credit
        lines are excluded for the same reason a credit is not a supply."""
        from billing.foundation import ChargeLineKind

        by_line = dict(overrides)
        return tuple(
            (line.id, by_line.get(line.id, default_code), line.amount.minor)
            for line in snapshot.lines
            if line.kind not in (ChargeLineKind.TAX, ChargeLineKind.CREDIT)
            and not line.amount.is_zero
        )

    # ── the customer (read-only) ────────────────────────────────────────────────

    def customer_residency(self, org_id: str, account_id: str) -> tuple[TaxResidency, str]:
        """Where the customer is, and what registration they hold — read from M9.8 and M9.2's tax
        profile, never invented here."""
        from billing.foundation import AddressKind, BillingAddress

        accounts = getattr(self._ctx.commercial_accounts, "accounts", None)
        account = accounts.get(org_id, account_id) if accounts is not None else None
        address = BillingAddress()
        registration = ""
        evidence: tuple[str, ...] = ()
        if account is not None:
            entry = account.addresses.primary(AddressKind.BILLING)
            if entry is not None:
                address = entry.address
                evidence = evidence + ("billing_address",)
            registration = account.legal_entity.tax_identifier
        profile = self._tax_profile(org_id, account_id)
        if profile is not None:
            registration = registration or self._profile_registration(profile)
            if profile.country:
                evidence = evidence + ("tax_profile",)
                address = replace(address, country=address.country or profile.country)
        return (
            TaxResidency(
                country=address.country,
                region=profile.place_of_supply if profile else address.region,
                address=address,
                evidence=evidence,
            ),
            registration,
        )

    def _tax_profile(self, org_id: str, account_id: str):
        profiles = getattr(self._ctx.repositories, "tax_profiles", None)
        return profiles.for_account(org_id, account_id) if profiles is not None else None

    @staticmethod
    def _profile_registration(profile) -> str:
        return next((r.number for r in profile.registrations if r.number), "")

    # ── the request ─────────────────────────────────────────────────────────────

    def build_request(
        self,
        *,
        org_id: str,
        account_id: str,
        subject_id: str,
        subject_kind: SubjectKind,
        currency: Currency,
        entity_code: str,
        origin_country: str,
        origin_region: str,
        supplier_registration: str,
        lines: tuple,
        regime: TaxRegime,
        taxpayer_kind: TaxpayerKind = TaxpayerKind.BUSINESS,
        prices_include_tax: bool = False,
        rounding: TaxRoundingPolicy | None = None,
        effective_at: float = 0.0,
        override=None,
    ) -> DeterminationRequest:
        destination, customer_registration = self.customer_residency(org_id, account_id)
        basis = TaxBasisInputs(
            org_id=org_id,
            account_id=account_id,
            entity_code=entity_code,
            subject_id=subject_id,
            subject_kind=subject_kind,
            currency=currency,
            origin=TaxResidency(
                country=origin_country,
                region=origin_region,
                evidence=("supplier_entity",) if origin_country else (),
            ),
            destination=destination,
            regime=regime,
            taxpayer_kind=taxpayer_kind,
            customer_registration=customer_registration,
            supplier_registration=supplier_registration,
            prices_include_tax=prices_include_tax,
            effective_at=effective_at,
            lines=lines,
        )
        return DeterminationRequest(
            basis=basis,
            jurisdictions=self._repos.jurisdictions.all(org_id),
            classifications=self._classifications(org_id, lines),
            nexus=self._repos.nexus.for_entity(org_id, entity_code),
            exemptions=self._repos.exemptions.for_account(org_id, account_id),
            rounding=rounding or TaxRoundingPolicy(),
            override=override,
        )

    def _classifications(self, org_id: str, lines: tuple) -> tuple:
        wanted = {code for _, code, _ in lines if code}
        declared = self._repos.classifications.all(org_id)
        matching = tuple(c for c in declared if c.code in wanted)
        return matching or declared[:1]

    def provider(self, org_id: str, jurisdiction_code: str) -> DeclaredRateTaxProvider:
        """The rate source for this determination.

        Built from the org's **declared rate evidence**, which is the shipped adapter. An external
        tax engine is a different `TaxRateProvider` injected in its place; neither becomes the owner
        of Billing's tax truth, because what is stored is the evidence a provider returned and not
        the provider's opinion of today (ADR-0023 TX1)."""
        rates = (
            self._repos.rates.for_jurisdiction(org_id, jurisdiction_code)
            if jurisdiction_code
            else self._repos.rates.all(org_id)
        )
        return DeclaredRateTaxProvider(rates)

    def resolved_jurisdiction(self, request: DeterminationRequest) -> str:
        return self._determination._places.resolve(request.basis).resolved_code


class TaxCommandUseCases(_TaxSupport):
    """Orchestration for the Tax Determination Engine. Coordinates; decides nothing.

    Not one method here resolves a place of supply, decides a treatment, chooses a rate, computes a
    tax, or rounds one. Those are `PlaceOfSupplyResolver`, `TreatmentResolver`, `TaxRateProvider`,
    `TaxCalculator`, and `TaxRoundingService` — and a test greps this module to keep it that way."""

    def __init__(
        self,
        *,
        context,
        tax_repos: TaxRepositories,
        factory,
        determination,
        lifecycle,
        rounding,
        validator,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        super().__init__(
            context=context,
            tax_repos=tax_repos,
            determination=determination,
            rounding=rounding,
        )
        self._factory = factory
        self._lifecycle = lifecycle
        self._validator = validator
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── configuration ───────────────────────────────────────────────────────────

    def declare_jurisdiction(self, command: DeclareTaxJurisdiction) -> TaxJurisdictionView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            code = jurisdiction_key(command.country, command.region)
            existing = self._repos.jurisdictions.by_code(command.org_id, code)
            if existing is not None:
                # Idempotent: a duplicate jurisdiction is a duplicate nexus, and a duplicate nexus
                # is a supplier registered twice in the same place.
                return jurisdiction_view(existing)
            stored = self._repos.jurisdictions.add(
                self._factory.jurisdiction(
                    org_id=command.org_id,
                    country=command.country,
                    region=command.region,
                    display_name=command.display_name,
                    level=command.level,
                    regime=command.regime,
                    parent_code=command.parent_code,
                    notes=command.notes,
                    actor_id=command.actor_id,
                )
            )
            self._record(command, TaxAction.JURISDICTION_DECLARED, jurisdiction_code=stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code))
            return jurisdiction_view(stored)

    def retire_jurisdiction(self, command: RetireTaxJurisdiction) -> TaxJurisdictionView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            found = self._repos.jurisdictions.get(command.org_id, command.jurisdiction_id)
            if found is None:
                raise _NotFound(f"jurisdiction {command.jurisdiction_id!r} not found")
            stored = self._repos.jurisdictions.replace(
                self._factory.retire(found, actor_id=command.actor_id, reason=command.reason)
            )
            self._factory.emit_retirement(
                _event_type("TAX_JURISDICTION_RETIRED"),
                stored,
                actor_id=command.actor_id,
                reason=command.reason,
            )
            self._record(
                command,
                TaxAction.JURISDICTION_RETIRED,
                jurisdiction_code=stored.code,
                reason=command.reason,
            )
            uow.stage(self._event(command, stored.id))
            return jurisdiction_view(stored)

    def declare_classification(self, command: DeclareTaxClassification) -> TaxClassificationView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            existing = self._repos.classifications.by_code(command.org_id, command.code)
            if existing is not None:
                return classification_view(existing)
            stored = self._repos.classifications.add(
                self._factory.classification(
                    org_id=command.org_id,
                    code=command.code,
                    display_name=command.display_name,
                    default_treatment=command.default_treatment,
                    codes=command.codes,
                    is_digital_service=command.is_digital_service,
                    notes=command.notes,
                    actor_id=command.actor_id,
                )
            )
            self._record(command, TaxAction.CLASSIFICATION_DECLARED, detail=stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code))
            return classification_view(stored)

    def register_nexus(self, command: RegisterTaxNexus) -> TaxNexusView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            registration = (
                TaxRegistration(
                    regime=command.registration_regime,
                    number=command.registration_number,
                    country=command.registration_country,
                )
                if command.registration_number
                else None
            )
            stored = self._repos.nexus.add(
                self._factory.nexus(
                    org_id=command.org_id,
                    entity_code=command.entity_code,
                    jurisdiction_code=command.jurisdiction_code,
                    kind=command.kind,
                    registration=registration,
                    effective_from=command.effective_from,
                    effective_until=command.effective_until,
                    established_reason=command.established_reason,
                    actor_id=command.actor_id,
                )
            )
            self._record(
                command,
                TaxAction.NEXUS_REGISTERED,
                jurisdiction_code=stored.jurisdiction_code,
                detail=stored.entity_code,
            )
            uow.stage(self._event(command, stored.id))
            return nexus_view(stored)

    def end_nexus(self, command: EndTaxNexus) -> TaxNexusView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            found = self._repos.nexus.get(command.org_id, command.nexus_id)
            if found is None:
                raise _NotFound(f"nexus {command.nexus_id!r} not found")
            stored = self._repos.nexus.replace(
                self._factory.retire(found, at=self._clock.now(), reason=command.reason)
            )
            self._factory.emit_retirement(
                _event_type("TAX_NEXUS_ENDED"),
                stored,
                actor_id=command.actor_id,
                reason=command.reason,
            )
            self._record(
                command,
                TaxAction.NEXUS_ENDED,
                jurisdiction_code=stored.jurisdiction_code,
                reason=command.reason,
            )
            uow.stage(self._event(command, stored.id))
            return nexus_view(stored)

    def record_exemption(self, command: RecordTaxExemption) -> TaxExemptionView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            stored = self._repos.exemptions.add(
                self._factory.exemption(
                    org_id=command.org_id,
                    account_id=command.account_id,
                    jurisdiction_code=command.jurisdiction_code,
                    kind=command.kind,
                    certificate_ref=command.certificate_ref,
                    reason=command.reason,
                    effective_from=command.effective_from,
                    effective_until=command.effective_until,
                    evidence_ref=command.evidence_ref,
                    actor_id=command.actor_id,
                )
            )
            self._record(
                command,
                TaxAction.EXEMPTION_RECORDED,
                account_id=stored.account_id,
                jurisdiction_code=stored.jurisdiction_code,
            )
            uow.stage(self._event(command, stored.id))
            return exemption_view(stored)

    def revoke_exemption(self, command: RevokeTaxExemption) -> TaxExemptionView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            found = self._repos.exemptions.get(command.org_id, command.exemption_id)
            if found is None:
                raise _NotFound(f"exemption {command.exemption_id!r} not found")
            stored = self._repos.exemptions.replace(
                self._factory.retire(found, at=self._clock.now(), reason=command.reason)
            )
            self._factory.emit_retirement(
                _event_type("TAX_EXEMPTION_REVOKED"),
                stored,
                actor_id=command.actor_id,
                reason=command.reason,
            )
            self._record(
                command,
                TaxAction.EXEMPTION_REVOKED,
                account_id=stored.account_id,
                reason=command.reason,
            )
            uow.stage(self._event(command, stored.id))
            return exemption_view(stored)

    def declare_rate(self, command: DeclareTaxRate) -> DeclaredTaxRateView:
        """Declare a rate as **evidence**, with the authority that set it. Append-only."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            stored = self._repos.rates.append(
                self._factory.rate(
                    org_id=command.org_id,
                    jurisdiction_code=command.jurisdiction_code,
                    tax_type=command.tax_type,
                    name=command.name,
                    rate_basis_points=command.rate_basis_points,
                    classification_code=command.classification_code,
                    treatment=command.treatment,
                    effective_from=command.effective_from,
                    effective_until=command.effective_until,
                    reference=command.reference,
                    source_version=command.source_version,
                    actor_id=command.actor_id,
                )
            )
            self._record(
                command,
                TaxAction.RATE_DECLARED,
                jurisdiction_code=stored.jurisdiction_code,
                detail=f"{stored.name} {stored.rate_basis_points}bp",
            )
            uow.stage(self._event(command, stored.id))
            return rate_view(stored)

    def retire_rate(self, command: RetireTaxRate) -> DeclaredTaxRateView:
        """Set a retirement timestamp. The rate row is never amended or removed: an issued invoice
        must stay explainable against the rate in force when it was issued."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            found = self._repos.rates.get(command.org_id, command.rate_id)
            if found is None:
                raise _NotFound(f"tax rate {command.rate_id!r} not found")
            stored = self._repos.rates.retire(
                self._factory.retire(found, at=self._clock.now(), reason=command.reason)
            )
            self._factory.emit_retirement(
                _event_type("TAX_RATE_RETIRED"),
                stored,
                actor_id=command.actor_id,
                reason=command.reason,
            )
            self._record(
                command,
                TaxAction.RATE_RETIRED,
                jurisdiction_code=stored.jurisdiction_code,
                reason=command.reason,
            )
            uow.stage(self._event(command, stored.id))
            return rate_view(stored)

    # ── determination ───────────────────────────────────────────────────────────

    def assess(self, command: AssessDocumentTax) -> TaxAssessmentView:
        """The main path: a document's amounts acquire a tax treatment.

        Idempotent on the basis digest — assessing the same facts twice is one assessment, and the
        second call emits `assessment_unchanged` rather than `determined`, because "we determined
        this again and reached the same conclusion" is the evidence determination is deterministic.
        """
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            document, snapshot = self.document_snapshot(command.org_id, command.document_id)
            request = self.build_request(
                org_id=command.org_id,
                account_id=document.account_id,
                subject_id=document.id,
                subject_kind=_subject_kind(document.kind),
                currency=document.currency,
                entity_code=command.entity_code or document.entity_code,
                origin_country=command.origin_country,
                origin_region=command.origin_region,
                supplier_registration=command.supplier_registration,
                lines=self.taxable_lines(
                    snapshot,
                    command.default_classification_code,
                    command.line_classifications,
                ),
                regime=command.regime,
                taxpayer_kind=command.taxpayer_kind,
                prices_include_tax=command.prices_include_tax,
                rounding=TaxRoundingPolicy(
                    mode=command.rounding_mode, scope=command.rounding_scope
                ),
                effective_at=(
                    command.effective_at if command.effective_at is not None else self._clock.now()
                ),
            )
            previous = self._repos.assessments.current_for_subject(command.org_id, document.id)
            self._guard_replaceable(previous)
            assessed = self._determination.determine(
                request,
                self.provider(command.org_id, self.resolved_jurisdiction(request)),
                assessed_at=self._clock.now(),
                assessed_by=command.actor_id,
                version=(previous.version + 1) if previous else 1,
                supersedes=previous.id if previous and previous.id else "",
            )
            existing = self._repos.assessments.get(command.org_id, assessed.id)
            stored = existing or self._repos.assessments.append(assessed)
            unchanged = existing is not None
            if previous is not None and not unchanged and previous.id != stored.id:
                self._repos.assessments.mark_state(
                    self._lifecycle.apply(
                        previous,
                        AssessmentTrigger.SUPERSEDE,
                        at=self._clock.now(),
                        actor_id=command.actor_id,
                    )
                )
            self._factory.emit_assessment(stored, unchanged=unchanged, actor_id=command.actor_id)
            self._record(
                command,
                TaxAction.ASSESSMENT_UNCHANGED if unchanged else TaxAction.ASSESSED,
                account_id=stored.account_id,
                subject_id=stored.subject_id,
                assessment_id=stored.id,
                jurisdiction_code=stored.place_of_supply.resolved_code,
                amount=stored.tax_total,
            )
            uow.stage(self._event(command, stored.id, unchanged=unchanged))
            return assessment_view(stored, self._lifecycle.allowed(stored.state))

    def finalize(self, command: FinalizeTaxAssessment) -> TaxAssessmentView:
        """Seal a determination and publish its projection.

        The projection onto M9.2's `TaxDetermination` is written here and only here, from
        `to_determination()` — the single derivation — so the record M9.5 and M9.14 read stays a
        view of one truth rather than a second copy of it."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVOICE_ISSUE)
        with self._uow.new() as uow:
            assessment = self._assessment(command.org_id, command.assessment_id)
            self._fail_on_errors(self._validator.validate_finalize(assessment))
            moved = self._lifecycle.apply(
                assessment,
                AssessmentTrigger.FINALIZE,
                at=self._clock.now(),
                actor_id=command.actor_id,
            )
            stored = self._repos.assessments.mark_state(moved)
            self._repos.determinations.append(stored.to_determination())
            self._factory.emit_transition(
                stored, actor_id=command.actor_id, tax_total_minor=stored.tax_total.minor
            )
            self._record(
                command,
                TaxAction.ASSESSMENT_FINALIZED,
                account_id=stored.account_id,
                subject_id=stored.subject_id,
                assessment_id=stored.id,
                amount=stored.tax_total,
            )
            uow.stage(self._event(command, stored.id, digest=stored.digest))
            return assessment_view(stored, self._lifecycle.allowed(stored.state))

    def override(self, command: OverrideTaxTreatment) -> TaxAssessmentView:
        """Re-determine under an explicit, attributed human decision.

        Not an edit: the override travels into a **new** determination which supersedes the
        previous one, and the original conclusion is kept on the override itself. Both remain
        readable, which is what an audit needs."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            previous = self._assessment(command.org_id, command.assessment_id)
            self._guard_replaceable(previous)
            override = self._factory.override(
                previous,
                treatment=command.treatment,
                reason=command.reason,
                actor_id=command.actor_id,
                reference=command.reference,
            )
            request = DeterminationRequest(
                basis=previous.basis,
                jurisdictions=self._repos.jurisdictions.all(command.org_id),
                classifications=self._repos.classifications.all(command.org_id),
                nexus=self._repos.nexus.for_entity(command.org_id, previous.basis.entity_code),
                exemptions=self._repos.exemptions.for_account(command.org_id, previous.account_id),
                rounding=previous.rounding,
                override=override,
            )
            redetermined = self._determination.determine(
                request,
                self.provider(command.org_id, previous.place_of_supply.resolved_code),
                assessed_at=self._clock.now(),
                assessed_by=command.actor_id,
                version=previous.version + 1,
                supersedes=previous.id,
            )
            # The basis is unchanged, so the derived id is too — the override is carried on a
            # distinct record by version rather than by identity.
            stored = self._repos.assessments.append(
                replace(redetermined, id=f"{redetermined.id}-v{previous.version + 1}")
            )
            self._repos.assessments.mark_state(
                self._lifecycle.apply(
                    previous,
                    AssessmentTrigger.SUPERSEDE,
                    at=self._clock.now(),
                    actor_id=command.actor_id,
                )
            )
            self._factory.emit_override(stored, actor_id=command.actor_id)
            self._record(
                command,
                TaxAction.TREATMENT_OVERRIDDEN,
                account_id=stored.account_id,
                subject_id=stored.subject_id,
                assessment_id=stored.id,
                reason=command.reason,
            )
            uow.stage(self._event(command, stored.id, treatment=stored.treatment.value))
            return assessment_view(stored, self._lifecycle.allowed(stored.state))

    def discard(self, command: DiscardTaxAssessment) -> TaxAssessmentView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            assessment = self._assessment(command.org_id, command.assessment_id)
            stored = self._repos.assessments.mark_state(
                self._lifecycle.apply(
                    assessment,
                    AssessmentTrigger.DISCARD,
                    at=self._clock.now(),
                    actor_id=command.actor_id,
                )
            )
            self._factory.emit_transition(stored, actor_id=command.actor_id)
            self._record(
                command,
                TaxAction.ASSESSMENT_DISCARDED,
                subject_id=stored.subject_id,
                assessment_id=stored.id,
                reason=command.reason,
            )
            uow.stage(self._event(command, stored.id))
            return assessment_view(stored, self._lifecycle.allowed(stored.state))

    # ── internals ───────────────────────────────────────────────────────────────

    def _guard_replaceable(self, previous) -> None:
        """A finalized determination is never re-determined in place.

        Belt and braces beside the FSM: the machine has no edge out of `final`, and this refuses the
        *intent* with the message a caller trying to fix a filed figure needs to read."""
        if previous is not None and previous.is_final:
            from billing.foundation import BillingConflict

            raise BillingConflict(
                f"tax assessment {previous.id} is final and was applied to a document; a filed "
                "determination is never re-determined in place — issue a correction document and "
                "assess that"
            )

    def _assessment(self, org_id: str, assessment_id: str) -> TaxAssessment:
        found = self._repos.assessments.get(org_id, assessment_id)
        if found is None:
            raise _NotFound(f"tax assessment {assessment_id!r} not found")
        return found

    def _fail_on_errors(self, diagnostics) -> None:
        if diagnostics.has_errors:
            from billing.foundation import BillingValidationError

            raise BillingValidationError(diagnostics)

    def _record(
        self,
        command,
        action: TaxAction,
        *,
        account_id: str = "",
        subject_id: str = "",
        assessment_id: str = "",
        jurisdiction_code: str = "",
        amount: Money | None = None,
        reason: str = "",
        detail: str = "",
    ) -> None:
        """Append the tax audit trail **inside the caller's transaction**, so the trail and the fact
        it describes commit or vanish together."""
        self._repos.activity.append(
            self._factory.activity(
                org_id=command.org_id,
                action=action,
                actor_id=command.actor_id,
                account_id=account_id,
                subject_id=subject_id,
                assessment_id=assessment_id,
                jurisdiction_code=jurisdiction_code,
                amount_minor=amount.minor if amount else 0,
                currency=amount.currency if amount else None,
                reason=reason,
                detail=detail,
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.TAX_PROFILE_SYNCHRONIZED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


def _subject_kind(document_kind) -> SubjectKind:
    return {
        "invoice": SubjectKind.INVOICE,
        "credit_note": SubjectKind.CREDIT_NOTE,
        "debit_note": SubjectKind.DEBIT_NOTE,
    }.get(getattr(document_kind, "value", str(document_kind)), SubjectKind.INVOICE)


def _event_type(name: str):
    from billing.foundation import BillingEventType

    return getattr(BillingEventType, name)


class TaxQueryUseCases(_TaxSupport):
    """Reads. No transaction, no mutation, no arithmetic — every figure comes from a stored
    assessment or a Foundation service."""

    def __init__(
        self,
        *,
        context,
        tax_repos: TaxRepositories,
        determination,
        lifecycle,
        rounding,
        explainer,
        inspector,
        reconciliation,
        search,
        validator,
        authz: AuthorizationOrchestrator,
        clock,
    ) -> None:
        super().__init__(
            context=context,
            tax_repos=tax_repos,
            determination=determination,
            rounding=rounding,
        )
        self._lifecycle = lifecycle
        self._explainer = explainer
        self._inspector = inspector
        self._reconciliation = reconciliation
        self._search = search
        self._validator = validator
        self._authz = authz
        self._clock = clock

    def get(self, query: GetTaxAssessment) -> TaxAssessmentView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._assessment(query.org_id, query.assessment_id)
        return assessment_view(found, self._lifecycle.allowed(found.state))

    def for_subject(self, query: GetSubjectTaxAssessment) -> TaxAssessmentView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.assessments.current_for_subject(query.org_id, query.subject_id)
        if found is None:
            raise _NotFound(f"no tax assessment for subject {query.subject_id!r}")
        return assessment_view(found, self._lifecycle.allowed(found.state))

    def list(self, query: ListTaxAssessments) -> tuple[TaxAssessmentView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        if query.subject_id:
            found = self._repos.assessments.for_subject(query.org_id, query.subject_id)
        elif query.account_id:
            found = self._repos.assessments.for_account(query.org_id, query.account_id, query.limit)
        else:
            found = self._repos.assessments.all(query.org_id, query.limit)
        return tuple(assessment_view(a, self._lifecycle.allowed(a.state)) for a in found)

    def explain(self, query: ExplainTaxAssessment) -> TaxExplanationView:
        """Answers "why was this tax applied?" from the assessment alone — no provider call."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tax_explanation_view(
            self._explainer.explain(self._assessment(query.org_id, query.assessment_id))
        )

    def preview(self, query: PreviewTaxAssessment) -> TaxAssessmentView:
        """What a determination *would* conclude. Persists nothing and finalizes nothing."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        document, snapshot = self.document_snapshot(query.org_id, query.document_id)
        request = self.build_request(
            org_id=query.org_id,
            account_id=document.account_id,
            subject_id=document.id,
            subject_kind=_subject_kind(document.kind),
            currency=document.currency,
            entity_code=query.entity_code or document.entity_code,
            origin_country=query.origin_country,
            origin_region=query.origin_region,
            supplier_registration="",
            lines=self.taxable_lines(snapshot, query.default_classification_code, ()),
            regime=query.regime,
            prices_include_tax=query.prices_include_tax,
            effective_at=self._clock.now(),
        )
        return assessment_view(
            self._determination.determine(
                request,
                self.provider(query.org_id, self.resolved_jurisdiction(request)),
                assessed_at=self._clock.now(),
            )
        )

    def place_of_supply(self, query: ResolveTaxPlaceOfSupply) -> PlaceOfSupplyView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        basis = TaxBasisInputs(
            org_id=query.org_id,
            account_id="",
            entity_code="",
            subject_id="",
            subject_kind=SubjectKind.PREVIEW,
            regime=query.regime,
            taxpayer_kind=query.taxpayer_kind,
            customer_registration=query.customer_registration,
            origin=TaxResidency(country=query.origin_country, region=query.origin_region),
            destination=TaxResidency(
                country=query.destination_country, region=query.destination_region
            ),
        )
        return place_view(self._determination._places.resolve(basis))

    def jurisdictions(self, query: ListTaxJurisdictions) -> tuple[TaxJurisdictionView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            jurisdiction_view(j)
            for j in self._repos.jurisdictions.all(query.org_id, query.include_retired)
        )

    def classifications(self, query: ListTaxClassifications) -> tuple[TaxClassificationView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(classification_view(c) for c in self._repos.classifications.all(query.org_id))

    def nexus(self, query: ListTaxNexus) -> tuple[TaxNexusView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = (
            self._repos.nexus.for_entity(query.org_id, query.entity_code)
            if query.entity_code
            else self._repos.nexus.all(query.org_id)
        )
        return tuple(nexus_view(n) for n in found)

    def exemptions(self, query: ListTaxExemptions) -> tuple[TaxExemptionView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = (
            self._repos.exemptions.for_account(query.org_id, query.account_id)
            if query.account_id
            else self._repos.exemptions.all(query.org_id)
        )
        return tuple(exemption_view(e) for e in found)

    def rates(self, query: ListDeclaredTaxRates) -> tuple[DeclaredTaxRateView, ...]:
        """Every rate ever declared, live or not. An audit reads the retired ones too."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = (
            self._repos.rates.for_jurisdiction(query.org_id, query.jurisdiction_code)
            if query.jurisdiction_code
            else self._repos.rates.all(query.org_id, query.limit)
        )
        return tuple(rate_view(r) for r in found)

    def reconcile(self, query: ReconcileTaxAssessment) -> TaxReconciliationView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        assessment = self._assessment(query.org_id, query.assessment_id)
        subject_lines: tuple = ()
        try:
            _, snapshot = self.document_snapshot(query.org_id, assessment.subject_id)
            subject_lines = tuple(
                (line_id, base) for line_id, _, base in self.taxable_lines(snapshot, "", ())
            )
        except _NotFound:
            subject_lines = ()
        return reconciliation_view(
            self._reconciliation.reconcile(
                assessment,
                org_id=query.org_id,
                subject_id=assessment.subject_id,
                subject_lines=subject_lines,
                at=self._clock.now(),
            )
        )

    def inspect(self, query: InspectTaxAssessment) -> TaxInspectionView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return inspection_view(
            self._inspector.inspect(self._assessment(query.org_id, query.assessment_id))
        )

    def diagnose(self, query: DiagnoseTaxAssessment) -> TaxDiagnosticsView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        assessment = self._assessment(query.org_id, query.assessment_id)
        return diagnostics_view(assessment.id, self._validator.validate_assessment(assessment))

    def search(self, query: SearchTaxAssessments) -> TaxSearchView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._search.search(self._repos.assessments.all(query.org_id, 2000), query.query)
        return TaxSearchView(
            assessments=tuple(
                assessment_view(a, self._lifecycle.allowed(a.state)) for a in found.assessments
            ),
            total=found.total,
        )

    def timeline(self, query: TaxTimeline) -> tuple[TaxActivityView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = (
            self._repos.activity.for_subject(query.org_id, query.subject_id)
            if query.subject_id
            else self._repos.activity.history(query.org_id, query.account_id, query.limit)
        )
        return tuple(activity_view(a) for a in found)

    def _assessment(self, org_id: str, assessment_id: str) -> TaxAssessment:
        found = self._repos.assessments.get(org_id, assessment_id)
        if found is None:
            raise _NotFound(f"tax assessment {assessment_id!r} not found")
        return found


# ── wiring table ────────────────────────────────────────────────────────────────


def register_tax_command_handlers(bus, uc: TaxCommandUseCases) -> None:
    bus.register(DeclareTaxJurisdiction, uc.declare_jurisdiction)
    bus.register(RetireTaxJurisdiction, uc.retire_jurisdiction)
    bus.register(DeclareTaxClassification, uc.declare_classification)
    bus.register(RegisterTaxNexus, uc.register_nexus)
    bus.register(EndTaxNexus, uc.end_nexus)
    bus.register(RecordTaxExemption, uc.record_exemption)
    bus.register(RevokeTaxExemption, uc.revoke_exemption)
    bus.register(DeclareTaxRate, uc.declare_rate)
    bus.register(RetireTaxRate, uc.retire_rate)
    bus.register(AssessDocumentTax, uc.assess)
    bus.register(FinalizeTaxAssessment, uc.finalize)
    bus.register(OverrideTaxTreatment, uc.override)
    bus.register(DiscardTaxAssessment, uc.discard)


def register_tax_query_handlers(bus, uc: TaxQueryUseCases) -> None:
    bus.register(GetTaxAssessment, uc.get)
    bus.register(GetSubjectTaxAssessment, uc.for_subject)
    bus.register(ListTaxAssessments, uc.list)
    bus.register(ExplainTaxAssessment, uc.explain)
    bus.register(PreviewTaxAssessment, uc.preview)
    bus.register(ResolveTaxPlaceOfSupply, uc.place_of_supply)
    bus.register(ListTaxJurisdictions, uc.jurisdictions)
    bus.register(ListTaxClassifications, uc.classifications)
    bus.register(ListTaxNexus, uc.nexus)
    bus.register(ListTaxExemptions, uc.exemptions)
    bus.register(ListDeclaredTaxRates, uc.rates)
    bus.register(ReconcileTaxAssessment, uc.reconcile)
    bus.register(InspectTaxAssessment, uc.inspect)
    bus.register(DiagnoseTaxAssessment, uc.diagnose)
    bus.register(SearchTaxAssessments, uc.search)
    bus.register(TaxTimeline, uc.timeline)


__all__ = [
    "ALL_TAX_COMMANDS",
    "ALL_TAX_QUERIES",
    "AssessDocumentTax",
    "AssessedComponentView",
    "AssessedLineView",
    "DeclareTaxClassification",
    "DeclareTaxJurisdiction",
    "DeclareTaxRate",
    "DeclaredTaxRateView",
    "DiagnoseTaxAssessment",
    "DiscardTaxAssessment",
    "EndTaxNexus",
    "ExplainTaxAssessment",
    "FinalizeTaxAssessment",
    "GetSubjectTaxAssessment",
    "GetTaxAssessment",
    "InspectTaxAssessment",
    "ListDeclaredTaxRates",
    "ListTaxAssessments",
    "ListTaxClassifications",
    "ListTaxExemptions",
    "ListTaxJurisdictions",
    "ListTaxNexus",
    "OverrideTaxTreatment",
    "PlaceOfSupplyView",
    "PreviewTaxAssessment",
    "RateProvenanceView",
    "ReconcileTaxAssessment",
    "RecordTaxExemption",
    "RegisterTaxNexus",
    "RetireTaxJurisdiction",
    "RetireTaxRate",
    "RevokeTaxExemption",
    "ResolveTaxPlaceOfSupply",
    "SearchTaxAssessments",
    "TaxActivityView",
    "TaxAssessmentView",
    "TaxClassificationView",
    "TaxCommandUseCases",
    "TaxDiagnosticView",
    "TaxDiagnosticsView",
    "TaxDiscrepancyView",
    "TaxExemptionView",
    "TaxExplanationStepView",
    "TaxExplanationView",
    "TaxInspectionView",
    "TaxJurisdictionView",
    "TaxNexusView",
    "TaxOverrideView",
    "TaxQueryUseCases",
    "TaxReconciliationView",
    "TaxRepositories",
    "TaxSearchView",
    "TaxTimeline",
    "assessment_view",
    "tax_explanation_view",
    "register_tax_command_handlers",
    "register_tax_query_handlers",
]

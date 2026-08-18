"""Tax Determination routes (M9.15) — transport only.

Additive to the M9.5–M9.14 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No tax rule is evaluated here
— where a supply happens, whether there is an obligation, what treatment applies, which rate is in
force, how the tax is computed and rounded are all Foundation behaviour reached through the
Application.

Mounted at **`/taxation`**, not under M9.5's `/taxes`. Same reason every domain since M9.8 took its
own prefix: siblings under an existing wildcard would be shadowed depending on router registration
order, and the M9.2 tax profile and determination still answer on the old paths — the determination
being the record this milestone *projects onto*.

**Six things no endpoint here does.** None computes a tax — every figure came from a Foundation
service. **None contains a rate**: there is no percentage, no default, and no fallback anywhere in
this file, and a test greps it. None edits a finalized determination: the surface has no PUT and no
PATCH on an assessment, and the FSM has no edge out of `final`. None deletes anything. None
recomputes a price, a charge, or a balance — the taxable base arrives from the document. And
none calls a rate provider to *explain* a determination: the explanation is assembled from
stored evidence, which is what makes it still answerable in five years.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    AssessDocumentTax,
    BillingApplication,
    DeclareTaxClassification,
    DeclareTaxJurisdiction,
    DeclareTaxRate,
    DiagnoseTaxAssessment,
    DiscardTaxAssessment,
    EndTaxNexus,
    ExplainTaxAssessment,
    FinalizeTaxAssessment,
    GetSubjectTaxAssessment,
    GetTaxAssessment,
    InspectTaxAssessment,
    ListDeclaredTaxRates,
    ListTaxAssessments,
    ListTaxClassifications,
    ListTaxExemptions,
    ListTaxJurisdictions,
    ListTaxNexus,
    OverrideTaxTreatment,
    PreviewTaxAssessment,
    ReconcileTaxAssessment,
    RecordTaxExemption,
    RegisterTaxNexus,
    ResolveTaxPlaceOfSupply,
    RetireTaxJurisdiction,
    RetireTaxRate,
    RevokeTaxExemption,
    SearchTaxAssessments,
    TaxTimeline,
)
from billing.foundation import AssessmentState, SubjectKind, TaxpayerKind, TaxRegime, TaxTreatment

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES
from ..tax_schemas import (
    AssessDocumentTaxBody,
    DeclareClassificationBody,
    DeclaredTaxRateBody,
    DeclaredTaxRateResponse,
    DeclareJurisdictionBody,
    OverrideTreatmentBody,
    PlaceOfSupplyResponse,
    RecordExemptionBody,
    RegisterNexusBody,
    TaxActivityResponse,
    TaxAssessmentResponse,
    TaxClassificationResponse,
    TaxDiagnosticsResponse,
    TaxExemptionResponse,
    TaxExplanationResponse,
    TaxInspectionResponse,
    TaxJurisdictionResponse,
    TaxNexusResponse,
    TaxReasonBody,
    TaxReconciliationResponse,
    TaxSearchResponse,
    build_tax_query,
)

router = APIRouter(prefix="/taxation", tags=["taxes"], responses=ERROR_RESPONSES)


def _assessment(view) -> TaxAssessmentResponse:
    return TaxAssessmentResponse.of(view)


# ── configuration: jurisdictions, classifications, nexus, exemptions ────────────


@router.post(
    "/jurisdictions",
    response_model=TaxJurisdictionResponse,
    status_code=201,
    summary="Declare a jurisdiction (a place with taxing authority — never a rate)",
)
def declare_jurisdiction(
    body: DeclareJurisdictionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxJurisdictionResponse:
    """Idempotent on the canonical code: declaring `IN:KA` twice returns the existing row, because
    a duplicate jurisdiction is a duplicate nexus."""
    return TaxJurisdictionResponse.of(
        app.commands.dispatch(
            DeclareTaxJurisdiction(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                country=body.country,
                region=body.region,
                display_name=body.display_name,
                level=body.level,
                regime=body.regime,
                parent_code=body.parent_code,
                notes=body.notes,
            )
        )
    )


@router.get(
    "/jurisdictions",
    response_model=list[TaxJurisdictionResponse],
    summary="List declared jurisdictions",
)
def list_jurisdictions(
    include_retired: bool = False,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[TaxJurisdictionResponse]:
    return [
        TaxJurisdictionResponse.of(j)
        for j in app.queries.dispatch(
            ListTaxJurisdictions(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                include_retired=include_retired,
            )
        )
    ]


@router.post(
    "/jurisdictions/{jurisdiction_id}/retire",
    response_model=TaxJurisdictionResponse,
    summary="Retire a jurisdiction (by flag; the row is never removed)",
)
def retire_jurisdiction(
    jurisdiction_id: str,
    body: TaxReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxJurisdictionResponse:
    return TaxJurisdictionResponse.of(
        app.commands.dispatch(
            RetireTaxJurisdiction(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                jurisdiction_id=jurisdiction_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/classifications",
    response_model=TaxClassificationResponse,
    status_code=201,
    summary="Declare a tax classification",
)
def declare_classification(
    body: DeclareClassificationBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxClassificationResponse:
    return TaxClassificationResponse.of(
        app.commands.dispatch(
            DeclareTaxClassification(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                code=body.code,
                display_name=body.display_name,
                default_treatment=body.default_treatment,
                codes=tuple(tuple(c) for c in body.codes),  # type: ignore[misc]
                is_digital_service=body.is_digital_service,
                notes=body.notes,
            )
        )
    )


@router.get(
    "/classifications",
    response_model=list[TaxClassificationResponse],
    summary="List tax classifications",
)
def list_classifications(
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[TaxClassificationResponse]:
    return [
        TaxClassificationResponse.of(c)
        for c in app.queries.dispatch(
            ListTaxClassifications(actor_id=ctx.actor(), org_id=ctx.organization())
        )
    ]


@router.post(
    "/nexus",
    response_model=TaxNexusResponse,
    status_code=201,
    summary="Register a supplier obligation and the registration discharging it",
)
def register_nexus(
    body: RegisterNexusBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxNexusResponse:
    return TaxNexusResponse.of(
        app.commands.dispatch(
            RegisterTaxNexus(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                entity_code=body.entity_code,
                jurisdiction_code=body.jurisdiction_code,
                kind=body.kind,
                registration_number=body.registration_number,
                registration_regime=body.registration_regime,
                registration_country=body.registration_country,
                effective_from=body.effective_from,
                effective_until=body.effective_until,
                established_reason=body.established_reason,
            )
        )
    )


@router.get("/nexus", response_model=list[TaxNexusResponse], summary="List supplier obligations")
def list_nexus(
    entity_code: str = "",
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[TaxNexusResponse]:
    return [
        TaxNexusResponse.of(n)
        for n in app.queries.dispatch(
            ListTaxNexus(
                actor_id=ctx.actor(), org_id=ctx.organization(), entity_code=entity_code
            )
        )
    ]


@router.post(
    "/nexus/{nexus_id}/end",
    response_model=TaxNexusResponse,
    summary="End a nexus (by timestamp; the row is never removed)",
)
def end_nexus(
    nexus_id: str,
    body: TaxReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxNexusResponse:
    return TaxNexusResponse.of(
        app.commands.dispatch(
            EndTaxNexus(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                nexus_id=nexus_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/exemptions",
    response_model=TaxExemptionResponse,
    status_code=201,
    summary="Record a customer exemption and its evidence",
)
def record_exemption(
    body: RecordExemptionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxExemptionResponse:
    return TaxExemptionResponse.of(
        app.commands.dispatch(
            RecordTaxExemption(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=body.account_id,
                jurisdiction_code=body.jurisdiction_code,
                kind=body.kind,
                certificate_ref=body.certificate_ref,
                reason=body.reason,
                effective_from=body.effective_from,
                effective_until=body.effective_until,
                evidence_ref=body.evidence_ref,
            )
        )
    )


@router.get(
    "/exemptions", response_model=list[TaxExemptionResponse], summary="List customer exemptions"
)
def list_exemptions(
    account_id: str = "",
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[TaxExemptionResponse]:
    return [
        TaxExemptionResponse.of(e)
        for e in app.queries.dispatch(
            ListTaxExemptions(
                actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id
            )
        )
    ]


@router.post(
    "/exemptions/{exemption_id}/revoke",
    response_model=TaxExemptionResponse,
    summary="Revoke an exemption (by timestamp; never deleted)",
)
def revoke_exemption(
    exemption_id: str,
    body: TaxReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxExemptionResponse:
    return TaxExemptionResponse.of(
        app.commands.dispatch(
            RevokeTaxExemption(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                exemption_id=exemption_id,
                reason=body.reason,
            )
        )
    )


# ── rate evidence ───────────────────────────────────────────────────────────────


@router.post(
    "/rates",
    response_model=DeclaredTaxRateResponse,
    status_code=201,
    summary="Declare an effective-dated rate as evidence, with the authority that set it",
)
def declare_rate(
    body: DeclaredTaxRateBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> DeclaredTaxRateResponse:
    """Append-only: superseding a rate writes a new row beside the one it replaces, because an
    issued invoice must stay explainable against the rate in force when it was issued."""
    return DeclaredTaxRateResponse.of(
        app.commands.dispatch(
            DeclareTaxRate(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                jurisdiction_code=body.jurisdiction_code,
                tax_type=body.tax_type,
                name=body.name,
                rate_basis_points=body.rate_basis_points,
                classification_code=body.classification_code,
                treatment=body.treatment,
                effective_from=body.effective_from,
                effective_until=body.effective_until,
                reference=body.reference,
                source_version=body.source_version,
            )
        )
    )


@router.get(
    "/rates",
    response_model=list[DeclaredTaxRateResponse],
    summary="List declared rates — including retired ones, which an audit reads too",
)
def list_rates(
    jurisdiction_code: str = "",
    limit: int = Query(default=500, ge=1, le=1000),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[DeclaredTaxRateResponse]:
    return [
        DeclaredTaxRateResponse.of(r)
        for r in app.queries.dispatch(
            ListDeclaredTaxRates(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                jurisdiction_code=jurisdiction_code,
                limit=limit,
            )
        )
    ]


@router.post(
    "/rates/{rate_id}/retire",
    response_model=DeclaredTaxRateResponse,
    summary="Retire a rate (by timestamp; the declaration is never amended)",
)
def retire_rate(
    rate_id: str,
    body: TaxReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> DeclaredTaxRateResponse:
    return DeclaredTaxRateResponse.of(
        app.commands.dispatch(
            RetireTaxRate(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                rate_id=rate_id,
                reason=body.reason,
            )
        )
    )


# ── determination ───────────────────────────────────────────────────────────────


@router.get(
    "/place-of-supply",
    response_model=PlaceOfSupplyResponse,
    summary="Where a supply would be taxed, and by which rule",
)
def place_of_supply(
    origin_country: str = "",
    origin_region: str = "",
    destination_country: str = "",
    destination_region: str = "",
    regime: TaxRegime = TaxRegime.NONE,
    customer_registration: str = "",
    taxpayer_kind: TaxpayerKind = TaxpayerKind.BUSINESS,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PlaceOfSupplyResponse:
    return PlaceOfSupplyResponse.of(
        app.queries.dispatch(
            ResolveTaxPlaceOfSupply(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                origin_country=origin_country,
                origin_region=origin_region,
                destination_country=destination_country,
                destination_region=destination_region,
                regime=regime,
                customer_registration=customer_registration,
                taxpayer_kind=taxpayer_kind,
            )
        )
    )


@router.get(
    "/assessments/search",
    response_model=TaxSearchResponse,
    summary="Search determinations deterministically",
)
def search_assessments(
    text: str = "",
    account_id: str = "",
    subject_id: str = "",
    subject_kinds: list[SubjectKind] = Query(default=[]),
    states: list[AssessmentState] = Query(default=[]),
    treatments: list[TaxTreatment] = Query(default=[]),
    regimes: list[TaxRegime] = Query(default=[]),
    jurisdiction_code: str = "",
    overridden_only: bool = False,
    unsourced_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxSearchResponse:
    return TaxSearchResponse.of(
        app.queries.dispatch(
            SearchTaxAssessments(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                query=build_tax_query(
                    text=text,
                    account_id=account_id,
                    subject_id=subject_id,
                    subject_kinds=list(subject_kinds),
                    states=list(states),
                    treatments=list(treatments),
                    regimes=list(regimes),
                    jurisdiction_code=jurisdiction_code,
                    overridden_only=overridden_only,
                    unsourced_only=unsourced_only,
                    limit=limit,
                    offset=offset,
                ),
            )
        )
    )


@router.get(
    "/assessments/activity",
    response_model=list[TaxActivityResponse],
    summary="Append-only tax audit trail",
)
def tax_activity(
    account_id: str = "",
    subject_id: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[TaxActivityResponse]:
    return [
        TaxActivityResponse.of(a)
        for a in app.queries.dispatch(
            TaxTimeline(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                subject_id=subject_id,
                limit=limit,
            )
        )
    ]


@router.get(
    "/assessments", response_model=list[TaxAssessmentResponse], summary="List determinations"
)
def list_assessments(
    account_id: str = "",
    subject_id: str = "",
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[TaxAssessmentResponse]:
    return [
        _assessment(a)
        for a in app.queries.dispatch(
            ListTaxAssessments(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                subject_id=subject_id,
                limit=limit,
            )
        )
    ]


@router.get(
    "/assessments/{assessment_id}",
    response_model=TaxAssessmentResponse,
    summary="Get a determination and its evidence",
)
def get_assessment(
    assessment_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxAssessmentResponse:
    return _assessment(
        app.queries.dispatch(
            GetTaxAssessment(
                actor_id=ctx.actor(), org_id=ctx.organization(), assessment_id=assessment_id
            )
        )
    )


@router.get(
    "/assessments/{assessment_id}/explain",
    response_model=TaxExplanationResponse,
    summary="Why was this tax applied? — answered from stored evidence, with no provider call",
)
def explain_assessment(
    assessment_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxExplanationResponse:
    return TaxExplanationResponse.of(
        app.queries.dispatch(
            ExplainTaxAssessment(
                actor_id=ctx.actor(), org_id=ctx.organization(), assessment_id=assessment_id
            )
        )
    )


@router.get(
    "/assessments/{assessment_id}/inspect",
    response_model=TaxInspectionResponse,
    summary="Is this determination sourced, explainable, and in-window?",
)
def inspect_assessment(
    assessment_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxInspectionResponse:
    return TaxInspectionResponse.of(
        app.queries.dispatch(
            InspectTaxAssessment(
                actor_id=ctx.actor(), org_id=ctx.organization(), assessment_id=assessment_id
            )
        )
    )


@router.get(
    "/assessments/{assessment_id}/diagnostics",
    response_model=TaxDiagnosticsResponse,
    summary="Structural findings for a determination",
)
def diagnose_assessment(
    assessment_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxDiagnosticsResponse:
    return TaxDiagnosticsResponse.of(
        app.queries.dispatch(
            DiagnoseTaxAssessment(
                actor_id=ctx.actor(), org_id=ctx.organization(), assessment_id=assessment_id
            )
        )
    )


@router.get(
    "/assessments/{assessment_id}/reconciliation",
    response_model=TaxReconciliationResponse,
    summary="Does the determination agree with what it was applied to?",
)
def reconcile_assessment(
    assessment_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxReconciliationResponse:
    return TaxReconciliationResponse.of(
        app.queries.dispatch(
            ReconcileTaxAssessment(
                actor_id=ctx.actor(), org_id=ctx.organization(), assessment_id=assessment_id
            )
        )
    )


@router.post(
    "/assessments/{assessment_id}/finalize",
    response_model=TaxAssessmentResponse,
    summary="Seal a determination and publish its projection (irreversible)",
)
def finalize_assessment(
    assessment_id: str,
    body: TaxReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxAssessmentResponse:
    return _assessment(
        app.commands.dispatch(
            FinalizeTaxAssessment(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                assessment_id=assessment_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/assessments/{assessment_id}/override",
    response_model=TaxAssessmentResponse,
    status_code=201,
    summary="Re-determine under an attributed human decision (a new assessment, not an edit)",
)
def override_treatment(
    assessment_id: str,
    body: OverrideTreatmentBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxAssessmentResponse:
    return _assessment(
        app.commands.dispatch(
            OverrideTaxTreatment(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                assessment_id=assessment_id,
                treatment=body.treatment,
                reason=body.reason,
                reference=body.reference,
            )
        )
    )


@router.post(
    "/assessments/{assessment_id}/discard",
    response_model=TaxAssessmentResponse,
    summary="Discard a provisional determination (a finalized one cannot be discarded)",
)
def discard_assessment(
    assessment_id: str,
    body: TaxReasonBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxAssessmentResponse:
    return _assessment(
        app.commands.dispatch(
            DiscardTaxAssessment(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                assessment_id=assessment_id,
                reason=body.reason,
            )
        )
    )


# ── documents ───────────────────────────────────────────────────────────────────


@router.post(
    "/documents/{document_id}/assess",
    response_model=TaxAssessmentResponse,
    status_code=201,
    summary="Determine the tax treatment of a document's already-determined amounts",
)
def assess_document(
    document_id: str,
    body: AssessDocumentTaxBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxAssessmentResponse:
    """Idempotent on the basis: determining the same facts twice is one assessment, and the second
    call records `assessment_unchanged` — the evidence determination is deterministic."""
    return _assessment(
        app.commands.dispatch(
            AssessDocumentTax(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                entity_code=body.entity_code,
                origin_country=body.origin_country,
                origin_region=body.origin_region,
                supplier_registration=body.supplier_registration,
                default_classification_code=body.default_classification_code,
                line_classifications=tuple(
                    tuple(c) for c in body.line_classifications  # type: ignore[misc]
                ),
                taxpayer_kind=body.taxpayer_kind,
                regime=body.regime,
                prices_include_tax=body.prices_include_tax,
                rounding_mode=body.rounding_mode,
                rounding_scope=body.rounding_scope,
                effective_at=body.effective_at,
            )
        )
    )


@router.get(
    "/documents/{document_id}/preview",
    response_model=TaxAssessmentResponse,
    summary="What a determination would conclude — persists nothing, finalizes nothing",
)
def preview_document_tax(
    document_id: str,
    entity_code: str = "",
    origin_country: str = "",
    origin_region: str = "",
    default_classification_code: str = "",
    regime: TaxRegime = TaxRegime.NONE,
    prices_include_tax: bool = False,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxAssessmentResponse:
    return _assessment(
        app.queries.dispatch(
            PreviewTaxAssessment(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=document_id,
                entity_code=entity_code,
                origin_country=origin_country,
                origin_region=origin_region,
                default_classification_code=default_classification_code,
                regime=regime,
                prices_include_tax=prices_include_tax,
            )
        )
    )


@router.get(
    "/documents/{document_id}/assessment",
    response_model=TaxAssessmentResponse,
    summary="The determination that stands for a document",
)
def document_assessment(
    document_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxAssessmentResponse:
    """A finalized determination stands over any provisional one — a stale draft must never shadow
    the figure a customer was actually charged."""
    return _assessment(
        app.queries.dispatch(
            GetSubjectTaxAssessment(
                actor_id=ctx.actor(), org_id=ctx.organization(), subject_id=document_id
            )
        )
    )

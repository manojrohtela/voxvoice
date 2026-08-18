"""Tax Determination wire DTOs (M9.15) — Pydantic v2, additive to the earlier schemas.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views. Foundation enums are reused so validation and OpenAPI enums come from the domain
vocabulary, and **M9.5's `MoneyResponse` is reused rather than redeclared**.

Named distinctly from the frozen surfaces they sit beside: `TaxAssessmentResponse` rather than a
second `TaxDeterminationResponse` (M9.5 owns that name for the M9.2 record this milestone
*projects onto*), and `DeclaredTaxRateBody` rather than anything that could read as a rate table.

Rates are expressed in **basis points**, never as a float percentage. 18% is `1800`. A float rate is
a rate that disagrees with itself after enough lines, and on a tax return that disagreement is a
finding.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    AssessmentState,
    ExemptionKind,
    JurisdictionLevel,
    NexusKind,
    Rounding,
    RoundingScope,
    SubjectKind,
    TaxpayerKind,
    TaxRegime,
    TaxSearchQuery,
    TaxTreatment,
)

from .schemas import MoneyResponse

# ── inbound ─────────────────────────────────────────────────────────────────────


class DeclareJurisdictionBody(BaseModel):
    """Declare a place with taxing authority — **never its percentage.**

    The canonical code is derived from country and region, so `IN:KA` and `in:ka` cannot both
    exist: a duplicate jurisdiction is a duplicate nexus."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "country": "IN",
                "region": "KA",
                "display_name": "Karnataka",
                "level": "state",
                "regime": "india_gst",
            }
        }
    )
    country: str = Field(min_length=2, max_length=2, description="ISO-3166 alpha-2")
    region: str = ""
    display_name: str = ""
    level: JurisdictionLevel = JurisdictionLevel.COUNTRY
    regime: TaxRegime = TaxRegime.NONE
    parent_code: str = ""
    notes: str = ""


class DeclareClassificationBody(BaseModel):
    """What is supplied, in the vocabulary a tax authority uses."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "saas",
                "display_name": "SaaS subscription",
                "is_digital_service": True,
                "codes": [["hsn_sac", "998314"]],
            }
        }
    )
    code: str = Field(min_length=1)
    display_name: str = ""
    default_treatment: TaxTreatment = TaxTreatment.STANDARD
    codes: list[tuple[str, str]] = Field(
        default_factory=list, description="Regime-specific identifiers, e.g. HSN/SAC or UNSPSC"
    )
    is_digital_service: bool = False
    notes: str = ""


class RegisterNexusBody(BaseModel):
    """The supplier's obligation in a jurisdiction, and the registration discharging it."""

    entity_code: str = Field(min_length=1)
    jurisdiction_code: str = Field(min_length=1)
    kind: NexusKind = NexusKind.REGISTRATION
    registration_number: str = ""
    registration_regime: TaxRegime = TaxRegime.NONE
    registration_country: str = ""
    effective_from: float | None = None
    effective_until: float | None = None
    established_reason: str = ""


class RecordExemptionBody(BaseModel):
    account_id: str = Field(min_length=1)
    jurisdiction_code: str = Field(min_length=1)
    kind: ExemptionKind = ExemptionKind.CERTIFICATE
    certificate_ref: str = ""
    reason: str = ""
    effective_from: float | None = None
    effective_until: float | None = None
    evidence_ref: str = ""


class DeclaredTaxRateBody(BaseModel):
    """Declare a rate as **evidence**, with the authority that set it.

    `reference` is what makes the row evidence rather than a number, and it is the field a
    challenge reads first. Rates are append-only: superseding one writes a new row beside it."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "jurisdiction_code": "IN:KA",
                "tax_type": "gst",
                "name": "CGST",
                "rate_basis_points": 900,
                "classification_code": "saas",
                "reference": "Notification 11/2017-Central Tax (Rate)",
                "source_version": "2017-06-28",
            }
        }
    )
    jurisdiction_code: str = Field(min_length=1)
    tax_type: str = Field(min_length=1, description='e.g. "gst", "vat", "sales_tax"')
    name: str = Field(min_length=1, description="CGST, SGST, IGST, VAT — part of the rate identity")
    rate_basis_points: int = Field(ge=0, description="18% is 1800. Never a float.")
    classification_code: str = Field(
        default="", description='"" applies to every classification in the jurisdiction'
    )
    treatment: TaxTreatment = TaxTreatment.STANDARD
    effective_from: float | None = None
    effective_until: float | None = None
    reference: str = Field(default="", description="The notification, statute, or schedule")
    source_version: str = ""


class AssessDocumentTaxBody(BaseModel):
    """Determine the tax treatment of a document's already-determined amounts."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "entity_code": "VOX",
                "origin_country": "IN",
                "origin_region": "KA",
                "supplier_registration": "29AABCV1234F1Z5",
                "default_classification_code": "saas",
                "regime": "india_gst",
            }
        }
    )
    entity_code: str = ""
    origin_country: str = ""
    origin_region: str = ""
    supplier_registration: str = ""
    default_classification_code: str = ""
    line_classifications: list[tuple[str, str]] = Field(default_factory=list)
    taxpayer_kind: TaxpayerKind = TaxpayerKind.BUSINESS
    regime: TaxRegime = TaxRegime.NONE
    prices_include_tax: bool = False
    rounding_mode: Rounding = Rounding.HALF_UP
    rounding_scope: RoundingScope = RoundingScope.PER_LINE
    effective_at: float | None = None


class OverrideTreatmentBody(BaseModel):
    """A human decision to depart from what determination concluded. Always attributed."""

    treatment: TaxTreatment
    reason: str = Field(min_length=1, description="Recorded on the assessment forever")
    reference: str = ""


class TaxReasonBody(BaseModel):
    reason: str = ""


# ── outbound ────────────────────────────────────────────────────────────────────


class TaxJurisdictionResponse(BaseModel):
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

    @classmethod
    def of(cls, view) -> TaxJurisdictionResponse:
        return cls(**vars(view))


class TaxClassificationResponse(BaseModel):
    id: str
    code: str
    display_name: str
    default_treatment: str
    codes: list[tuple[str, str]] = Field(default_factory=list)
    is_digital_service: bool = False
    notes: str = ""

    @classmethod
    def of(cls, view) -> TaxClassificationResponse:
        return cls(
            id=view.id,
            code=view.code,
            display_name=view.display_name,
            default_treatment=view.default_treatment,
            codes=[tuple(c) for c in view.codes],  # type: ignore[misc]
            is_digital_service=view.is_digital_service,
            notes=view.notes,
        )


class TaxNexusResponse(BaseModel):
    id: str
    entity_code: str
    jurisdiction_code: str
    kind: str
    registration_number: str = ""
    registration_regime: str = ""
    effective_from: float = 0.0
    effective_until: float | None = None
    established_reason: str = ""

    @classmethod
    def of(cls, view) -> TaxNexusResponse:
        return cls(**vars(view))


class TaxExemptionResponse(BaseModel):
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

    @classmethod
    def of(cls, view) -> TaxExemptionResponse:
        return cls(**vars(view))


class DeclaredTaxRateResponse(BaseModel):
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

    @classmethod
    def of(cls, view) -> DeclaredTaxRateResponse:
        return cls(**vars(view))


class RateProvenanceResponse(BaseModel):
    """Where a rate came from, when it applied, and how to find it again. **The field that makes a
    determination evidence rather than an assertion.**"""

    source: str
    source_code: str = ""
    source_version: str = ""
    reference: str = ""
    effective_from: float = 0.0
    effective_until: float | None = None
    retrieved_at: float = 0.0
    is_sourced: bool = False

    @classmethod
    def of(cls, view) -> RateProvenanceResponse:
        return cls(**vars(view))


class AssessedComponentResponse(BaseModel):
    name: str
    tax_type: str
    jurisdiction_code: str
    rate_basis_points: int
    taxable_base: MoneyResponse
    amount: MoneyResponse
    basis: str
    provenance: RateProvenanceResponse

    @classmethod
    def of(cls, view) -> AssessedComponentResponse:
        return cls(
            name=view.name,
            tax_type=view.tax_type,
            jurisdiction_code=view.jurisdiction_code,
            rate_basis_points=view.rate_basis_points,
            taxable_base=_money(view.taxable_base),
            amount=_money(view.amount),
            basis=view.basis,
            provenance=RateProvenanceResponse.of(view.provenance),
        )


class AssessedLineResponse(BaseModel):
    subject_line_id: str
    classification_code: str
    taxable_base: MoneyResponse
    tax_total: MoneyResponse
    treatment: str
    exemption_reason: str = ""
    components: list[AssessedComponentResponse] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> AssessedLineResponse:
        return cls(
            subject_line_id=view.subject_line_id,
            classification_code=view.classification_code,
            taxable_base=_money(view.taxable_base),
            tax_total=_money(view.tax_total),
            treatment=view.treatment,
            exemption_reason=view.exemption_reason,
            components=[AssessedComponentResponse.of(c) for c in view.components],
        )


class PlaceOfSupplyResponse(BaseModel):
    origin_code: str
    destination_code: str
    resolved_code: str
    direction: str
    gst_split: str
    rule: str
    is_cross_border: bool = False
    is_digital_service: bool = False
    customer_registered: bool = False

    @classmethod
    def of(cls, view) -> PlaceOfSupplyResponse:
        return cls(**vars(view))


class TaxOverrideResponse(BaseModel):
    treatment: str
    reason: str
    actor_id: str
    at: float = 0.0
    supersedes_treatment: str = ""
    reference: str = ""


class TaxAssessmentResponse(BaseModel):
    """A determination and its evidence. Everything needed to answer "why was this applied?".

    Named distinctly from M9.5's `TaxDeterminationResponse`, which serves the thinner M9.2 record
    this projects onto — one truth, two surfaces, never two copies."""

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
    taxable_base: MoneyResponse
    tax_total: MoneyResponse
    place_of_supply: PlaceOfSupplyResponse
    lines: list[AssessedLineResponse] = Field(default_factory=list)
    rounding_mode: str = "half_up"
    rounding_scope: str = "per_line"
    nexus_id: str = ""
    nexus_kind: str = "none"
    registration_used: str = ""
    exemption_id: str = ""
    exemption_reason: str = ""
    override: TaxOverrideResponse | None = None
    withholding_minor: int = 0
    supersedes_assessment_id: str = ""
    is_final: bool = False
    is_sourced: bool = True
    is_explainable: bool = True
    notes: list[str] = Field(default_factory=list)
    assessed_at: float = 0.0
    finalized_at: float | None = None
    allowed_triggers: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> TaxAssessmentResponse:
        return cls(
            id=view.id,
            account_id=view.account_id,
            subject_id=view.subject_id,
            subject_kind=view.subject_kind,
            state=view.state,
            treatment=view.treatment,
            regime=view.regime,
            currency=view.currency,
            version=view.version,
            digest=view.digest,
            taxable_base=_money(view.taxable_base),
            tax_total=_money(view.tax_total),
            place_of_supply=PlaceOfSupplyResponse.of(view.place_of_supply),
            lines=[AssessedLineResponse.of(line) for line in view.lines],
            rounding_mode=view.rounding_mode,
            rounding_scope=view.rounding_scope,
            nexus_id=view.nexus_id,
            nexus_kind=view.nexus_kind,
            registration_used=view.registration_used,
            exemption_id=view.exemption_id,
            exemption_reason=view.exemption_reason,
            override=(
                TaxOverrideResponse(**vars(view.override)) if view.override is not None else None
            ),
            withholding_minor=view.withholding_minor,
            supersedes_assessment_id=view.supersedes_assessment_id,
            is_final=view.is_final,
            is_sourced=view.is_sourced,
            is_explainable=view.is_explainable,
            notes=list(view.notes),
            assessed_at=view.assessed_at,
            finalized_at=view.finalized_at,
            allowed_triggers=list(view.allowed_triggers),
        )


class TaxExplanationStepResponse(BaseModel):
    stage: str
    conclusion: str
    evidence: str = ""
    reference: str = ""


class TaxExplanationResponse(BaseModel):
    """The answer to "why was this tax applied?", assembled from the assessment alone.

    `answered_without_provider` is always `True` by construction — the explainer holds no provider
    and takes none. Stated on the wire so a consumer can see the guarantee is structural."""

    assessment_id: str
    subject_id: str
    answered_without_provider: bool
    steps: list[TaxExplanationStepResponse] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> TaxExplanationResponse:
        return cls(
            assessment_id=view.assessment_id,
            subject_id=view.subject_id,
            answered_without_provider=view.answered_without_provider,
            steps=[TaxExplanationStepResponse(**vars(s)) for s in view.steps],
        )


class TaxDiscrepancyResponse(BaseModel):
    kind: str
    assessment_id: str = ""
    line_id: str = ""
    expected_minor: int = 0
    actual_minor: int = 0
    detail: str = ""


class TaxReconciliationResponse(BaseModel):
    subject_id: str
    assessment_id: str
    lines_examined: int
    components_examined: int
    is_clean: bool
    discrepancies: list[TaxDiscrepancyResponse] = Field(default_factory=list)
    at: float = 0.0

    @classmethod
    def of(cls, view) -> TaxReconciliationResponse:
        return cls(
            subject_id=view.subject_id,
            assessment_id=view.assessment_id,
            lines_examined=view.lines_examined,
            components_examined=view.components_examined,
            is_clean=view.is_clean,
            discrepancies=[TaxDiscrepancyResponse(**vars(d)) for d in view.discrepancies],
            at=view.at,
        )


class TaxInspectionResponse(BaseModel):
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
    notes: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> TaxInspectionResponse:
        return cls(**{**vars(view), "notes": list(view.notes)})


class TaxDiagnosticResponse(BaseModel):
    path: str
    code: str
    severity: str
    message: str
    remediation: str = ""


class TaxDiagnosticsResponse(BaseModel):
    assessment_id: str
    is_valid: bool
    findings: list[TaxDiagnosticResponse] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> TaxDiagnosticsResponse:
        return cls(
            assessment_id=view.assessment_id,
            is_valid=view.is_valid,
            findings=[TaxDiagnosticResponse(**vars(f)) for f in view.findings],
        )


class TaxActivityResponse(BaseModel):
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

    @classmethod
    def of(cls, view) -> TaxActivityResponse:
        return cls(**vars(view))


class TaxSearchResponse(BaseModel):
    assessments: list[TaxAssessmentResponse] = Field(default_factory=list)
    total: int = 0

    @classmethod
    def of(cls, view) -> TaxSearchResponse:
        return cls(
            assessments=[TaxAssessmentResponse.of(a) for a in view.assessments],
            total=view.total,
        )


# ── query construction ──────────────────────────────────────────────────────────


def build_tax_query(
    *,
    text: str = "",
    account_id: str = "",
    subject_id: str = "",
    subject_kinds: list[SubjectKind] | None = None,
    states: list[AssessmentState] | None = None,
    treatments: list[TaxTreatment] | None = None,
    regimes: list[TaxRegime] | None = None,
    jurisdiction_code: str = "",
    overridden_only: bool = False,
    unsourced_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> TaxSearchQuery:
    """Transport → the Foundation's deterministic filter. No filtering happens here."""
    return TaxSearchQuery(
        text=text,
        account_id=account_id,
        subject_id=subject_id,
        subject_kinds=tuple(subject_kinds or ()),
        states=tuple(states or ()),
        treatments=tuple(treatments or ()),
        regimes=tuple(regimes or ()),
        jurisdiction_code=jurisdiction_code,
        overridden_only=overridden_only,
        unsourced_only=unsourced_only,
        limit=limit,
        offset=offset,
    )


def _money(view) -> MoneyResponse:
    return MoneyResponse(minor=view.minor, currency=view.currency)

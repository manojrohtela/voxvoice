"""Rating Engine wire DTOs (M9.12) — Pydantic v2, additive to the earlier schemas.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views, with `to_*()` inbound and `from_*()` outbound. Foundation enums are reused so
validation and OpenAPI enums come from the domain vocabulary.

**Money on this surface is always `{minor, currency}`** — an integer in the currency's minor unit
plus the currency, never a formatted string and never a float (ADR-0023 RS5). A pre-formatted
amount invites a client to parse money back out of text, and a float invites a rounding artefact
nobody can reproduce. Rendering is the Studio's job.

Named distinctly from the frozen surfaces they sit beside: `RatedChargeResponse` rather than a
second `ChargeLineResponse`, `ChargeDiscountResponse` rather than a second `AppliedDiscountResponse`
(M9.7 owns that name), and `RatingPeriodBody` rather than a second `PeriodBody` (M9.5 owns that
one, and M9.11 already had to make this exact call).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    BillingInterval,
    ChargeKind,
    Currency,
    RatingSearchQuery,
    RatingState,
    RerateReason,
)

from .schemas import MoneyResponse

# ── shared ──────────────────────────────────────────────────────────────────────


class RatingPeriodBody(BaseModel):
    """Named distinctly from M9.5's `PeriodBody` and M9.11's `UsagePeriodBody`. This one names
    the *agreement*, because a rating period belongs to one."""

    subscription_id: str
    period_start: float = Field(gt=0)
    period_end: float = Field(gt=0)


class AccountPeriodBody(BaseModel):
    account_id: str
    period_start: float = Field(gt=0)
    period_end: float = Field(gt=0)


class RerateBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "subscription_id": "csb_1",
                "period_start": 1735689600.0,
                "period_end": 1738368000.0,
                "reason": "late_usage",
                "detail": "three sessions arrived after the period closed",
            }
        }
    )
    subscription_id: str
    period_start: float = Field(gt=0)
    period_end: float = Field(gt=0)
    reason: RerateReason = Field(
        default=RerateReason.MANUAL_REVIEW,
        description="Why the period is being rated again. A closed vocabulary, because "
        "'why did my bill change?' has a small number of honest answers",
    )
    detail: str = ""


class ReasonBody(BaseModel):
    reason: str = ""


class AdjustmentBody(BaseModel):
    """Express the difference against a **finalized** run as adjustment charges. The finalized
    run is read and left exactly as it was billed — its lifecycle has no edge out of it."""

    against_run_id: str
    detail: str = ""


class DefineCommitmentBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": "bac_1",
                "code": "ENTERPRISE_FLOOR_2026",
                "amount": {"minor": 5000000, "currency": "INR"},
                "price_codes": ["voice.minutes", "platform.fee"],
                "interval": "monthly",
                "description": "₹50,000 minimum monthly spend",
            }
        }
    )
    account_id: str
    code: str = Field(min_length=1, description="Unique per organization")
    amount: MoneyResponse = Field(
        description="The floor. Never negative — a negative floor is a credit, and credits are "
        "a different domain"
    )
    subscription_id: str = ""
    price_codes: list[str] = Field(
        default_factory=list, description="Empty ⇒ the whole agreement counts toward the floor"
    )
    interval: BillingInterval = BillingInterval.MONTHLY
    contract_id: str = ""
    description: str = ""
    effective_from: float = 0.0
    effective_until: float | None = None

    def currency(self) -> Currency:
        return Currency(self.amount.currency)


# ── responses ───────────────────────────────────────────────────────────────────


class ComponentResponse(BaseModel):
    """One step of a charge's arithmetic. The components sum **exactly** to the charge."""

    kind: str
    label: str
    amount: MoneyResponse
    units: int
    unit_amount: MoneyResponse | None
    detail: str


class ChargeDiscountResponse(BaseModel):
    code: str
    display_name: str
    kind: str
    priority: int
    stackable: bool
    reduction: MoneyResponse
    source: str


class ChargeProvenanceResponse(BaseModel):
    """Everything needed to re-derive the charge. Every field names a frozen artefact."""

    price_code: str
    price_version: int
    price_pin: str
    price_book_code: str
    price_book_version: int
    book_pin: str
    catalog_version: int | None
    pricing_version: int | None
    subscription_id: str
    subscription_version: int
    item_id: str
    meter_code: str
    meter_pin: str
    usage_ids: list[str]
    usage_record_count: int
    usage_digest: str
    entitlement_version: int
    included_units_source: str
    override_applied: bool
    resolution_path: list[str]


class RatedChargeResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "chg_9f2c",
                "kind": "overage",
                "description": "Overage — voice.minutes",
                "currency": "INR",
                "quantity": 1400,
                "unit": "minute",
                "net_amount": {"minor": 20000, "currency": "INR"},
                "breaks_down_correctly": True,
            }
        }
    )
    id: str
    kind: str
    description: str
    currency: str
    quantity: int
    unit: str
    list_amount: MoneyResponse | None
    gross_amount: MoneyResponse | None
    discount_amount: MoneyResponse | None
    net_amount: MoneyResponse
    shape: str
    interval: str
    components: list[ComponentResponse]
    discounts: list[ChargeDiscountResponse]
    provenance: ChargeProvenanceResponse
    breaks_down_correctly: bool
    digest: str


class RatingBasisResponse(BaseModel):
    """The immutable inputs. `digest` is what makes rerating idempotent — an unchanged digest
    means an unchanged figure, provably."""

    catalog_version: int
    pricing_version: int
    subscription_version: int
    entitlement_version: int
    usage_digest: str
    usage_record_count: int
    price_pins: list[str]
    seat_counts: list[tuple[str, int]]
    metered_units: list[tuple[str, int]]
    commitment_codes: list[str]
    digest: str


class RatingResultResponse(BaseModel):
    account_id: str
    subscription_id: str
    period_start: float
    period_end: float
    currency: str
    mode: str
    charges: list[RatedChargeResponse]
    charge_count: int
    kinds: list[str]
    is_hybrid: bool
    subtotal: MoneyResponse
    discount_total: MoneyResponse
    total: MoneyResponse
    basis: RatingBasisResponse
    digest: str
    breaks_down_correctly: bool
    at: float


class RatingRunResponse(BaseModel):
    id: str
    account_id: str
    subscription_id: str
    period_start: float
    period_end: float
    currency: str
    state: RatingState
    sequence: int
    basis_digest: str
    result_digest: str
    total: MoneyResponse
    charge_count: int
    reason: str
    supersedes: str
    adjusts: str
    superseded_by: str
    is_rerate: bool
    is_finalized: bool
    detail: str
    rated_at: float
    rated_by: str
    finalized_at: float | None
    finalized_by: str
    allowed_triggers: list[str] = Field(
        description="Which lifecycle moves the server will accept next. The Studio takes its "
        "buttons from here rather than deciding for itself"
    )
    result: RatingResultResponse | None


class RatingReceiptResponse(BaseModel):
    """What one rating call did. `outcome` is `rated`, `rerated`, or **`unchanged`** — the last
    one is a success, and the HTTP status distinguishes it (201 / 200)."""

    outcome: str
    rating_run_id: str
    subscription_id: str
    basis_digest: str
    total: MoneyResponse
    charge_count: int
    run: RatingRunResponse


class BatchRatingReceiptResponse(BaseModel):
    subscriptions_seen: int
    rated: int
    rerated: int
    unchanged: int
    skipped: int
    receipts: list[RatingReceiptResponse]


class DifferenceResponse(BaseModel):
    kind: str
    item_id: str
    price_pin: str
    charge_kind: str
    description: str
    before: MoneyResponse | None
    after: MoneyResponse | None
    delta: MoneyResponse | None
    before_quantity: int
    after_quantity: int
    reason: str


class RatingDeltaResponse(BaseModel):
    subscription_id: str
    period_start: float
    period_end: float
    before_run_id: str
    after_run_id: str
    currency: str
    total_before: MoneyResponse | None
    total_after: MoneyResponse | None
    total_delta: MoneyResponse
    differences: list[DifferenceResponse]
    charges_changed: int
    is_material: bool


class ChargeExplanationResponse(BaseModel):
    charge_id: str
    kind: str
    description: str
    amount: MoneyResponse
    lines: list[str]
    provenance_lines: list[str]


class RatingExplanationResponse(BaseModel):
    rating_run_id: str
    subscription_id: str
    period_start: float
    period_end: float
    currency: str
    total: MoneyResponse
    basis_lines: list[str]
    charges: list[ChargeExplanationResponse]


class ChargeInspectionResponse(BaseModel):
    charge_id: str
    kind: str
    amount: MoneyResponse
    breaks_down_correctly: bool
    has_provenance: bool
    has_usage_attribution: bool
    component_count: int
    discount_count: int
    is_sound: bool
    notes: list[str]


class RatingInspectionResponse(BaseModel):
    rating_run_id: str
    subscription_id: str
    period_start: float
    period_end: float
    state: str
    total: MoneyResponse
    basis_digest: str
    result_digest: str
    is_sound: bool
    unsound: list[str]
    charges: list[ChargeInspectionResponse]
    notes: list[str]


class RerateVerdictResponse(BaseModel):
    """Whether a period needs rating again — answered without rating anything."""

    subscription_id: str
    period_start: float
    period_end: float
    required: bool
    current_run_id: str
    current_state: str
    current_basis_digest: str
    proposed_basis_digest: str
    would_adjust_finalized: bool
    detail: str


class CommitmentResponse(BaseModel):
    id: str
    code: str
    account_id: str
    subscription_id: str
    amount: MoneyResponse
    price_codes: list[str]
    interval: str
    contract_id: str
    description: str
    active: bool
    effective_from: float
    effective_until: float | None
    retired_at: float | None


class RatingSnapshotResponse(BaseModel):
    id: str
    rating_run_id: str
    subscription_id: str
    period_start: float
    period_end: float
    sequence: int
    result_digest: str
    basis_digest: str
    total: MoneyResponse | None
    charge_count: int
    taken_at: float
    taken_by: str
    reason: str


class RatingActivityResponse(BaseModel):
    id: str
    action: str
    actor_id: str
    at: float
    account_id: str
    subscription_id: str
    rating_run_id: str
    period_start: float | None
    period_end: float | None
    total: MoneyResponse | None
    reason: str
    detail: str


class RatingDiagnosticResponse(BaseModel):
    path: str
    code: str
    severity: str
    message: str
    remediation: str


class RatingDiagnosticsResponse(BaseModel):
    rating_run_id: str
    findings: list[RatingDiagnosticResponse]
    error_count: int
    warning_count: int
    is_valid: bool


class RatingSearchResponse(BaseModel):
    runs: list[RatingRunResponse]
    total: int


# ── inbound query construction ──────────────────────────────────────────────────


def build_rating_query(
    *,
    text: str = "",
    account_id: str = "",
    subscription_id: str = "",
    states: list[RatingState] | None = None,
    kinds: list[ChargeKind] | None = None,
    reasons: list[RerateReason] | None = None,
    period_start: float | None = None,
    period_end: float | None = None,
    min_total_minor: int | None = None,
    max_total_minor: int | None = None,
    rerates_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> RatingSearchQuery:
    """Transport → Foundation query object. No filtering happens here; the Foundation's
    `RatingSearchService` owns every predicate, so the API and a unit test ask the same thing."""
    return RatingSearchQuery(
        text=text,
        account_id=account_id,
        subscription_id=subscription_id,
        states=tuple(states or ()),
        kinds=tuple(kinds or ()),
        reasons=tuple(reasons or ()),
        period_start=period_start,
        period_end=period_end,
        min_total_minor=min_total_minor,
        max_total_minor=max_total_minor,
        rerates_only=rerates_only,
        limit=limit,
        offset=offset,
    )

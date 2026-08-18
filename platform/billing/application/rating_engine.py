"""Rating Engine application layer (M9.12) — commands, queries, use cases, mapping.

Additive to M9.3/M9.6–M9.11: the buses, Unit of Work, authorization orchestrator, application-event
vocabulary, and dispatch discipline are reused exactly as they are. This module adds 9 commands, 12
queries, their use cases, and the view DTOs — and nothing else.

The four beats are unchanged: **authorize → open a transaction → coordinate the Foundation → stage
an event.** Every rating rule — how a tier folds, what a package rounds to, which allowance wins,
how a fixed-sum reduction is re-based, when a run may be finalized, whether two runs differ — is
Foundation behaviour these methods *call*. There is no arithmetic here, and a test greps this module
for the operators that would be tempting to inline.

## Reading six other domains without owning any of them

Rating is the first Billing milestone that must read almost every other one: the agreement (M9.9),
the pinned pricing version (M9.7), the published entitlement snapshot (M9.10), and the commercial
usage (M9.11). It does so **through their repository interfaces, read-only, at the orchestration
altitude** — which is exactly what an application layer is for. Three rules keep that from becoming
coupling:

* **Nothing is written outside rating's own tables.** No use case here calls `add`, `replace`, or
  `append` on a repository belonging to another domain, and a test asserts it. Rating cannot mutate
  a usage record, a subscription, a catalog version, a pricing version, or an entitlement snapshot,
  because it never calls anything that could.
* **Every read is of a *published, immutable* artefact.** A pricing version, an entitlement
  version, a sealed period's usage. Rating never reads a draft, so nothing it consumes can move
  underneath a rerun.
* **Price resolution is delegated, never re-implemented.** The M9.7 `PriceResolver` is injected and
  called; rating owns no book selection, no discount eligibility, and no override precedence.

Names differ from the frozen surfaces where they must: M9.3 owns `ChargeLine` and the invoicing
vocabulary. Everything here is named distinctly — the same call M9.6 made for `CreateCatalogPlan`
and M9.11 for `IngestMetricSignal`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from billing.foundation import (
    BillingInterval,
    BillingPeriod,
    Currency,
    MeterAggregation,
    MinimumCommitment,
    Money,
    PriceResolutionRequest,
    RatingAction,
    RatingBasis,
    RatingMode,
    RatingResult,
    RatingRun,
    RatingSearchQuery,
    RatingState,
    RatingTrigger,
    RerateReason,
    Scope,
    UsageWindow,
    WindowGranularity,
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
class DefineMinimumCommitment(_Command):
    """Declare a spend floor for an account, and what counts toward it."""

    account_id: str = ""
    code: str = ""
    amount_minor: int = 0
    currency: Currency = Currency.INR
    subscription_id: str = ""
    price_codes: tuple[str, ...] = ()
    interval: BillingInterval = BillingInterval.MONTHLY
    contract_id: str = ""
    description: str = ""
    effective_from: float = 0.0
    effective_until: float | None = None


@dataclass(frozen=True, slots=True)
class RetireMinimumCommitment(_Command):
    """Retirement is a state, never a deletion — a commitment that produced a true-up on an issued
    invoice must remain readable."""

    code: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RateSubscriptionPeriod(_Command):
    """The correctness core. Idempotent on the basis digest: rating unchanged inputs twice is one
    run, and the second call returns the first one's result."""

    subscription_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class RateAccountPeriod(_Command):
    """Rate every live agreement on an account. Safe to run on several instances at once: each
    agreement goes through the same idempotent path, so overlapping runs converge."""

    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class RerateSubscriptionPeriod(_Command):
    """Rate a period again because an input moved.

    Never an edit. A provisional predecessor is superseded; a **finalized** one is adjusted and
    left exactly as it was billed."""

    subscription_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0
    reason: RerateReason = RerateReason.MANUAL_REVIEW
    detail: str = ""


@dataclass(frozen=True, slots=True)
class FinalizeRatingRun(_Command):
    """Freeze a run. **A one-way door** — after this, a revision is a new run plus a credit note,
    never a retroactive change."""

    rating_run_id: str = ""


@dataclass(frozen=True, slots=True)
class DiscardRatingRun(_Command):
    rating_run_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RecordRatingAdjustment(_Command):
    """Turn the difference between a finalized run and a newer one into adjustment charges."""

    rating_run_id: str = ""
    against_run_id: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SnapshotRatingRun(_Command):
    rating_run_id: str = ""
    reason: str = ""


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetRatingRun(_Query):
    rating_run_id: str = ""


@dataclass(frozen=True, slots=True)
class ListRatingRuns(_Query):
    account_id: str = ""
    subscription_id: str = ""
    period_start: float | None = None
    period_end: float | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class PreviewSubscriptionRating(_Query):
    """What a period *would* cost. A query, so it writes nothing and can never become the billed
    figure — the preview/authoritative distinction is enforced by which bus it travels on."""

    subscription_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class GetChargeBreakdown(_Query):
    rating_run_id: str = ""
    charge_id: str = ""


@dataclass(frozen=True, slots=True)
class ExplainRatingRun(_Query):
    rating_run_id: str = ""


@dataclass(frozen=True, slots=True)
class CompareRatingRuns(_Query):
    before_run_id: str = ""
    after_run_id: str = ""


@dataclass(frozen=True, slots=True)
class GetRerateVerdict(_Query):
    """Whether a period needs rating again — asked without rating anything."""

    subscription_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class InspectRatingRun(_Query):
    rating_run_id: str = ""


@dataclass(frozen=True, slots=True)
class DiagnoseRatingRun(_Query):
    rating_run_id: str = ""


@dataclass(frozen=True, slots=True)
class SearchRatingRuns(_Query):
    query: RatingSearchQuery = field(default_factory=RatingSearchQuery)


@dataclass(frozen=True, slots=True)
class ListMinimumCommitments(_Query):
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class RatingTimeline(_Query):
    subscription_id: str = ""
    limit: int = 100


#: The whole command and query surface, declared as tuples so "every message is registered on a
#: bus and every one is org-scoped" is checkable by iteration rather than by a reviewer's memory.
ALL_RATING_COMMANDS = (
    DefineMinimumCommitment,
    RetireMinimumCommitment,
    RateSubscriptionPeriod,
    RateAccountPeriod,
    RerateSubscriptionPeriod,
    FinalizeRatingRun,
    DiscardRatingRun,
    RecordRatingAdjustment,
    SnapshotRatingRun,
)

ALL_RATING_QUERIES = (
    GetRatingRun,
    ListRatingRuns,
    PreviewSubscriptionRating,
    GetChargeBreakdown,
    ExplainRatingRun,
    CompareRatingRuns,
    GetRerateVerdict,
    InspectRatingRun,
    DiagnoseRatingRun,
    SearchRatingRuns,
    ListMinimumCommitments,
    RatingTimeline,
)


# ── view DTOs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ComponentView:
    kind: str
    label: str
    amount: MoneyView
    units: int
    unit_amount: MoneyView | None
    detail: str


@dataclass(frozen=True, slots=True)
class ChargeDiscountView:
    code: str
    display_name: str
    kind: str
    priority: int
    stackable: bool
    reduction: MoneyView
    source: str


@dataclass(frozen=True, slots=True)
class ChargeProvenanceView:
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
    usage_ids: tuple[str, ...]
    usage_record_count: int
    usage_digest: str
    entitlement_version: int
    included_units_source: str
    override_applied: bool
    resolution_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChargeView:
    id: str
    kind: str
    description: str
    currency: str
    quantity: int
    unit: str
    list_amount: MoneyView | None
    gross_amount: MoneyView | None
    discount_amount: MoneyView | None
    net_amount: MoneyView
    shape: str
    interval: str
    components: tuple[ComponentView, ...]
    discounts: tuple[ChargeDiscountView, ...]
    provenance: ChargeProvenanceView
    breaks_down_correctly: bool
    digest: str


@dataclass(frozen=True, slots=True)
class BasisView:
    catalog_version: int
    pricing_version: int
    subscription_version: int
    entitlement_version: int
    usage_digest: str
    usage_record_count: int
    price_pins: tuple[str, ...]
    seat_counts: tuple[tuple[str, int], ...]
    metered_units: tuple[tuple[str, int], ...]
    commitment_codes: tuple[str, ...]
    digest: str


@dataclass(frozen=True, slots=True)
class RatingResultView:
    account_id: str
    subscription_id: str
    period_start: float
    period_end: float
    currency: str
    mode: str
    charges: tuple[ChargeView, ...]
    charge_count: int
    kinds: tuple[str, ...]
    is_hybrid: bool
    subtotal: MoneyView
    discount_total: MoneyView
    total: MoneyView
    basis: BasisView
    digest: str
    breaks_down_correctly: bool
    at: float


@dataclass(frozen=True, slots=True)
class RatingRunView:
    id: str
    account_id: str
    subscription_id: str
    period_start: float
    period_end: float
    currency: str
    state: str
    sequence: int
    basis_digest: str
    result_digest: str
    total: MoneyView
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
    allowed_triggers: tuple[str, ...]
    result: RatingResultView | None


@dataclass(frozen=True, slots=True)
class RatingReceiptView:
    """What one `RateSubscriptionPeriod` did. `outcome` is `rated`, `rerated`, or **`unchanged`** —
    the last one is a success, and saying so explicitly is what lets a nightly job report that it
    checked and found nothing to do."""

    outcome: str
    rating_run_id: str
    subscription_id: str
    basis_digest: str
    total: MoneyView
    charge_count: int
    run: RatingRunView


@dataclass(frozen=True, slots=True)
class BatchRatingReceiptView:
    subscriptions_seen: int
    rated: int
    rerated: int
    unchanged: int
    skipped: int
    receipts: tuple[RatingReceiptView, ...]


@dataclass(frozen=True, slots=True)
class DifferenceView:
    kind: str
    item_id: str
    price_pin: str
    charge_kind: str
    description: str
    before: MoneyView | None
    after: MoneyView | None
    delta: MoneyView | None
    before_quantity: int
    after_quantity: int
    reason: str


@dataclass(frozen=True, slots=True)
class RatingDeltaView:
    subscription_id: str
    period_start: float
    period_end: float
    before_run_id: str
    after_run_id: str
    currency: str
    total_before: MoneyView | None
    total_after: MoneyView | None
    total_delta: MoneyView
    differences: tuple[DifferenceView, ...]
    charges_changed: int
    is_material: bool


@dataclass(frozen=True, slots=True)
class ChargeExplanationView:
    charge_id: str
    kind: str
    description: str
    amount: MoneyView
    lines: tuple[str, ...]
    provenance_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RatingExplanationView:
    rating_run_id: str
    subscription_id: str
    period_start: float
    period_end: float
    currency: str
    total: MoneyView
    basis_lines: tuple[str, ...]
    charges: tuple[ChargeExplanationView, ...]


@dataclass(frozen=True, slots=True)
class ChargeInspectionView:
    charge_id: str
    kind: str
    amount: MoneyView
    breaks_down_correctly: bool
    has_provenance: bool
    has_usage_attribution: bool
    component_count: int
    discount_count: int
    is_sound: bool
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RatingInspectionView:
    rating_run_id: str
    subscription_id: str
    period_start: float
    period_end: float
    state: str
    total: MoneyView
    basis_digest: str
    result_digest: str
    is_sound: bool
    unsound: tuple[str, ...]
    charges: tuple[ChargeInspectionView, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RerateVerdictView:
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


@dataclass(frozen=True, slots=True)
class CommitmentView:
    id: str
    code: str
    account_id: str
    subscription_id: str
    amount: MoneyView
    price_codes: tuple[str, ...]
    interval: str
    contract_id: str
    description: str
    active: bool
    effective_from: float
    effective_until: float | None
    retired_at: float | None


@dataclass(frozen=True, slots=True)
class RatingSnapshotView:
    id: str
    rating_run_id: str
    subscription_id: str
    period_start: float
    period_end: float
    sequence: int
    result_digest: str
    basis_digest: str
    total: MoneyView | None
    charge_count: int
    taken_at: float
    taken_by: str
    reason: str


@dataclass(frozen=True, slots=True)
class RatingActivityView:
    id: str
    action: str
    actor_id: str
    at: float
    account_id: str
    subscription_id: str
    rating_run_id: str
    period_start: float | None
    period_end: float | None
    total: MoneyView | None
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class RatingDiagnosticView:
    path: str
    code: str
    severity: str
    message: str
    remediation: str


@dataclass(frozen=True, slots=True)
class RatingDiagnosticsView:
    rating_run_id: str
    findings: tuple[RatingDiagnosticView, ...]
    error_count: int
    warning_count: int
    is_valid: bool


@dataclass(frozen=True, slots=True)
class RatingSearchView:
    runs: tuple[RatingRunView, ...]
    total: int


# ── mapping ─────────────────────────────────────────────────────────────────────


def money_view(amount: Money | None) -> MoneyView | None:
    if amount is None:
        return None
    return MoneyView(minor=amount.minor, currency=amount.currency.value)


def _money(amount: Money) -> MoneyView:
    """Money on the wire is integer minor units plus a currency, never a formatted string
    (ADR-0023 RS5). Rendering is the Studio's; a pre-formatted amount invites a client to parse
    money back out of text."""
    return MoneyView(minor=amount.minor, currency=amount.currency.value)


def component_view(component) -> ComponentView:
    return ComponentView(
        kind=component.kind.value,
        label=component.label,
        amount=_money(component.amount),
        units=component.units,
        unit_amount=money_view(component.unit_amount),
        detail=component.detail,
    )


def discount_view(discount) -> ChargeDiscountView:
    return ChargeDiscountView(
        code=discount.code,
        display_name=discount.display_name,
        kind=discount.kind.value,
        priority=discount.priority,
        stackable=discount.stackable,
        reduction=_money(discount.reduction),
        source=discount.source,
    )


def provenance_view(p) -> ChargeProvenanceView:
    return ChargeProvenanceView(
        price_code=p.price_code,
        price_version=p.price_version,
        price_pin=p.price_pin,
        price_book_code=p.price_book_code,
        price_book_version=p.price_book_version,
        book_pin=p.book_pin,
        catalog_version=p.catalog_version,
        pricing_version=p.pricing_version,
        subscription_id=p.subscription_id,
        subscription_version=p.subscription_version,
        item_id=p.item_id,
        meter_code=p.meter_code,
        meter_pin=p.meter_pin,
        usage_ids=p.usage_ids,
        usage_record_count=p.usage_record_count,
        usage_digest=p.usage_digest,
        entitlement_version=p.entitlement_version,
        included_units_source=p.included_units_source.value,
        override_applied=p.override_applied,
        resolution_path=p.resolution_path,
    )


def charge_view(charge) -> ChargeView:
    return ChargeView(
        id=charge.id,
        kind=charge.kind.value,
        description=charge.description,
        currency=charge.currency.value,
        quantity=charge.quantity,
        unit=charge.unit,
        list_amount=money_view(charge.list_amount),
        gross_amount=money_view(charge.gross_amount),
        discount_amount=money_view(charge.discount_amount),
        net_amount=_money(charge.amount),
        shape=charge.shape.value if charge.shape else "",
        interval=charge.interval.value if charge.interval else "",
        components=tuple(component_view(c) for c in charge.components),
        discounts=tuple(discount_view(d) for d in charge.discounts),
        provenance=provenance_view(charge.provenance),
        breaks_down_correctly=charge.breaks_down_correctly,
        digest=charge.digest,
    )


def basis_view(basis: RatingBasis) -> BasisView:
    return BasisView(
        catalog_version=basis.catalog_version,
        pricing_version=basis.pricing_version,
        subscription_version=basis.subscription_version,
        entitlement_version=basis.entitlement_version,
        usage_digest=basis.usage_digest,
        usage_record_count=basis.usage_record_count,
        price_pins=basis.price_pins,
        seat_counts=basis.seat_counts,
        metered_units=basis.metered_units,
        commitment_codes=basis.commitment_codes,
        digest=basis.digest,
    )


def result_view(result: RatingResult) -> RatingResultView:
    return RatingResultView(
        account_id=result.account_id,
        subscription_id=result.subscription_id,
        period_start=result.period.start,
        period_end=result.period.end,
        currency=result.currency.value,
        mode=result.mode.value,
        charges=tuple(charge_view(c) for c in result.charges),
        charge_count=result.charge_count,
        kinds=tuple(k.value for k in result.kinds),
        is_hybrid=result.is_hybrid,
        subtotal=_money(result.subtotal),
        discount_total=_money(result.discount_total),
        total=_money(result.total),
        basis=basis_view(result.basis),
        digest=result.digest,
        breaks_down_correctly=result.breaks_down_correctly,
        at=result.at,
    )


def run_view(run: RatingRun, allowed: tuple = ()) -> RatingRunView:
    return RatingRunView(
        id=run.id,
        account_id=run.account_id,
        subscription_id=run.subscription_id,
        period_start=run.period.start,
        period_end=run.period.end,
        currency=run.currency.value,
        state=run.state.value,
        sequence=run.sequence,
        basis_digest=run.basis_digest,
        result_digest=run.result_digest,
        total=_money(run.total),
        charge_count=run.charge_count,
        reason=run.reason.value if run.reason else "",
        supersedes=run.supersedes,
        adjusts=run.adjusts,
        superseded_by=run.superseded_by,
        is_rerate=run.is_rerate,
        is_finalized=run.is_finalized,
        detail=run.detail,
        rated_at=run.rated_at,
        rated_by=run.rated_by,
        finalized_at=run.finalized_at,
        finalized_by=run.finalized_by,
        allowed_triggers=tuple(t.value for t in allowed),
        result=result_view(run.result) if run.result else None,
    )


def delta_view(delta) -> RatingDeltaView:
    return RatingDeltaView(
        subscription_id=delta.subscription_id,
        period_start=delta.period.start,
        period_end=delta.period.end,
        before_run_id=delta.before_run_id,
        after_run_id=delta.after_run_id,
        currency=delta.currency.value,
        total_before=money_view(delta.total_before),
        total_after=money_view(delta.total_after),
        total_delta=_money(delta.total_delta),
        differences=tuple(
            DifferenceView(
                kind=d.kind.value,
                item_id=d.comparison_key[0],
                price_pin=d.comparison_key[1],
                charge_kind=d.comparison_key[2],
                description=d.description,
                before=money_view(d.before),
                after=money_view(d.after),
                delta=money_view(d.delta),
                before_quantity=d.before_quantity,
                after_quantity=d.after_quantity,
                reason=d.reason,
            )
            for d in delta.differences
        ),
        charges_changed=delta.charges_changed,
        is_material=delta.is_material,
    )


def explanation_view(explanation) -> RatingExplanationView:
    return RatingExplanationView(
        rating_run_id=explanation.rating_run_id,
        subscription_id=explanation.subscription_id,
        period_start=explanation.period.start,
        period_end=explanation.period.end,
        currency=explanation.currency.value,
        total=_money(explanation.total),
        basis_lines=explanation.basis_lines,
        charges=tuple(
            ChargeExplanationView(
                charge_id=c.charge_id,
                kind=c.kind.value,
                description=c.description,
                amount=_money(c.amount),
                lines=c.lines,
                provenance_lines=c.provenance_lines,
            )
            for c in explanation.charges
        ),
    )


def inspection_view(inspection) -> RatingInspectionView:
    return RatingInspectionView(
        rating_run_id=inspection.rating_run_id,
        subscription_id=inspection.subscription_id,
        period_start=inspection.period.start,
        period_end=inspection.period.end,
        state=inspection.state.value,
        total=_money(inspection.total),
        basis_digest=inspection.basis_digest,
        result_digest=inspection.result_digest,
        is_sound=inspection.is_sound,
        unsound=inspection.unsound,
        charges=tuple(
            ChargeInspectionView(
                charge_id=c.charge_id,
                kind=c.kind.value,
                amount=_money(c.amount),
                breaks_down_correctly=c.breaks_down_correctly,
                has_provenance=c.has_provenance,
                has_usage_attribution=c.has_usage_attribution,
                component_count=c.component_count,
                discount_count=c.discount_count,
                is_sound=c.is_sound,
                notes=c.notes,
            )
            for c in inspection.charges
        ),
        notes=inspection.notes,
    )


def verdict_view(verdict) -> RerateVerdictView:
    return RerateVerdictView(
        subscription_id=verdict.subscription_id,
        period_start=verdict.period.start,
        period_end=verdict.period.end,
        required=verdict.required,
        current_run_id=verdict.current_run_id,
        current_state=verdict.current_state.value if verdict.current_state else "",
        current_basis_digest=verdict.current_basis_digest,
        proposed_basis_digest=verdict.proposed_basis_digest,
        would_adjust_finalized=verdict.would_adjust_finalized,
        detail=verdict.detail,
    )


def commitment_view(commitment: MinimumCommitment) -> CommitmentView:
    return CommitmentView(
        id=commitment.id,
        code=commitment.code,
        account_id=commitment.account_id,
        subscription_id=commitment.subscription_id,
        amount=_money(commitment.amount),
        price_codes=commitment.price_codes,
        interval=commitment.interval.value,
        contract_id=commitment.contract_id,
        description=commitment.description,
        active=commitment.active,
        effective_from=commitment.effective_from,
        effective_until=commitment.effective_until,
        retired_at=commitment.retired_at,
    )


def snapshot_view(snapshot) -> RatingSnapshotView:
    return RatingSnapshotView(
        id=snapshot.id,
        rating_run_id=snapshot.rating_run_id,
        subscription_id=snapshot.subscription_id,
        period_start=snapshot.period.start,
        period_end=snapshot.period.end,
        sequence=snapshot.sequence,
        result_digest=snapshot.result_digest,
        basis_digest=snapshot.basis_digest,
        total=money_view(snapshot.total),
        charge_count=snapshot.charge_count,
        taken_at=snapshot.taken_at,
        taken_by=snapshot.taken_by,
        reason=snapshot.reason,
    )


def activity_view(activity) -> RatingActivityView:
    return RatingActivityView(
        id=activity.id,
        action=activity.action.value,
        actor_id=activity.actor_id,
        at=activity.at,
        account_id=activity.account_id,
        subscription_id=activity.subscription_id,
        rating_run_id=activity.rating_run_id,
        period_start=activity.period_start,
        period_end=activity.period_end,
        total=(
            MoneyView(minor=activity.total_minor, currency=activity.currency.value)
            if activity.currency
            else None
        ),
        reason=activity.reason,
        detail=activity.detail,
    )


def diagnostics_view(rating_run_id: str, diagnostics) -> RatingDiagnosticsView:
    return RatingDiagnosticsView(
        rating_run_id=rating_run_id,
        findings=tuple(
            RatingDiagnosticView(
                path=f.path,
                code=f.code.value,
                severity=f.severity.value,
                message=f.message,
                remediation=f.remediation,
            )
            for f in diagnostics.findings
        ),
        error_count=len(diagnostics.errors),
        warning_count=len(diagnostics.warnings),
        is_valid=diagnostics.is_valid,
    )


# ── use cases ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RatingRepositories:
    """The rating seams, injected. Interfaces only — implementations in persistence."""

    runs: object
    charges: object
    snapshots: object
    commitments: object
    activity: object


class _RatingInputs:
    """Reads the immutable inputs rating consumes, through the other domains' interfaces.

    Read-only by construction: the only repository methods named in this class are readers. There
    is no `add`, no `replace`, and no `append` anywhere in it, which is what makes "rating never
    mutates usage, subscriptions, catalog versions, pricing versions, or entitlement snapshots" a
    property a test can check by scanning for the method names rather than by reasoning about
    intent."""

    def __init__(
        self, *, context, resolver, aggregator, sealing, requests, basis_builder, commitments
    ) -> None:
        self._ctx = context
        self._resolver = resolver
        self._aggregator = aggregator
        self._sealing = sealing
        self._requests = requests
        self._basis = basis_builder
        self._commitment_repo = commitments

    def subscription(self, org_id: str, subscription_id: str):
        subs = getattr(self._ctx.subscriptions_engine, "subscriptions", None)
        found = subs.get(org_id, subscription_id) if subs is not None else None
        if found is None:
            raise _NotFound(f"subscription {subscription_id!r} not found")
        return found

    def live_subscriptions(self, org_id: str, account_id: str) -> tuple:
        subs = getattr(self._ctx.subscriptions_engine, "subscriptions", None)
        if subs is None:
            return ()
        return tuple(s for s in subs.for_account(org_id, account_id) if s.is_live)

    def gather(self, org_id: str, subscription, period: BillingPeriod, *, at: float):
        """Everything one rating run consumes, assembled from published artefacts only."""
        pricing = self._pricing_version(org_id, subscription.pricing_version)
        entitlements, entitlement_version = self._entitlements(org_id, subscription.account_id)
        usage = self._usage(org_id, subscription.account_id, period)
        usage_digest = self._sealing.seal_digest(usage)
        units, usage_ids = self._metered(org_id, subscription, usage, period, at=at)
        prices = self._prices(org_id, subscription, pricing, at=at)
        request_set = self._requests.build(
            subscription.items,
            prices=prices,
            usage_units=units,
            usage_ids=usage_ids,
            usage_digest=usage_digest,
            allowances=self._allowances(entitlements),
            entitlement_version=entitlement_version,
            subscription_version=subscription.version,
            catalog_version=subscription.catalog_version,
            pricing_version=subscription.pricing_version,
        )
        commitments = self._commitments(org_id, subscription)
        basis = self._basis.build(
            org_id=org_id,
            account_id=subscription.account_id,
            subscription_id=subscription.id,
            period=period,
            currency=subscription.currency,
            catalog_version=subscription.catalog_version,
            pricing_version=subscription.pricing_version,
            subscription_version=subscription.version,
            entitlement_version=entitlement_version,
            usage_digest=usage_digest,
            usage_record_count=len([u for u in usage if u.is_billable]),
            price_pins=subscription.price_pins,
            seat_counts=tuple(
                (i.price_code, i.quantity) for i in subscription.items if i.is_seat_based
            ),
            metered_units=tuple(units.items()),
            commitment_codes=tuple(c.code for c in commitments),
            at=at,
        )
        return basis, request_set, commitments

    # ── individual reads ────────────────────────────────────────────────────────

    def _pricing_version(self, org_id: str, version: int):
        pricing = getattr(self._ctx.pricing, "versions", None)
        found = pricing.version(org_id, version) if pricing is not None else None
        if found is None:
            raise _NotFound(f"pricing version {version} not found")
        return found

    def _entitlements(self, org_id: str, account_id: str) -> tuple[object | None, int]:
        versions = getattr(self._ctx.entitlements_engine, "versions", None)
        latest = versions.latest(org_id, account_id) if versions is not None else None
        if latest is None:
            return (None, 0)
        return (latest.entitlements, latest.version)

    def _usage(self, org_id: str, account_id: str, period: BillingPeriod) -> tuple:
        usage = getattr(self._ctx.metering_engine, "usage", None)
        if usage is None:
            return ()
        return usage.for_period(org_id, account_id, period.start, period.end)

    def _metered(self, org_id: str, subscription, usage, period: BillingPeriod, *, at: float):
        """Per-meter totals, folded by the **metering** aggregator rather than by a sum here.

        Reusing M9.11's fold is not politeness: two definitions of "how does this meter reduce?"
        would eventually disagree, and the one rating used would be the one on the invoice."""
        meters = getattr(self._ctx.metering_engine, "meters", None)
        window = UsageWindow(WindowGranularity.PERIOD, period.start, period.end)
        units: dict[str, int] = {}
        ids: dict[str, tuple[str, ...]] = {}
        for code in subscription.metered_codes:
            meter = meters.by_code(org_id, code) if meters is not None else None
            aggregate = self._aggregator.aggregate(
                usage,
                aggregate_id=f"{subscription.id}:{code}",
                org_id=org_id,
                account_id=subscription.account_id,
                meter_code=code,
                scope=Scope(org_id=org_id),
                period=period,
                window=window,
                aggregation=meter.aggregation if meter is not None else MeterAggregation.SUM,
                unit=meter.unit if meter is not None else "unit",
                at=at,
            )
            units[code] = aggregate.quantity.units
            ids[code] = tuple(sorted(u.id for u in usage if u.meter_code == code and u.is_billable))
        return units, ids

    def _prices(self, org_id: str, subscription, pricing, *, at: float) -> dict:
        """Resolve every subscribed item's price against the **pinned** pricing version.

        Delegated to M9.7's `PriceResolver` in full: book selection, override precedence, and
        discount eligibility are its decisions, and rating owns none of them."""
        resolved: dict = {}
        for item in subscription.items:
            answer = self._resolver.resolve(
                PriceResolutionRequest(
                    org_id=org_id,
                    price_code=item.price_code,
                    currency=subscription.currency,
                    at=at,
                    catalog_version=subscription.catalog_version,
                    account_id=subscription.account_id,
                    quantity=item.quantity,
                    pricing_version=pricing.version,
                ),
                books=pricing.books,
                discounts=pricing.discounts,
                promotions=pricing.promotions,
                coupons=pricing.coupons,
            )
            if answer is not None:
                resolved[item.price_code] = answer
        return resolved

    def _commitments(self, org_id: str, subscription) -> tuple:
        repo = self._commitment_repo
        if repo is None:
            return ()
        return tuple(
            c
            for c in repo.for_account(org_id, subscription.account_id)
            if c.active
            and c.retired_at is None
            and (not c.subscription_id or c.subscription_id == subscription.id)
        )

    @staticmethod
    def _allowances(entitlements) -> dict[str, int]:
        """Quota allowances, keyed by the meter the gate names.

        A gate without a meter is a feature flag, not a quota, and contributes nothing here — the
        entitlement domain owns that distinction and rating reads it rather than re-deciding it."""
        if entitlements is None:
            return {}
        out: dict[str, int] = {}
        for gate in entitlements.gates:
            if not gate.meter_code:
                continue
            allowance = entitlements.allowance(gate.code)
            if allowance is not None:
                out[gate.meter_code] = allowance
        return out


class RatingCommandUseCases:
    """Orchestration for the Rating Engine. Coordinates; decides nothing.

    Not one method here multiplies a rate, folds a tier, rounds a package, resolves an allowance,
    or compares two amounts to decide a charge. Those are `ChargeCalculator`, `ChargeAssembler`,
    `IncludedQuantityResolver`, `CommitmentService`, and `RatingComparisonService` — and a test
    greps this module to keep it that way."""

    def __init__(
        self,
        *,
        context,
        rating_repos: RatingRepositories,
        factory,
        engine,
        lifecycle,
        rerating,
        comparison,
        adjustments,
        inputs: _RatingInputs,
        validator,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        self._ctx = context
        self._repos = rating_repos
        self._factory = factory
        self._engine = engine
        self._lifecycle = lifecycle
        self._rerating = rerating
        self._comparison = comparison
        self._adjustments = adjustments
        self._inputs = inputs
        self._validator = validator
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── commitments ─────────────────────────────────────────────────────────────

    def define_commitment(self, command: DefineMinimumCommitment) -> MinimumCommitment:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONTRACT_MANAGE)
        with self._uow.new() as uow:
            commitment = self._factory.define_commitment(
                org_id=command.org_id,
                account_id=command.account_id,
                code=command.code,
                amount=Money(command.amount_minor, command.currency),
                subscription_id=command.subscription_id,
                price_codes=command.price_codes,
                interval=command.interval,
                contract_id=command.contract_id,
                description=command.description,
                effective_from=command.effective_from,
                effective_until=command.effective_until,
                existing=self._repos.commitments.for_account(command.org_id, command.account_id),
                actor_id=command.actor_id,
            )
            stored = self._repos.commitments.add(commitment)
            self._record(
                command,
                RatingAction.COMMITMENT_DEFINED,
                account_id=stored.account_id,
                detail=stored.code,
                total=stored.amount,
            )
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    def retire_commitment(self, command: RetireMinimumCommitment) -> MinimumCommitment:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONTRACT_MANAGE)
        with self._uow.new() as uow:
            commitment = self._commitment(command.org_id, command.code)
            stored = self._repos.commitments.replace(
                self._factory.retire_commitment(
                    commitment, actor_id=command.actor_id, reason=command.reason
                )
            )
            self._record(
                command,
                RatingAction.COMMITMENT_RETIRED,
                account_id=stored.account_id,
                detail=stored.code,
                reason=command.reason,
            )
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    # ── rating ──────────────────────────────────────────────────────────────────

    def rate(self, command: RateSubscriptionPeriod) -> RatingReceiptView:
        """Rate one agreement for one period. Idempotent on the basis digest."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            return self._rate_one(
                command,
                command.subscription_id,
                BillingPeriod(command.period_start, command.period_end),
                reason=None,
                detail="",
                uow=uow,
            )

    def rate_account(self, command: RateAccountPeriod) -> BatchRatingReceiptView:
        """Rate every live agreement on an account, each in its own transaction.

        One transaction per agreement rather than one for the batch: a single bad agreement must
        not roll back the twelve that rated correctly, and the derived run id makes re-running the
        batch safe."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        period = BillingPeriod(command.period_start, command.period_end)
        subscriptions = self._inputs.live_subscriptions(command.org_id, command.account_id)
        receipts: list[RatingReceiptView] = []
        counts = {"rated": 0, "rerated": 0, "unchanged": 0, "skipped": 0}
        for subscription in subscriptions:
            with self._uow.new() as uow:
                receipt = self._rate_one(
                    command, subscription.id, period, reason=None, detail="", uow=uow
                )
            receipts.append(receipt)
            counts[receipt.outcome] = counts.get(receipt.outcome, 0) + 1
        return BatchRatingReceiptView(
            subscriptions_seen=len(subscriptions),
            rated=counts["rated"],
            rerated=counts["rerated"],
            unchanged=counts["unchanged"],
            skipped=counts["skipped"],
            receipts=tuple(receipts),
        )

    def rerate(self, command: RerateSubscriptionPeriod) -> RatingReceiptView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            return self._rate_one(
                command,
                command.subscription_id,
                BillingPeriod(command.period_start, command.period_end),
                reason=command.reason,
                detail=command.detail,
                uow=uow,
            )

    def _rate_one(
        self, command, subscription_id: str, period: BillingPeriod, *, reason, detail: str, uow
    ) -> RatingReceiptView:
        """One agreement, one period, one transaction. Three outcomes, all successes.

        **`unchanged` is the interesting one.** The basis digest is compared before anything is
        rated, so a rerun over unmoved inputs writes no run, emits `rating.unchanged`, and returns
        the existing figure. That is what stops a nightly job from appending an identical run every
        night — and it is the same trick M9.11 used for a duplicate source event."""
        now = self._clock.now()
        subscription = self._inputs.subscription(command.org_id, subscription_id)
        basis, request_set, commitments = self._inputs.gather(
            command.org_id, subscription, period, at=now
        )
        current = self._repos.runs.latest_for_period(
            command.org_id, subscription_id, period.start, period.end
        )
        verdict = self._rerating.assess(basis, current, reason=reason)
        if not verdict.required and current is not None:
            self._factory.emit_unchanged(current, actor_id=command.actor_id)
            uow.stage(self._event(command, current.id, outcome="unchanged"))
            return self._receipt("unchanged", current)

        supersedes, adjusts = self._rerating.links(current)
        sequence = self._repos.runs.next_sequence(
            command.org_id, subscription_id, period.start, period.end
        )
        run = self._factory.open_run(
            basis=basis,
            sequence=sequence,
            supersedes=supersedes,
            adjusts=adjusts,
            reason=reason,
            detail=detail or ", ".join(request_set.unpriced),
            actor_id=command.actor_id,
        )
        result = self._engine.rate(
            basis,
            request_set.requests,
            rating_run_id=run.id,
            commitments=commitments,
            mode=RatingMode.AUTHORITATIVE,
            at=now,
        )
        rated = self._factory.complete_run(
            run, result, state=RatingState.RATED, actor_id=command.actor_id
        )
        stored = self._repos.runs.append(rated)
        self._repos.charges.append_all(result.charges, rating_run_id=stored.id)
        if current is not None and supersedes:
            self._supersede(command, current, stored.id, at=now)
        self._record(
            command,
            RatingAction.RERATED if stored.is_rerate else RatingAction.RATED,
            account_id=stored.account_id,
            subscription_id=stored.subscription_id,
            rating_run_id=stored.id,
            period=period,
            total=stored.total,
            reason=reason.value if reason else "",
            detail=stored.basis_digest,
        )
        uow.stage(
            self._event(
                command,
                stored.id,
                outcome="rerated" if stored.is_rerate else "rated",
                total_minor=stored.total.minor,
                charges=stored.charge_count,
            )
        )
        return self._receipt("rerated" if stored.is_rerate else "rated", stored)

    def _supersede(self, command, current: RatingRun, successor_id: str, *, at: float) -> None:
        moved = self._lifecycle.apply(
            current,
            RatingTrigger.SUPERSEDE,
            at=at,
            actor_id=command.actor_id,
            superseded_by=successor_id,
        )
        self._repos.runs.replace(moved)
        self._factory.emit_run_fact(
            moved,
            event_type=_superseded_event(),
            actor_id=command.actor_id,
            superseded_by=successor_id,
        )

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def finalize(self, command: FinalizeRatingRun) -> RatingRunView:
        """Freeze a run. Refuses a run whose arithmetic does not explain itself — a figure nobody
        can defend must not reach an invoice."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_INVOICE_ISSUE)
        with self._uow.new() as uow:
            run = self._run(command.org_id, command.rating_run_id)
            self._lifecycle.guard_editable(run)
            self._fail_on_errors(self._validator.validate_run(run))
            moved = self._lifecycle.apply(
                run, RatingTrigger.FINALIZE, at=self._clock.now(), actor_id=command.actor_id
            )
            stored = self._repos.runs.replace(moved)
            self._factory.emit_run_fact(
                stored,
                event_type=_finalized_event(),
                actor_id=command.actor_id,
                total_minor=stored.total.minor,
                result_digest=stored.result_digest,
            )
            self._record(
                command,
                RatingAction.FINALIZED,
                account_id=stored.account_id,
                subscription_id=stored.subscription_id,
                rating_run_id=stored.id,
                period=stored.period,
                total=stored.total,
            )
            uow.stage(self._event(command, stored.id, total_minor=stored.total.minor))
            return run_view(stored, self._lifecycle.allowed(stored.state))

    def discard(self, command: DiscardRatingRun) -> RatingRunView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            run = self._run(command.org_id, command.rating_run_id)
            self._lifecycle.guard_editable(run)
            moved = self._lifecycle.apply(
                run,
                RatingTrigger.DISCARD,
                at=self._clock.now(),
                actor_id=command.actor_id,
                detail=command.reason,
            )
            stored = self._repos.runs.replace(moved)
            self._factory.emit_run_fact(
                stored,
                event_type=_discarded_event(),
                actor_id=command.actor_id,
                reason=command.reason,
            )
            self._record(
                command,
                RatingAction.DISCARDED,
                account_id=stored.account_id,
                subscription_id=stored.subscription_id,
                rating_run_id=stored.id,
                reason=command.reason,
            )
            uow.stage(self._event(command, stored.id, reason=command.reason))
            return run_view(stored, self._lifecycle.allowed(stored.state))

    def adjust(self, command: RecordRatingAdjustment) -> RatingRunView:
        """Express the difference between a finalized run and a newer one as adjustment charges.

        The finalized run is read and left exactly as it is. Its lifecycle has no edge out of it,
        so this method could not amend it even if it tried."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            run = self._run(command.org_id, command.rating_run_id)
            against = self._run(command.org_id, command.against_run_id)
            delta = self._comparison.compare(against, run, at=self._clock.now())
            charges = self._adjustments.charges(
                delta,
                org_id=command.org_id,
                account_id=run.account_id,
                rating_run_id=run.id,
            )
            self._repos.charges.append_all(charges, rating_run_id=run.id)
            self._factory.emit_run_fact(
                run,
                event_type=_adjusted_event(),
                actor_id=command.actor_id,
                against=against.id,
                delta_minor=delta.total_delta.minor,
                charges=len(charges),
            )
            self._record(
                command,
                RatingAction.ADJUSTED,
                account_id=run.account_id,
                subscription_id=run.subscription_id,
                rating_run_id=run.id,
                total=delta.total_delta,
                detail=command.detail or f"against {against.id}",
            )
            uow.stage(self._event(command, run.id, delta_minor=delta.total_delta.minor))
            return run_view(run, self._lifecycle.allowed(run.state))

    def snapshot(self, command: SnapshotRatingRun) -> RatingSnapshotView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            run = self._run(command.org_id, command.rating_run_id)
            existing = self._repos.snapshots.for_run(command.org_id, run.id)
            snapshot = self._repos.snapshots.append(
                self._factory.snapshot(
                    run,
                    actor_id=command.actor_id,
                    reason=command.reason,
                    sequence=len(existing) + 1,
                )
            )
            self._record(
                command,
                RatingAction.SNAPSHOT_TAKEN,
                account_id=run.account_id,
                subscription_id=run.subscription_id,
                rating_run_id=run.id,
                total=run.total,
                detail=snapshot.result_digest,
            )
            uow.stage(self._event(command, snapshot.id, rating_run_id=run.id))
            return snapshot_view(snapshot)

    # ── internals ───────────────────────────────────────────────────────────────

    def _run(self, org_id: str, run_id: str) -> RatingRun:
        found = self._repos.runs.get(org_id, run_id)
        if found is None:
            raise _NotFound(f"rating run {run_id!r} not found")
        return found

    def _commitment(self, org_id: str, code: str) -> MinimumCommitment:
        found = self._repos.commitments.by_code(org_id, code)
        if found is None:
            raise _NotFound(f"minimum commitment {code!r} not found")
        return found

    def _receipt(self, outcome: str, run: RatingRun) -> RatingReceiptView:
        return RatingReceiptView(
            outcome=outcome,
            rating_run_id=run.id,
            subscription_id=run.subscription_id,
            basis_digest=run.basis_digest,
            total=_money(run.total),
            charge_count=run.charge_count,
            run=run_view(run, self._lifecycle.allowed(run.state)),
        )

    def _fail_on_errors(self, diagnostics) -> None:
        """Fail fast on error-severity findings (§A2.6). The Foundation decided *what* is wrong;
        this only decides that a wrong run does not proceed."""
        if diagnostics.has_errors:
            from billing.foundation import BillingValidationError

            raise BillingValidationError(diagnostics)

    def _record(
        self,
        command,
        action: RatingAction,
        *,
        account_id: str = "",
        subscription_id: str = "",
        rating_run_id: str = "",
        period: BillingPeriod | None = None,
        total: Money | None = None,
        reason: str = "",
        detail: str = "",
    ) -> None:
        """Append rating history **inside the caller's transaction**, so the history and the fact
        it describes commit or vanish together."""
        self._repos.activity.append(
            self._factory.activity(
                org_id=command.org_id,
                action=action,
                actor_id=command.actor_id,
                account_id=account_id,
                subscription_id=subscription_id,
                rating_run_id=rating_run_id,
                period=period,
                total=total,
                reason=reason,
                detail=detail,
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.CHARGES_RATED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


class RatingQueryUseCases:
    """Reads. No transaction, no mutation, no arithmetic — every number comes from a Foundation
    service or a stored aggregate."""

    def __init__(
        self,
        *,
        context,
        rating_repos: RatingRepositories,
        engine,
        lifecycle,
        rerating,
        comparison,
        explainer,
        inspector,
        search,
        history,
        validator,
        inputs: _RatingInputs,
        authz: AuthorizationOrchestrator,
        clock,
    ) -> None:
        self._ctx = context
        self._repos = rating_repos
        self._engine = engine
        self._lifecycle = lifecycle
        self._rerating = rerating
        self._comparison = comparison
        self._explainer = explainer
        self._inspector = inspector
        self._search = search
        self._history = history
        self._validator = validator
        self._inputs = inputs
        self._authz = authz
        self._clock = clock

    def run(self, query: GetRatingRun) -> RatingRunView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return run_view(self._run(query.org_id, query.rating_run_id), self._allowed(query))

    def runs(self, query: ListRatingRuns) -> tuple[RatingRunView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        if query.subscription_id and query.period_end is not None:
            found = self._repos.runs.for_period(
                query.org_id, query.subscription_id, query.period_start or 0.0, query.period_end
            )
        elif query.account_id:
            found = self._repos.runs.for_account(query.org_id, query.account_id, query.limit)
        else:
            found = self._repos.runs.all(query.org_id, query.limit)
        return tuple(run_view(r, self._lifecycle.allowed(r.state)) for r in found)

    def preview(self, query: PreviewSubscriptionRating) -> RatingResultView:
        """What a period *would* cost, computed and thrown away.

        Nothing is written: no run, no charge, no activity entry, no event. A preview that left a
        trace would eventually be mistaken for the billed figure, and the whole reason it lives on
        the query bus is that the query bus has no Unit of Work to write with."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        now = self._clock.now()
        period = BillingPeriod(query.period_start, query.period_end)
        subscription = self._inputs.subscription(query.org_id, query.subscription_id)
        basis, request_set, commitments = self._inputs.gather(
            query.org_id, subscription, period, at=now
        )
        from billing.foundation import derive_rating_run_id

        result = self._engine.rate(
            basis,
            request_set.requests,
            rating_run_id=derive_rating_run_id(
                basis.org_id, basis.subscription_id, period.start, period.end, basis.digest
            ),
            commitments=commitments,
            mode=RatingMode.PREVIEW,
            at=now,
        )
        return result_view(result)

    def breakdown(self, query: GetChargeBreakdown) -> ChargeView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        run = self._run(query.org_id, query.rating_run_id)
        charge = run.result.charge(query.charge_id) if run.result else None
        if charge is None:
            raise _NotFound(f"charge {query.charge_id!r} not found on run {query.rating_run_id!r}")
        return charge_view(charge)

    def explain(self, query: ExplainRatingRun) -> RatingExplanationView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        run = self._run(query.org_id, query.rating_run_id)
        return explanation_view(self._explainer.explain(run))

    def compare(self, query: CompareRatingRuns) -> RatingDeltaView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return delta_view(
            self._comparison.compare(
                self._run(query.org_id, query.before_run_id),
                self._run(query.org_id, query.after_run_id),
                at=self._clock.now(),
            )
        )

    def verdict(self, query: GetRerateVerdict) -> RerateVerdictView:
        """Whether the period needs rating again — computed without rating anything."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        now = self._clock.now()
        period = BillingPeriod(query.period_start, query.period_end)
        subscription = self._inputs.subscription(query.org_id, query.subscription_id)
        basis, _, _ = self._inputs.gather(query.org_id, subscription, period, at=now)
        current = self._repos.runs.latest_for_period(
            query.org_id, query.subscription_id, period.start, period.end
        )
        return verdict_view(self._rerating.assess(basis, current))

    def inspect(self, query: InspectRatingRun) -> RatingInspectionView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        run = self._run(query.org_id, query.rating_run_id)
        return inspection_view(self._inspector.inspect(run))

    def diagnose(self, query: DiagnoseRatingRun) -> RatingDiagnosticsView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        run = self._run(query.org_id, query.rating_run_id)
        return diagnostics_view(run.id, self._validator.validate_run(run))

    def search(self, query: SearchRatingRuns) -> RatingSearchView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._search.search(self._repos.runs.all(query.org_id, 1000), query.query)
        return RatingSearchView(
            runs=tuple(run_view(r, self._lifecycle.allowed(r.state)) for r in found.runs),
            total=found.total,
        )

    def commitments(self, query: ListMinimumCommitments) -> tuple[CommitmentView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = (
            self._repos.commitments.for_account(query.org_id, query.account_id)
            if query.account_id
            else self._repos.commitments.all(query.org_id)
        )
        return tuple(commitment_view(c) for c in found)

    def timeline(self, query: RatingTimeline) -> tuple[RatingActivityView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            activity_view(a)
            for a in self._repos.activity.history(query.org_id, query.subscription_id, query.limit)
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _run(self, org_id: str, run_id: str) -> RatingRun:
        found = self._repos.runs.get(org_id, run_id)
        if found is None:
            raise _NotFound(f"rating run {run_id!r} not found")
        return found

    def _allowed(self, query) -> tuple:
        run = self._repos.runs.get(query.org_id, query.rating_run_id)
        return self._lifecycle.allowed(run.state) if run else ()


# ── event lookups ───────────────────────────────────────────────────────────────


def _superseded_event():
    from billing.foundation import BillingEventType as E

    return E.RATING_SUPERSEDED


def _finalized_event():
    from billing.foundation import BillingEventType as E

    return E.RATING_FINALIZED


def _discarded_event():
    from billing.foundation import BillingEventType as E

    return E.RATING_DISCARDED


def _adjusted_event():
    from billing.foundation import BillingEventType as E

    return E.RATING_ADJUSTED


# ── wiring table ────────────────────────────────────────────────────────────────


def register_rating_command_handlers(bus, uc: RatingCommandUseCases) -> None:
    bus.register(DefineMinimumCommitment, uc.define_commitment)
    bus.register(RetireMinimumCommitment, uc.retire_commitment)
    bus.register(RateSubscriptionPeriod, uc.rate)
    bus.register(RateAccountPeriod, uc.rate_account)
    bus.register(RerateSubscriptionPeriod, uc.rerate)
    bus.register(FinalizeRatingRun, uc.finalize)
    bus.register(DiscardRatingRun, uc.discard)
    bus.register(RecordRatingAdjustment, uc.adjust)
    bus.register(SnapshotRatingRun, uc.snapshot)


def register_rating_query_handlers(bus, uc: RatingQueryUseCases) -> None:
    bus.register(GetRatingRun, uc.run)
    bus.register(ListRatingRuns, uc.runs)
    bus.register(PreviewSubscriptionRating, uc.preview)
    bus.register(GetChargeBreakdown, uc.breakdown)
    bus.register(ExplainRatingRun, uc.explain)
    bus.register(CompareRatingRuns, uc.compare)
    bus.register(GetRerateVerdict, uc.verdict)
    bus.register(InspectRatingRun, uc.inspect)
    bus.register(DiagnoseRatingRun, uc.diagnose)
    bus.register(SearchRatingRuns, uc.search)
    bus.register(ListMinimumCommitments, uc.commitments)
    bus.register(RatingTimeline, uc.timeline)

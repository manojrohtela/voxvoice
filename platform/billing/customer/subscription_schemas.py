"""Subscription Engine wire DTOs (M9.9) — Pydantic v2, additive to the earlier schemas.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views, with `to_*()` inbound and `from_*()` outbound. Foundation enums are reused so
validation and OpenAPI enums come from the domain vocabulary.

**Not one field here carries money.** A subscription says what was agreed; what it costs is
resolved against the pinned pricing version, and what it bills is rating's (M9.11). The absence
is asserted by a test, because "we simply did not add an amount field" is a property that decays
the moment nobody is checking.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    AgreementState,
    BillingInterval,
    CancellationPolicy,
    CancellationTiming,
    ChangeTiming,
    Currency,
    DowngradePolicy,
    ItemKind,
    PausePolicy,
    ProrationBehaviour,
    RenewalMode,
    RenewalPolicy,
    ScheduledChangeKind,
    SubscriptionSearchQuery,
    TrialPolicy,
    UpgradePolicy,
)

# ── policy bodies ───────────────────────────────────────────────────────────────


class TrialBody(BaseModel):
    enabled: bool = False
    length_days: int = Field(default=0, ge=0)
    converts_automatically: bool = True
    requires_payment_method: bool = Field(
        default=False,
        description="A declared requirement a consuming surface enforces at conversion. "
        "Billing talks to no payment provider here",
    )
    extension_allowed: bool = False

    def to_policy(self) -> TrialPolicy:
        return TrialPolicy(
            enabled=self.enabled,
            length_days=self.length_days,
            converts_automatically=self.converts_automatically,
            requires_payment_method=self.requires_payment_method,
            extension_allowed=self.extension_allowed,
        )


class RenewalBody(BaseModel):
    mode: RenewalMode = RenewalMode.AUTO
    notice_days: int = Field(default=30, ge=0)
    terms: int | None = Field(default=None, description="Null = perpetual; N = renews N times")

    def to_policy(self) -> RenewalPolicy:
        return RenewalPolicy(mode=self.mode, notice_days=self.notice_days, terms=self.terms)


class PauseBody(BaseModel):
    max_pause_days: int = Field(default=90, ge=0)
    pauses_per_term: int = Field(default=2, ge=0)
    entitlements_retained: bool = Field(
        default=True,
        description="Pausing does not by itself alter entitlements; the Entitlement domain "
        "decides, and this records what was agreed",
    )

    def to_policy(self) -> PausePolicy:
        return PausePolicy(
            max_pause_days=self.max_pause_days,
            pauses_per_term=self.pauses_per_term,
            entitlements_retained=self.entitlements_retained,
        )


class CancellationBody(BaseModel):
    default_timing: CancellationTiming = CancellationTiming.PERIOD_END
    notice_days: int = Field(default=0, ge=0)
    refund_eligible: bool = Field(
        default=False,
        description="A declared flag the Refund domain reads. No refund is issued here",
    )

    def to_policy(self) -> CancellationPolicy:
        return CancellationPolicy(
            default_timing=self.default_timing,
            notice_days=self.notice_days,
            refund_eligible=self.refund_eligible,
        )


class ChangePolicyBody(BaseModel):
    timing: ChangeTiming = ChangeTiming.PERIOD_END
    proration: ProrationBehaviour = ProrationBehaviour.NONE
    allow_mid_period: bool = True
    requires_approval: bool = False

    def to_upgrade(self) -> UpgradePolicy:
        return UpgradePolicy(
            timing=self.timing,
            proration=self.proration,
            allow_mid_period=self.allow_mid_period,
            requires_approval=self.requires_approval,
        )

    def to_downgrade(self) -> DowngradePolicy:
        return DowngradePolicy(
            timing=self.timing,
            proration=self.proration,
            allow_mid_period=self.allow_mid_period,
            requires_approval=self.requires_approval,
        )


class ItemBody(BaseModel):
    price_code: str
    price_version: int = Field(default=1, ge=1, description="The pin that makes it re-derivable")
    kind: ItemKind = ItemKind.FLAT
    quantity: int = Field(default=1, ge=0)
    meter_code: str = ""
    included_units: int = Field(default=0, ge=0)
    label: str = ""


# ── requests ────────────────────────────────────────────────────────────────────


class CreateSubscriptionBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": "cma_1",
                "catalog_version": 3,
                "pricing_version": 7,
                "currency": "INR",
                "plan_code": "pro",
                "items": [
                    {
                        "price_code": "pro_monthly",
                        "price_version": 1,
                        "kind": "seat",
                        "quantity": 10,
                    }
                ],
            }
        }
    )
    account_id: str
    catalog_version: int = Field(ge=1, description="A **published** catalog version — immutable")
    pricing_version: int = Field(ge=1, description="A **published** pricing version — immutable")
    currency: Currency
    plan_code: str = ""
    subscription_number: str = Field(default="", description="Empty = allocated from the sequence")
    interval: BillingInterval = BillingInterval.MONTHLY
    anchor_day: int = Field(default=1, ge=1, le=28)
    items: list[ItemBody] = Field(default_factory=list)
    trial: TrialBody = Field(default_factory=TrialBody)
    renewal: RenewalBody = Field(default_factory=RenewalBody)
    pause: PauseBody = Field(default_factory=PauseBody)
    cancellation: CancellationBody = Field(default_factory=CancellationBody)
    contract_id: str = ""


class StartTrialBody(BaseModel):
    length_days: int = Field(default=0, ge=0, description="0 = use the subscription's policy")


class ReasonBody(BaseModel):
    reason: str = ""


class CancelBody(BaseModel):
    timing: CancellationTiming = CancellationTiming.PERIOD_END
    reason: str = ""
    reason_code: str = ""


class AddItemBody(ItemBody):
    pass


class UpdateItemBody(BaseModel):
    """Every field optional: `null` means "not supplied"."""

    quantity: int | None = Field(default=None, ge=0)
    included_units: int | None = Field(default=None, ge=0)
    label: str | None = None


class ScheduleChangeBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "kind": "downgrade",
                "effective_at": 1767225600.0,
                "target_pricing_version": 8,
                "reason": "customer requested smaller plan at renewal",
            }
        }
    )
    kind: ScheduledChangeKind = ScheduledChangeKind.UPGRADE
    effective_at: float = Field(gt=0, description="Epoch seconds. Intent only — nothing fires it")
    target_catalog_version: int | None = Field(default=None, ge=1)
    target_pricing_version: int | None = Field(default=None, ge=1)
    target_plan_code: str = ""
    items: list[ItemBody] = Field(default_factory=list)
    timing: ChangeTiming = ChangeTiming.PERIOD_END
    reason: str = ""


class SetRenewalBody(RenewalBody):
    pass


class PublishVersionBody(BaseModel):
    reason: str = ""


class RollbackBody(BaseModel):
    to_version: int = Field(ge=1)
    reason: str = ""


# ── responses ───────────────────────────────────────────────────────────────────


class PeriodResponse(BaseModel):
    start: float
    end: float


class CycleResponse(BaseModel):
    interval: str
    anchor_day: int
    cycle_number: int
    current_period: PeriodResponse | None

    @classmethod
    def from_view(cls, view) -> CycleResponse:
        return cls(
            interval=view.interval,
            anchor_day=view.anchor_day,
            cycle_number=view.cycle_number,
            current_period=(
                None
                if view.current_period is None
                else PeriodResponse(start=view.current_period.start, end=view.current_period.end)
            ),
        )


class ItemResponse(BaseModel):
    id: str
    price_code: str
    price_version: int
    pin: str
    kind: str
    quantity: int
    meter_code: str
    included_units: int
    label: str
    is_metered: bool
    is_seat_based: bool

    @classmethod
    def from_view(cls, view) -> ItemResponse:
        return cls(**{f: getattr(view, f) for f in cls.model_fields})


class TrialResponse(BaseModel):
    enabled: bool
    length_days: int
    ends_at: float | None
    converts_automatically: bool
    requires_payment_method: bool
    extension_allowed: bool

    @classmethod
    def from_view(cls, view) -> TrialResponse:
        return cls(**{f: getattr(view, f) for f in cls.model_fields})


class RenewalResponse(BaseModel):
    mode: str
    notice_days: int
    terms: int | None
    terms_used: int
    next_renewal_at: float | None
    renews: bool
    is_automatic: bool

    @classmethod
    def from_view(cls, view) -> RenewalResponse:
        return cls(**{f: getattr(view, f) for f in cls.model_fields})


class PauseResponse(BaseModel):
    max_pause_days: int
    pauses_per_term: int
    pauses_used: int
    pauses_remaining: int
    entitlements_retained: bool
    paused_at: float | None
    resume_at: float | None

    @classmethod
    def from_view(cls, view) -> PauseResponse:
        return cls(**{f: getattr(view, f) for f in cls.model_fields})


class CancellationResponse(BaseModel):
    default_timing: str
    notice_days: int
    refund_eligible: bool
    reason: str
    reason_code: str
    requested_at: float | None
    effective_at: float | None
    is_requested: bool

    @classmethod
    def from_view(cls, view) -> CancellationResponse:
        return cls(**{f: getattr(view, f) for f in cls.model_fields})


class ChangePolicyResponse(BaseModel):
    timing: str
    proration: str
    allow_mid_period: bool
    requires_approval: bool

    @classmethod
    def from_view(cls, view) -> ChangePolicyResponse:
        return cls(**{f: getattr(view, f) for f in cls.model_fields})


class ScheduledChangeResponse(BaseModel):
    id: str
    kind: str
    status: str
    effective_at: float
    target_catalog_version: int | None
    target_pricing_version: int | None
    target_plan_code: str
    item_count: int
    timing: str
    reason: str
    requested_by: str
    requested_at: float
    applied_at: float | None
    is_commercial: bool

    @classmethod
    def from_view(cls, view) -> ScheduledChangeResponse:
        return cls(**{f: getattr(view, f) for f in cls.model_fields})


class SubscriptionResponse(BaseModel):
    id: str
    subscription_number: str
    account_id: str
    catalog_version: int = Field(description="Immutable pin into a published catalog snapshot")
    pricing_version: int = Field(description="Immutable pin into a published pricing snapshot")
    currency: str
    plan_code: str
    state: str
    version: int
    items: list[ItemResponse]
    cycle: CycleResponse
    trial: TrialResponse
    renewal: RenewalResponse
    pause: PauseResponse
    cancellation: CancellationResponse
    upgrade: ChangePolicyResponse
    downgrade: ChangePolicyResponse
    scheduled_changes: list[ScheduledChangeResponse]
    contract_id: str
    seat_count: int
    metered_codes: list[str]
    price_pins: list[str]
    is_live: bool
    is_impaired: bool
    is_closed: bool
    is_hybrid: bool
    is_editable: bool
    allowed_triggers: list[str] = Field(
        description="Lifecycle moves legal from the current state — a UI can render only "
        "buttons that cannot produce a 409"
    )
    created_at: float
    updated_at: float
    started_at: float | None
    cancelled_at: float | None
    ended_at: float | None
    archived_at: float | None

    @classmethod
    def from_view(cls, view) -> SubscriptionResponse:
        return cls(
            id=view.id,
            subscription_number=view.subscription_number,
            account_id=view.account_id,
            catalog_version=view.catalog_version,
            pricing_version=view.pricing_version,
            currency=view.currency,
            plan_code=view.plan_code,
            state=view.state,
            version=view.version,
            items=[ItemResponse.from_view(i) for i in view.items],
            cycle=CycleResponse.from_view(view.cycle),
            trial=TrialResponse.from_view(view.trial),
            renewal=RenewalResponse.from_view(view.renewal),
            pause=PauseResponse.from_view(view.pause),
            cancellation=CancellationResponse.from_view(view.cancellation),
            upgrade=ChangePolicyResponse.from_view(view.upgrade),
            downgrade=ChangePolicyResponse.from_view(view.downgrade),
            scheduled_changes=[
                ScheduledChangeResponse.from_view(c) for c in view.scheduled_changes
            ],
            contract_id=view.contract_id,
            seat_count=view.seat_count,
            metered_codes=list(view.metered_codes),
            price_pins=list(view.price_pins),
            is_live=view.is_live,
            is_impaired=view.is_impaired,
            is_closed=view.is_closed,
            is_hybrid=view.is_hybrid,
            is_editable=view.is_editable,
            allowed_triggers=list(view.allowed_triggers),
            created_at=view.created_at,
            updated_at=view.updated_at,
            started_at=view.started_at,
            cancelled_at=view.cancelled_at,
            ended_at=view.ended_at,
            archived_at=view.archived_at,
        )


class SubscriptionSearchResponse(BaseModel):
    subscriptions: list[SubscriptionResponse]
    total: int = Field(description="Unpaged total, so a caller can render '20 of 137'")

    @classmethod
    def from_view(cls, view) -> SubscriptionSearchResponse:
        return cls(
            subscriptions=[SubscriptionResponse.from_view(s) for s in view.subscriptions],
            total=view.total,
        )


class SubscriptionVersionResponse(BaseModel):
    id: str
    subscription_id: str
    version: int
    published_at: float
    published_by: str
    reason: str
    rolled_back_from: int | None
    item_count: int
    catalog_version: int | None
    pricing_version: int | None
    state: str

    @classmethod
    def from_view(cls, view) -> SubscriptionVersionResponse:
        return cls(**{f: getattr(view, f) for f in cls.model_fields})

    @classmethod
    def from_aggregate(cls, version) -> SubscriptionVersionResponse:
        """Commands return Foundation aggregates; queries return views. Both reach the wire, so
        both get an explicit serializer rather than a duck-typed one."""
        embedded = version.subscription
        return cls(
            id=version.id,
            subscription_id=version.subscription_id,
            version=version.version,
            published_at=version.published_at,
            published_by=version.published_by,
            reason=version.reason,
            rolled_back_from=version.rolled_back_from,
            item_count=version.item_count,
            catalog_version=embedded.catalog_version if embedded else None,
            pricing_version=embedded.pricing_version if embedded else None,
            state=embedded.state.value if embedded else "",
        )


class SubscriptionActivityResponse(BaseModel):
    id: str
    subscription_id: str
    action: str
    actor_id: str
    at: float
    from_state: str
    to_state: str
    version: int
    detail: str

    @classmethod
    def from_view(cls, view) -> SubscriptionActivityResponse:
        return cls(**{f: getattr(view, f) for f in cls.model_fields})


class SubscriptionSnapshotResponse(BaseModel):
    """What agreement existed at an instant. Derived, never stored."""

    subscription_id: str
    at: float
    version: int
    catalog_version: int
    pricing_version: int
    account_id: str
    state: str
    items: list[ItemResponse]
    seat_count: int
    metered_codes: list[str]
    pending_change_count: int
    in_period: bool
    is_reproducible: bool = Field(
        description="True when both pins name real versions — the property that makes an old "
        "invoice re-derivable"
    )

    @classmethod
    def from_view(cls, view) -> SubscriptionSnapshotResponse:
        return cls(
            subscription_id=view.subscription_id,
            at=view.at,
            version=view.version,
            catalog_version=view.catalog_version,
            pricing_version=view.pricing_version,
            account_id=view.account_id,
            state=view.state,
            items=[ItemResponse.from_view(i) for i in view.items],
            seat_count=view.seat_count,
            metered_codes=list(view.metered_codes),
            pending_change_count=view.pending_change_count,
            in_period=view.in_period,
            is_reproducible=view.is_reproducible,
        )


def build_subscription_query(
    *,
    text: str = "",
    states: list[AgreementState] | None = None,
    account_id: str = "",
    plan_code: str = "",
    currencies: list[Currency] | None = None,
    intervals: list[BillingInterval] | None = None,
    catalog_version: int | None = None,
    pricing_version: int | None = None,
    meter_code: str = "",
    live_only: bool = False,
    trialing_only: bool = False,
    with_pending_changes: bool = False,
    renews_automatically: bool | None = None,
    active_at: float | None = None,
    limit: int = 100,
    offset: int = 0,
) -> SubscriptionSearchQuery:
    """Query params → the Foundation's deterministic filter. Translation only."""
    return SubscriptionSearchQuery(
        text=text,
        states=tuple(states or ()),
        account_id=account_id,
        plan_code=plan_code,
        currencies=tuple(currencies or ()),
        intervals=tuple(intervals or ()),
        catalog_version=catalog_version,
        pricing_version=pricing_version,
        meter_code=meter_code,
        live_only=live_only,
        trialing_only=trialing_only,
        with_pending_changes=with_pending_changes,
        renews_automatically=renews_automatically,
        active_at=active_at,
        limit=limit,
        offset=offset,
    )

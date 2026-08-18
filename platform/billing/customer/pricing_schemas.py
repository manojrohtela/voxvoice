"""Pricing Engine wire DTOs (M9.7) — Pydantic v2, additive to the M9.5/M9.6 schemas.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views, with `to_*()` inbound and `from_*()` outbound. Foundation enums are reused
so validation and OpenAPI enums come from the domain vocabulary.

**Money crosses this boundary as integer minor units plus an explicit currency**, never as a
float and never as a formatted string — the Foundation's discipline reaches all the way to
the wire, because a rounding decision made in a JSON serializer is a rounding decision nobody
reviewed (ADR-0023 RS5). Percentages are integer basis points for the same reason.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    BillingInterval,
    CommercialChannel,
    Currency,
    CustomerTier,
    DiscountKind,
    Money,
    PriceBookAudience,
    PriceBookSearchQuery,
    PriceOverride,
    PriceResolutionRequest,
    PriceShape,
    PriceTier,
    PricingState,
    Region,
    Rounding,
)

from .schemas import MoneyBody, MoneyResponse


def _money(view) -> MoneyResponse | None:
    return None if view is None else MoneyResponse(minor=view.minor, currency=view.currency)


def _money_agg(amount) -> MoneyResponse | None:
    """Foundation `Money` → wire money. Integer minor units, explicit currency, no float."""
    return (
        None
        if amount is None
        else MoneyResponse(minor=amount.minor, currency=amount.currency.value)
    )


def _scope_agg(scope) -> ScopeResponse:
    return ScopeResponse(
        region=scope.region.value,
        channels=[c.value for c in scope.channels],
        customer_tiers=[t.value for t in scope.customer_tiers],
        audience=scope.audience.value,
        specificity=scope.specificity,
    )


def _effective_agg(window) -> EffectiveResponse:
    return EffectiveResponse(
        effective_from=window.effective_from, effective_until=window.effective_until
    )


def _discount_agg(policy) -> DiscountResponse:
    return DiscountResponse(
        id=policy.id,
        code=policy.code,
        display_name=policy.display_name,
        kind=policy.kind.value,
        percentage_bp=policy.percentage_bp,
        amount=_money_agg(policy.amount),
        priority=policy.priority,
        stackable=policy.stackable,
        price_codes=list(policy.price_codes),
        bundle_codes=list(policy.bundle_codes),
        min_quantity=policy.min_quantity,
        min_commitment=_money_agg(policy.min_commitment),
        scope=_scope_agg(policy.scope),
        effective=_effective_agg(policy.effective),
        description=policy.description,
    )


# ── shared value shapes ─────────────────────────────────────────────────────────


class EffectiveResponse(BaseModel):
    effective_from: float
    effective_until: float | None = Field(
        default=None, description="Exclusive end; null means open-ended"
    )

    @classmethod
    def from_view(cls, view) -> EffectiveResponse:
        return cls(effective_from=view.effective_from, effective_until=view.effective_until)


class ScopeBody(BaseModel):
    """Who a book or discount is for. `region=global` matches every region; an **empty**
    channel or tier list matches every channel or tier."""

    region: Region = Region.GLOBAL
    channels: list[CommercialChannel] = Field(default_factory=list)
    customer_tiers: list[CustomerTier] = Field(default_factory=list)
    audience: PriceBookAudience = PriceBookAudience.PUBLIC


class ScopeResponse(BaseModel):
    region: str
    channels: list[str]
    customer_tiers: list[str]
    audience: str
    specificity: int = Field(
        description="How narrowly the scope is drawn — the most specific book wins a tie"
    )

    @classmethod
    def from_view(cls, view) -> ScopeResponse:
        return cls(
            region=view.region,
            channels=list(view.channels),
            customer_tiers=list(view.customer_tiers),
            audience=view.audience,
            specificity=view.specificity,
        )


class TierBody(BaseModel):
    up_to: int | None = Field(default=None, description="Exclusive upper bound; null = open")
    unit_price: MoneyBody
    flat_fee: MoneyBody | None = None

    def to_tier(self) -> PriceTier:
        return PriceTier(
            up_to=self.up_to,
            unit_price=self.unit_price.to_money(),
            flat_fee=self.flat_fee.to_money() if self.flat_fee else None,
        )


class TierResponse(BaseModel):
    up_to: int | None
    unit_price: MoneyResponse
    flat_fee: MoneyResponse | None

    @classmethod
    def from_view(cls, view) -> TierResponse:
        return cls(
            up_to=view.up_to,
            unit_price=_money(view.unit_price),
            flat_fee=_money(view.flat_fee),
        )


class PriceResponse(BaseModel):
    id: str
    code: str
    shape: str
    version: int
    currency: str
    interval: str | None
    meter_code: str
    amount: MoneyResponse | None
    unit_amount: MoneyResponse | None
    included_units: int
    package_size: int
    tiers: list[TierResponse]
    effective: EffectiveResponse
    description: str

    @classmethod
    def from_view(cls, view) -> PriceResponse:
        return cls(
            id=view.id,
            code=view.code,
            shape=view.shape,
            version=view.version,
            currency=view.currency,
            interval=view.interval,
            meter_code=view.meter_code,
            amount=_money(view.amount),
            unit_amount=_money(view.unit_amount),
            included_units=view.included_units,
            package_size=view.package_size,
            tiers=[TierResponse.from_view(t) for t in view.tiers],
            effective=EffectiveResponse.from_view(view.effective),
            description=view.description,
        )


class OverrideResponse(BaseModel):
    price_code: str
    amount: MoneyResponse | None
    unit_amount: MoneyResponse | None
    account_id: str
    contract_id: str
    reason: str
    effective: EffectiveResponse

    @classmethod
    def from_view(cls, view) -> OverrideResponse:
        return cls(
            price_code=view.price_code,
            amount=_money(view.amount),
            unit_amount=_money(view.unit_amount),
            account_id=view.account_id,
            contract_id=view.contract_id,
            reason=view.reason,
            effective=EffectiveResponse.from_view(view.effective),
        )


# ── price books ─────────────────────────────────────────────────────────────────


class CreatePriceBookBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "public_inr",
                "display_name": "Public price list (INR)",
                "currency": "INR",
                "scope": {"region": "in", "audience": "public"},
            }
        }
    )
    code: str = Field(description="Stable commercial code — appears in quotes and contracts")
    display_name: str
    currency: Currency = Field(description="A price book is single-currency by construction")
    scope: ScopeBody = Field(default_factory=ScopeBody)
    catalog_version: int | None = Field(
        default=None,
        description="The published catalog version this book prices. Pricing references a "
        "frozen snapshot, never a mutable catalog object",
    )
    effective_from: float = 0.0
    effective_until: float | None = None
    notes: str = ""


class DuplicatePriceBookBody(BaseModel):
    code: str = Field(
        default="",
        description="Empty or the same code creates the next revision (the reprice flow); a "
        "different code creates a new book seeded from this one",
    )
    display_name: str = ""


class PublishPriceBookBody(BaseModel):
    notes: str = ""


class PricingBookResponse(BaseModel):
    id: str
    code: str
    display_name: str
    currency: str
    revision: int
    state: str
    scope: ScopeResponse
    catalog_version: int | None
    effective: EffectiveResponse
    prices: list[PriceResponse]
    overrides: list[OverrideResponse]
    discount_codes: list[str]
    published_version: int | None
    is_editable: bool = Field(
        description="Only a draft accepts a price change; a published book is a historical fact"
    )
    allowed_triggers: list[str] = Field(
        description="Lifecycle moves legal from the current state — a UI can render only "
        "buttons that cannot produce a 409"
    )
    notes: str

    @classmethod
    def from_view(cls, view) -> PricingBookResponse:
        return cls(
            id=view.id,
            code=view.code,
            display_name=view.display_name,
            currency=view.currency,
            revision=view.revision,
            state=view.state,
            scope=ScopeResponse.from_view(view.scope),
            catalog_version=view.catalog_version,
            effective=EffectiveResponse.from_view(view.effective),
            prices=[PriceResponse.from_view(p) for p in view.prices],
            overrides=[OverrideResponse.from_view(o) for o in view.overrides],
            discount_codes=list(view.discount_codes),
            published_version=view.published_version,
            is_editable=view.is_editable,
            allowed_triggers=list(view.allowed_triggers),
            notes=view.notes,
        )


class PriceBookVersionResponse(BaseModel):
    id: str
    code: str
    version: int
    display_name: str
    currency: str
    scope: ScopeResponse
    catalog_version: int | None
    effective: EffectiveResponse
    published_at: float
    published_by: str
    notes: str
    rolled_back_from: int | None
    price_count: int
    price_codes: list[str]

    @classmethod
    def from_view(cls, view) -> PriceBookVersionResponse:
        return cls(
            id=view.id,
            code=view.code,
            version=view.version,
            display_name=view.display_name,
            currency=view.currency,
            scope=ScopeResponse.from_view(view.scope),
            catalog_version=view.catalog_version,
            effective=EffectiveResponse.from_view(view.effective),
            published_at=view.published_at,
            published_by=view.published_by,
            notes=view.notes,
            rolled_back_from=view.rolled_back_from,
            price_count=view.price_count,
            price_codes=list(view.price_codes),
        )

    @classmethod
    def from_aggregate(cls, version) -> PriceBookVersionResponse:
        """Commands return Foundation aggregates; queries return views. Both shapes reach the
        wire, so both are given an explicit serializer rather than a duck-typed one."""
        return cls(
            id=version.id,
            code=version.code,
            version=version.version,
            display_name=version.display_name,
            currency=version.currency.value,
            scope=ScopeResponse(
                region=version.scope.region.value,
                channels=[c.value for c in version.scope.channels],
                customer_tiers=[t.value for t in version.scope.customer_tiers],
                audience=version.scope.audience.value,
                specificity=version.scope.specificity,
            ),
            catalog_version=version.catalog_version,
            effective=EffectiveResponse(
                effective_from=version.effective.effective_from,
                effective_until=version.effective.effective_until,
            ),
            published_at=version.published_at,
            published_by=version.published_by,
            notes=version.notes,
            rolled_back_from=version.rolled_back_from,
            price_count=version.price_count,
            price_codes=sorted({p.code for p in version.prices}),
        )


class PriceBookSearchResponse(BaseModel):
    books: list[PricingBookResponse]
    total: int = Field(description="Unpaged total, so a caller can render '20 of 137'")

    @classmethod
    def from_view(cls, view) -> PriceBookSearchResponse:
        return cls(books=[PricingBookResponse.from_view(b) for b in view.books], total=view.total)


# ── prices ──────────────────────────────────────────────────────────────────────


class CreatePriceBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "pro_monthly",
                "shape": "flat_recurring",
                "amount": {"minor": 999900, "currency": "INR"},
                "interval": "monthly",
            }
        }
    )
    code: str
    shape: PriceShape
    version: int = Field(default=1, ge=1)
    amount: MoneyBody | None = None
    unit_amount: MoneyBody | None = None
    interval: BillingInterval | None = None
    meter_code: str = ""
    included_units: int = Field(default=0, ge=0)
    package_size: int = Field(default=0, ge=0)
    tiers: list[TierBody] = Field(default_factory=list)
    credit_units_per_amount: int = Field(default=0, ge=0)
    rounding: Rounding = Rounding.HALF_UP
    effective_from: float = 0.0
    effective_until: float | None = None
    description: str = ""


class UpdateDraftPriceBody(BaseModel):
    """Every field is optional: `null` means "not supplied". Clearing a value is a different
    command shape, not a silent side effect of omitting it."""

    version: int = Field(default=1, ge=1)
    amount: MoneyBody | None = None
    unit_amount: MoneyBody | None = None
    included_units: int | None = None
    package_size: int | None = None
    tiers: list[TierBody] | None = None
    effective_from: float | None = None
    effective_until: float | None = None
    description: str | None = None


# ── discounts, promotions, coupons ──────────────────────────────────────────────


class CreateDiscountBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "annual_10",
                "display_name": "Annual commitment 10%",
                "kind": "percentage",
                "percentage_bp": 1000,
                "priority": 100,
            }
        }
    )
    code: str
    display_name: str
    kind: DiscountKind = DiscountKind.PERCENTAGE
    percentage_bp: int = Field(
        default=0, ge=0, le=10_000, description="Integer basis points: 2500 = 25%. Never a float"
    )
    amount: MoneyBody | None = None
    priority: int = Field(default=100, description="Lower applies first")
    stackable: bool = Field(
        default=True, description="A non-stackable discount ends the chain, by design"
    )
    price_codes: list[str] = Field(default_factory=list, description="Empty = every price")
    bundle_codes: list[str] = Field(default_factory=list)
    min_quantity: int = Field(default=0, ge=0, description="Eligibility gate; never multiplied")
    min_commitment: MoneyBody | None = None
    scope: ScopeBody = Field(default_factory=ScopeBody)
    rounding: Rounding = Rounding.HALF_UP
    effective_from: float = 0.0
    effective_until: float | None = None
    description: str = ""


class CreatePromotionBody(BaseModel):
    code: str
    display_name: str
    kind: DiscountKind = DiscountKind.PROMOTIONAL
    percentage_bp: int = Field(default=0, ge=0, le=10_000)
    amount: MoneyBody | None = None
    priority: int = 50
    stackable: bool = True
    exclusive: bool = Field(
        default=False, description="This offer is the offer — it does not combine"
    )
    campaign: str = ""
    price_codes: list[str] = Field(default_factory=list)
    scope: ScopeBody = Field(default_factory=ScopeBody)
    effective_from: float = 0.0
    effective_until: float | None = None
    description: str = ""


class CreateCouponBody(BaseModel):
    code: str
    display_name: str
    kind: DiscountKind = DiscountKind.COUPON
    percentage_bp: int = Field(default=0, ge=0, le=10_000)
    amount: MoneyBody | None = None
    priority: int = 40
    stackable: bool = False
    price_codes: list[str] = Field(default_factory=list)
    promotion_code: str = ""
    single_use: bool = False
    max_redemptions: int = Field(
        default=0,
        ge=0,
        description="0 = unlimited. Declared here and enforced by whoever records a "
        "redemption — Pricing never redeems anything",
    )
    effective_from: float = 0.0
    effective_until: float | None = None
    description: str = ""


class DiscountResponse(BaseModel):
    id: str
    code: str
    display_name: str
    kind: str
    percentage_bp: int
    amount: MoneyResponse | None
    priority: int
    stackable: bool
    price_codes: list[str]
    bundle_codes: list[str]
    min_quantity: int
    min_commitment: MoneyResponse | None
    scope: ScopeResponse
    effective: EffectiveResponse
    description: str

    @classmethod
    def from_view(cls, view) -> DiscountResponse:
        return cls(
            id=view.id,
            code=view.code,
            display_name=view.display_name,
            kind=view.kind,
            percentage_bp=view.percentage_bp,
            amount=_money(view.amount),
            priority=view.priority,
            stackable=view.stackable,
            price_codes=list(view.price_codes),
            bundle_codes=list(view.bundle_codes),
            min_quantity=view.min_quantity,
            min_commitment=_money(view.min_commitment),
            scope=ScopeResponse.from_view(view.scope),
            effective=EffectiveResponse.from_view(view.effective),
            description=view.description,
        )

    @classmethod
    def from_aggregate(cls, policy) -> DiscountResponse:
        return _discount_agg(policy)


class PromotionResponse(BaseModel):
    id: str
    code: str
    display_name: str
    campaign: str
    exclusive: bool
    discount: DiscountResponse
    scope: ScopeResponse
    effective: EffectiveResponse
    description: str

    @classmethod
    def from_view(cls, view) -> PromotionResponse:
        return cls(
            id=view.id,
            code=view.code,
            display_name=view.display_name,
            campaign=view.campaign,
            exclusive=view.exclusive,
            discount=DiscountResponse.from_view(view.discount),
            scope=ScopeResponse.from_view(view.scope),
            effective=EffectiveResponse.from_view(view.effective),
            description=view.description,
        )

    @classmethod
    def from_aggregate(cls, promotion) -> PromotionResponse:
        return cls(
            id=promotion.id,
            code=promotion.code,
            display_name=promotion.display_name,
            campaign=promotion.campaign,
            exclusive=promotion.exclusive,
            discount=_discount_agg(promotion.discount),
            scope=_scope_agg(promotion.scope),
            effective=_effective_agg(promotion.effective),
            description=promotion.description,
        )


class CouponResponse(BaseModel):
    id: str
    code: str
    display_name: str
    promotion_code: str
    single_use: bool
    max_redemptions: int
    discount: DiscountResponse
    effective: EffectiveResponse
    description: str

    @classmethod
    def from_view(cls, view) -> CouponResponse:
        return cls(
            id=view.id,
            code=view.code,
            display_name=view.display_name,
            promotion_code=view.promotion_code,
            single_use=view.single_use,
            max_redemptions=view.max_redemptions,
            discount=DiscountResponse.from_view(view.discount),
            effective=EffectiveResponse.from_view(view.effective),
            description=view.description,
        )

    @classmethod
    def from_aggregate(cls, coupon) -> CouponResponse:
        return cls(
            id=coupon.id,
            code=coupon.code,
            display_name=coupon.display_name,
            promotion_code=coupon.promotion_code,
            single_use=coupon.single_use,
            max_redemptions=coupon.max_redemptions,
            discount=_discount_agg(coupon.discount),
            effective=_effective_agg(coupon.effective),
            description=coupon.description,
        )


# ── resolution ──────────────────────────────────────────────────────────────────


class OverrideBody(BaseModel):
    """A negotiated price supplied by the caller. Pricing applies it; the Contract domain
    owns the negotiation."""

    amount: MoneyBody | None = None
    unit_amount: MoneyBody | None = None
    account_id: str = ""
    contract_id: str = ""
    reason: str = ""


class ResolvePriceBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "price_code": "pro_monthly",
                "currency": "INR",
                "at": 1735689600.0,
                "region": "in",
                "customer_tier": "enterprise",
                "coupon_code": "WELCOME20",
            }
        }
    )
    price_code: str
    currency: Currency
    at: float = Field(description="The instant to price at, epoch seconds")
    catalog_version: int | None = None
    price_book_code: str = Field(default="", description="Empty = choose the best-scoped book")
    region: Region = Region.GLOBAL
    channel: CommercialChannel = CommercialChannel.DIRECT
    customer_tier: CustomerTier = CustomerTier.STANDARD
    audience: PriceBookAudience | None = None
    account_id: str = ""
    coupon_code: str = ""
    promotion_codes: list[str] = Field(default_factory=list)
    quantity: int = Field(
        default=0,
        ge=0,
        description="Eligibility input only — a volume discount needs to know it qualifies. "
        "Never multiplied by a price; that is rating",
    )
    commitment: MoneyBody | None = Field(default=None, description="Eligibility input only")
    override: OverrideBody | None = None
    pricing_version: int | None = Field(
        default=None, description="Resolve against a frozen historical snapshot"
    )

    def to_request(self, org_id: str) -> PriceResolutionRequest:
        return PriceResolutionRequest(
            org_id=org_id,
            price_code=self.price_code,
            currency=self.currency,
            at=self.at,
            catalog_version=self.catalog_version,
            price_book_code=self.price_book_code,
            region=self.region,
            channel=self.channel,
            customer_tier=self.customer_tier,
            audience=self.audience,
            account_id=self.account_id,
            coupon_code=self.coupon_code,
            promotion_codes=tuple(self.promotion_codes),
            quantity=self.quantity,
            commitment=self.commitment.to_money() if self.commitment else None,
            override=(
                PriceOverride(
                    price_code=self.price_code,
                    amount=self.override.amount.to_money() if self.override.amount else None,
                    unit_amount=(
                        self.override.unit_amount.to_money() if self.override.unit_amount else None
                    ),
                    account_id=self.override.account_id,
                    contract_id=self.override.contract_id,
                    reason=self.override.reason,
                )
                if self.override
                else None
            ),
            pricing_version=self.pricing_version,
        )


class AppliedDiscountResponse(BaseModel):
    code: str
    display_name: str
    kind: str
    priority: int
    stackable: bool
    reduction: MoneyResponse
    source: str

    @classmethod
    def from_view(cls, view) -> AppliedDiscountResponse:
        return cls(
            code=view.code,
            display_name=view.display_name,
            kind=view.kind,
            priority=view.priority,
            stackable=view.stackable,
            reduction=_money(view.reduction),
            source=view.source,
        )


class ResolvedPriceResponse(BaseModel):
    """What this price *is*, for this customer, at this instant. Not a charge: there is no
    quantity here, no period, no tax, and no total."""

    price_code: str
    price_book_code: str
    price_book_version: int
    currency: str
    shape: str
    at: float
    catalog_version: int | None
    interval: str | None
    meter_code: str
    list_amount: MoneyResponse | None
    net_amount: MoneyResponse | None
    unit_amount: MoneyResponse | None
    tiers: list[TierResponse]
    net_tiers: list[TierResponse]
    included_units: int
    package_size: int
    discounts: list[AppliedDiscountResponse]
    total_discount: MoneyResponse | None
    override_applied: bool
    region: str
    channel: str
    customer_tier: str
    resolution_path: list[str] = Field(
        description="How the answer was reached — which book, which version, which discounts, "
        "in order. A disputed price should be explainable without a debugger"
    )

    @classmethod
    def from_view(cls, view) -> ResolvedPriceResponse:
        return cls(
            price_code=view.price_code,
            price_book_code=view.price_book_code,
            price_book_version=view.price_book_version,
            currency=view.currency,
            shape=view.shape,
            at=view.at,
            catalog_version=view.catalog_version,
            interval=view.interval,
            meter_code=view.meter_code,
            list_amount=_money(view.list_amount),
            net_amount=_money(view.net_amount),
            unit_amount=_money(view.unit_amount),
            tiers=[TierResponse.from_view(t) for t in view.tiers],
            net_tiers=[TierResponse.from_view(t) for t in view.net_tiers],
            included_units=view.included_units,
            package_size=view.package_size,
            discounts=[AppliedDiscountResponse.from_view(d) for d in view.discounts],
            total_discount=_money(view.total_discount),
            override_applied=view.override_applied,
            region=view.region,
            channel=view.channel,
            customer_tier=view.customer_tier,
            resolution_path=list(view.resolution_path),
        )


# ── pricing versions + history ──────────────────────────────────────────────────


class PublishPricingBody(BaseModel):
    catalog_version: int | None = Field(
        default=None, description="The published catalog version this pricing prices"
    )
    notes: str = ""
    require_priced_codes: list[str] = Field(
        default_factory=list,
        description="Completeness gate: publishing fails if any of these has no price",
    )


class RollbackPricingBody(BaseModel):
    to_version: int = Field(ge=1)
    notes: str = ""


class PricingVersionResponse(BaseModel):
    id: str
    version: int
    published_at: float
    published_by: str
    notes: str
    rolled_back_from: int | None
    catalog_version: int | None
    entity_count: int
    price_count: int
    book_codes: list[str]
    currencies: list[str]
    promotion_codes: list[str]
    coupon_codes: list[str]

    @classmethod
    def from_view(cls, view) -> PricingVersionResponse:
        return cls(
            id=view.id,
            version=view.version,
            published_at=view.published_at,
            published_by=view.published_by,
            notes=view.notes,
            rolled_back_from=view.rolled_back_from,
            catalog_version=view.catalog_version,
            entity_count=view.entity_count,
            price_count=view.price_count,
            book_codes=list(view.book_codes),
            currencies=list(view.currencies),
            promotion_codes=list(view.promotion_codes),
            coupon_codes=list(view.coupon_codes),
        )

    @classmethod
    def from_aggregate(cls, snapshot) -> PricingVersionResponse:
        return cls(
            id=snapshot.id,
            version=snapshot.version,
            published_at=snapshot.published_at,
            published_by=snapshot.published_by,
            notes=snapshot.notes,
            rolled_back_from=snapshot.rolled_back_from,
            catalog_version=snapshot.catalog_version,
            entity_count=snapshot.entity_count,
            price_count=snapshot.price_count,
            book_codes=[f"{b.code}@{b.version}" for b in snapshot.books],
            currencies=[c.value for c in snapshot.currencies],
            promotion_codes=[p.code for p in snapshot.promotions],
            coupon_codes=[c.code for c in snapshot.coupons],
        )


class PricingActivityResponse(BaseModel):
    id: str
    action: str
    subject_kind: str
    subject_code: str
    actor_id: str
    at: float
    pricing_version: int | None
    detail: str

    @classmethod
    def from_view(cls, view) -> PricingActivityResponse:
        return cls(
            id=view.id,
            action=view.action,
            subject_kind=view.subject_kind,
            subject_code=view.subject_code,
            actor_id=view.actor_id,
            at=view.at,
            pricing_version=view.pricing_version,
            detail=view.detail,
        )


def build_price_book_query(
    *,
    text: str = "",
    currencies: list[Currency] | None = None,
    regions: list[Region] | None = None,
    channels: list[CommercialChannel] | None = None,
    customer_tiers: list[CustomerTier] | None = None,
    states: list[PricingState] | None = None,
    audiences: list[PriceBookAudience] | None = None,
    price_code: str = "",
    catalog_version: int | None = None,
    active_at: float | None = None,
    limit: int = 100,
    offset: int = 0,
) -> PriceBookSearchQuery:
    """Query params → the Foundation's deterministic filter. Translation only; the filtering
    itself is Foundation behaviour."""
    return PriceBookSearchQuery(
        text=text,
        currencies=tuple(currencies or ()),
        regions=tuple(regions or ()),
        channels=tuple(channels or ()),
        customer_tiers=tuple(customer_tiers or ()),
        states=tuple(states or ()),
        audiences=tuple(audiences or ()),
        price_code=price_code,
        catalog_version=catalog_version,
        active_at=active_at,
        limit=limit,
        offset=offset,
    )


def to_money(body: MoneyBody | None) -> Money | None:
    return body.to_money() if body else None

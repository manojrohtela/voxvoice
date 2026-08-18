"""Pricing Engine application layer (M9.7) — commands, queries, use cases, mapping.

Additive to M9.3 and M9.6: the buses, Unit of Work, authorization orchestrator,
application-event vocabulary, and dispatch discipline are reused exactly as they are. This
module adds 12 commands, 9 queries, their use cases, and the view DTOs — and nothing else.

The four beats are unchanged: **authorize → open a transaction → coordinate the Foundation →
stage an event.** Every pricing rule — price validity, effective-date collision, discount
eligibility and ordering, book selection, lifecycle legality, snapshot assembly, search
filtering — is Foundation behaviour these methods *call*. There is no rule here, and a test
greps for the ones that would be tempting to inline (including, especially, any arithmetic on
`Money`).

Two names differ from the milestone brief on purpose, because M9.3 already owns them:
`PublishPricingBook` (M9.3 has a `PublishPriceBook` command that writes an M9.2 `PriceBook`)
and `GetPricingBook` (M9.3 has `GetPriceBook`). Two clear names beat one name that quietly
changed meaning — the same call M9.6 made for `CreateCatalogPlan`.

As in M9.6, **every command also appends a `PricingActivity`**, inside the same transaction as
the change it describes. Pricing whose history could disagree with its prices would be worse
than pricing with no history at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from billing.foundation import BillingConflict as _Conflict
from billing.foundation import BillingNotFound as _NotFound
from billing.foundation import (
    CommercialChannel,
    Coupon,
    Currency,
    CustomerTier,
    DiscountKind,
    DiscountPolicy,
    EffectiveRange,
    Money,
    Price,
    PriceBookAudience,
    PriceBookSearchQuery,
    PriceBookVersion,
    PriceOverride,
    PriceResolutionRequest,
    PriceShape,
    PriceTier,
    PricingAction,
    PricingBook,
    PricingScope,
    PricingState,
    PricingTrigger,
    PricingVersion,
    Promotion,
    Region,
    ResolvedPrice,
    Rounding,
)

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .mapping import MoneyView  # M9.3's — reused, never shadowed
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CreatePriceBook(_Command):
    code: str = ""
    display_name: str = ""
    currency: Currency = Currency.INR
    region: Region = Region.GLOBAL
    channels: tuple[CommercialChannel, ...] = ()
    customer_tiers: tuple[CustomerTier, ...] = ()
    audience: PriceBookAudience = PriceBookAudience.PUBLIC
    catalog_version: int | None = None
    effective_from: float = 0.0
    effective_until: float | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class PublishPricingBook(_Command):
    """Freeze a draft book into an immutable `PriceBookVersion`.

    Named distinctly from M9.3's `PublishPriceBook`, which writes the M9.2 `PriceBook`
    aggregate. Both exist and mean different things."""

    code: str = ""
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ArchivePriceBook(_Command):
    code: str = ""


@dataclass(frozen=True, slots=True)
class RestorePriceBook(_Command):
    code: str = ""


@dataclass(frozen=True, slots=True)
class DuplicatePriceBook(_Command):
    """Copy a book into a new draft. Same code ⇒ the next revision (the reprice flow); a
    different code ⇒ a new book seeded from this one."""

    source_code: str = ""
    code: str = ""
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class CreatePrice(_Command):
    book_code: str = ""
    code: str = ""
    shape: PriceShape = PriceShape.FLAT_RECURRING
    version: int = 1
    amount: Money | None = None
    unit_amount: Money | None = None
    interval: object | None = None
    meter_code: str = ""
    included_units: int = 0
    package_size: int = 0
    tiers: tuple[PriceTier, ...] = ()
    credit_units_per_amount: int = 0
    rounding: Rounding = Rounding.HALF_UP
    effective_from: float = 0.0
    effective_until: float | None = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class UpdateDraftPrice(_Command):
    """Amend a price in a **draft** book. A published book is immutable; repricing duplicates
    it into a new revision instead."""

    book_code: str = ""
    code: str = ""
    version: int = 1
    amount: Money | None = None
    unit_amount: Money | None = None
    included_units: int | None = None
    package_size: int | None = None
    tiers: tuple[PriceTier, ...] | None = None
    effective_from: float | None = None
    effective_until: float | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class CreateDiscountPolicy(_Command):
    code: str = ""
    display_name: str = ""
    kind: DiscountKind = DiscountKind.PERCENTAGE
    percentage_bp: int = 0
    amount: Money | None = None
    priority: int = 100
    stackable: bool = True
    price_codes: tuple[str, ...] = ()
    bundle_codes: tuple[str, ...] = ()
    min_quantity: int = 0
    min_commitment: Money | None = None
    region: Region = Region.GLOBAL
    channels: tuple[CommercialChannel, ...] = ()
    customer_tiers: tuple[CustomerTier, ...] = ()
    rounding: Rounding = Rounding.HALF_UP
    effective_from: float = 0.0
    effective_until: float | None = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class CreatePromotion(_Command):
    code: str = ""
    display_name: str = ""
    percentage_bp: int = 0
    amount: Money | None = None
    kind: DiscountKind = DiscountKind.PROMOTIONAL
    priority: int = 50
    stackable: bool = True
    exclusive: bool = False
    campaign: str = ""
    price_codes: tuple[str, ...] = ()
    region: Region = Region.GLOBAL
    channels: tuple[CommercialChannel, ...] = ()
    customer_tiers: tuple[CustomerTier, ...] = ()
    effective_from: float = 0.0
    effective_until: float | None = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class CreateCoupon(_Command):
    code: str = ""
    display_name: str = ""
    percentage_bp: int = 0
    amount: Money | None = None
    kind: DiscountKind = DiscountKind.COUPON
    priority: int = 40
    stackable: bool = False
    price_codes: tuple[str, ...] = ()
    promotion_code: str = ""
    single_use: bool = False
    max_redemptions: int = 0
    effective_from: float = 0.0
    effective_until: float | None = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class PublishPricing(_Command):
    catalog_version: int | None = None
    notes: str = ""
    require_priced_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RollbackPricing(_Command):
    to_version: int = 1
    notes: str = ""


ALL_PRICING_COMMANDS = (
    CreatePriceBook,
    PublishPricingBook,
    ArchivePriceBook,
    RestorePriceBook,
    DuplicatePriceBook,
    CreatePrice,
    UpdateDraftPrice,
    CreateDiscountPolicy,
    CreatePromotion,
    CreateCoupon,
    PublishPricing,
    RollbackPricing,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetPricingBook(_Query):
    """Named distinctly from M9.3's `GetPriceBook`, which reads the M9.2 aggregate by id."""

    code: str = ""
    revision: int | None = None  # None ⇒ the highest revision


@dataclass(frozen=True, slots=True)
class SearchPriceBooks(_Query):
    query: PriceBookSearchQuery = field(default_factory=PriceBookSearchQuery)


@dataclass(frozen=True, slots=True)
class ResolvePrice(_Query):
    request: PriceResolutionRequest | None = None


@dataclass(frozen=True, slots=True)
class PricingHistory(_Query):
    subject_code: str = ""
    limit: int = 100


@dataclass(frozen=True, slots=True)
class PriceVersionHistory(_Query):
    code: str = ""  # "" ⇒ every book
    limit: int = 50


@dataclass(frozen=True, slots=True)
class GetPricingVersion(_Query):
    version: int | None = None  # None ⇒ latest


@dataclass(frozen=True, slots=True)
class PricingVersionHistory(_Query):
    """Org-wide pricing snapshots, newest first. Distinct from `PriceVersionHistory`, which
    is the *per-book* version line — the two version independently and both matter."""

    limit: int = 50


@dataclass(frozen=True, slots=True)
class ListDiscountPolicies(_Query):
    pass


@dataclass(frozen=True, slots=True)
class ListPromotions(_Query):
    live_at: float | None = None


@dataclass(frozen=True, slots=True)
class ListCoupons(_Query):
    pass


ALL_PRICING_QUERIES = (
    GetPricingBook,
    SearchPriceBooks,
    ResolvePrice,
    PricingHistory,
    PriceVersionHistory,
    GetPricingVersion,
    PricingVersionHistory,
    ListDiscountPolicies,
    ListPromotions,
    ListCoupons,
)


# ── view DTOs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ScopeView:
    region: str
    channels: tuple[str, ...]
    customer_tiers: tuple[str, ...]
    audience: str
    specificity: int


@dataclass(frozen=True, slots=True)
class EffectiveView:
    effective_from: float
    effective_until: float | None


@dataclass(frozen=True, slots=True)
class TierView:
    up_to: int | None
    unit_price: MoneyView
    flat_fee: MoneyView | None


@dataclass(frozen=True, slots=True)
class PriceView:
    id: str
    code: str
    shape: str
    version: int
    currency: str
    interval: str | None
    meter_code: str
    amount: MoneyView | None
    unit_amount: MoneyView | None
    included_units: int
    package_size: int
    tiers: tuple[TierView, ...]
    effective: EffectiveView
    description: str


@dataclass(frozen=True, slots=True)
class OverrideView:
    price_code: str
    amount: MoneyView | None
    unit_amount: MoneyView | None
    account_id: str
    contract_id: str
    reason: str
    effective: EffectiveView


@dataclass(frozen=True, slots=True)
class PricingBookView:
    id: str
    code: str
    display_name: str
    currency: str
    revision: int
    state: str
    scope: ScopeView
    catalog_version: int | None
    effective: EffectiveView
    prices: tuple[PriceView, ...]
    overrides: tuple[OverrideView, ...]
    discount_codes: tuple[str, ...]
    published_version: int | None
    is_editable: bool
    allowed_triggers: tuple[str, ...]
    notes: str


@dataclass(frozen=True, slots=True)
class PriceBookVersionView:
    id: str
    code: str
    version: int
    display_name: str
    currency: str
    scope: ScopeView
    catalog_version: int | None
    effective: EffectiveView
    published_at: float
    published_by: str
    notes: str
    rolled_back_from: int | None
    price_count: int
    price_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiscountView:
    id: str
    code: str
    display_name: str
    kind: str
    percentage_bp: int
    amount: MoneyView | None
    priority: int
    stackable: bool
    price_codes: tuple[str, ...]
    bundle_codes: tuple[str, ...]
    min_quantity: int
    min_commitment: MoneyView | None
    scope: ScopeView
    effective: EffectiveView
    description: str


@dataclass(frozen=True, slots=True)
class PromotionView:
    id: str
    code: str
    display_name: str
    campaign: str
    exclusive: bool
    discount: DiscountView
    scope: ScopeView
    effective: EffectiveView
    description: str


@dataclass(frozen=True, slots=True)
class CouponView:
    id: str
    code: str
    display_name: str
    promotion_code: str
    single_use: bool
    max_redemptions: int
    discount: DiscountView
    effective: EffectiveView
    description: str


@dataclass(frozen=True, slots=True)
class AppliedDiscountView:
    code: str
    display_name: str
    kind: str
    priority: int
    stackable: bool
    reduction: MoneyView
    source: str


@dataclass(frozen=True, slots=True)
class ResolvedPriceView:
    price_code: str
    price_book_code: str
    price_book_version: int
    currency: str
    shape: str
    at: float
    catalog_version: int | None
    interval: str | None
    meter_code: str
    list_amount: MoneyView | None
    net_amount: MoneyView | None
    unit_amount: MoneyView | None
    tiers: tuple[TierView, ...]
    net_tiers: tuple[TierView, ...]
    included_units: int
    package_size: int
    discounts: tuple[AppliedDiscountView, ...]
    total_discount: MoneyView | None
    override_applied: bool
    region: str
    channel: str
    customer_tier: str
    resolution_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PricingVersionView:
    id: str
    version: int
    published_at: float
    published_by: str
    notes: str
    rolled_back_from: int | None
    catalog_version: int | None
    entity_count: int
    price_count: int
    book_codes: tuple[str, ...]
    currencies: tuple[str, ...]
    promotion_codes: tuple[str, ...]
    coupon_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PricingActivityView:
    id: str
    action: str
    subject_kind: str
    subject_code: str
    actor_id: str
    at: float
    pricing_version: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class PriceBookSearchView:
    books: tuple[PricingBookView, ...]
    total: int


def money_view(amount: Money | None) -> MoneyView | None:
    return None if amount is None else MoneyView(minor=amount.minor, currency=amount.currency.value)


def _effective_view(window: EffectiveRange) -> EffectiveView:
    return EffectiveView(
        effective_from=window.effective_from, effective_until=window.effective_until
    )


def _scope_view(scope: PricingScope) -> ScopeView:
    return ScopeView(
        region=scope.region.value,
        channels=tuple(c.value for c in scope.channels),
        customer_tiers=tuple(t.value for t in scope.customer_tiers),
        audience=scope.audience.value,
        specificity=scope.specificity,
    )


def _tier_view(tier: PriceTier) -> TierView:
    return TierView(
        up_to=tier.up_to,
        unit_price=money_view(tier.unit_price),  # type: ignore[arg-type]
        flat_fee=money_view(tier.flat_fee),
    )


def price_view(price: Price) -> PriceView:
    return PriceView(
        id=price.id,
        code=price.code,
        shape=price.shape.value,
        version=price.version,
        currency=price.currency.value,
        interval=price.interval.value if price.interval is not None else None,
        meter_code=price.meter_code,
        amount=money_view(price.amount),
        unit_amount=money_view(price.unit_amount),
        included_units=price.included_units,
        package_size=price.package_size,
        tiers=tuple(_tier_view(t) for t in price.tiers),
        effective=_effective_view(price.effective),
        description=price.description,
    )


def _override_view(override: PriceOverride) -> OverrideView:
    return OverrideView(
        price_code=override.price_code,
        amount=money_view(override.amount),
        unit_amount=money_view(override.unit_amount),
        account_id=override.account_id,
        contract_id=override.contract_id,
        reason=override.reason,
        effective=_effective_view(override.effective),
    )


def pricing_book_view(
    book: PricingBook, allowed: tuple[PricingTrigger, ...] = ()
) -> PricingBookView:
    return PricingBookView(
        id=book.id,
        code=book.code,
        display_name=book.display_name,
        currency=book.currency.value,
        revision=book.revision,
        state=book.state.value,
        scope=_scope_view(book.scope),
        catalog_version=book.catalog_version,
        effective=_effective_view(book.effective),
        prices=tuple(price_view(p) for p in book.prices),
        overrides=tuple(_override_view(o) for o in book.overrides),
        discount_codes=book.discount_codes,
        published_version=book.published_version,
        is_editable=book.is_editable,
        allowed_triggers=tuple(t.value for t in allowed),
        notes=book.notes,
    )


def price_book_version_view(version: PriceBookVersion) -> PriceBookVersionView:
    return PriceBookVersionView(
        id=version.id,
        code=version.code,
        version=version.version,
        display_name=version.display_name,
        currency=version.currency.value,
        scope=_scope_view(version.scope),
        catalog_version=version.catalog_version,
        effective=_effective_view(version.effective),
        published_at=version.published_at,
        published_by=version.published_by,
        notes=version.notes,
        rolled_back_from=version.rolled_back_from,
        price_count=version.price_count,
        price_codes=tuple(sorted({p.code for p in version.prices})),
    )


def discount_view(policy: DiscountPolicy) -> DiscountView:
    return DiscountView(
        id=policy.id,
        code=policy.code,
        display_name=policy.display_name,
        kind=policy.kind.value,
        percentage_bp=policy.percentage_bp,
        amount=money_view(policy.amount),
        priority=policy.priority,
        stackable=policy.stackable,
        price_codes=policy.price_codes,
        bundle_codes=policy.bundle_codes,
        min_quantity=policy.min_quantity,
        min_commitment=money_view(policy.min_commitment),
        scope=_scope_view(policy.scope),
        effective=_effective_view(policy.effective),
        description=policy.description,
    )


def promotion_view(promotion: Promotion) -> PromotionView:
    return PromotionView(
        id=promotion.id,
        code=promotion.code,
        display_name=promotion.display_name,
        campaign=promotion.campaign,
        exclusive=promotion.exclusive,
        discount=discount_view(promotion.discount),
        scope=_scope_view(promotion.scope),
        effective=_effective_view(promotion.effective),
        description=promotion.description,
    )


def coupon_view(coupon: Coupon) -> CouponView:
    return CouponView(
        id=coupon.id,
        code=coupon.code,
        display_name=coupon.display_name,
        promotion_code=coupon.promotion_code,
        single_use=coupon.single_use,
        max_redemptions=coupon.max_redemptions,
        discount=discount_view(coupon.discount),
        effective=_effective_view(coupon.effective),
        description=coupon.description,
    )


def resolved_price_view(resolved: ResolvedPrice) -> ResolvedPriceView:
    return ResolvedPriceView(
        price_code=resolved.price_code,
        price_book_code=resolved.price_book_code,
        price_book_version=resolved.price_book_version,
        currency=resolved.currency.value,
        shape=resolved.shape.value,
        at=resolved.at,
        catalog_version=resolved.catalog_version,
        interval=resolved.interval.value if resolved.interval is not None else None,
        meter_code=resolved.meter_code,
        list_amount=money_view(resolved.list_amount),
        net_amount=money_view(resolved.net_amount),
        unit_amount=money_view(resolved.unit_amount),
        tiers=tuple(_tier_view(t) for t in resolved.tiers),
        net_tiers=tuple(_tier_view(t) for t in resolved.net_tiers),
        included_units=resolved.included_units,
        package_size=resolved.package_size,
        discounts=tuple(
            AppliedDiscountView(
                code=a.code,
                display_name=a.display_name,
                kind=a.kind.value,
                priority=a.priority,
                stackable=a.stackable,
                reduction=money_view(a.reduction),  # type: ignore[arg-type]
                source=a.source,
            )
            for a in resolved.discounts
        ),
        total_discount=money_view(resolved.total_discount),
        override_applied=resolved.override_applied,
        region=resolved.region.value,
        channel=resolved.channel.value,
        customer_tier=resolved.customer_tier.value,
        resolution_path=resolved.resolution_path,
    )


def pricing_version_view(snapshot: PricingVersion) -> PricingVersionView:
    return PricingVersionView(
        id=snapshot.id,
        version=snapshot.version,
        published_at=snapshot.published_at,
        published_by=snapshot.published_by,
        notes=snapshot.notes,
        rolled_back_from=snapshot.rolled_back_from,
        catalog_version=snapshot.catalog_version,
        entity_count=snapshot.entity_count,
        price_count=snapshot.price_count,
        book_codes=tuple(f"{b.code}@{b.version}" for b in snapshot.books),
        currencies=tuple(c.value for c in snapshot.currencies),
        promotion_codes=tuple(p.code for p in snapshot.promotions),
        coupon_codes=tuple(c.code for c in snapshot.coupons),
    )


def pricing_activity_view(activity) -> PricingActivityView:
    return PricingActivityView(
        id=activity.id,
        action=activity.action.value,
        subject_kind=activity.subject_kind,
        subject_code=activity.subject_code,
        actor_id=activity.actor_id,
        at=activity.at,
        pricing_version=activity.pricing_version,
        detail=activity.detail,
    )


# ── use cases ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PricingRepositories:
    """The pricing seams, injected. Interfaces only — implementations land in persistence."""

    books: object
    book_versions: object
    discounts: object
    promotions: object
    coupons: object
    versions: object
    activity: object


class PricingCommandUseCases:
    """Orchestration for the Pricing Engine. Coordinates; decides nothing.

    Not one method here performs arithmetic on `Money`. Reductions are computed by the
    Foundation's `DiscountEngine`, prices are composed by the `PricingFactory`, and the
    boundary is asserted by a test rather than trusted."""

    def __init__(
        self,
        *,
        context,
        pricing_repos: PricingRepositories,
        factory,
        lifecycle,
        resolver,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        self._ctx = context
        self._repos = pricing_repos
        self._factory = factory
        self._lifecycle = lifecycle
        self._resolver = resolver
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── price books ─────────────────────────────────────────────────────────────

    def create_book(self, command: CreatePriceBook) -> PricingBook:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            if self._repos.books.get(command.org_id, command.code) is not None:
                raise _Conflict(
                    f"price book {command.code!r} already exists; duplicate it to create a "
                    "new revision"
                )
            book = self._factory.create_book(
                org_id=command.org_id,
                code=command.code,
                display_name=command.display_name,
                currency=command.currency,
                scope=PricingScope(
                    region=command.region,
                    channels=command.channels,
                    customer_tiers=command.customer_tiers,
                    audience=command.audience,
                ),
                catalog_version=command.catalog_version,
                effective=EffectiveRange(command.effective_from, command.effective_until),
                notes=command.notes,
                known_catalog_versions=self._known_catalog_versions(command.org_id),
                actor_id=command.actor_id,
            )
            stored = self._repos.books.add(book)
            self._record(command, PricingAction.PRICE_BOOK_CREATED, "price_book", stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    def publish_book(self, command: PublishPricingBook) -> PriceBookVersion:
        """Freeze the draft into an immutable version and mark the book published."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            book = self._book(command.org_id, command.code)
            self._lifecycle.guard_editable(book)
            version = self._repos.book_versions.next_version(command.org_id, book.code)
            frozen = self._factory.publish_book(
                book,
                version=version,
                notes=command.notes,
                known_catalog_versions=self._known_catalog_versions(command.org_id),
                actor_id=command.actor_id,
            )
            stored = self._repos.book_versions.publish(frozen)
            self._repos.books.replace(
                self._lifecycle.apply(
                    book,
                    PricingTrigger.PUBLISH,
                    at=self._clock.now(),
                    published_version=stored.version,
                )
            )
            self._record(
                command,
                PricingAction.PRICE_BOOK_PUBLISHED,
                "price_book",
                stored.code,
                detail=f"v{stored.version}, {stored.price_count} price(s)",
            )
            uow.stage(self._event(command, stored.id, code=stored.code, version=stored.version))
            return stored

    def archive_book(self, command: ArchivePriceBook) -> PricingBook:
        return self._transition(
            command,
            command.code,
            PricingTrigger.ARCHIVE,
            PricingAction.PRICE_BOOK_ARCHIVED,
        )

    def restore_book(self, command: RestorePriceBook) -> PricingBook:
        return self._transition(
            command,
            command.code,
            PricingTrigger.RESTORE,
            PricingAction.PRICE_BOOK_RESTORED,
        )

    def duplicate_book(self, command: DuplicatePriceBook) -> PricingBook:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            source = self._book(command.org_id, command.source_code)
            copy = self._factory.duplicate_book(
                source,
                code=command.code,
                display_name=command.display_name,
                actor_id=command.actor_id,
            )
            stored = self._repos.books.add(copy)
            self._record(
                command,
                PricingAction.PRICE_BOOK_DUPLICATED,
                "price_book",
                stored.code,
                detail=f"copied from {source.code} r{source.revision}",
            )
            uow.stage(
                self._event(
                    command,
                    stored.id,
                    code=stored.code,
                    revision=stored.revision,
                    source=source.code,
                )
            )
            return stored

    # ── prices ──────────────────────────────────────────────────────────────────

    def create_price(self, command: CreatePrice) -> PricingBook:
        """Returns the **book**, because a price is not independently addressable: it belongs
        to the book that gives it its currency, its scope, and its version."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            book = self._book(command.org_id, command.book_code)
            self._lifecycle.guard_editable(book)
            price = self._factory.create_price(
                book,
                code=command.code,
                shape=command.shape,
                version=command.version,
                effective=EffectiveRange(command.effective_from, command.effective_until),
                interval=command.interval,
                amount=command.amount,
                meter_code=command.meter_code,
                unit_amount=command.unit_amount,
                included_units=command.included_units,
                package_size=command.package_size,
                tiers=command.tiers,
                credit_units_per_amount=command.credit_units_per_amount,
                rounding=command.rounding,
                description=command.description,
                actor_id=command.actor_id,
            )
            stored = self._repos.books.replace(
                replace(book, prices=book.prices + (price,), updated_at=self._clock.now())
            )
            self._record(
                command,
                PricingAction.PRICE_CREATED,
                "price",
                command.code,
                detail=f"book {book.code}",
            )
            uow.stage(self._event(command, price.id, book=book.code, code=command.code))
            return stored

    def update_price(self, command: UpdateDraftPrice) -> PricingBook:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            book = self._book(command.org_id, command.book_code)
            self._lifecycle.guard_editable(book)
            existing = next(
                (p for p in book.prices if p.code == command.code and p.version == command.version),
                None,
            )
            if existing is None:
                raise _NotFound(
                    f"price {command.code!r} v{command.version} is not in book {book.code!r}"
                )
            changes = self._price_changes(command, existing)
            updated = self._factory.update_price(
                book, existing, actor_id=command.actor_id, **changes
            )
            stored = self._repos.books.replace(
                replace(
                    book,
                    prices=tuple(updated if p is existing else p for p in book.prices),
                    updated_at=self._clock.now(),
                )
            )
            self._record(
                command,
                PricingAction.PRICE_UPDATED,
                "price",
                command.code,
                detail=f"book {book.code}: {', '.join(sorted(changes)) or 'no change'}",
            )
            uow.stage(self._event(command, updated.id, book=book.code, code=updated.code))
            return stored

    @staticmethod
    def _price_changes(command: UpdateDraftPrice, existing: Price) -> dict:
        """Translate an amend command into the fields it actually changes. `None` means "not
        supplied"; clearing a value is a different command shape, not a silent side effect."""
        changes: dict = {}
        for name in (
            "amount",
            "unit_amount",
            "included_units",
            "package_size",
            "tiers",
            "description",
        ):
            value = getattr(command, name)
            if value is not None:
                changes[name] = value
        if command.effective_from is not None or command.effective_until is not None:
            changes["effective"] = EffectiveRange(
                command.effective_from
                if command.effective_from is not None
                else existing.effective.effective_from,
                command.effective_until
                if command.effective_until is not None
                else existing.effective.effective_until,
            )
        return changes

    # ── discounts, promotions, coupons ──────────────────────────────────────────

    def create_discount(self, command: CreateDiscountPolicy) -> DiscountPolicy:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            policy = self._factory.create_discount(
                org_id=command.org_id,
                code=command.code,
                display_name=command.display_name,
                kind=command.kind,
                percentage_bp=command.percentage_bp,
                amount=command.amount,
                priority=command.priority,
                stackable=command.stackable,
                effective=EffectiveRange(command.effective_from, command.effective_until),
                price_codes=command.price_codes,
                bundle_codes=command.bundle_codes,
                min_quantity=command.min_quantity,
                min_commitment=command.min_commitment,
                scope=self._scope(command),
                rounding=command.rounding,
                description=command.description,
                actor_id=command.actor_id,
            )
            stored = self._repos.discounts.add(policy)
            self._record(command, PricingAction.DISCOUNT_CREATED, "discount", stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    def create_promotion(self, command: CreatePromotion) -> Promotion:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            window = EffectiveRange(command.effective_from, command.effective_until)
            promotion = self._factory.create_promotion(
                org_id=command.org_id,
                code=command.code,
                display_name=command.display_name,
                discount=self._embedded_discount(command, window),
                effective=window,
                campaign=command.campaign,
                exclusive=command.exclusive,
                scope=self._scope(command),
                description=command.description,
                known=self._repos.promotions.all(command.org_id),
                actor_id=command.actor_id,
            )
            stored = self._repos.promotions.add(promotion)
            self._record(command, PricingAction.PROMOTION_CREATED, "promotion", stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    def create_coupon(self, command: CreateCoupon) -> Coupon:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            window = EffectiveRange(command.effective_from, command.effective_until)
            coupon = self._factory.create_coupon(
                org_id=command.org_id,
                code=command.code,
                display_name=command.display_name,
                discount=self._embedded_discount(command, window),
                effective=window,
                promotion_code=command.promotion_code,
                single_use=command.single_use,
                max_redemptions=command.max_redemptions,
                description=command.description,
                known=self._repos.coupons.all(command.org_id),
                actor_id=command.actor_id,
            )
            stored = self._repos.coupons.add(coupon)
            self._record(command, PricingAction.COUPON_CREATED, "coupon", stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    # ── publishing ──────────────────────────────────────────────────────────────

    def publish_pricing(self, command: PublishPricing) -> PricingVersion:
        """Freeze the whole pricing surface into the next immutable version."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            version = self._repos.versions.next_version(command.org_id)
            books = self._repos.books.all(command.org_id)
            frozen = self._published_book_versions(command.org_id, books)
            snapshot = self._factory.publish_pricing(
                org_id=command.org_id,
                version=version,
                catalog_version=command.catalog_version,
                books=frozen,
                drafts=tuple(b for b in books if b.state is not PricingState.DELETED),
                discounts=self._repos.discounts.all(command.org_id),
                promotions=self._repos.promotions.all(command.org_id),
                coupons=self._repos.coupons.all(command.org_id),
                priced_codes=(
                    frozenset(command.require_priced_codes)
                    if command.require_priced_codes
                    else None
                ),
                known_catalog_versions=self._known_catalog_versions(command.org_id),
                notes=command.notes,
                actor_id=command.actor_id,
            )
            stored = self._repos.versions.publish(snapshot)
            self._record(
                command,
                PricingAction.PRICING_PUBLISHED,
                "pricing",
                f"v{stored.version}",
                pricing_version=stored.version,
                detail=command.notes,
            )
            uow.stage(
                self._event(
                    command,
                    stored.id,
                    version=stored.version,
                    catalog_version=stored.catalog_version,
                    prices=stored.price_count,
                )
            )
            return stored

    def rollback_pricing(self, command: RollbackPricing) -> PricingVersion:
        """Publish a **new** version carrying an older version's prices. History is never
        rewritten and the intervening versions remain readable forever."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            source = self._repos.versions.version(command.org_id, command.to_version)
            if source is None:
                raise _NotFound(f"pricing version {command.to_version} not found")
            snapshot = self._factory.rollback_pricing(
                source,
                version=self._repos.versions.next_version(command.org_id),
                notes=command.notes,
                actor_id=command.actor_id,
            )
            stored = self._repos.versions.publish(snapshot)
            self._record(
                command,
                PricingAction.PRICING_ROLLED_BACK,
                "pricing",
                f"v{stored.version}",
                pricing_version=stored.version,
                detail=f"prices restored from v{source.version}",
            )
            uow.stage(
                self._event(
                    command, stored.id, version=stored.version, rolled_back_from=source.version
                )
            )
            return stored

    # ── internals ───────────────────────────────────────────────────────────────

    def _transition(
        self, command, code: str, trigger: PricingTrigger, action: PricingAction
    ) -> PricingBook:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            book = self._book(command.org_id, code)
            # Legality is the Foundation's FSM decision, not this method's.
            moved = self._lifecycle.apply(book, trigger, at=self._clock.now())
            stored = self._repos.books.replace(moved)
            self._record(command, action, "price_book", stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code, state=stored.state.value))
            return stored

    def _book(self, org_id: str, code: str) -> PricingBook:
        book = self._repos.books.get(org_id, code)
        if book is None:
            raise _NotFound(f"price book {code!r} not found")
        return book

    def _published_book_versions(self, org_id: str, books) -> tuple[PriceBookVersion, ...]:
        """The latest published version of every book that has one. A draft book contributes
        nothing to a snapshot — there is nothing frozen to contribute."""
        found = []
        for book in books:
            latest = self._repos.book_versions.latest(org_id, book.code)
            if latest is not None and not any(f.code == latest.code for f in found):
                found.append(latest)
        return tuple(found)

    def _known_catalog_versions(self, org_id: str) -> frozenset[int] | None:
        """The published catalog versions a book may pin to. `None` when the catalog is not
        wired — "not checked here", which is different from "there are none"."""
        catalog = getattr(self._ctx, "catalog", None)
        if catalog is None:
            return None
        return frozenset(v.version for v in catalog.versions.history(org_id, limit=1000))

    @staticmethod
    def _scope(command) -> PricingScope:
        return PricingScope(
            region=getattr(command, "region", Region.GLOBAL),
            channels=getattr(command, "channels", ()),
            customer_tiers=getattr(command, "customer_tiers", ()),
        )

    def _embedded_discount(self, command, window: EffectiveRange) -> DiscountPolicy:
        """A promotion and a coupon each carry their own reduction rather than referencing a
        shared one: a campaign whose discount could be edited from elsewhere would not be
        reproducible after the fact."""
        return self._factory.create_discount(
            org_id=command.org_id,
            code=command.code,
            display_name=command.display_name,
            kind=command.kind,
            percentage_bp=command.percentage_bp,
            amount=command.amount,
            priority=command.priority,
            stackable=command.stackable,
            effective=window,
            price_codes=command.price_codes,
            scope=self._scope(command),
            description=command.description,
            actor_id=command.actor_id,
            emit=False,  # the promotion/coupon fact is the one that matters
        )

    def _record(
        self,
        command,
        action: PricingAction,
        subject_kind: str,
        subject_code: str,
        *,
        pricing_version: int | None = None,
        detail: str = "",
    ) -> None:
        """Append pricing history **inside the caller's transaction**, so the history and the
        price change it describes commit or vanish together."""
        self._repos.activity.append(
            self._factory.activity(
                org_id=command.org_id,
                action=action,
                subject_kind=subject_kind,
                subject_code=subject_code,
                actor_id=command.actor_id,
                pricing_version=pricing_version,
                detail=detail,
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.PRICE_BOOK_PUBLISHED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


class PricingQueryUseCases:
    """Read-only. No transaction, no audit, no mutation."""

    def __init__(self, *, context, pricing_repos: PricingRepositories, search, resolver, authz):
        self._ctx = context
        self._repos = pricing_repos
        self._search = search
        self._resolver = resolver
        self._authz = authz
        from billing.foundation import PricingLifecycleService

        self._lifecycle = PricingLifecycleService()

    def get_book(self, query: GetPricingBook) -> PricingBookView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        book = self._repos.books.get(query.org_id, query.code, query.revision)
        if book is None:
            return None
        return pricing_book_view(book, self._lifecycle.allowed(book.state))

    def search(self, query: SearchPriceBooks) -> PriceBookSearchView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        result = self._search.search(self._repos.books.all(query.org_id), query.query)
        return PriceBookSearchView(
            books=tuple(
                pricing_book_view(b, self._lifecycle.allowed(b.state)) for b in result.books
            ),
            total=result.total,
        )

    def resolve(self, query: ResolvePrice) -> ResolvedPriceView | None:
        """The Pricing Engine's one question. `None` when nothing prices the request — an
        unpriced code is a fact the caller handles, never a zero this layer invents."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        request = query.request
        if request is None:
            return None
        # A historical lookup resolves against the frozen snapshot; a current one resolves
        # against the live published versions. Same resolver, different inputs.
        if request.pricing_version is not None:
            snapshot = self._repos.versions.version(query.org_id, request.pricing_version)
            if snapshot is None:
                raise _NotFound(f"pricing version {request.pricing_version} not found")
            resolved = self._resolver.resolve(
                request,
                books=snapshot.books,
                discounts=snapshot.discounts,
                promotions=snapshot.promotions,
                coupons=snapshot.coupons,
            )
        else:
            resolved = self._resolver.resolve(
                request,
                books=self._repos.book_versions.all(query.org_id),
                discounts=self._repos.discounts.all(query.org_id),
                promotions=self._repos.promotions.all(query.org_id),
                coupons=self._repos.coupons.all(query.org_id),
            )
        return resolved_price_view(resolved) if resolved is not None else None

    def pricing_history(self, query: PricingHistory) -> tuple[PricingActivityView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        entries = (
            self._repos.activity.for_subject(query.org_id, query.subject_code)
            if query.subject_code
            else self._repos.activity.history(query.org_id, query.limit)
        )
        return tuple(pricing_activity_view(a) for a in entries)

    def version_history(self, query: PriceVersionHistory) -> tuple[PriceBookVersionView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            price_book_version_view(v)
            for v in self._repos.book_versions.history(query.org_id, query.code, query.limit)
        )

    def get_version(self, query: GetPricingVersion) -> PricingVersionView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        snapshot = (
            self._repos.versions.version(query.org_id, query.version)
            if query.version is not None
            else self._repos.versions.latest(query.org_id)
        )
        return pricing_version_view(snapshot) if snapshot else None

    def pricing_version_history(
        self, query: PricingVersionHistory
    ) -> tuple[PricingVersionView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            pricing_version_view(s) for s in self._repos.versions.history(query.org_id, query.limit)
        )

    def list_discounts(self, query: ListDiscountPolicies) -> tuple[DiscountView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(discount_view(d) for d in self._repos.discounts.all(query.org_id))

    def list_promotions(self, query: ListPromotions) -> tuple[PromotionView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        promotions = (
            self._repos.promotions.live(query.org_id, query.live_at)
            if query.live_at is not None
            else self._repos.promotions.all(query.org_id)
        )
        return tuple(promotion_view(p) for p in promotions)

    def list_coupons(self, query: ListCoupons) -> tuple[CouponView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(coupon_view(c) for c in self._repos.coupons.all(query.org_id))


# ── wiring table ────────────────────────────────────────────────────────────────


def register_pricing_command_handlers(bus, uc: PricingCommandUseCases) -> None:
    bus.register(CreatePriceBook, uc.create_book)
    bus.register(PublishPricingBook, uc.publish_book)
    bus.register(ArchivePriceBook, uc.archive_book)
    bus.register(RestorePriceBook, uc.restore_book)
    bus.register(DuplicatePriceBook, uc.duplicate_book)
    bus.register(CreatePrice, uc.create_price)
    bus.register(UpdateDraftPrice, uc.update_price)
    bus.register(CreateDiscountPolicy, uc.create_discount)
    bus.register(CreatePromotion, uc.create_promotion)
    bus.register(CreateCoupon, uc.create_coupon)
    bus.register(PublishPricing, uc.publish_pricing)
    bus.register(RollbackPricing, uc.rollback_pricing)


def register_pricing_query_handlers(bus, uc: PricingQueryUseCases) -> None:
    bus.register(GetPricingBook, uc.get_book)
    bus.register(SearchPriceBooks, uc.search)
    bus.register(ResolvePrice, uc.resolve)
    bus.register(PricingHistory, uc.pricing_history)
    bus.register(PriceVersionHistory, uc.version_history)
    bus.register(GetPricingVersion, uc.get_version)
    bus.register(PricingVersionHistory, uc.pricing_version_history)
    bus.register(ListDiscountPolicies, uc.list_discounts)
    bus.register(ListPromotions, uc.list_promotions)
    bus.register(ListCoupons, uc.list_coupons)

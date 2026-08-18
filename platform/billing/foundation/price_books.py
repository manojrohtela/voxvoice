"""Pricing Engine domain (M9.7) — what a catalog version costs, and nothing else.

M9.6 drew the boundary and asserted it: the Catalog says *what a customer gets*; Pricing says
*what they pay*. This module is the other side of that line, and it is deliberately narrow:

**Pricing owns price books, price-book versions, currencies, regional pricing, effective
dating, discounts, promotions, coupons, and price resolution. It owns no product, no plan, no
subscription, no invoice, no usage, and no payment.** Not one type here holds a subscription,
an invoice, a usage record, a tax figure, or a quantity it multiplies — a test asserts each.

Three properties are structural rather than conventional:

* **A price book is immutable once published.** Publishing freezes a `PriceBookVersion`; the
  authoring `PricingBook` becomes read-only. Repricing publishes a *new revision*, so an
  invoice raised two years ago still resolves to the prices it was rated against.
* **Pricing references a published catalog version, never a mutable catalog object.** The two
  version independently on purpose: repricing must not republish the catalog, and correcting
  a product headline must not republish prices.
* **Resolution is deterministic.** Same books, same discounts, same request ⇒ same
  `ResolvedPrice`, including the order discounts were applied in and the path that chose the
  book. `resolution_path` exists so "why is this customer seeing ₹8,991?" is a readable
  answer rather than an investigation.

All amounts are `Money` — integer minor units plus an explicit currency (ADR-0023 RS5).
Percentages are **basis points as integers**, never floats, for the same reason.

Nothing here computes a charge. Resolution answers "what is the price?"; multiplying a price
by a quantity is *rating* (M9.11) and lives elsewhere. `quantity` and `commitment` appear in
a resolution request as **eligibility inputs only** — a volume discount needs to know it
qualifies — and a test asserts no arithmetic ever consumes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .domain import BillingInterval
from .money import Currency, Money, Rounding
from .pricing import EffectiveRange, Price, PriceBookAudience, PriceShape, PriceTier

# ── lifecycle ───────────────────────────────────────────────────────────────────


class PricingState(StrEnum):
    """One lifecycle for a price book.

    `DELETED` is a recorded terminal state rather than a row removal, for the same reason it
    is in the catalog: a book code that once priced a sale must never become silently
    reusable, and *why* a book left the shelf is itself a commercial fact."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    DELETED = "deleted"


class PricingTrigger(StrEnum):
    PUBLISH = "publish"
    ARCHIVE = "archive"
    RESTORE = "restore"
    DELETE = "delete"


# The price-book lifecycle. Three absences are each a decision:
#
#   * there is no PUBLISHED → DELETED edge — a book a customer may have been priced from
#     must be archived first, and archival keeps it readable forever;
#   * there is no PUBLISHED → DRAFT edge — a published book is immutable, and "edit the live
#     price book" is precisely the operation that makes an old invoice unexplainable.
#     Repricing publishes a new revision instead;
#   * there is no edge out of DELETED, which is what makes it terminal.
PRICE_BOOK_LIFECYCLE: StateMachineDefinition = define(
    [
        (PricingState.DRAFT, PricingTrigger.PUBLISH, PricingState.PUBLISHED),
        (PricingState.DRAFT, PricingTrigger.DELETE, PricingState.DELETED),
        (PricingState.PUBLISHED, PricingTrigger.ARCHIVE, PricingState.ARCHIVED),
        (PricingState.ARCHIVED, PricingTrigger.RESTORE, PricingState.PUBLISHED),
        (PricingState.ARCHIVED, PricingTrigger.DELETE, PricingState.DELETED),
    ]
)


# ── commercial dimensions ───────────────────────────────────────────────────────


class Region(StrEnum):
    """A pricing region. `GLOBAL` is the wildcard: a global book prices every region, and a
    regional book prices only its own — which is what makes "list price everywhere, a local
    price in India" expressible without a second pricing model."""

    GLOBAL = "global"
    IN = "in"
    US = "us"
    EU = "eu"
    UK = "uk"
    AE = "ae"
    SG = "sg"
    AU = "au"
    CA = "ca"


class CommercialChannel(StrEnum):
    """How the sale reaches the customer. A channel is a *pricing* dimension because the same
    plan legitimately costs different amounts direct, through a reseller, and on a
    marketplace that takes a cut."""

    DIRECT = "direct"
    SELF_SERVE = "self_serve"
    PARTNER = "partner"
    RESELLER = "reseller"
    OEM = "oem"
    MARKETPLACE = "marketplace"


class CustomerTier(StrEnum):
    """A commercial segment. Not a permission and not an entitlement — it selects which price
    book applies, and decides nothing about what the customer may do."""

    STANDARD = "standard"
    SMB = "smb"
    ENTERPRISE = "enterprise"
    STRATEGIC = "strategic"
    INTERNAL = "internal"


class DiscountKind(StrEnum):
    """The supported reduction shapes. `PERCENTAGE` carries basis points; every other kind
    that reduces by a fixed sum carries `Money`."""

    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"
    BUNDLE = "bundle"
    VOLUME = "volume"
    COMMITMENT = "commitment"
    PROMOTIONAL = "promotional"
    COUPON = "coupon"


@dataclass(frozen=True, slots=True)
class PricingScope:
    """Which customers a price book (or a discount) is for.

    Each dimension has an explicit "any" value rather than a magic sentinel: `Region.GLOBAL`
    matches every region, and an **empty** channel or tier tuple matches every channel or
    tier. `specificity` breaks resolution ties deterministically — the most specific book
    wins, so adding an India-only enterprise book never silently changes anyone else's
    price."""

    region: Region = Region.GLOBAL
    channels: tuple[CommercialChannel, ...] = ()
    customer_tiers: tuple[CustomerTier, ...] = ()
    audience: PriceBookAudience = PriceBookAudience.PUBLIC

    def matches(
        self,
        *,
        region: Region = Region.GLOBAL,
        channel: CommercialChannel = CommercialChannel.DIRECT,
        customer_tier: CustomerTier = CustomerTier.STANDARD,
        audience: PriceBookAudience | None = None,
    ) -> bool:
        if self.region is not Region.GLOBAL and self.region is not region:
            return False
        if self.channels and channel not in self.channels:
            return False
        if self.customer_tiers and customer_tier not in self.customer_tiers:
            return False
        return not (audience is not None and self.audience is not audience)

    @property
    def specificity(self) -> int:
        """How narrowly this scope is drawn. Higher wins a tie; the weights are ordered
        region > channel > tier because that is the order a commercial team reasons in."""
        return (
            (4 if self.region is not Region.GLOBAL else 0)
            + (2 if self.channels else 0)
            + (1 if self.customer_tiers else 0)
        )


# ── overrides ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PriceOverride:
    """A negotiated price that replaces the book price for one account.

    Pricing *applies* an override; it does not own the negotiation. `contract_id` is
    provenance only — the Contract domain owns the contract, and this type carries no term,
    no signature, and no commitment schedule."""

    price_code: str
    amount: Money | None = None
    unit_amount: Money | None = None
    account_id: str = ""  # "" ⇒ applies to every account priced from this book
    contract_id: str = ""
    reason: str = ""
    effective: EffectiveRange = EffectiveRange(0.0)

    def applies_to(self, *, account_id: str, at: float) -> bool:
        if self.account_id and self.account_id != account_id:
            return False
        return self.effective.covers(at)

    @property
    def specificity(self) -> int:
        """An account-scoped override beats a book-wide one, always."""
        return 1 if self.account_id else 0


# ── discounts, promotions, coupons ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DiscountPolicy:
    """One reduction rule and the conditions under which it is *eligible*.

    Percentages are integer basis points (2500 = 25%), applied through `Money.scaled` with a
    declared rounding policy — there is no float anywhere in a discount, because a rounding
    artefact in a discount is a revenue incident nobody reviewed.

    `min_quantity` and `min_commitment` are **eligibility gates**: a volume discount needs to
    know it qualifies. Neither is ever multiplied by anything — that is rating, and it is not
    this domain."""

    id: str
    org_id: str
    code: str
    display_name: str
    kind: DiscountKind = DiscountKind.PERCENTAGE
    percentage_bp: int = 0  # basis points; 10_000 = 100%
    amount: Money | None = None  # FIXED_AMOUNT / BUNDLE / COMMITMENT reductions
    priority: int = 100  # lower applies first
    stackable: bool = True
    effective: EffectiveRange = EffectiveRange(0.0)
    price_codes: tuple[str, ...] = ()  # empty ⇒ every price in scope
    bundle_codes: tuple[str, ...] = ()
    min_quantity: int = 0
    min_commitment: Money | None = None
    scope: PricingScope = field(default_factory=PricingScope)
    rounding: Rounding = Rounding.HALF_UP
    description: str = ""

    @property
    def is_percentage(self) -> bool:
        """Whether this reduction is proportional, decided by *what was supplied* rather than
        by the kind. `kind` says why the discount exists — volume, commitment, a campaign — and
        any of those may legitimately be expressed either way: a volume discount is usually a
        percentage, a bundle discount usually a fixed sum. Tying the shape to the reason would
        force one of them into the wrong form."""
        return self.percentage_bp > 0

    def covers_price(self, price_code: str) -> bool:
        return not self.price_codes or price_code in self.price_codes

    def reduction(self, base: Money) -> Money:
        """The amount this policy takes off `base`. Never negative, never more than `base` —
        a discount that could invert a price would be a credit, and credits are a different
        domain entirely."""
        if self.is_percentage:
            off = base.scaled(max(self.percentage_bp, 0), 10_000, self.rounding)
        elif self.amount is not None:
            off = self.amount
        else:
            return Money.zero(base.currency)
        if off.currency is not base.currency:
            return Money.zero(base.currency)
        if off.is_negative:
            return Money.zero(base.currency)
        return off if off.minor <= base.minor else base


@dataclass(frozen=True, slots=True)
class Promotion:
    """A time-boxed campaign carrying a reduction.

    The reduction is *embedded* rather than referenced: a promotion is a whole commercial
    offer, and a promotion whose discount could be edited from elsewhere would not be
    reproducible. `exclusive` is an eligibility rule (this offer does not combine), not a
    redemption rule — Pricing never redeems anything."""

    id: str
    org_id: str
    code: str
    display_name: str
    discount: DiscountPolicy
    effective: EffectiveRange = EffectiveRange(0.0)
    campaign: str = ""
    exclusive: bool = False
    scope: PricingScope = field(default_factory=PricingScope)
    description: str = ""

    def is_live(self, at: float) -> bool:
        return self.effective.covers(at)


@dataclass(frozen=True, slots=True)
class Coupon:
    """A named, customer-presented discount.

    **Eligibility only.** `single_use` and `max_redemptions` are *declared* limits that a
    consuming domain enforces when it records a redemption; nothing here counts a redemption,
    stores one, or decides whether one already happened. Pricing answers "is this coupon
    valid for this price, right now?" and stops."""

    id: str
    org_id: str
    code: str
    display_name: str
    discount: DiscountPolicy
    effective: EffectiveRange = EffectiveRange(0.0)
    promotion_code: str = ""
    single_use: bool = False
    max_redemptions: int = 0  # 0 ⇒ unlimited; declared here, counted elsewhere
    scope: PricingScope = field(default_factory=PricingScope)
    description: str = ""

    def is_live(self, at: float) -> bool:
        return self.effective.covers(at)


# ── price books ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PricingBook:
    """The **authoring** price book: a mutable draft until it is published.

    Identity is `(org_id, code, revision)`. Repricing does not edit a published book — it
    creates the next revision as a new draft, which is why `is_editable` is a property of the
    state rather than a permission a caller could argue with.

    `catalog_version` pins the book to a *published* catalog version. That pin is the whole
    relationship between the two domains: Pricing reads a frozen snapshot and never reaches
    for a live product."""

    id: str
    org_id: str
    code: str
    display_name: str
    currency: Currency
    revision: int = 1
    state: PricingState = PricingState.DRAFT
    scope: PricingScope = field(default_factory=PricingScope)
    catalog_version: int | None = None
    effective: EffectiveRange = EffectiveRange(0.0)
    prices: tuple[Price, ...] = ()
    overrides: tuple[PriceOverride, ...] = ()
    discount_codes: tuple[str, ...] = ()
    notes: str = ""
    published_version: int | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def is_editable(self) -> bool:
        """Only a draft accepts a price change. A published book is a historical fact."""
        return self.state is PricingState.DRAFT

    @property
    def is_live(self) -> bool:
        return self.state is PricingState.PUBLISHED

    def price(self, code: str, *, at: float | None = None) -> Price | None:
        """Deterministic lookup: among covering versions the highest `version` wins, so
        resolution never depends on insertion order."""
        found = [p for p in self.prices if p.code == code]
        if at is not None:
            found = [p for p in found if p.covers(at)]
        if not found:
            return None
        return max(found, key=lambda p: (p.version, p.effective.effective_from))

    def versions_of(self, code: str) -> tuple[Price, ...]:
        return tuple(sorted((p for p in self.prices if p.code == code), key=lambda p: p.version))

    @property
    def price_codes(self) -> tuple[str, ...]:
        seen: list[str] = []
        for p in self.prices:
            if p.code not in seen:
                seen.append(p.code)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class PriceBookVersion:
    """An immutable published price book.

    Self-contained, exactly like a catalog version: it embeds its prices and overrides as
    they were, so reading revision 3 never depends on what revision 7 did. A subscription
    references `(code, version)` and can be re-priced identically years later.

    `rolled_back_from` records provenance when this version restored an older one. History is
    appended to; nothing is rewritten (§A5)."""

    id: str
    org_id: str
    code: str
    version: int
    display_name: str = ""
    currency: Currency = Currency.INR
    scope: PricingScope = field(default_factory=PricingScope)
    catalog_version: int | None = None
    effective: EffectiveRange = EffectiveRange(0.0)
    published_at: float = 0.0
    published_by: str = ""
    notes: str = ""
    rolled_back_from: int | None = None
    prices: tuple[Price, ...] = ()
    overrides: tuple[PriceOverride, ...] = ()
    discount_codes: tuple[str, ...] = ()

    @property
    def price_count(self) -> int:
        return len(self.prices)

    @property
    def is_rollback(self) -> bool:
        return self.rolled_back_from is not None

    def covers(self, ts: float) -> bool:
        return self.effective.covers(ts)

    def price(self, code: str, *, at: float | None = None) -> Price | None:
        found = [p for p in self.prices if p.code == code]
        if at is not None:
            found = [p for p in found if p.covers(at)]
        if not found:
            return None
        return max(found, key=lambda p: (p.version, p.effective.effective_from))

    def override_for(self, price_code: str, *, account_id: str, at: float) -> PriceOverride | None:
        """The most specific applicable override: account-scoped beats book-wide, and among
        equals the latest `effective_from` wins. Deterministic, never insertion-ordered."""
        candidates = [
            o
            for o in self.overrides
            if o.price_code == price_code and o.applies_to(account_id=account_id, at=at)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda o: (o.specificity, o.effective.effective_from))


# ── resolution ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PriceResolutionRequest:
    """Everything resolution is allowed to consider. Nothing else may influence a price —
    which is what makes the answer reproducible from the request alone.

    `quantity` and `commitment` are **eligibility inputs**: a volume or commitment discount
    needs to know whether it qualifies. Neither is ever multiplied by a price. Rating (M9.11)
    owns that, and a test asserts this module never does it."""

    org_id: str
    price_code: str
    currency: Currency
    at: float = 0.0
    catalog_version: int | None = None
    price_book_code: str = ""  # "" ⇒ choose the best-scoped book
    region: Region = Region.GLOBAL
    channel: CommercialChannel = CommercialChannel.DIRECT
    customer_tier: CustomerTier = CustomerTier.STANDARD
    audience: PriceBookAudience | None = None
    account_id: str = ""
    coupon_code: str = ""
    promotion_codes: tuple[str, ...] = ()
    quantity: int = 0  # eligibility only
    commitment: Money | None = None  # eligibility only
    override: PriceOverride | None = None
    pricing_version: int | None = None  # historical lookup against a frozen snapshot


@dataclass(frozen=True, slots=True)
class AppliedDiscount:
    """One reduction that was applied, and why it was allowed to apply."""

    code: str
    display_name: str
    kind: DiscountKind
    priority: int
    stackable: bool
    reduction: Money
    source: str = "policy"  # policy | promotion | coupon


@dataclass(frozen=True, slots=True)
class ResolvedPrice:
    """The answer: what this price *is*, for this customer, at this instant.

    Not a charge and not an invoice line. There is no quantity here, no period, no tax, and
    no total — a caller that wants a charge multiplies this by usage, and that is rating.

    `resolution_path` records how the answer was reached (which book, which version, which
    discounts, in order). A price a customer disputes should be explainable without a
    debugger."""

    price_code: str
    price_book_code: str
    price_book_version: int
    currency: Currency
    shape: PriceShape
    at: float
    catalog_version: int | None = None
    interval: BillingInterval | None = None
    meter_code: str = ""
    list_amount: Money | None = None
    net_amount: Money | None = None
    unit_amount: Money | None = None
    tiers: tuple[PriceTier, ...] = ()
    net_tiers: tuple[PriceTier, ...] = ()
    included_units: int = 0
    package_size: int = 0
    discounts: tuple[AppliedDiscount, ...] = ()
    override_applied: bool = False
    region: Region = Region.GLOBAL
    channel: CommercialChannel = CommercialChannel.DIRECT
    customer_tier: CustomerTier = CustomerTier.STANDARD
    resolution_path: tuple[str, ...] = ()

    @property
    def total_discount(self) -> Money | None:
        if not self.discounts:
            return None if self.list_amount is None else Money.zero(self.currency)
        total = Money.zero(self.currency)
        for applied in self.discounts:
            total = total + applied.reduction
        return total

    @property
    def is_discounted(self) -> bool:
        return bool(self.discounts)

    @property
    def is_metered(self) -> bool:
        return self.shape in (
            PriceShape.PER_UNIT,
            PriceShape.ALLOWANCE_OVERAGE,
            PriceShape.TIERED_GRADUATED,
            PriceShape.VOLUME,
            PriceShape.PACKAGE,
        )


# ── pricing versions + history ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PricingVersion:
    """An immutable, self-contained snapshot of the whole pricing surface at one instant.

    Pinned to a published `catalog_version`: together the pair answers "what was on sale, and
    what did it cost, on the day this subscription started?" from two frozen snapshots rather
    than from live tables that have since moved on.

    Publishing pricing does **not** republish the catalog, and publishing the catalog does not
    republish pricing. That independence is the point of having two version lines."""

    id: str
    org_id: str
    version: int
    published_at: float = 0.0
    published_by: str = ""
    notes: str = ""
    rolled_back_from: int | None = None
    catalog_version: int | None = None
    books: tuple[PriceBookVersion, ...] = ()
    discounts: tuple[DiscountPolicy, ...] = ()
    promotions: tuple[Promotion, ...] = ()
    coupons: tuple[Coupon, ...] = ()

    @property
    def is_rollback(self) -> bool:
        return self.rolled_back_from is not None

    @property
    def entity_count(self) -> int:
        return len(self.books) + len(self.discounts) + len(self.promotions) + len(self.coupons)

    @property
    def price_count(self) -> int:
        return sum(b.price_count for b in self.books)

    def book(self, code: str) -> PriceBookVersion | None:
        matches = [b for b in self.books if b.code == code]
        return max(matches, key=lambda b: b.version) if matches else None

    @property
    def currencies(self) -> tuple[Currency, ...]:
        return tuple(sorted({b.currency for b in self.books}))


class PricingAction(StrEnum):
    """What happened to pricing. The activity log's closed vocabulary."""

    PRICE_BOOK_CREATED = "price_book.created"
    PRICE_BOOK_PUBLISHED = "price_book.published"
    PRICE_BOOK_ARCHIVED = "price_book.archived"
    PRICE_BOOK_RESTORED = "price_book.restored"
    PRICE_BOOK_DUPLICATED = "price_book.duplicated"
    PRICE_CREATED = "price.created"
    PRICE_UPDATED = "price.updated"
    DISCOUNT_CREATED = "discount.created"
    PROMOTION_CREATED = "promotion.created"
    COUPON_CREATED = "coupon.created"
    PRICING_PUBLISHED = "pricing.published"
    PRICING_ROLLED_BACK = "pricing.rolled_back"


@dataclass(frozen=True, slots=True)
class PricingActivity:
    """One append-only entry in pricing's own history: who changed which price, when.

    Kept alongside the `billing.*` fact stream rather than instead of it — the stream is the
    platform-wide log, this is pricing's ordered, queryable history, and a pricing screen
    should not have to filter the whole revenue stream to render "who changed this rate?"."""

    id: str
    org_id: str
    action: PricingAction
    subject_kind: str
    subject_code: str
    actor_id: str = ""
    at: float = 0.0
    pricing_version: int | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PriceBookSearchQuery:
    """A deterministic filter. No relevance scoring and no index — the same books and the
    same query always come back in the same order, which is what a pricing admin screen and a
    reproducible test both need."""

    text: str = ""  # case-insensitive substring over code, name, notes
    currencies: tuple[Currency, ...] = ()
    regions: tuple[Region, ...] = ()
    channels: tuple[CommercialChannel, ...] = ()
    customer_tiers: tuple[CustomerTier, ...] = ()
    states: tuple[PricingState, ...] = ()
    audiences: tuple[PriceBookAudience, ...] = ()
    price_code: str = ""
    catalog_version: int | None = None
    active_at: float | None = None
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class PriceBookSearchResult:
    """A page of matches plus the unpaged total, so a caller can render "20 of 137"."""

    books: tuple[PricingBook, ...] = ()
    total: int = 0

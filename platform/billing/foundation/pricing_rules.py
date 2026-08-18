"""Pricing rules (M9.7) — validation, effective dating, discounts, resolution, assembly.

Every pricing *decision* lives here: what makes a price book valid, which effective ranges
collide, which discounts are eligible and in what order they apply, which book prices a given
customer, and how a pricing snapshot is assembled. Layers above coordinate these; they never
re-decide.

Validation follows the Foundation discipline exactly — it **returns diagnostics and never
raises**, reports *every* finding rather than the first, and orders them deterministically, so
a publish wizard can show a complete checklist instead of a whack-a-mole sequence. The M9.2
`BillingValidator` already owns per-price shape and tier validation; this module **calls it**
rather than re-implementing it, and adds only what M9.7 introduced: effective-date overlap,
book-level completeness, discount/promotion/coupon conflicts, and snapshot integrity.

Pure: no I/O, no clock read, no persistence. `pricing_rules` is the module a reviewer should
read to answer "what does this system consider a valid price, and how did it arrive at one?".

Nothing here multiplies a price by a quantity. Resolution answers *what the price is*;
turning a price and a usage figure into a charge is rating (M9.11).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace

from core.fsm import StateMachineDefinition

from .diagnostics import BillingDiagnosticCode as C
from .diagnostics import BillingDiagnostics, Diagnostic, Severity
from .errors import BillingConflict, InvalidBillingTransition
from .money import Currency, Money
from .price_books import (
    PRICE_BOOK_LIFECYCLE,
    AppliedDiscount,
    CommercialChannel,
    Coupon,
    CustomerTier,
    DiscountKind,
    DiscountPolicy,
    PriceBookSearchQuery,
    PriceBookSearchResult,
    PriceBookVersion,
    PriceOverride,
    PriceResolutionRequest,
    PricingBook,
    PricingState,
    PricingTrigger,
    PricingVersion,
    Promotion,
    Region,
    ResolvedPrice,
)
from .pricing import EffectiveRange, Price, PriceTier
from .validation import BillingValidator


def _err(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, message, remediation)


def _warn(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.WARNING, message, remediation)


def _duplicates(codes: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for code in codes:
        if code in seen:
            dupes.add(code)
        seen.add(code)
    return sorted(dupes)


# ── effective dating ────────────────────────────────────────────────────────────


class EffectiveDatingService:
    """Half-open [from, until) windows, and the questions a pricing team actually asks of
    them: is this live, is it scheduled, has it expired, and do two of them collide?

    Half-open is deliberate: a price that ends at midnight and its successor that starts at
    midnight must not both apply for one instant, and closed intervals guarantee they would."""

    def overlaps(self, a: EffectiveRange, b: EffectiveRange) -> bool:
        a_end, b_end = a.effective_until, b.effective_until
        # A degenerate (empty) window overlaps nothing — including itself.
        if a_end is not None and a_end <= a.effective_from:
            return False
        if b_end is not None and b_end <= b.effective_from:
            return False
        left = max(a.effective_from, b.effective_from)
        ends = [x for x in (a_end, b_end) if x is not None]
        return True if not ends else left < min(ends)

    def is_scheduled(self, window: EffectiveRange, *, at: float) -> bool:
        return window.effective_from > at

    def is_expired(self, window: EffectiveRange, *, at: float) -> bool:
        return window.effective_until is not None and window.effective_until <= at

    def is_live(self, window: EffectiveRange, *, at: float) -> bool:
        return window.covers(at)

    def is_valid(self, window: EffectiveRange) -> bool:
        return window.effective_until is None or window.effective_until > window.effective_from

    def active(self, prices: Sequence[Price], *, at: float) -> tuple[Price, ...]:
        return tuple(p for p in prices if p.covers(at))

    def resolve(self, prices: Sequence[Price], *, at: float) -> Price | None:
        """The one price in force at `at`. Among covering candidates the highest version
        wins, then the latest start — deterministic, never insertion-ordered."""
        covering = [p for p in prices if p.covers(at)]
        if not covering:
            return None
        return max(covering, key=lambda p: (p.version, p.effective.effective_from))

    def collisions(self, prices: Sequence[Price]) -> tuple[tuple[Price, Price], ...]:
        """Every pair of same-code, same-version prices whose windows overlap.

        Same code with *different* versions is not a collision — that is exactly how a
        superseding price is expressed, and resolution picks the highest version."""
        found: list[tuple[Price, Price]] = []
        ordered = sorted(prices, key=lambda p: (p.code, p.version, p.effective.effective_from))
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                if left.code != right.code or left.version != right.version:
                    continue
                if self.overlaps(left.effective, right.effective):
                    found.append((left, right))
        return tuple(found)


# ── validation ──────────────────────────────────────────────────────────────────


class PricingValidator:
    """Structural, referential, and temporal validation for pricing. Never raises.

    Per-price shape and tier validation is the M9.2 `BillingValidator`'s job and is delegated
    to it — one definition of "is this price well formed", not two that can drift."""

    def __init__(self, prices: BillingValidator | None = None) -> None:
        self._prices = prices or BillingValidator()
        self._dates = EffectiveDatingService()

    def validate_price(self, price: Price, *, currency: Currency | None = None):
        """Delegated wholesale to M9.2. Present so callers have one entry point."""
        return self._prices.validate_price(price, currency=currency)

    def validate_book(
        self, book: PricingBook, *, known_catalog_versions: frozenset[int] | None = None
    ) -> BillingDiagnostics:
        """`known_catalog_versions=None` means "not checked here"; an **empty set** means
        "this organization has published no catalog", which must reject the pin rather than
        skip the check — the distinction an empty-collection falsiness test silently loses."""
        out: list[Diagnostic] = []
        if not book.code:
            out.append(_err("price_book.code", C.EMPTY_IDENTITY, "a price book code is required"))
        if not book.org_id:
            out.append(_err("price_book.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not book.display_name:
            out.append(
                _err("price_book.display_name", C.INVALID_NAME, "a display name is required")
            )
        if book.revision < 1:
            out.append(
                _err("price_book.revision", C.INVALID_CODE, "revisions start at 1 and increase")
            )
        if not self._dates.is_valid(book.effective):
            out.append(
                _err(
                    "price_book.effective",
                    C.INVALID_EFFECTIVE_RANGE,
                    "a price book's validity window must end after it starts",
                )
            )
        if book.catalog_version is not None and book.catalog_version < 1:
            out.append(
                _err(
                    "price_book.catalog_version",
                    C.UNKNOWN_CATALOG_VERSION,
                    "catalog versions start at 1",
                )
            )
        elif (
            known_catalog_versions is not None
            and book.catalog_version is not None
            and book.catalog_version not in known_catalog_versions
        ):
            out.append(
                _err(
                    "price_book.catalog_version",
                    C.UNKNOWN_CATALOG_VERSION,
                    f"no published catalog version {book.catalog_version}",
                    "pricing references a published catalog snapshot, never a mutable object",
                )
            )

        # Every price: shape, tiers, currency compatibility — all M9.2's decision.
        for i, price in enumerate(book.prices):
            for finding in self._prices.validate_price(price, currency=book.currency).findings:
                out.append(replace(finding, path=f"price_book.prices[{i}].{finding.path}"))

        for dupe in _duplicates(f"{p.code}@{p.version}" for p in book.prices):
            out.append(
                _err(
                    "price_book.prices",
                    C.DUPLICATE_PRICE_CODE,
                    f"price {dupe!r} is defined twice",
                    "a duplicate price version makes resolution ambiguous",
                )
            )

        for left, _right in self._dates.collisions(book.prices):
            out.append(
                _err(
                    "price_book.prices",
                    C.OVERLAPPING_EFFECTIVE_RANGE,
                    f"price {left.code!r} v{left.version} has two overlapping effective windows",
                    "use a new version to supersede a price; overlapping windows are ambiguous",
                )
            )

        known_prices = frozenset(p.code for p in book.prices)
        for i, override in enumerate(book.overrides):
            if not override.price_code:
                out.append(
                    _err(
                        f"price_book.overrides[{i}].price_code",
                        C.EMPTY_IDENTITY,
                        "an override must name the price it replaces",
                    )
                )
            elif override.price_code not in known_prices:
                out.append(
                    _err(
                        f"price_book.overrides[{i}].price_code",
                        C.UNKNOWN_PRICE,
                        f"override targets {override.price_code!r}, which this book does not price",
                    )
                )
            for amount, label in ((override.amount, "amount"), (override.unit_amount, "unit")):
                if amount is None:
                    continue
                if amount.is_negative:
                    out.append(
                        _err(
                            f"price_book.overrides[{i}].{label}",
                            C.NEGATIVE_AMOUNT,
                            "an override must not be negative",
                        )
                    )
                if amount.currency is not book.currency:
                    out.append(
                        _err(
                            f"price_book.overrides[{i}].{label}",
                            C.CURRENCY_MISMATCH,
                            f"override is {amount.currency}, the book is {book.currency}",
                            "a price book is single-currency by construction",
                        )
                    )
            if override.amount is None and override.unit_amount is None:
                out.append(
                    _err(
                        f"price_book.overrides[{i}]",
                        C.INVALID_PRICE_SHAPE,
                        "an override must replace either an amount or a unit amount",
                    )
                )

        if book.state is PricingState.PUBLISHED and not book.prices:
            out.append(
                _err(
                    "price_book.prices",
                    C.EMPTY_PRICE_BOOK,
                    "a published price book with no prices cannot price anything",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_discount(self, policy: DiscountPolicy) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        if not policy.code:
            out.append(_err("discount.code", C.EMPTY_IDENTITY, "a discount code is required"))
        if not policy.org_id:
            out.append(_err("discount.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not policy.display_name:
            out.append(_err("discount.display_name", C.INVALID_NAME, "a display name is required"))
        if not self._dates.is_valid(policy.effective):
            out.append(
                _err(
                    "discount.effective",
                    C.INVALID_EFFECTIVE_RANGE,
                    "a discount's window must end after it starts",
                )
            )
        # Exactly one reduction shape, whichever it is. `kind` says *why* the discount exists
        # (volume, commitment, a campaign); basis points versus a fixed amount says *how much*.
        # Both, or neither, makes the result ambiguous.
        if policy.percentage_bp and policy.amount is not None:
            out.append(
                _err(
                    "discount.amount",
                    C.INVALID_DISCOUNT,
                    "a discount carries either basis points or a fixed amount, never both",
                    "one reduction rule per policy — two would make the result ambiguous",
                )
            )
        elif not policy.percentage_bp and policy.amount is None:
            out.append(
                _err(
                    "discount.percentage_bp",
                    C.INVALID_DISCOUNT,
                    f"a {policy.kind.value} discount must declare basis points or a fixed amount",
                )
            )
        if policy.percentage_bp and not 0 < policy.percentage_bp <= 10_000:
            out.append(
                _err(
                    "discount.percentage_bp",
                    C.INVALID_DISCOUNT,
                    f"{policy.percentage_bp} basis points is outside (0, 10000]",
                    "basis points are integers: 2500 = 25%. 100% is the maximum",
                )
            )
        if policy.amount is not None and policy.amount.is_negative:
            out.append(
                _err(
                    "discount.amount",
                    C.NEGATIVE_AMOUNT,
                    "a discount amount must not be negative",
                    "a negative discount is a surcharge; model it as a price, not a discount",
                )
            )
        if policy.min_quantity < 0:
            out.append(
                _err("discount.min_quantity", C.INVALID_QUANTITY, "min_quantity must be >= 0")
            )
        if policy.kind is DiscountKind.VOLUME and policy.min_quantity <= 0:
            out.append(
                _err(
                    "discount.min_quantity",
                    C.INVALID_DISCOUNT,
                    "a volume discount must declare the quantity it starts at",
                )
            )
        if policy.kind is DiscountKind.COMMITMENT and policy.min_commitment is None:
            out.append(
                _err(
                    "discount.min_commitment",
                    C.INVALID_DISCOUNT,
                    "a commitment discount must declare the commitment it requires",
                )
            )
        if policy.kind is DiscountKind.BUNDLE and not policy.bundle_codes:
            out.append(
                _err(
                    "discount.bundle_codes",
                    C.INVALID_DISCOUNT,
                    "a bundle discount must name the bundles it applies to",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_promotion(
        self, promotion: Promotion, *, known: Sequence[Promotion] = ()
    ) -> BillingDiagnostics:
        out: list[Diagnostic] = list(
            replace(f, path=f"promotion.{f.path}")
            for f in self.validate_discount(promotion.discount).findings
        )
        if not promotion.code:
            out.append(_err("promotion.code", C.EMPTY_IDENTITY, "a promotion code is required"))
        if not self._dates.is_valid(promotion.effective):
            out.append(
                _err(
                    "promotion.effective",
                    C.INVALID_EFFECTIVE_RANGE,
                    "a promotion's window must end after it starts",
                )
            )
        for other in known:
            if other.code == promotion.code and other.id != promotion.id:
                out.append(
                    _err(
                        "promotion.code",
                        C.PROMOTION_CONFLICT,
                        f"promotion {promotion.code!r} already exists",
                    )
                )
            elif (
                other.exclusive
                and promotion.exclusive
                and other.campaign
                and other.campaign == promotion.campaign
                and self._dates.overlaps(other.effective, promotion.effective)
            ):
                out.append(
                    _err(
                        "promotion.effective",
                        C.PROMOTION_CONFLICT,
                        f"exclusive promotion {other.code!r} already covers this window in "
                        f"campaign {promotion.campaign!r}",
                        "two exclusive offers in one campaign cannot both be the offer",
                    )
                )
        return BillingDiagnostics.of(out)

    def validate_coupon(
        self, coupon: Coupon, *, known: Sequence[Coupon] = ()
    ) -> BillingDiagnostics:
        out: list[Diagnostic] = list(
            replace(f, path=f"coupon.{f.path}")
            for f in self.validate_discount(coupon.discount).findings
        )
        if not coupon.code:
            out.append(_err("coupon.code", C.EMPTY_IDENTITY, "a coupon code is required"))
        if not self._dates.is_valid(coupon.effective):
            out.append(
                _err(
                    "coupon.effective",
                    C.INVALID_EFFECTIVE_RANGE,
                    "a coupon's window must end after it starts",
                )
            )
        if coupon.max_redemptions < 0:
            out.append(
                _err("coupon.max_redemptions", C.INVALID_QUANTITY, "max_redemptions must be >= 0")
            )
        if coupon.single_use and coupon.max_redemptions > 1:
            out.append(
                _err(
                    "coupon.max_redemptions",
                    C.COUPON_CONFLICT,
                    "a single-use coupon cannot also allow several redemptions",
                )
            )
        for other in known:
            if other.code == coupon.code and other.id != coupon.id:
                out.append(
                    _err("coupon.code", C.COUPON_CONFLICT, f"coupon {coupon.code!r} already exists")
                )
        return BillingDiagnostics.of(out)

    def validate_pricing(
        self,
        *,
        books: Sequence[PricingBook] = (),
        discounts: Sequence[DiscountPolicy] = (),
        promotions: Sequence[Promotion] = (),
        coupons: Sequence[Coupon] = (),
        priced_codes: frozenset[str] | None = None,
        known_catalog_versions: frozenset[int] | None = None,
    ) -> BillingDiagnostics:
        """Whole-surface consistency — the check that runs before a pricing version is
        published.

        A publish is the one moment pricing is examined as a whole, so this is where duplicate
        books, colliding effective windows, dangling discount references, and *incomplete*
        pricing must be caught. After publication the snapshot is immutable, and a gap inside
        one means a plan nobody can quote."""
        out: list[Diagnostic] = []

        # Per-entity validation first: the surface cannot be consistent if the things in it
        # are individually invalid, and reporting only the cross-entity findings would hide
        # the simpler cause underneath them.
        for book in books:
            out.extend(
                self.validate_book(book, known_catalog_versions=known_catalog_versions).findings
            )
        for policy in discounts:
            out.extend(self.validate_discount(policy).findings)
        for promotion in promotions:
            out.extend(self.validate_promotion(promotion, known=promotions).findings)
        for coupon in coupons:
            out.extend(self.validate_coupon(coupon, known=coupons).findings)

        for dupe in _duplicates(f"{b.code}@{b.revision}" for b in books):
            out.append(
                _err("pricing.books", C.DUPLICATE_PRICE_BOOK_CODE, f"duplicate price book {dupe!r}")
            )
        for dupe in _duplicates(d.code for d in discounts):
            out.append(
                _err("pricing.discounts", C.DISCOUNT_CONFLICT, f"duplicate discount {dupe!r}")
            )
        for dupe in _duplicates(p.code for p in promotions):
            out.append(
                _err("pricing.promotions", C.PROMOTION_CONFLICT, f"duplicate promotion {dupe!r}")
            )
        for dupe in _duplicates(c.code for c in coupons):
            out.append(_err("pricing.coupons", C.COUPON_CONFLICT, f"duplicate coupon {dupe!r}"))

        discount_codes = frozenset(d.code for d in discounts)
        for book in books:
            for code in book.discount_codes:
                if discount_codes and code not in discount_codes:
                    out.append(
                        _err(
                            f"pricing.books[{book.code}].discount_codes",
                            C.DISCOUNT_CONFLICT,
                            f"unknown discount {code!r}",
                        )
                    )

        promotion_codes = frozenset(p.code for p in promotions)
        for coupon in coupons:
            if (
                coupon.promotion_code
                and promotion_codes
                and coupon.promotion_code not in promotion_codes
            ):
                out.append(
                    _err(
                        f"pricing.coupons[{coupon.code}].promotion_code",
                        C.COUPON_CONFLICT,
                        f"unknown promotion {coupon.promotion_code!r}",
                    )
                )

        # Two books that could both price the same code, for the same customer, in the same
        # currency, at the same instant. Resolution breaks the tie deterministically, but an
        # ambiguity nobody intended is worth surfacing before it is frozen.
        for i, left in enumerate(sorted(books, key=lambda b: (b.code, b.revision))):
            for right in sorted(books, key=lambda b: (b.code, b.revision))[i + 1 :]:
                if left.currency is not right.currency or left.code == right.code:
                    continue
                if left.scope.specificity != right.scope.specificity:
                    continue
                shared = set(left.price_codes) & set(right.price_codes)
                if shared and left.scope == right.scope:
                    out.append(
                        _warn(
                            f"pricing.books[{left.code}]",
                            C.DISCOUNT_CONFLICT,
                            f"books {left.code!r} and {right.code!r} share the same scope and "
                            f"price {sorted(shared)[0]!r}",
                            "the lower book code wins; scope one of them more narrowly",
                        )
                    )

        # Pricing completeness: every code the caller says must be priced, is.
        if priced_codes is not None:
            available = {code for book in books for code in book.price_codes}
            for missing in sorted(priced_codes - available):
                out.append(
                    _err(
                        "pricing.completeness",
                        C.INCOMPLETE_PRICING,
                        f"nothing prices {missing!r}",
                        "a sellable plan with no price cannot be quoted, ordered, or invoiced",
                    )
                )

        if not books:
            out.append(
                _err(
                    "pricing",
                    C.EMPTY_PRICE_BOOK,
                    "a pricing version must contain at least one book",
                )
            )
        for book in books:
            if book.state is PricingState.DRAFT:
                out.append(
                    _warn(
                        f"pricing.books[{book.code}]",
                        C.UNPUBLISHABLE_LIFECYCLE_STATE,
                        f"{book.code!r} is a draft and will be snapshotted as one",
                        "publish it first if customers should be priced from it",
                    )
                )
        return BillingDiagnostics.of(out)

    def validate_snapshot(self, snapshot: PricingVersion) -> BillingDiagnostics:
        """Snapshot integrity — a published version must be internally complete, because
        nothing may ever be added to it."""
        out: list[Diagnostic] = []
        if snapshot.version < 1:
            out.append(
                _err(
                    "pricing_snapshot.version",
                    C.PRICING_SNAPSHOT_INTEGRITY,
                    "versions start at 1 and increase",
                )
            )
        if not snapshot.org_id:
            out.append(_err("pricing_snapshot.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if snapshot.entity_count == 0:
            out.append(
                _err(
                    "pricing_snapshot",
                    C.EMPTY_PRICE_BOOK,
                    "an empty pricing version prices nothing",
                )
            )
        if snapshot.rolled_back_from is not None and snapshot.rolled_back_from >= snapshot.version:
            out.append(
                _err(
                    "pricing_snapshot.rolled_back_from",
                    C.PRICING_SNAPSHOT_INTEGRITY,
                    "a rollback source must be an earlier version",
                    "rollback moves forward by copying backward — history is never rewritten",
                )
            )
        for book in snapshot.books:
            if book.version < 1:
                out.append(
                    _err(
                        f"pricing_snapshot.books[{book.code}].version",
                        C.PRICING_SNAPSHOT_INTEGRITY,
                        "price book versions start at 1",
                    )
                )
            for price in book.prices:
                if price.currency is not book.currency:
                    out.append(
                        _err(
                            f"pricing_snapshot.books[{book.code}].prices[{price.code}]",
                            C.CURRENCY_MISMATCH,
                            f"price is {price.currency}, book is {book.currency}",
                        )
                    )
        for dupe in _duplicates(f"{b.code}@{b.version}" for b in snapshot.books):
            out.append(
                _err(
                    "pricing_snapshot.books",
                    C.DUPLICATE_PRICE_BOOK_CODE,
                    f"duplicate price book version {dupe!r}",
                )
            )
        return BillingDiagnostics.of(out)


# ── discounts ───────────────────────────────────────────────────────────────────


class DiscountEngine:
    """Decides which reductions apply, in which order, and what each takes off.

    Two rules make the result deterministic and defensible:

    * **Ordering is by `(priority, code)`** — never by insertion, never by size. A pricing
      team decides which discount applies first; the engine does not decide it for them.
    * **A non-stackable discount ends the chain.** If several are eligible, the
      highest-priority one wins and every later discount is excluded, so "which of my three
      offers did the customer get?" has one answer.

    Each reduction is computed from the **list price**, not from the running balance. That
    keeps two 25% discounts worth 50%, not 43.75%, and it means the order discounts are listed
    in never silently changes the total. The result is floored at zero: a discount that could
    invert a price would be a credit, and credits are a different domain."""

    def eligible(
        self,
        *,
        policies: Sequence[DiscountPolicy],
        price_code: str,
        currency: Currency,
        at: float,
        quantity: int = 0,
        commitment: Money | None = None,
        region: Region = Region.GLOBAL,
        channel: CommercialChannel = CommercialChannel.DIRECT,
        customer_tier: CustomerTier = CustomerTier.STANDARD,
        bundle_codes: Sequence[str] = (),
    ) -> tuple[DiscountPolicy, ...]:
        """Which policies qualify. `quantity` and `commitment` are read as *gates* here and
        are never arithmetic operands — a volume discount asks "do I qualify?", not "how
        much?"."""
        found = []
        for policy in policies:
            if not policy.effective.covers(at):
                continue
            if not policy.covers_price(price_code):
                continue
            if not policy.scope.matches(
                region=region, channel=channel, customer_tier=customer_tier
            ):
                continue
            if policy.min_quantity and quantity < policy.min_quantity:
                continue
            if policy.min_commitment is not None:
                if commitment is None or commitment.currency is not policy.min_commitment.currency:
                    continue
                if commitment.minor < policy.min_commitment.minor:
                    continue
            if policy.bundle_codes and not (set(policy.bundle_codes) & set(bundle_codes)):
                continue
            if policy.amount is not None and policy.amount.currency is not currency:
                continue
            found.append(policy)
        return tuple(sorted(found, key=lambda p: (p.priority, p.code)))

    def apply(
        self,
        base: Money,
        policies: Sequence[DiscountPolicy],
        *,
        sources: dict[str, str] | None = None,
    ) -> tuple[Money, tuple[AppliedDiscount, ...]]:
        """Reduce `base` by every applicable policy, in `(priority, code)` order."""
        ordered = sorted(policies, key=lambda p: (p.priority, p.code))
        applied: list[AppliedDiscount] = []
        total_off = Money.zero(base.currency)
        for policy in ordered:
            off = policy.reduction(base)
            remaining = Money(base.minor - total_off.minor, base.currency)
            if off.minor > remaining.minor:
                off = remaining
            if off.is_zero and applied and not policy.is_percentage:
                continue
            applied.append(
                AppliedDiscount(
                    code=policy.code,
                    display_name=policy.display_name,
                    kind=policy.kind,
                    priority=policy.priority,
                    stackable=policy.stackable,
                    reduction=off,
                    source=(sources or {}).get(policy.code, "policy"),
                )
            )
            total_off = total_off + off
            if not policy.stackable:
                break  # a non-stackable discount ends the chain, by design
        net = Money(max(base.minor - total_off.minor, 0), base.currency)
        return net, tuple(applied)


# ── resolution ──────────────────────────────────────────────────────────────────


class PriceResolver:
    """The Pricing Engine's one question: *what does this cost, for this customer, now?*

    Resolution is a pure function of its inputs. Given the same books, the same discounts and
    the same request it returns the same `ResolvedPrice` — the same book, the same version,
    the same discounts in the same order — and records how it got there in `resolution_path`.

    It never reads a clock (the instant is in the request), never touches persistence, and
    never multiplies by a quantity."""

    def __init__(
        self,
        *,
        discounts: DiscountEngine | None = None,
        dates: EffectiveDatingService | None = None,
    ) -> None:
        self._discounts = discounts or DiscountEngine()
        self._dates = dates or EffectiveDatingService()

    def select_book(
        self, books: Sequence[PriceBookVersion], request: PriceResolutionRequest
    ) -> PriceBookVersion | None:
        """Choose the book that prices this request.

        Ordering, in priority: the most specific scope, then the newest version, then the
        code — so a tie is broken by something stable rather than by whichever row the
        database happened to return first."""
        candidates = [
            b
            for b in books
            if b.currency is request.currency
            and (not request.price_book_code or b.code == request.price_book_code)
            and b.covers(request.at)
            and b.price(request.price_code, at=request.at) is not None
            and b.scope.matches(
                region=request.region,
                channel=request.channel,
                customer_tier=request.customer_tier,
                audience=request.audience,
            )
            and (
                request.catalog_version is None
                or b.catalog_version is None
                or b.catalog_version == request.catalog_version
            )
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda b: (b.scope.specificity, b.version, _inverse(b.code)))

    def resolve(
        self,
        request: PriceResolutionRequest,
        *,
        books: Sequence[PriceBookVersion],
        discounts: Sequence[DiscountPolicy] = (),
        promotions: Sequence[Promotion] = (),
        coupons: Sequence[Coupon] = (),
        bundle_codes: Sequence[str] = (),
    ) -> ResolvedPrice | None:
        """`None` when nothing prices this request — an unpriced code is a fact the caller
        must handle, never a zero this layer invents."""
        book = self.select_book(books, request)
        if book is None:
            return None
        price = book.price(request.price_code, at=request.at)
        if price is None:  # pragma: no cover — select_book already required one
            return None

        path = [
            f"book={book.code}@v{book.version}",
            f"scope=specificity:{book.scope.specificity}",
            f"price={price.code}@v{price.version}",
        ]

        # 1. Overrides, most specific first. A request-supplied override (a negotiated
        #    contract price) outranks anything authored in the book.
        override = request.override or book.override_for(
            price.code, account_id=request.account_id, at=request.at
        )
        if override is not None:
            price = _with_override(price, override)
            path.append(
                f"override={'request' if request.override is not None else 'book'}"
                f"{':' + override.account_id if override.account_id else ''}"
            )

        # 2. The primary amount this shape prices on. Tiered shapes carry their money in the
        #    tiers, so they discount per tier instead.
        list_amount = price.amount if price.amount is not None else price.unit_amount

        # 3. Eligible reductions: book policies, then live promotions, then the presented
        #    coupon. Each embeds its own policy, so one engine handles all three.
        sources: dict[str, str] = {}
        policies: list[DiscountPolicy] = list(
            self._discounts.eligible(
                policies=[
                    d for d in discounts if d.code in book.discount_codes or not book.discount_codes
                ],
                price_code=price.code,
                currency=book.currency,
                at=request.at,
                quantity=request.quantity,
                commitment=request.commitment,
                region=request.region,
                channel=request.channel,
                customer_tier=request.customer_tier,
                bundle_codes=bundle_codes,
            )
        )
        for promotion in promotions:
            if promotion.code not in request.promotion_codes or not promotion.is_live(request.at):
                continue
            if not promotion.scope.matches(
                region=request.region,
                channel=request.channel,
                customer_tier=request.customer_tier,
            ):
                continue
            eligible = self._discounts.eligible(
                policies=[promotion.discount],
                price_code=price.code,
                currency=book.currency,
                at=request.at,
                quantity=request.quantity,
                commitment=request.commitment,
                region=request.region,
                channel=request.channel,
                customer_tier=request.customer_tier,
                bundle_codes=bundle_codes,
            )
            for policy in eligible:
                sources[policy.code] = "promotion"
                policies.append(policy)
            if promotion.exclusive and eligible:
                # An exclusive promotion is the offer, not one of the offers.
                policies = list(eligible)
                path.append(f"promotion={promotion.code}:exclusive")
                break

        if request.coupon_code:
            coupon = next((c for c in coupons if c.code == request.coupon_code), None)
            if coupon is not None and coupon.is_live(request.at):
                eligible = self._discounts.eligible(
                    policies=[coupon.discount],
                    price_code=price.code,
                    currency=book.currency,
                    at=request.at,
                    quantity=request.quantity,
                    commitment=request.commitment,
                    region=request.region,
                    channel=request.channel,
                    customer_tier=request.customer_tier,
                    bundle_codes=bundle_codes,
                )
                for policy in eligible:
                    sources[policy.code] = "coupon"
                    policies.append(policy)
                if eligible:
                    path.append(f"coupon={coupon.code}")

        net_amount: Money | None = None
        applied: tuple[AppliedDiscount, ...] = ()
        net_tiers: tuple[PriceTier, ...] = ()
        if list_amount is not None:
            net_amount, applied = self._discounts.apply(list_amount, policies, sources=sources)
        elif price.tiers:
            net_tiers, applied = self._discount_tiers(price.tiers, policies, sources)

        path.extend(f"discount={a.code}:{a.source}" for a in applied)

        return ResolvedPrice(
            price_code=price.code,
            price_book_code=book.code,
            price_book_version=book.version,
            currency=book.currency,
            shape=price.shape,
            at=request.at,
            catalog_version=book.catalog_version,
            interval=price.interval,
            meter_code=price.meter_code,
            list_amount=list_amount,
            net_amount=net_amount,
            unit_amount=price.unit_amount,
            tiers=price.tiers,
            net_tiers=net_tiers,
            included_units=price.included_units,
            package_size=price.package_size,
            discounts=applied,
            override_applied=override is not None,
            region=request.region,
            channel=request.channel,
            customer_tier=request.customer_tier,
            resolution_path=tuple(path),
        )

    def _discount_tiers(
        self,
        tiers: Sequence[PriceTier],
        policies: Sequence[DiscountPolicy],
        sources: dict[str, str],
    ) -> tuple[tuple[PriceTier, ...], tuple[AppliedDiscount, ...]]:
        """A tiered price carries its money in the bands, so a discount applies to each band's
        unit price. The reported reduction is the one taken off the *first* band, because a
        per-band figure is meaningless without the quantity — and quantity is rating's."""
        out: list[PriceTier] = []
        applied: tuple[AppliedDiscount, ...] = ()
        for i, tier in enumerate(tiers):
            net, band_applied = self._discounts.apply(tier.unit_price, policies, sources=sources)
            if i == 0:
                applied = band_applied
            out.append(replace(tier, unit_price=net))
        return tuple(out), applied


def _inverse(code: str) -> tuple[int, ...]:
    """Sort key that makes the *lower* code win a `max()` tie-break — so the deterministic
    winner is the one a human would predict from an alphabetical list."""
    return tuple(-ord(ch) for ch in code)


def _with_override(price: Price, override: PriceOverride) -> Price:
    changes: dict = {}
    if override.amount is not None:
        changes["amount"] = override.amount
    if override.unit_amount is not None:
        changes["unit_amount"] = override.unit_amount
    return replace(price, **changes) if changes else price


# ── lifecycle ───────────────────────────────────────────────────────────────────


class PricingLifecycleService:
    """Applies price-book lifecycle triggers through the shared FSM. Illegal edges are
    rejected with a typed error, never coerced into an adjacent state."""

    def __init__(self, definition: StateMachineDefinition = PRICE_BOOK_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: PricingState, trigger: PricingTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: PricingState, trigger: PricingTrigger) -> PricingState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"price book cannot {trigger.value} from {current.value}; legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: PricingState) -> tuple[PricingTrigger, ...]:
        """Which moves are legal from here — what a UI needs to render buttons that cannot
        produce a 409."""
        return tuple(t for t in PricingTrigger if self.can(current, t))

    def apply(
        self,
        book: PricingBook,
        trigger: PricingTrigger,
        *,
        at: float,
        published_version: int | None = None,
    ) -> PricingBook:
        state = self.next_state(book.state, trigger)
        return replace(
            book,
            state=state,
            updated_at=at,
            published_version=(
                published_version if published_version is not None else book.published_version
            ),
        )

    def guard_editable(self, book: PricingBook) -> None:
        """A published price book is a historical fact. Repricing publishes a new revision;
        it never edits the book an existing subscription was priced from."""
        if not book.is_editable:
            raise BillingConflict(
                f"price book {book.code!r} r{book.revision} is {book.state.value} and is "
                "immutable; duplicate it into a new draft revision to reprice"
            )


# ── search ──────────────────────────────────────────────────────────────────────


class PricingSearchService:
    """Deterministic price-book filtering. No scoring, no ranking, no index — the same books
    and the same query always produce the same rows in the same order.

    Ordering is by `(code, revision)`, always. Arbitrary but *stable*, which is the property a
    paged admin screen and a reproducible test both actually need."""

    def search(
        self, books: Sequence[PricingBook], query: PriceBookSearchQuery
    ) -> PriceBookSearchResult:
        matched = [b for b in books if self._matches(b, query)]
        matched.sort(key=lambda b: (b.code, b.revision))
        start = max(query.offset, 0)
        page = tuple(matched[start : start + max(query.limit, 0)])
        return PriceBookSearchResult(books=page, total=len(matched))

    def _matches(self, book: PricingBook, query: PriceBookSearchQuery) -> bool:
        return (
            self._text_matches(query.text, book.code, book.display_name, book.notes)
            and (not query.currencies or book.currency in query.currencies)
            and (not query.regions or book.scope.region in query.regions)
            and (not query.channels or self._any_channel(book, query))
            and (not query.customer_tiers or self._any_tier(book, query))
            and (not query.states or book.state in query.states)
            and (not query.audiences or book.scope.audience in query.audiences)
            and (not query.price_code or query.price_code in book.price_codes)
            and (query.catalog_version is None or book.catalog_version == query.catalog_version)
            and (query.active_at is None or book.effective.covers(query.active_at))
        )

    @staticmethod
    def _any_channel(book: PricingBook, query: PriceBookSearchQuery) -> bool:
        # An unscoped book serves every channel, so it matches a channel filter.
        return not book.scope.channels or bool(set(book.scope.channels) & set(query.channels))

    @staticmethod
    def _any_tier(book: PricingBook, query: PriceBookSearchQuery) -> bool:
        return not book.scope.customer_tiers or bool(
            set(book.scope.customer_tiers) & set(query.customer_tiers)
        )

    @staticmethod
    def _text_matches(text: str, *fields: str) -> bool:
        if not text:
            return True
        needle = text.casefold()
        return any(needle in (value or "").casefold() for value in fields)


# ── snapshot assembly ───────────────────────────────────────────────────────────


class PricingAssembler:
    """Composes an immutable `PricingVersion` from the current pricing surface, and freezes a
    single `PricingBook` into a `PriceBookVersion`.

    Pure: given the same entities and the same version number it produces an identical
    snapshot. Everything is sorted on the way in, so two publishes of unchanged pricing are
    byte-identical — which makes "did anything actually change between v7 and v8?" a diff
    rather than an investigation."""

    def freeze_book(
        self,
        book: PricingBook,
        *,
        version_id: str,
        version: int,
        published_at: float,
        published_by: str = "",
        notes: str = "",
        rolled_back_from: int | None = None,
    ) -> PriceBookVersion:
        return PriceBookVersion(
            id=version_id,
            org_id=book.org_id,
            code=book.code,
            version=version,
            display_name=book.display_name,
            currency=book.currency,
            scope=book.scope,
            catalog_version=book.catalog_version,
            effective=book.effective,
            published_at=published_at,
            published_by=published_by,
            notes=notes or book.notes,
            rolled_back_from=rolled_back_from,
            prices=tuple(sorted(book.prices, key=lambda p: (p.code, p.version))),
            overrides=tuple(
                sorted(book.overrides, key=lambda o: (o.price_code, o.account_id, o.contract_id))
            ),
            discount_codes=tuple(sorted(book.discount_codes)),
        )

    def assemble(
        self,
        *,
        snapshot_id: str,
        org_id: str,
        version: int,
        published_at: float,
        published_by: str = "",
        notes: str = "",
        catalog_version: int | None = None,
        rolled_back_from: int | None = None,
        books: Sequence[PriceBookVersion] = (),
        discounts: Sequence[DiscountPolicy] = (),
        promotions: Sequence[Promotion] = (),
        coupons: Sequence[Coupon] = (),
    ) -> PricingVersion:
        return PricingVersion(
            id=snapshot_id,
            org_id=org_id,
            version=version,
            published_at=published_at,
            published_by=published_by,
            notes=notes,
            rolled_back_from=rolled_back_from,
            catalog_version=catalog_version,
            books=tuple(sorted(books, key=lambda b: (b.code, b.version))),
            discounts=tuple(sorted(discounts, key=lambda d: d.code)),
            promotions=tuple(sorted(promotions, key=lambda p: p.code)),
            coupons=tuple(sorted(coupons, key=lambda c: c.code)),
        )

    def rollback(
        self,
        source: PricingVersion,
        *,
        snapshot_id: str,
        version: int,
        published_at: float,
        published_by: str = "",
        notes: str = "",
    ) -> PricingVersion:
        """A rollback is a **new version** whose content is copied from an older one.

        Nothing is rewritten and nothing is deleted: the versions in between remain readable
        forever, and the new version records where its content came from. That is the whole
        difference between auditable pricing and mutable pricing (§A5) — and with money, the
        difference is not academic."""
        if version <= source.version:
            raise BillingConflict(
                f"rollback must publish a *new* version; {version} does not follow {source.version}"
            )
        return replace(
            source,
            id=snapshot_id,
            version=version,
            published_at=published_at,
            published_by=published_by,
            notes=notes or f"rollback to v{source.version}",
            rolled_back_from=source.version,
        )


__all__ = [
    "DiscountEngine",
    "EffectiveDatingService",
    "PriceResolver",
    "PricingAssembler",
    "PricingLifecycleService",
    "PricingSearchService",
    "PricingValidator",
]

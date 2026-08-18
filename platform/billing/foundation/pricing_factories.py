"""Pricing factories + repository contracts (M9.7).

`PricingFactory` is the one place a pricing aggregate is constructed, following the M9.2
factory discipline exactly: validate structurally, **fail fast** on error-severity findings
(`BillingValidationError`, §A2.6), compose an immutable aggregate from the injected id and
clock seams, and emit the corresponding `billing.*` fact through the injected emitter.

No I/O, no persistence, no networking. Emission is the only effect and it is injected.

The repository interfaces below extend the M9.2 seam additively — same shape, same rules:
org-scoped reads by construction (§A11.2), and the three append-only stores (price-book
versions, pricing versions, pricing activity) deliberately declare **no update and no
delete**. A published price that could be edited would not be a price anyone could re-derive
an invoice from.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import replace

from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .money import Currency, Money, Rounding
from .price_books import (
    Coupon,
    DiscountKind,
    DiscountPolicy,
    PriceBookVersion,
    PriceOverride,
    PricingAction,
    PricingActivity,
    PricingBook,
    PricingScope,
    PricingState,
    PricingVersion,
    Promotion,
)
from .pricing import EffectiveRange, Price, PriceShape, PriceTier
from .pricing_rules import PricingAssembler, PricingValidator
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

PREFIX_PRICING_BOOK = "pbk"
PREFIX_PRICE = "prc"
PREFIX_PRICE_BOOK_VERSION = "pbv"
PREFIX_DISCOUNT = "dsc"
PREFIX_PROMOTION = "pmo"
PREFIX_COUPON = "cpn"
PREFIX_PRICING_VERSION = "pvr"
PREFIX_PRICING_ACTIVITY = "pac"


class PricingFactory:
    """Composes validated pricing aggregates. Stateless apart from its injected seams."""

    def __init__(
        self,
        *,
        ids: IdGenerator | None = None,
        clock: Clock | None = None,
        emitter: EventEmitter | None = None,
        validator: PricingValidator | None = None,
    ) -> None:
        self._ids = ids or SequentialIdGenerator()
        self._clock = clock or SystemClock()
        self._emitter = emitter or NullEventEmitter()
        self._validator = validator or PricingValidator()
        self._assembler = PricingAssembler()

    # ── price books ─────────────────────────────────────────────────────────────

    def create_book(
        self,
        *,
        org_id: str,
        code: str,
        display_name: str,
        currency: Currency,
        revision: int = 1,
        scope: PricingScope | None = None,
        catalog_version: int | None = None,
        effective: EffectiveRange | None = None,
        notes: str = "",
        known_catalog_versions: frozenset[int] | None = None,
        actor_id: str = "",
    ) -> PricingBook:
        now = self._clock.now()
        book = PricingBook(
            id=self._ids.new(PREFIX_PRICING_BOOK),
            org_id=org_id,
            code=code,
            display_name=display_name,
            currency=currency,
            revision=revision,
            state=PricingState.DRAFT,  # every book starts as a draft, always
            scope=scope or PricingScope(),
            catalog_version=catalog_version,
            effective=effective or EffectiveRange(now),
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        self._fail_fast(
            self._validator.validate_book(book, known_catalog_versions=known_catalog_versions)
        )
        self._emit(
            BillingEventType.PRICE_BOOK_CREATED,
            org_id,
            book.id,
            actor_id,
            code=code,
            currency=currency.value,
            revision=revision,
        )
        return book

    def duplicate_book(
        self,
        source: PricingBook,
        *,
        code: str = "",
        display_name: str = "",
        revision: int | None = None,
        actor_id: str = "",
    ) -> PricingBook:
        """Copy a book into a **new draft**.

        Two shapes, one command, because they are the same commercial act. Duplicating into a
        *different* code creates a new book (a partner book seeded from the public one).
        Duplicating into the *same* code creates the next revision — and that is the reprice
        flow, because a published book is immutable and editing it is exactly the operation
        that makes an old invoice unexplainable.

        Never copies the id, never copies lifecycle state, and never copies the published
        version pointer: a copy has priced nobody."""
        now = self._clock.now()
        target_code = code or source.code
        next_revision = (
            revision
            if revision is not None
            else (source.revision + 1 if target_code == source.code else 1)
        )
        book = replace(
            source,
            id=self._ids.new(PREFIX_PRICING_BOOK),
            code=target_code,
            display_name=display_name or source.display_name,
            revision=next_revision,
            state=PricingState.DRAFT,
            published_version=None,
            created_at=now,
            updated_at=now,
        )
        self._fail_fast(self._validator.validate_book(book))
        self._emit(
            BillingEventType.PRICE_BOOK_DUPLICATED,
            source.org_id,
            book.id,
            actor_id,
            code=book.code,
            revision=book.revision,
            source_code=source.code,
            source_revision=source.revision,
        )
        return book

    def publish_book(
        self,
        book: PricingBook,
        *,
        version: int,
        notes: str = "",
        known_catalog_versions: frozenset[int] | None = None,
        actor_id: str = "",
    ) -> PriceBookVersion:
        """Validate the whole book, then freeze it into an immutable version.

        Validation runs **before** the version exists, not after: once published, a version can
        never be corrected, only superseded."""
        candidate = replace(book, state=PricingState.PUBLISHED)
        self._fail_fast(
            self._validator.validate_book(candidate, known_catalog_versions=known_catalog_versions)
        )
        frozen = self._assembler.freeze_book(
            candidate,
            version_id=self._ids.new(PREFIX_PRICE_BOOK_VERSION),
            version=version,
            published_at=self._clock.now(),
            published_by=actor_id,
            notes=notes,
        )
        self._emit(
            BillingEventType.PRICE_BOOK_PUBLISHED,
            book.org_id,
            frozen.id,
            actor_id,
            code=frozen.code,
            version=version,
            prices=frozen.price_count,
        )
        return frozen

    # ── prices ──────────────────────────────────────────────────────────────────

    def create_price(
        self,
        book: PricingBook,
        *,
        code: str,
        shape: PriceShape,
        version: int = 1,
        effective: EffectiveRange | None = None,
        interval=None,
        amount: Money | None = None,
        meter_code: str = "",
        unit_amount: Money | None = None,
        included_units: int = 0,
        package_size: int = 0,
        tiers: Sequence[PriceTier] = (),
        credit_units_per_amount: int = 0,
        rounding: Rounding = Rounding.HALF_UP,
        description: str = "",
        actor_id: str = "",
    ) -> Price:
        """Compose a price *for this book*, inheriting its currency.

        The price is validated against the book's currency here rather than trusted: a book is
        single-currency by construction (ADR-0023 RS5), because a book that mixed currencies
        could silently produce a cross-currency charge line."""
        price = Price(
            id=self._ids.new(PREFIX_PRICE),
            price_book_id=book.id,
            code=code,
            shape=shape,
            currency=book.currency,
            version=version,
            effective=effective or EffectiveRange(self._clock.now()),
            interval=interval,
            amount=amount,
            meter_code=meter_code,
            unit_amount=unit_amount,
            included_units=included_units,
            package_size=package_size,
            tiers=tuple(tiers),
            credit_units_per_amount=credit_units_per_amount,
            rounding=rounding,
            description=description,
        )
        self._fail_fast(self._validator.validate_price(price, currency=book.currency))
        self._emit(
            BillingEventType.PRICE_CREATED,
            book.org_id,
            price.id,
            actor_id,
            book=book.code,
            code=code,
            shape=shape.value,
            version=version,
        )
        return price

    def update_price(
        self, book: PricingBook, price: Price, *, actor_id: str = "", **changes
    ) -> Price:
        """Amend a **draft** price in place. The book's editability is the lifecycle service's
        decision, checked by the caller before this is reached; what is enforced here is that
        the amended price is still a valid price."""
        updated = replace(price, **changes)
        self._fail_fast(self._validator.validate_price(updated, currency=book.currency))
        self._emit(
            BillingEventType.PRICE_UPDATED,
            book.org_id,
            updated.id,
            actor_id,
            book=book.code,
            code=updated.code,
            fields=sorted(changes),
        )
        return updated

    def override(
        self,
        *,
        price_code: str,
        amount: Money | None = None,
        unit_amount: Money | None = None,
        account_id: str = "",
        contract_id: str = "",
        reason: str = "",
        effective: EffectiveRange | None = None,
    ) -> PriceOverride:
        """A negotiated price. Pricing applies it; the Contract domain owns the negotiation."""
        return PriceOverride(
            price_code=price_code,
            amount=amount,
            unit_amount=unit_amount,
            account_id=account_id,
            contract_id=contract_id,
            reason=reason,
            effective=effective or EffectiveRange(self._clock.now()),
        )

    # ── discounts, promotions, coupons ──────────────────────────────────────────

    def create_discount(
        self,
        *,
        org_id: str,
        code: str,
        display_name: str,
        kind: DiscountKind = DiscountKind.PERCENTAGE,
        percentage_bp: int = 0,
        amount: Money | None = None,
        priority: int = 100,
        stackable: bool = True,
        effective: EffectiveRange | None = None,
        price_codes: Sequence[str] = (),
        bundle_codes: Sequence[str] = (),
        min_quantity: int = 0,
        min_commitment: Money | None = None,
        scope: PricingScope | None = None,
        rounding: Rounding = Rounding.HALF_UP,
        description: str = "",
        actor_id: str = "",
        emit: bool = True,
    ) -> DiscountPolicy:
        policy = DiscountPolicy(
            id=self._ids.new(PREFIX_DISCOUNT),
            org_id=org_id,
            code=code,
            display_name=display_name,
            kind=kind,
            percentage_bp=percentage_bp,
            amount=amount,
            priority=priority,
            stackable=stackable,
            effective=effective or EffectiveRange(self._clock.now()),
            price_codes=tuple(price_codes),
            bundle_codes=tuple(bundle_codes),
            min_quantity=min_quantity,
            min_commitment=min_commitment,
            scope=scope or PricingScope(),
            rounding=rounding,
            description=description,
        )
        self._fail_fast(self._validator.validate_discount(policy))
        if emit:
            self._emit(
                BillingEventType.DISCOUNT_CREATED,
                org_id,
                policy.id,
                actor_id,
                code=code,
                kind=kind.value,
            )
        return policy

    def create_promotion(
        self,
        *,
        org_id: str,
        code: str,
        display_name: str,
        discount: DiscountPolicy,
        effective: EffectiveRange | None = None,
        campaign: str = "",
        exclusive: bool = False,
        scope: PricingScope | None = None,
        description: str = "",
        known: Sequence[Promotion] = (),
        actor_id: str = "",
    ) -> Promotion:
        promotion = Promotion(
            id=self._ids.new(PREFIX_PROMOTION),
            org_id=org_id,
            code=code,
            display_name=display_name,
            discount=discount,
            effective=effective or EffectiveRange(self._clock.now()),
            campaign=campaign,
            exclusive=exclusive,
            scope=scope or PricingScope(),
            description=description,
        )
        self._fail_fast(self._validator.validate_promotion(promotion, known=known))
        self._emit(
            BillingEventType.PROMOTION_CREATED,
            org_id,
            promotion.id,
            actor_id,
            code=code,
            campaign=campaign,
        )
        return promotion

    def create_coupon(
        self,
        *,
        org_id: str,
        code: str,
        display_name: str,
        discount: DiscountPolicy,
        effective: EffectiveRange | None = None,
        promotion_code: str = "",
        single_use: bool = False,
        max_redemptions: int = 0,
        scope: PricingScope | None = None,
        description: str = "",
        known: Sequence[Coupon] = (),
        actor_id: str = "",
    ) -> Coupon:
        """Eligibility only. Nothing here counts a redemption or decides whether one already
        happened — Pricing answers "is this coupon valid for this price, now?" and stops."""
        coupon = Coupon(
            id=self._ids.new(PREFIX_COUPON),
            org_id=org_id,
            code=code,
            display_name=display_name,
            discount=discount,
            effective=effective or EffectiveRange(self._clock.now()),
            promotion_code=promotion_code,
            single_use=single_use,
            max_redemptions=max_redemptions,
            scope=scope or PricingScope(),
            description=description,
        )
        self._fail_fast(self._validator.validate_coupon(coupon, known=known))
        self._emit(BillingEventType.COUPON_CREATED, org_id, coupon.id, actor_id, code=code)
        return coupon

    # ── snapshots ───────────────────────────────────────────────────────────────

    def publish_pricing(
        self,
        *,
        org_id: str,
        version: int,
        catalog_version: int | None = None,
        books: Sequence[PriceBookVersion] = (),
        drafts: Sequence[PricingBook] = (),
        discounts: Sequence[DiscountPolicy] = (),
        promotions: Sequence[Promotion] = (),
        coupons: Sequence[Coupon] = (),
        priced_codes: frozenset[str] | None = None,
        known_catalog_versions: frozenset[int] | None = None,
        notes: str = "",
        actor_id: str = "",
    ) -> PricingVersion:
        """Freeze the whole pricing surface into the next immutable version.

        `drafts` are the authoring books the whole-surface validation runs over (completeness,
        collisions, dangling discount references); `books` are the already-frozen versions the
        snapshot actually carries. Validating the drafts and snapshotting the frozen versions
        is deliberate: what is checked is what a pricing team is about to promise, and what is
        stored is what can never change afterwards."""
        self._fail_fast(
            self._validator.validate_pricing(
                books=drafts,
                discounts=discounts,
                promotions=promotions,
                coupons=coupons,
                priced_codes=priced_codes,
                known_catalog_versions=known_catalog_versions,
            )
        )
        snapshot = self._assembler.assemble(
            snapshot_id=self._ids.new(PREFIX_PRICING_VERSION),
            org_id=org_id,
            version=version,
            published_at=self._clock.now(),
            published_by=actor_id,
            notes=notes,
            catalog_version=catalog_version,
            books=books,
            discounts=discounts,
            promotions=promotions,
            coupons=coupons,
        )
        self._fail_fast(self._validator.validate_snapshot(snapshot))
        self._emit(
            BillingEventType.PRICING_PUBLISHED,
            org_id,
            snapshot.id,
            actor_id,
            version=version,
            catalog_version=catalog_version,
            books=len(snapshot.books),
            prices=snapshot.price_count,
        )
        return snapshot

    def rollback_pricing(
        self, source: PricingVersion, *, version: int, notes: str = "", actor_id: str = ""
    ) -> PricingVersion:
        snapshot = self._assembler.rollback(
            source,
            snapshot_id=self._ids.new(PREFIX_PRICING_VERSION),
            version=version,
            published_at=self._clock.now(),
            published_by=actor_id,
            notes=notes,
        )
        self._fail_fast(self._validator.validate_snapshot(snapshot))
        self._emit(
            BillingEventType.PRICING_ROLLED_BACK,
            source.org_id,
            snapshot.id,
            actor_id,
            version=version,
            rolled_back_from=source.version,
        )
        return snapshot

    def activity(
        self,
        *,
        org_id: str,
        action: PricingAction,
        subject_kind: str,
        subject_code: str,
        actor_id: str = "",
        pricing_version: int | None = None,
        detail: str = "",
    ) -> PricingActivity:
        return PricingActivity(
            id=self._ids.new(PREFIX_PRICING_ACTIVITY),
            org_id=org_id,
            action=action,
            subject_kind=subject_kind,
            subject_code=subject_code,
            actor_id=actor_id,
            at=self._clock.now(),
            pricing_version=pricing_version,
            detail=detail,
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _fail_fast(self, diagnostics) -> None:
        if diagnostics.has_errors:
            raise BillingValidationError(diagnostics)

    def _emit(
        self,
        event_type: BillingEventType,
        org_id: str,
        subject_id: str,
        actor_id: str,
        **data,
    ) -> None:
        self._emitter.emit(
            BillingEvent(
                id=self._ids.new("bev"),
                type=event_type,
                ts=self._clock.now(),
                org_id=org_id,
                subject_id=subject_id,
                actor_id=actor_id,
                data=dict(data),
            )
        )


# ── repository contracts (additive to the M9.2 seam) ────────────────────────────


class PricingBookRepository(ABC):
    """The authoring books. Identity is `(org_id, code, revision)`: revisions accumulate, so
    the book a subscription was priced from is still there after a reprice."""

    @abstractmethod
    def add(self, book: PricingBook) -> PricingBook: ...
    @abstractmethod
    def get(self, org_id: str, code: str, revision: int | None = None) -> PricingBook | None:
        """`revision=None` ⇒ the highest revision — the one being worked on."""

    @abstractmethod
    def by_id(self, org_id: str, book_id: str) -> PricingBook | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[PricingBook, ...]: ...
    @abstractmethod
    def revisions(self, org_id: str, code: str) -> tuple[PricingBook, ...]: ...
    @abstractmethod
    def replace(self, book: PricingBook) -> PricingBook:
        """Whole-aggregate replacement. A book is mutable **until published**; the published
        version is what becomes immutable, not the draft."""


class PriceBookVersionRepository(ABC):
    """Append-only: published price-book versions are immutable and remain readable forever.
    No update, no delete — there is no method to call."""

    @abstractmethod
    def publish(self, version: PriceBookVersion) -> PriceBookVersion: ...
    @abstractmethod
    def get(self, org_id: str, code: str, version: int) -> PriceBookVersion | None: ...
    @abstractmethod
    def latest(self, org_id: str, code: str) -> PriceBookVersion | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[PriceBookVersion, ...]: ...
    @abstractmethod
    def history(self, org_id: str, code: str = "", limit: int = 50) -> tuple[PriceBookVersion, ...]:
        """Every published version, newest first. `code=""` spans every book."""

    @abstractmethod
    def next_version(self, org_id: str, code: str) -> int:
        """The next version number for this book. Monotonic and never reused."""


class DiscountPolicyRepository(ABC):
    @abstractmethod
    def add(self, policy: DiscountPolicy) -> DiscountPolicy: ...
    @abstractmethod
    def get(self, org_id: str, code: str) -> DiscountPolicy | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[DiscountPolicy, ...]: ...


class PromotionRepository(ABC):
    @abstractmethod
    def add(self, promotion: Promotion) -> Promotion: ...
    @abstractmethod
    def get(self, org_id: str, code: str) -> Promotion | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[Promotion, ...]: ...
    @abstractmethod
    def live(self, org_id: str, at: float) -> tuple[Promotion, ...]: ...


class CouponRepository(ABC):
    @abstractmethod
    def add(self, coupon: Coupon) -> Coupon: ...
    @abstractmethod
    def get(self, org_id: str, code: str) -> Coupon | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[Coupon, ...]: ...


class PricingVersionRepository(ABC):
    """Append-only org-wide pricing snapshots. Immutable once published."""

    @abstractmethod
    def publish(self, snapshot: PricingVersion) -> PricingVersion: ...
    @abstractmethod
    def latest(self, org_id: str) -> PricingVersion | None: ...
    @abstractmethod
    def version(self, org_id: str, version: int) -> PricingVersion | None: ...
    @abstractmethod
    def history(self, org_id: str, limit: int = 50) -> tuple[PricingVersion, ...]: ...
    @abstractmethod
    def next_version(self, org_id: str) -> int:
        """Monotonic and never reused — including across a rollback, which moves *forward* to
        restore old prices."""


class PricingActivityRepository(ABC):
    """Append-only pricing history."""

    @abstractmethod
    def append(self, activity: PricingActivity) -> PricingActivity: ...
    @abstractmethod
    def history(self, org_id: str, limit: int = 100) -> tuple[PricingActivity, ...]: ...
    @abstractmethod
    def for_subject(self, org_id: str, subject_code: str) -> tuple[PricingActivity, ...]: ...


__all__ = [
    "PREFIX_COUPON",
    "PREFIX_DISCOUNT",
    "PREFIX_PRICE",
    "PREFIX_PRICE_BOOK_VERSION",
    "PREFIX_PRICING_ACTIVITY",
    "PREFIX_PRICING_BOOK",
    "PREFIX_PRICING_VERSION",
    "PREFIX_PROMOTION",
    "CouponRepository",
    "DiscountPolicyRepository",
    "PriceBookVersionRepository",
    "PricingActivityRepository",
    "PricingBookRepository",
    "PricingFactory",
    "PricingVersionRepository",
    "PromotionRepository",
]

"""Pricing Engine repositories (M9.7) — additive to the M9.4 persistence layer.

Seven SQL implementations of the M9.7 Foundation contracts, reusing the M9.4 machinery
unchanged: the same `_Repo` session handle, the same codec, the same projection-plus-payload
mapping, the same "never commit, never roll back" rule, and the same org-scoped-by-
construction reads (§A11.2).

Three are append-only and expose no update or delete method at all: published price-book
versions, pricing versions, and pricing activity. `next_version` is monotonic and never reuses
a number — including across a rollback, which moves *forward* to restore old prices.

No repository here performs arithmetic, applies a discount, or decides which book wins. A
repository that quietly filtered by effective date would be making a pricing decision in the
storage layer, where nobody would look for it.
"""

from __future__ import annotations

from sqlmodel import select

from billing.foundation import (
    Coupon,
    CouponRepository,
    DiscountPolicy,
    DiscountPolicyRepository,
    ImmutabilityViolation,
    PriceBookVersion,
    PriceBookVersionRepository,
    PricingActivity,
    PricingActivityRepository,
    PricingBook,
    PricingBookRepository,
    PricingVersion,
    PricingVersionRepository,
    Promotion,
    PromotionRepository,
)

from . import pricing_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo


def _book_projection(b: PricingBook) -> dict:
    return {
        "id": b.id,
        "org_id": b.org_id,
        "code": b.code,
        "revision": b.revision,
        "currency": b.currency.value,
        "state": b.state.value,
        "region": b.scope.region.value,
        "audience": b.scope.audience.value,
        "catalog_version": b.catalog_version,
        "effective_from": b.effective.effective_from,
        "effective_until": b.effective.effective_until,
        "published_version": b.published_version,
        "updated_at": b.updated_at,
    }


def _book_version_projection(v: PriceBookVersion) -> dict:
    return {
        "id": v.id,
        "org_id": v.org_id,
        "code": v.code,
        "version": v.version,
        "currency": v.currency.value,
        "region": v.scope.region.value,
        "audience": v.scope.audience.value,
        "catalog_version": v.catalog_version,
        "effective_from": v.effective.effective_from,
        "effective_until": v.effective.effective_until,
        "published_at": v.published_at,
        "published_by": v.published_by,
        "rolled_back_from": v.rolled_back_from,
        "price_count": v.price_count,
    }


def _discount_projection(d: DiscountPolicy) -> dict:
    return {
        "id": d.id,
        "org_id": d.org_id,
        "code": d.code,
        "kind": d.kind.value,
        "priority": d.priority,
        "stackable": d.stackable,
        "effective_from": d.effective.effective_from,
        "effective_until": d.effective.effective_until,
    }


def _promotion_projection(p: Promotion) -> dict:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "code": p.code,
        "campaign": p.campaign,
        "exclusive": p.exclusive,
        "effective_from": p.effective.effective_from,
        "effective_until": p.effective.effective_until,
    }


def _coupon_projection(c: Coupon) -> dict:
    return {
        "id": c.id,
        "org_id": c.org_id,
        "code": c.code,
        "promotion_code": c.promotion_code,
        "single_use": c.single_use,
        "effective_from": c.effective.effective_from,
        "effective_until": c.effective.effective_until,
    }


def _pricing_version_projection(v: PricingVersion) -> dict:
    return {
        "id": v.id,
        "org_id": v.org_id,
        "version": v.version,
        "catalog_version": v.catalog_version,
        "published_at": v.published_at,
        "published_by": v.published_by,
        "rolled_back_from": v.rolled_back_from,
        "entity_count": v.entity_count,
        "price_count": v.price_count,
        "notes": v.notes,
    }


def _activity_projection(a: PricingActivity) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "action": a.action.value,
        "subject_kind": a.subject_kind,
        "subject_code": a.subject_code,
        "actor_id": a.actor_id,
        "at": a.at,
        "pricing_version": a.pricing_version,
    }


class SqlPricingBookRepository(_Repo, PricingBookRepository):
    """Upsert on the natural key `(org, code, revision)` — `add` means *store this
    revision*, which is what makes `replace` and `add` the same operation for a whole
    aggregate."""

    def add(self, book: PricingBook) -> PricingBook:
        existing = self._one(
            select(rows.BillingPricingBookRow).where(
                rows.BillingPricingBookRow.org_id == book.org_id,
                rows.BillingPricingBookRow.code == book.code,
                rows.BillingPricingBookRow.revision == book.revision,
            )
        )
        if existing is not None:
            update_row(existing, book, _book_projection(book))
            self._session.flush()
            return book
        self._add(rows.BillingPricingBookRow(payload=encode(book), **_book_projection(book)))
        return book

    def get(self, org_id: str, code: str, revision: int | None = None) -> PricingBook | None:
        statement = select(rows.BillingPricingBookRow).where(
            rows.BillingPricingBookRow.org_id == org_id,
            rows.BillingPricingBookRow.code == code,
        )
        if revision is not None:
            statement = statement.where(rows.BillingPricingBookRow.revision == revision)
        else:
            statement = statement.order_by(
                rows.BillingPricingBookRow.revision.desc()  # type: ignore[attr-defined]
            )
        row = self._one(statement)
        return from_row(PricingBook, row) if row else None

    def by_id(self, org_id: str, book_id: str) -> PricingBook | None:
        row = self._one(
            select(rows.BillingPricingBookRow).where(
                rows.BillingPricingBookRow.org_id == org_id,
                rows.BillingPricingBookRow.id == book_id,
            )
        )
        return from_row(PricingBook, row) if row else None

    def all(self, org_id: str) -> tuple[PricingBook, ...]:
        found = self._all(
            select(rows.BillingPricingBookRow)
            .where(rows.BillingPricingBookRow.org_id == org_id)
            .order_by(rows.BillingPricingBookRow.code, rows.BillingPricingBookRow.revision)
        )
        return tuple(from_row(PricingBook, r) for r in found)

    def revisions(self, org_id: str, code: str) -> tuple[PricingBook, ...]:
        found = self._all(
            select(rows.BillingPricingBookRow)
            .where(
                rows.BillingPricingBookRow.org_id == org_id,
                rows.BillingPricingBookRow.code == code,
            )
            .order_by(rows.BillingPricingBookRow.revision)
        )
        return tuple(from_row(PricingBook, r) for r in found)

    def replace(self, book: PricingBook) -> PricingBook:
        return self.add(book)


class SqlPriceBookVersionRepository(_Repo, PriceBookVersionRepository):
    """Append-only. There is no update and no delete method to call, and the table's unique
    `(org_id, code, version)` makes republishing a version a database-level error."""

    def publish(self, version: PriceBookVersion) -> PriceBookVersion:
        existing = self._one(
            select(rows.BillingPriceBookVersionRow).where(
                rows.BillingPriceBookVersionRow.org_id == version.org_id,
                rows.BillingPriceBookVersionRow.code == version.code,
                rows.BillingPriceBookVersionRow.version == version.version,
            )
        )
        if existing is not None:
            # Defence in depth above the constraint: a published price is what an old invoice
            # was rated against, and a price that could be replaced would not be one.
            raise ImmutabilityViolation(
                f"price book {version.code!r} version {version.version} is already published "
                "and is immutable"
            )
        self._add(
            rows.BillingPriceBookVersionRow(
                payload=encode(version), **_book_version_projection(version)
            )
        )
        return version

    def get(self, org_id: str, code: str, version: int) -> PriceBookVersion | None:
        row = self._one(
            select(rows.BillingPriceBookVersionRow).where(
                rows.BillingPriceBookVersionRow.org_id == org_id,
                rows.BillingPriceBookVersionRow.code == code,
                rows.BillingPriceBookVersionRow.version == version,
            )
        )
        return from_row(PriceBookVersion, row) if row else None

    def latest(self, org_id: str, code: str) -> PriceBookVersion | None:
        row = self._one(
            select(rows.BillingPriceBookVersionRow)
            .where(
                rows.BillingPriceBookVersionRow.org_id == org_id,
                rows.BillingPriceBookVersionRow.code == code,
            )
            .order_by(rows.BillingPriceBookVersionRow.version.desc())  # type: ignore[attr-defined]
        )
        return from_row(PriceBookVersion, row) if row else None

    def all(self, org_id: str) -> tuple[PriceBookVersion, ...]:
        """Every published version. Resolution is given the whole set and decides which one
        applies — filtering here would move a pricing decision into storage."""
        found = self._all(
            select(rows.BillingPriceBookVersionRow)
            .where(rows.BillingPriceBookVersionRow.org_id == org_id)
            .order_by(rows.BillingPriceBookVersionRow.code, rows.BillingPriceBookVersionRow.version)
        )
        return tuple(from_row(PriceBookVersion, r) for r in found)

    def history(self, org_id: str, code: str = "", limit: int = 50) -> tuple[PriceBookVersion, ...]:
        statement = select(rows.BillingPriceBookVersionRow).where(
            rows.BillingPriceBookVersionRow.org_id == org_id
        )
        if code:
            statement = statement.where(rows.BillingPriceBookVersionRow.code == code)
        found = self._all(
            statement.order_by(
                rows.BillingPriceBookVersionRow.code,
                rows.BillingPriceBookVersionRow.version.desc(),  # type: ignore[attr-defined]
            ).limit(limit)
        )
        return tuple(from_row(PriceBookVersion, r) for r in found)

    def next_version(self, org_id: str, code: str) -> int:
        latest = self.latest(org_id, code)
        return 1 if latest is None else latest.version + 1


class SqlDiscountPolicyRepository(_Repo, DiscountPolicyRepository):
    def add(self, policy: DiscountPolicy) -> DiscountPolicy:
        existing = self._one(
            select(rows.BillingDiscountPolicyRow).where(
                rows.BillingDiscountPolicyRow.org_id == policy.org_id,
                rows.BillingDiscountPolicyRow.code == policy.code,
            )
        )
        if existing is not None:
            update_row(existing, policy, _discount_projection(policy))
            self._session.flush()
            return policy
        self._add(
            rows.BillingDiscountPolicyRow(payload=encode(policy), **_discount_projection(policy))
        )
        return policy

    def get(self, org_id: str, code: str) -> DiscountPolicy | None:
        row = self._one(
            select(rows.BillingDiscountPolicyRow).where(
                rows.BillingDiscountPolicyRow.org_id == org_id,
                rows.BillingDiscountPolicyRow.code == code,
            )
        )
        return from_row(DiscountPolicy, row) if row else None

    def all(self, org_id: str) -> tuple[DiscountPolicy, ...]:
        found = self._all(
            select(rows.BillingDiscountPolicyRow)
            .where(rows.BillingDiscountPolicyRow.org_id == org_id)
            .order_by(rows.BillingDiscountPolicyRow.priority, rows.BillingDiscountPolicyRow.code)
        )
        return tuple(from_row(DiscountPolicy, r) for r in found)


class SqlPromotionRepository(_Repo, PromotionRepository):
    def add(self, promotion: Promotion) -> Promotion:
        existing = self._one(
            select(rows.BillingPromotionRow).where(
                rows.BillingPromotionRow.org_id == promotion.org_id,
                rows.BillingPromotionRow.code == promotion.code,
            )
        )
        if existing is not None:
            update_row(existing, promotion, _promotion_projection(promotion))
            self._session.flush()
            return promotion
        self._add(
            rows.BillingPromotionRow(payload=encode(promotion), **_promotion_projection(promotion))
        )
        return promotion

    def get(self, org_id: str, code: str) -> Promotion | None:
        row = self._one(
            select(rows.BillingPromotionRow).where(
                rows.BillingPromotionRow.org_id == org_id,
                rows.BillingPromotionRow.code == code,
            )
        )
        return from_row(Promotion, row) if row else None

    def all(self, org_id: str) -> tuple[Promotion, ...]:
        found = self._all(
            select(rows.BillingPromotionRow)
            .where(rows.BillingPromotionRow.org_id == org_id)
            .order_by(rows.BillingPromotionRow.code)
        )
        return tuple(from_row(Promotion, r) for r in found)

    def live(self, org_id: str, at: float) -> tuple[Promotion, ...]:
        """Indexed window filter — the one temporal predicate a repository may express,
        because it is a *row selection*, not a pricing decision: which promotion wins, and
        what it takes off, is still resolution's."""
        found = self._all(
            select(rows.BillingPromotionRow)
            .where(
                rows.BillingPromotionRow.org_id == org_id,
                rows.BillingPromotionRow.effective_from <= at,
            )
            .order_by(rows.BillingPromotionRow.code)
        )
        live = [from_row(Promotion, r) for r in found]
        return tuple(p for p in live if p.is_live(at))


class SqlCouponRepository(_Repo, CouponRepository):
    def add(self, coupon: Coupon) -> Coupon:
        existing = self._one(
            select(rows.BillingCouponRow).where(
                rows.BillingCouponRow.org_id == coupon.org_id,
                rows.BillingCouponRow.code == coupon.code,
            )
        )
        if existing is not None:
            update_row(existing, coupon, _coupon_projection(coupon))
            self._session.flush()
            return coupon
        self._add(rows.BillingCouponRow(payload=encode(coupon), **_coupon_projection(coupon)))
        return coupon

    def get(self, org_id: str, code: str) -> Coupon | None:
        row = self._one(
            select(rows.BillingCouponRow).where(
                rows.BillingCouponRow.org_id == org_id, rows.BillingCouponRow.code == code
            )
        )
        return from_row(Coupon, row) if row else None

    def all(self, org_id: str) -> tuple[Coupon, ...]:
        found = self._all(
            select(rows.BillingCouponRow)
            .where(rows.BillingCouponRow.org_id == org_id)
            .order_by(rows.BillingCouponRow.code)
        )
        return tuple(from_row(Coupon, r) for r in found)


class SqlPricingVersionRepository(_Repo, PricingVersionRepository):
    """Append-only org-wide pricing snapshots."""

    def publish(self, snapshot: PricingVersion) -> PricingVersion:
        existing = self._one(
            select(rows.BillingPricingVersionRow).where(
                rows.BillingPricingVersionRow.org_id == snapshot.org_id,
                rows.BillingPricingVersionRow.version == snapshot.version,
            )
        )
        if existing is not None:
            raise ImmutabilityViolation(
                f"pricing version {snapshot.version} is already published and is immutable"
            )
        self._add(
            rows.BillingPricingVersionRow(
                payload=encode(snapshot), **_pricing_version_projection(snapshot)
            )
        )
        return snapshot

    def latest(self, org_id: str) -> PricingVersion | None:
        row = self._one(
            select(rows.BillingPricingVersionRow)
            .where(rows.BillingPricingVersionRow.org_id == org_id)
            .order_by(rows.BillingPricingVersionRow.version.desc())  # type: ignore[attr-defined]
        )
        return from_row(PricingVersion, row) if row else None

    def version(self, org_id: str, version: int) -> PricingVersion | None:
        row = self._one(
            select(rows.BillingPricingVersionRow).where(
                rows.BillingPricingVersionRow.org_id == org_id,
                rows.BillingPricingVersionRow.version == version,
            )
        )
        return from_row(PricingVersion, row) if row else None

    def history(self, org_id: str, limit: int = 50) -> tuple[PricingVersion, ...]:
        found = self._all(
            select(rows.BillingPricingVersionRow)
            .where(rows.BillingPricingVersionRow.org_id == org_id)
            .order_by(rows.BillingPricingVersionRow.version.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return tuple(from_row(PricingVersion, r) for r in found)

    def next_version(self, org_id: str) -> int:
        latest = self.latest(org_id)
        return 1 if latest is None else latest.version + 1


class SqlPricingActivityRepository(_Repo, PricingActivityRepository):
    """Append-only pricing history."""

    def append(self, activity: PricingActivity) -> PricingActivity:
        self._add(
            rows.BillingPricingActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        return activity

    def history(self, org_id: str, limit: int = 100) -> tuple[PricingActivity, ...]:
        found = self._all(
            select(rows.BillingPricingActivityRow)
            .where(rows.BillingPricingActivityRow.org_id == org_id)
            .order_by(
                rows.BillingPricingActivityRow.at.desc(),  # type: ignore[attr-defined]
                rows.BillingPricingActivityRow.id.desc(),  # type: ignore[attr-defined]
            )
            .limit(limit)
        )
        return tuple(from_row(PricingActivity, r) for r in found)

    def for_subject(self, org_id: str, subject_code: str) -> tuple[PricingActivity, ...]:
        found = self._all(
            select(rows.BillingPricingActivityRow)
            .where(
                rows.BillingPricingActivityRow.org_id == org_id,
                rows.BillingPricingActivityRow.subject_code == subject_code,
            )
            .order_by(rows.BillingPricingActivityRow.at, rows.BillingPricingActivityRow.id)
        )
        return tuple(from_row(PricingActivity, r) for r in found)


PRICING_SQL_REPOSITORIES = (
    (PricingBookRepository, SqlPricingBookRepository),
    (PriceBookVersionRepository, SqlPriceBookVersionRepository),
    (DiscountPolicyRepository, SqlDiscountPolicyRepository),
    (PromotionRepository, SqlPromotionRepository),
    (CouponRepository, SqlCouponRepository),
    (PricingVersionRepository, SqlPricingVersionRepository),
    (PricingActivityRepository, SqlPricingActivityRepository),
)

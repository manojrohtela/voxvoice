"""Commercial Catalog repositories (M9.6) — additive to the M9.4 persistence layer.

Six SQL implementations of the M9.6 Foundation contracts, reusing the M9.4 machinery
unchanged: the same `_Repo` session handle, the same codec, the same projection-plus-payload
mapping, the same "never commit, never roll back" rule, and the same org-scoped-by-
construction reads (§A11.2).

Two are append-only and expose no update or delete method at all: published catalog versions
and catalog activity. `next_version` is monotonic and never reuses a number — including
across a rollback, which moves *forward* to restore old content rather than rewinding.
"""

from __future__ import annotations

from sqlmodel import select

from billing.foundation import (
    Bundle,
    BundleRepository,
    CatalogActivity,
    CatalogActivityRepository,
    CatalogVersion,
    CatalogVersionRepository,
    Edition,
    EditionRepository,
    FeatureGroup,
    FeatureGroupRepository,
    ImmutabilityViolation,
    Product,
    ProductRepository,
)

from . import catalog_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo


def _product_projection(p: Product) -> dict:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "code": p.code,
        "kind": p.kind.value,
        "state": p.state.value,
        "visibility": p.visibility.value,
        "audience": p.audience.value,
        "updated_at": p.updated_at,
    }


def _edition_projection(e: Edition) -> dict:
    return {
        "id": e.id,
        "org_id": e.org_id,
        "code": e.code,
        "product_code": e.product_code,
        "state": e.state.value,
        "visibility": e.visibility.value,
        "rank": e.rank,
    }


def _bundle_projection(b: Bundle) -> dict:
    return {
        "id": b.id,
        "org_id": b.org_id,
        "code": b.code,
        "state": b.state.value,
        "visibility": b.visibility.value,
        "audience": b.audience.value,
    }


def _group_projection(g: FeatureGroup) -> dict:
    return {"id": g.id, "org_id": g.org_id, "code": g.code, "rank": g.rank}


def _version_projection(v: CatalogVersion) -> dict:
    return {
        "id": v.id,
        "org_id": v.org_id,
        "version": v.version,
        "published_at": v.published_at,
        "published_by": v.published_by,
        "rolled_back_from": v.rolled_back_from,
        "entity_count": v.entity_count,
        "notes": v.notes,
    }


def _activity_projection(a: CatalogActivity) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "action": a.action.value,
        "subject_kind": a.subject_kind,
        "subject_code": a.subject_code,
        "actor_id": a.actor_id,
        "at": a.at,
        "catalog_version": a.catalog_version,
    }


class SqlProductRepository(_Repo, ProductRepository):
    def add(self, product: Product) -> Product:
        existing = self._one(
            select(rows.BillingProductRow).where(
                rows.BillingProductRow.org_id == product.org_id,
                rows.BillingProductRow.code == product.code,
            )
        )
        if existing is not None:
            update_row(existing, product, _product_projection(product))
            self._session.flush()
            return product
        self._add(rows.BillingProductRow(payload=encode(product), **_product_projection(product)))
        return product

    def get(self, org_id: str, code: str) -> Product | None:
        row = self._one(
            select(rows.BillingProductRow).where(
                rows.BillingProductRow.org_id == org_id, rows.BillingProductRow.code == code
            )
        )
        return from_row(Product, row) if row else None

    def by_id(self, org_id: str, product_id: str) -> Product | None:
        row = self._one(
            select(rows.BillingProductRow).where(
                rows.BillingProductRow.org_id == org_id, rows.BillingProductRow.id == product_id
            )
        )
        return from_row(Product, row) if row else None

    def all(self, org_id: str) -> tuple[Product, ...]:
        found = self._all(
            select(rows.BillingProductRow)
            .where(rows.BillingProductRow.org_id == org_id)
            .order_by(rows.BillingProductRow.code)
        )
        return tuple(from_row(Product, r) for r in found)

    def replace(self, product: Product) -> Product:
        return self.add(product)


class SqlEditionRepository(_Repo, EditionRepository):
    def add(self, edition: Edition) -> Edition:
        existing = self._one(
            select(rows.BillingEditionRow).where(
                rows.BillingEditionRow.org_id == edition.org_id,
                rows.BillingEditionRow.product_code == edition.product_code,
                rows.BillingEditionRow.code == edition.code,
            )
        )
        if existing is not None:
            update_row(existing, edition, _edition_projection(edition))
            self._session.flush()
            return edition
        self._add(rows.BillingEditionRow(payload=encode(edition), **_edition_projection(edition)))
        return edition

    def get(self, org_id: str, product_code: str, code: str) -> Edition | None:
        statement = select(rows.BillingEditionRow).where(
            rows.BillingEditionRow.org_id == org_id, rows.BillingEditionRow.code == code
        )
        if product_code:
            statement = statement.where(rows.BillingEditionRow.product_code == product_code)
        row = self._one(statement)
        return from_row(Edition, row) if row else None

    def for_product(self, org_id: str, product_code: str) -> tuple[Edition, ...]:
        found = self._all(
            select(rows.BillingEditionRow)
            .where(
                rows.BillingEditionRow.org_id == org_id,
                rows.BillingEditionRow.product_code == product_code,
            )
            .order_by(rows.BillingEditionRow.rank, rows.BillingEditionRow.code)
        )
        return tuple(from_row(Edition, r) for r in found)

    def all(self, org_id: str) -> tuple[Edition, ...]:
        found = self._all(
            select(rows.BillingEditionRow)
            .where(rows.BillingEditionRow.org_id == org_id)
            .order_by(
                rows.BillingEditionRow.product_code,
                rows.BillingEditionRow.rank,
                rows.BillingEditionRow.code,
            )
        )
        return tuple(from_row(Edition, r) for r in found)

    def replace(self, edition: Edition) -> Edition:
        return self.add(edition)


class SqlBundleRepository(_Repo, BundleRepository):
    def add(self, bundle: Bundle) -> Bundle:
        existing = self._one(
            select(rows.BillingBundleRow).where(
                rows.BillingBundleRow.org_id == bundle.org_id,
                rows.BillingBundleRow.code == bundle.code,
            )
        )
        if existing is not None:
            update_row(existing, bundle, _bundle_projection(bundle))
            self._session.flush()
            return bundle
        self._add(rows.BillingBundleRow(payload=encode(bundle), **_bundle_projection(bundle)))
        return bundle

    def get(self, org_id: str, code: str) -> Bundle | None:
        row = self._one(
            select(rows.BillingBundleRow).where(
                rows.BillingBundleRow.org_id == org_id, rows.BillingBundleRow.code == code
            )
        )
        return from_row(Bundle, row) if row else None

    def all(self, org_id: str) -> tuple[Bundle, ...]:
        found = self._all(
            select(rows.BillingBundleRow)
            .where(rows.BillingBundleRow.org_id == org_id)
            .order_by(rows.BillingBundleRow.code)
        )
        return tuple(from_row(Bundle, r) for r in found)

    def replace(self, bundle: Bundle) -> Bundle:
        return self.add(bundle)


class SqlFeatureGroupRepository(_Repo, FeatureGroupRepository):
    def add(self, group: FeatureGroup) -> FeatureGroup:
        existing = self._one(
            select(rows.BillingFeatureGroupRow).where(
                rows.BillingFeatureGroupRow.org_id == group.org_id,
                rows.BillingFeatureGroupRow.code == group.code,
            )
        )
        if existing is not None:
            update_row(existing, group, _group_projection(group))
            self._session.flush()
            return group
        self._add(rows.BillingFeatureGroupRow(payload=encode(group), **_group_projection(group)))
        return group

    def get(self, org_id: str, code: str) -> FeatureGroup | None:
        row = self._one(
            select(rows.BillingFeatureGroupRow).where(
                rows.BillingFeatureGroupRow.org_id == org_id,
                rows.BillingFeatureGroupRow.code == code,
            )
        )
        return from_row(FeatureGroup, row) if row else None

    def all(self, org_id: str) -> tuple[FeatureGroup, ...]:
        found = self._all(
            select(rows.BillingFeatureGroupRow)
            .where(rows.BillingFeatureGroupRow.org_id == org_id)
            .order_by(rows.BillingFeatureGroupRow.rank, rows.BillingFeatureGroupRow.code)
        )
        return tuple(from_row(FeatureGroup, r) for r in found)


class SqlCatalogVersionRepository(_Repo, CatalogVersionRepository):
    """Append-only. There is no update and no delete method to call, and the table's unique
    `(org_id, version)` makes republishing a version a database-level error."""

    def publish(self, snapshot: CatalogVersion) -> CatalogVersion:
        existing = self._one(
            select(rows.BillingCatalogVersionRow).where(
                rows.BillingCatalogVersionRow.org_id == snapshot.org_id,
                rows.BillingCatalogVersionRow.version == snapshot.version,
            )
        )
        if existing is not None:
            # Defence in depth above the constraint: a published version is a snapshot, and
            # a snapshot that could be replaced would not be one.
            raise ImmutabilityViolation(
                f"catalog version {snapshot.version} is already published and is immutable"
            )
        self._add(
            rows.BillingCatalogVersionRow(payload=encode(snapshot), **_version_projection(snapshot))
        )
        return snapshot

    def latest(self, org_id: str) -> CatalogVersion | None:
        row = self._one(
            select(rows.BillingCatalogVersionRow)
            .where(rows.BillingCatalogVersionRow.org_id == org_id)
            .order_by(rows.BillingCatalogVersionRow.version.desc())  # type: ignore[attr-defined]
        )
        return from_row(CatalogVersion, row) if row else None

    def version(self, org_id: str, version: int) -> CatalogVersion | None:
        row = self._one(
            select(rows.BillingCatalogVersionRow).where(
                rows.BillingCatalogVersionRow.org_id == org_id,
                rows.BillingCatalogVersionRow.version == version,
            )
        )
        return from_row(CatalogVersion, row) if row else None

    def history(self, org_id: str, limit: int = 50) -> tuple[CatalogVersion, ...]:
        found = self._all(
            select(rows.BillingCatalogVersionRow)
            .where(rows.BillingCatalogVersionRow.org_id == org_id)
            .order_by(rows.BillingCatalogVersionRow.version.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return tuple(from_row(CatalogVersion, r) for r in found)

    def next_version(self, org_id: str) -> int:
        latest = self.latest(org_id)
        return 1 if latest is None else latest.version + 1


class SqlCatalogActivityRepository(_Repo, CatalogActivityRepository):
    """Append-only catalog history."""

    def append(self, activity: CatalogActivity) -> CatalogActivity:
        self._add(
            rows.BillingCatalogActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        return activity

    def history(self, org_id: str, limit: int = 100) -> tuple[CatalogActivity, ...]:
        found = self._all(
            select(rows.BillingCatalogActivityRow)
            .where(rows.BillingCatalogActivityRow.org_id == org_id)
            .order_by(
                rows.BillingCatalogActivityRow.at.desc(),  # type: ignore[attr-defined]
                rows.BillingCatalogActivityRow.id.desc(),  # type: ignore[attr-defined]
            )
            .limit(limit)
        )
        return tuple(from_row(CatalogActivity, r) for r in found)

    def for_subject(self, org_id: str, subject_code: str) -> tuple[CatalogActivity, ...]:
        found = self._all(
            select(rows.BillingCatalogActivityRow)
            .where(
                rows.BillingCatalogActivityRow.org_id == org_id,
                rows.BillingCatalogActivityRow.subject_code == subject_code,
            )
            .order_by(rows.BillingCatalogActivityRow.at, rows.BillingCatalogActivityRow.id)
        )
        return tuple(from_row(CatalogActivity, r) for r in found)


CATALOG_SQL_REPOSITORIES = (
    (ProductRepository, SqlProductRepository),
    (EditionRepository, SqlEditionRepository),
    (BundleRepository, SqlBundleRepository),
    (FeatureGroupRepository, SqlFeatureGroupRepository),
    (CatalogVersionRepository, SqlCatalogVersionRepository),
    (CatalogActivityRepository, SqlCatalogActivityRepository),
)

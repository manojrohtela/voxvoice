"""Catalog factories + repository contracts (M9.6).

`CatalogFactory` is the one place a catalog aggregate is constructed, following the M9.2
factory discipline exactly: validate structurally, **fail fast** on error-severity findings
(`BillingValidationError`, §A2.6), compose an immutable aggregate from the injected id and
clock seams, and emit the corresponding `billing.*` fact through the injected emitter.

No I/O, no persistence, no networking. Emission is the only effect and it is injected.

The repository interfaces below extend the M9.2 seam additively — same shape, same rules:
org-scoped reads by construction (§A11.2), and the two append-only stores (catalog versions,
catalog activity) deliberately declare **no update and no delete**. A published catalog
version that could be edited would not be a snapshot.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import replace

from .catalog import Feature, FeatureKind, MeterDefinition, Plan
from .catalog_rules import CatalogAssembler, CatalogValidator
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .products import (
    Bundle,
    CatalogAction,
    CatalogActivity,
    CatalogState,
    CatalogVersion,
    CommercialMetadata,
    Edition,
    FeatureGroup,
    Product,
    ProductKind,
    TargetAudience,
    Visibility,
)
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

PREFIX_PRODUCT = "prd"
PREFIX_EDITION = "edn"
PREFIX_BUNDLE = "bnd"
PREFIX_FEATURE_GROUP = "fgr"
PREFIX_CATALOG_VERSION = "cvr"
PREFIX_CATALOG_ACTIVITY = "cac"


class CatalogFactory:
    """Composes validated catalog aggregates. Stateless apart from its injected seams."""

    def __init__(
        self,
        *,
        ids: IdGenerator | None = None,
        clock: Clock | None = None,
        emitter: EventEmitter | None = None,
        validator: CatalogValidator | None = None,
    ) -> None:
        self._ids = ids or SequentialIdGenerator()
        self._clock = clock or SystemClock()
        self._emitter = emitter or NullEventEmitter()
        self._validator = validator or CatalogValidator()
        self._assembler = CatalogAssembler()

    # ── entities ────────────────────────────────────────────────────────────────

    def create_product(
        self,
        *,
        org_id: str,
        code: str,
        display_name: str,
        kind: ProductKind = ProductKind.PLATFORM,
        visibility: Visibility = Visibility.PUBLIC,
        audience: TargetAudience = TargetAudience.SELF_SERVE,
        feature_codes: tuple[str, ...] = (),
        edition_codes: tuple[str, ...] = (),
        metadata: CommercialMetadata | None = None,
        actor_id: str = "",
    ) -> Product:
        now = self._clock.now()
        product = Product(
            id=self._ids.new(PREFIX_PRODUCT),
            org_id=org_id,
            code=code,
            display_name=display_name,
            kind=kind,
            state=CatalogState.DRAFT,  # everything starts as a draft, always
            visibility=visibility,
            audience=audience,
            edition_codes=edition_codes,
            feature_codes=feature_codes,
            metadata=metadata or CommercialMetadata(),
            created_at=now,
            updated_at=now,
        )
        self._fail_fast(self._validator.validate_product(product))
        self._emit(BillingEventType.PRODUCT_CREATED, org_id, product.id, actor_id, code=code)
        return product

    def duplicate_product(
        self, source: Product, *, code: str, display_name: str = "", actor_id: str = ""
    ) -> Product:
        """Copy a product into a **new draft**.

        Duplication never copies lifecycle state: a copy of a published product starts as a
        draft, because the copy has been sold to nobody. It also never copies the id, and it
        clears `replaced_by_code` — a duplicate is a new commercial thing, not a clone of
        another product's deprecation history."""
        now = self._clock.now()
        product = replace(
            source,
            id=self._ids.new(PREFIX_PRODUCT),
            code=code,
            display_name=display_name or f"{source.display_name} (copy)",
            state=CatalogState.DRAFT,
            revision=1,
            created_at=now,
            updated_at=now,
            replaced_by_code="",
        )
        self._fail_fast(self._validator.validate_product(product))
        self._emit(
            BillingEventType.PRODUCT_DUPLICATED,
            source.org_id,
            product.id,
            actor_id,
            code=code,
            source_code=source.code,
        )
        return product

    def create_edition(
        self,
        *,
        org_id: str,
        code: str,
        display_name: str,
        product_code: str,
        plan_codes: tuple[str, ...] = (),
        feature_codes: tuple[str, ...] = (),
        rank: int = 0,
        visibility: Visibility = Visibility.PUBLIC,
        metadata: CommercialMetadata | None = None,
        known_products: frozenset[str] | None = None,
        actor_id: str = "",
    ) -> Edition:
        edition = Edition(
            id=self._ids.new(PREFIX_EDITION),
            org_id=org_id,
            code=code,
            display_name=display_name,
            product_code=product_code,
            state=CatalogState.DRAFT,
            visibility=visibility,
            plan_codes=plan_codes,
            feature_codes=feature_codes,
            rank=rank,
            metadata=metadata or CommercialMetadata(),
        )
        self._fail_fast(self._validator.validate_edition(edition, known_products=known_products))
        self._emit(
            BillingEventType.EDITION_CREATED,
            org_id,
            edition.id,
            actor_id,
            code=code,
            product_code=product_code,
        )
        return edition

    def create_bundle(
        self,
        *,
        org_id: str,
        code: str,
        display_name: str,
        product_codes: tuple[str, ...] = (),
        bundle_codes: tuple[str, ...] = (),
        feature_codes: tuple[str, ...] = (),
        visibility: Visibility = Visibility.PUBLIC,
        audience: TargetAudience = TargetAudience.SELF_SERVE,
        metadata: CommercialMetadata | None = None,
        known_bundles: Sequence[Bundle] = (),
        actor_id: str = "",
    ) -> Bundle:
        bundle = Bundle(
            id=self._ids.new(PREFIX_BUNDLE),
            org_id=org_id,
            code=code,
            display_name=display_name,
            state=CatalogState.DRAFT,
            visibility=visibility,
            audience=audience,
            product_codes=product_codes,
            bundle_codes=bundle_codes,
            feature_codes=feature_codes,
            metadata=metadata or CommercialMetadata(),
        )
        self._fail_fast(self._validator.validate_bundle(bundle, known_bundles=known_bundles))
        self._emit(BillingEventType.BUNDLE_CREATED, org_id, bundle.id, actor_id, code=code)
        return bundle

    def create_feature_group(
        self,
        *,
        org_id: str,
        code: str,
        display_name: str,
        feature_codes: tuple[str, ...] = (),
        description: str = "",
        rank: int = 0,
        actor_id: str = "",
    ) -> FeatureGroup:
        group = FeatureGroup(
            id=self._ids.new(PREFIX_FEATURE_GROUP),
            org_id=org_id,
            code=code,
            display_name=display_name,
            feature_codes=feature_codes,
            description=description,
            rank=rank,
        )
        self._fail_fast(self._validator.validate_feature_group(group))
        self._emit(BillingEventType.FEATURE_GROUP_CREATED, org_id, group.id, actor_id, code=code)
        return group

    def create_feature(
        self,
        *,
        org_id: str,
        code: str,
        display_name: str,
        kind: FeatureKind = FeatureKind.FLAG,
        meter_code: str = "",
        description: str = "",
        actor_id: str = "",
    ) -> Feature:
        """Features are catalog-global (their `Feature` aggregate carries no org), so this
        emits against the caller's organization for audit while keeping the aggregate as
        M9.2 defined it — additive extension means adding behaviour, not reshaping a type."""
        feature = Feature(
            id=self._ids.new("ftr"),
            code=code,
            display_name=display_name,
            kind=kind,
            meter_code=meter_code,
            description=description,
        )
        self._emit(BillingEventType.FEATURE_CREATED, org_id, feature.id, actor_id, code=code)
        return feature

    # ── snapshots ───────────────────────────────────────────────────────────────

    def publish_catalog(
        self,
        *,
        org_id: str,
        version: int,
        products: Sequence[Product] = (),
        editions: Sequence[Edition] = (),
        bundles: Sequence[Bundle] = (),
        feature_groups: Sequence[FeatureGroup] = (),
        plans: Sequence[Plan] = (),
        features: Sequence[Feature] = (),
        meters: Sequence[MeterDefinition] = (),
        notes: str = "",
        actor_id: str = "",
    ) -> CatalogVersion:
        """Validate the whole catalog, then freeze it into an immutable version.

        Validation runs **before** the snapshot exists, not after: once published, a version
        can never be corrected, only superseded."""
        diagnostics = self._validator.validate_catalog(
            products=products,
            editions=editions,
            bundles=bundles,
            feature_groups=feature_groups,
            plans=plans,
            features=features,
            meters=meters,
        )
        self._fail_fast(diagnostics)
        snapshot = self._assembler.assemble(
            snapshot_id=self._ids.new(PREFIX_CATALOG_VERSION),
            org_id=org_id,
            version=version,
            published_at=self._clock.now(),
            published_by=actor_id,
            notes=notes,
            products=products,
            editions=editions,
            bundles=bundles,
            feature_groups=feature_groups,
            plans=plans,
            features=features,
            meters=meters,
        )
        self._fail_fast(self._validator.validate_snapshot(snapshot))
        self._emit(
            BillingEventType.CATALOG_PUBLISHED,
            org_id,
            snapshot.id,
            actor_id,
            version=version,
            entities=snapshot.entity_count,
        )
        return snapshot

    def rollback_catalog(
        self, source: CatalogVersion, *, version: int, notes: str = "", actor_id: str = ""
    ) -> CatalogVersion:
        snapshot = self._assembler.rollback(
            source,
            snapshot_id=self._ids.new(PREFIX_CATALOG_VERSION),
            version=version,
            published_at=self._clock.now(),
            published_by=actor_id,
            notes=notes,
        )
        self._fail_fast(self._validator.validate_snapshot(snapshot))
        self._emit(
            BillingEventType.CATALOG_ROLLED_BACK,
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
        action: CatalogAction,
        subject_kind: str,
        subject_code: str,
        actor_id: str = "",
        catalog_version: int | None = None,
        detail: str = "",
    ) -> CatalogActivity:
        return CatalogActivity(
            id=self._ids.new(PREFIX_CATALOG_ACTIVITY),
            org_id=org_id,
            action=action,
            subject_kind=subject_kind,
            subject_code=subject_code,
            actor_id=actor_id,
            at=self._clock.now(),
            catalog_version=catalog_version,
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


class ProductRepository(ABC):
    @abstractmethod
    def add(self, product: Product) -> Product: ...
    @abstractmethod
    def get(self, org_id: str, code: str) -> Product | None: ...
    @abstractmethod
    def by_id(self, org_id: str, product_id: str) -> Product | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[Product, ...]: ...
    @abstractmethod
    def replace(self, product: Product) -> Product:
        """Whole-aggregate replacement. Catalog entities are mutable **until published into
        a version**; the published snapshot is what becomes immutable, not the draft."""


class EditionRepository(ABC):
    @abstractmethod
    def add(self, edition: Edition) -> Edition: ...
    @abstractmethod
    def get(self, org_id: str, product_code: str, code: str) -> Edition | None: ...
    @abstractmethod
    def for_product(self, org_id: str, product_code: str) -> tuple[Edition, ...]: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[Edition, ...]: ...
    @abstractmethod
    def replace(self, edition: Edition) -> Edition: ...


class BundleRepository(ABC):
    @abstractmethod
    def add(self, bundle: Bundle) -> Bundle: ...
    @abstractmethod
    def get(self, org_id: str, code: str) -> Bundle | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[Bundle, ...]: ...
    @abstractmethod
    def replace(self, bundle: Bundle) -> Bundle: ...


class FeatureGroupRepository(ABC):
    @abstractmethod
    def add(self, group: FeatureGroup) -> FeatureGroup: ...
    @abstractmethod
    def get(self, org_id: str, code: str) -> FeatureGroup | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[FeatureGroup, ...]: ...


class CatalogVersionRepository(ABC):
    """Append-only: published versions are immutable and historical versions remain readable
    forever. No update, no delete — there is no method to call."""

    @abstractmethod
    def publish(self, snapshot: CatalogVersion) -> CatalogVersion: ...
    @abstractmethod
    def latest(self, org_id: str) -> CatalogVersion | None: ...
    @abstractmethod
    def version(self, org_id: str, version: int) -> CatalogVersion | None: ...
    @abstractmethod
    def history(self, org_id: str, limit: int = 50) -> tuple[CatalogVersion, ...]: ...
    @abstractmethod
    def next_version(self, org_id: str) -> int:
        """The next version number for this organization. Monotonic and never reused —
        including across a rollback, which moves *forward* to restore old content."""


class CatalogActivityRepository(ABC):
    """Append-only catalog history."""

    @abstractmethod
    def append(self, activity: CatalogActivity) -> CatalogActivity: ...
    @abstractmethod
    def history(self, org_id: str, limit: int = 100) -> tuple[CatalogActivity, ...]: ...
    @abstractmethod
    def for_subject(self, org_id: str, subject_code: str) -> tuple[CatalogActivity, ...]: ...

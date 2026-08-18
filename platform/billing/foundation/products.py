"""Commercial Catalog domain (M9.6) — what is sold, and nothing about what it costs.

ADR-0023 §5.1 gives Billing the commercial catalog; this module gives that catalog its
shape. The organizing rule is a boundary, stated once and enforced everywhere below:

**The Catalog owns products, editions, plans, features, bundles, and commercial metadata.
It owns no price, no subscription, no invoice, no entitlement, and no usage.** Not one type
here carries a `Money`, a currency, an amount, or a rate — a test asserts it. A plan says
*what a customer gets*; a price book (M9.7) says what they pay, and the two version
independently on purpose: repricing a plan must not require republishing the product, and
correcting a marketing headline must not touch revenue.

**Immutable after publication.** Publishing produces a `CatalogVersion` — a whole,
self-contained snapshot. Subscriptions reference a snapshot version, never a mutable code,
which is what makes a two-year-old invoice reproducible. Rollback publishes a *new* version
whose content is copied from an old one; history is appended to, never rewritten (§A5).

The lifecycle (draft → published → deprecated → archived → deleted) is a table-driven FSM on
`core.fsm`, so an illegal move is rejected rather than coerced (§A2.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .catalog import Feature, MeterDefinition, Plan


class CatalogState(StrEnum):
    """One lifecycle for every catalog entity — products, editions, bundles, plans.

    `DELETED` is a recorded terminal state, not a row removal: a code that was once sold
    must never be silently reusable, and *why* something left the catalog is itself a
    commercial fact."""

    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    DELETED = "deleted"


class CatalogTrigger(StrEnum):
    PUBLISH = "publish"
    DEPRECATE = "deprecate"
    ARCHIVE = "archive"
    RESTORE = "restore"
    DELETE = "delete"


# The catalog lifecycle. Deliberately narrow:
#   * a DRAFT may be deleted outright — nothing was ever sold under it;
#   * a PUBLISHED entity may never jump straight to DELETED — it must be deprecated and
#     archived first, so an entity a customer may hold cannot vanish in one step;
#   * RESTORE returns an ARCHIVED or DEPRECATED entity to PUBLISHED, and is the *only*
#     way back — there is no edge out of DELETED, which is what makes it terminal.
CATALOG_LIFECYCLE: StateMachineDefinition = define(
    [
        (CatalogState.DRAFT, CatalogTrigger.PUBLISH, CatalogState.PUBLISHED),
        (CatalogState.DRAFT, CatalogTrigger.DELETE, CatalogState.DELETED),
        (CatalogState.PUBLISHED, CatalogTrigger.DEPRECATE, CatalogState.DEPRECATED),
        (CatalogState.PUBLISHED, CatalogTrigger.ARCHIVE, CatalogState.ARCHIVED),
        (CatalogState.DEPRECATED, CatalogTrigger.ARCHIVE, CatalogState.ARCHIVED),
        (CatalogState.DEPRECATED, CatalogTrigger.RESTORE, CatalogState.PUBLISHED),
        (CatalogState.ARCHIVED, CatalogTrigger.RESTORE, CatalogState.PUBLISHED),
        (CatalogState.ARCHIVED, CatalogTrigger.DELETE, CatalogState.DELETED),
    ]
)


class Visibility(StrEnum):
    """Who may *see* an entity. Not who may buy it, and emphatically not a permission —
    Billing evaluates no permission (ADR-0023 §5.3). Visibility is catalog metadata that a
    consuming surface filters on; the authorization decision stays where it belongs."""

    PUBLIC = "public"
    PARTNER = "partner"
    PRIVATE = "private"  # named accounts only
    INTERNAL = "internal"  # never shown to a customer


class TargetAudience(StrEnum):
    SELF_SERVE = "self_serve"
    SMB = "smb"
    ENTERPRISE = "enterprise"
    PARTNER = "partner"
    INTERNAL = "internal"


class ProductKind(StrEnum):
    PLATFORM = "platform"  # the core product
    MODULE = "module"  # a separately-sold capability
    ADD_ON = "add_on"  # only sellable alongside a platform product
    SERVICE = "service"  # professional services, support


@dataclass(frozen=True, slots=True)
class CommercialMetadata:
    """Documentation and marketing copy. Deliberately part of the catalog and therefore
    part of a published snapshot: what a customer was *told* they were buying is evidence,
    and a support conversation about a two-year-old plan needs the copy of that day."""

    headline: str = ""
    description: str = ""
    documentation_url: str = ""
    marketing_url: str = ""
    icon: str = ""
    tags: tuple[str, ...] = ()
    highlights: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Product:
    """A commercial product — the thing a customer buys, independent of how it is packaged.

    A product references its editions by code rather than containing them, so an edition can
    be added without a new product revision. Prices are absent by construction."""

    id: str
    org_id: str
    code: str
    display_name: str
    kind: ProductKind = ProductKind.PLATFORM
    state: CatalogState = CatalogState.DRAFT
    visibility: Visibility = Visibility.PUBLIC
    audience: TargetAudience = TargetAudience.SELF_SERVE
    edition_codes: tuple[str, ...] = ()
    feature_codes: tuple[str, ...] = ()
    metadata: CommercialMetadata = field(default_factory=CommercialMetadata)
    revision: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    replaced_by_code: str = ""  # set on deprecation: what to migrate to

    @property
    def is_sellable(self) -> bool:
        """Only a published product may be sold. Deprecated products keep serving existing
        customers but accept no new ones — the distinction that makes deprecation useful."""
        return self.state is CatalogState.PUBLISHED

    @property
    def is_visible_to_customers(self) -> bool:
        return self.visibility is not Visibility.INTERNAL and self.state in (
            CatalogState.PUBLISHED,
            CatalogState.DEPRECATED,
        )


@dataclass(frozen=True, slots=True)
class Edition:
    """A commercial edition of a product (Starter / Pro / Enterprise).

    `rank` orders editions for upgrade/downgrade reasoning **without encoding price**: an
    edition is above another commercially, which is a catalog fact, not a monetary one."""

    id: str
    org_id: str
    code: str
    display_name: str
    product_code: str
    state: CatalogState = CatalogState.DRAFT
    visibility: Visibility = Visibility.PUBLIC
    plan_codes: tuple[str, ...] = ()
    feature_codes: tuple[str, ...] = ()
    rank: int = 0
    metadata: CommercialMetadata = field(default_factory=CommercialMetadata)


@dataclass(frozen=True, slots=True)
class FeatureGroup:
    """A named set of features, for presentation and reuse. Grouping is a catalog concern;
    it grants nothing — an entitlement is a separate fact the Entitlement domain owns."""

    id: str
    org_id: str
    code: str
    display_name: str
    feature_codes: tuple[str, ...] = ()
    description: str = ""
    rank: int = 0


@dataclass(frozen=True, slots=True)
class Bundle:
    """Several products (and, recursively, other bundles) sold as one commercial unit.

    Nesting is allowed because real bundles nest — but a cycle is rejected at validation,
    not discovered at expansion time by a stack overflow."""

    id: str
    org_id: str
    code: str
    display_name: str
    state: CatalogState = CatalogState.DRAFT
    visibility: Visibility = Visibility.PUBLIC
    audience: TargetAudience = TargetAudience.SELF_SERVE
    product_codes: tuple[str, ...] = ()
    bundle_codes: tuple[str, ...] = ()  # nested bundles
    feature_codes: tuple[str, ...] = ()
    metadata: CommercialMetadata = field(default_factory=CommercialMetadata)

    @property
    def member_codes(self) -> tuple[str, ...]:
        return self.product_codes + self.bundle_codes


@dataclass(frozen=True, slots=True)
class CatalogVersion:
    """An immutable, self-contained snapshot of the whole commercial catalog.

    Self-contained is the point: a snapshot embeds the products, editions, bundles, groups,
    plans, features, and meters as they were, so reading version 7 never depends on what
    version 12 did. A subscription references `(org_id, version)` and can always be
    explained, years later, from the snapshot alone.

    `rolled_back_from` records provenance when this version was produced by a rollback —
    the history stays append-only and the rollback is visible as an event, not a gap."""

    id: str
    org_id: str
    version: int
    published_at: float
    published_by: str = ""
    notes: str = ""
    rolled_back_from: int | None = None
    products: tuple[Product, ...] = ()
    editions: tuple[Edition, ...] = ()
    bundles: tuple[Bundle, ...] = ()
    feature_groups: tuple[FeatureGroup, ...] = ()
    plans: tuple[Plan, ...] = ()
    features: tuple[Feature, ...] = ()
    meters: tuple[MeterDefinition, ...] = ()

    @property
    def is_rollback(self) -> bool:
        return self.rolled_back_from is not None

    @property
    def entity_count(self) -> int:
        return (
            len(self.products)
            + len(self.editions)
            + len(self.bundles)
            + len(self.feature_groups)
            + len(self.plans)
            + len(self.features)
            + len(self.meters)
        )

    def product(self, code: str) -> Product | None:
        return next((p for p in self.products if p.code == code), None)

    def plan(self, code: str, version: int | None = None) -> Plan | None:
        matches = [p for p in self.plans if p.code == code]
        if version is not None:
            matches = [p for p in matches if p.version == version]
        return max(matches, key=lambda p: p.version) if matches else None


class CatalogAction(StrEnum):
    """What happened to the catalog. The activity log's closed vocabulary."""

    PRODUCT_CREATED = "product.created"
    PRODUCT_PUBLISHED = "product.published"
    PRODUCT_DEPRECATED = "product.deprecated"
    PRODUCT_ARCHIVED = "product.archived"
    PRODUCT_RESTORED = "product.restored"
    PRODUCT_DUPLICATED = "product.duplicated"
    PRODUCT_DELETED = "product.deleted"
    PLAN_CREATED = "plan.created"
    PLAN_PUBLISHED = "plan.published"
    PLAN_ARCHIVED = "plan.archived"
    EDITION_CREATED = "edition.created"
    BUNDLE_CREATED = "bundle.created"
    FEATURE_CREATED = "feature.created"
    FEATURE_GROUP_CREATED = "feature_group.created"
    CATALOG_PUBLISHED = "catalog.published"
    CATALOG_ROLLED_BACK = "catalog.rolled_back"


@dataclass(frozen=True, slots=True)
class CatalogActivity:
    """One append-only entry in the catalog's own history: who changed what, when.

    Kept alongside the `billing.*` event stream rather than instead of it — the event stream
    is the platform-wide fact log, this is the catalog's queryable, ordered history, and a
    catalog screen should not have to filter the whole revenue stream to render it."""

    id: str
    org_id: str
    action: CatalogAction
    subject_kind: str
    subject_code: str
    actor_id: str
    at: float
    catalog_version: int | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CatalogSearchQuery:
    """A deterministic filter. No relevance scoring, no full-text engine, no ranking model —
    given the same catalog and the same query, the same rows come back in the same order,
    which is what a catalog admin screen and a reproducible test both need."""

    text: str = ""  # case-insensitive substring over code, name, description, tags
    kinds: tuple[ProductKind, ...] = ()
    states: tuple[CatalogState, ...] = ()
    visibilities: tuple[Visibility, ...] = ()
    audiences: tuple[TargetAudience, ...] = ()
    tags: tuple[str, ...] = ()
    feature_code: str = ""
    product_code: str = ""
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class CatalogSearchResult:
    """A page of matches plus the unpaged total, so a caller can render "20 of 137"."""

    products: tuple[Product, ...] = ()
    editions: tuple[Edition, ...] = ()
    bundles: tuple[Bundle, ...] = ()
    plans: tuple[Plan, ...] = ()
    features: tuple[Feature, ...] = ()
    feature_groups: tuple[FeatureGroup, ...] = ()
    total: int = 0

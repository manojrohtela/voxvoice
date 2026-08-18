"""Commercial Catalog application layer (M9.6) — commands, queries, use cases, mapping.

Additive to M9.3: the buses, Unit of Work, authorization orchestrator, application-event
vocabulary, and dispatch discipline are reused exactly as they are. This module adds 13
commands, 7 queries, their use cases, and the view DTOs — and nothing else.

The four beats are unchanged: **authorize → open a transaction → coordinate the Foundation →
stage an event.** Every catalog rule — code validity, duplicate detection, bundle cycles,
edition conflicts, lifecycle legality, snapshot assembly, search filtering — is Foundation
behaviour these methods *call*. There is no rule here, and a test greps for the ones that
would be tempting to inline.

One catalog-specific discipline worth stating: **every command also appends a
`CatalogActivity`**, inside the same transaction as the change it describes. A catalog whose
history could disagree with its content would be worse than one with no history at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from billing.foundation import BillingNotFound as _NotFound
from billing.foundation import (
    Bundle,
    CatalogAction,
    CatalogSearchQuery,
    CatalogTrigger,
    CatalogVersion,
    CommercialMetadata,
    Edition,
    Feature,
    FeatureGroup,
    FeatureKind,
    Plan,
    Product,
    ProductKind,
    TargetAudience,
    Visibility,
)

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CreateProduct(_Command):
    code: str = ""
    display_name: str = ""
    kind: ProductKind = ProductKind.PLATFORM
    visibility: Visibility = Visibility.PUBLIC
    audience: TargetAudience = TargetAudience.SELF_SERVE
    feature_codes: tuple[str, ...] = ()
    edition_codes: tuple[str, ...] = ()
    metadata: CommercialMetadata = field(default_factory=CommercialMetadata)


@dataclass(frozen=True, slots=True)
class PublishProduct(_Command):
    code: str = ""


@dataclass(frozen=True, slots=True)
class DeprecateProduct(_Command):
    code: str = ""
    replaced_by_code: str = ""


@dataclass(frozen=True, slots=True)
class ArchiveProduct(_Command):
    code: str = ""


@dataclass(frozen=True, slots=True)
class RestoreProduct(_Command):
    code: str = ""


@dataclass(frozen=True, slots=True)
class DuplicateProduct(_Command):
    source_code: str = ""
    code: str = ""
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class CreateCatalogPlan(_Command):
    """Catalog-side plan creation. Named distinctly from M9.3's `DefinePlan` because both
    exist and mean different things: `DefinePlan` is the M9.3 command the pricing-facing
    surface already dispatches, while this one additionally records catalog activity and
    associates the plan with an edition. Two names beat one name that quietly changed."""

    code: str = ""
    display_name: str = ""
    tier: str = "free"
    edition_code: str = ""
    feature_codes: tuple[str, ...] = ()
    metered_codes: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True, slots=True)
class PublishCatalogPlan(_Command):
    code: str = ""
    version: int = 1


@dataclass(frozen=True, slots=True)
class ArchiveCatalogPlan(_Command):
    code: str = ""
    version: int = 1


@dataclass(frozen=True, slots=True)
class CreateEdition(_Command):
    code: str = ""
    display_name: str = ""
    product_code: str = ""
    plan_codes: tuple[str, ...] = ()
    feature_codes: tuple[str, ...] = ()
    rank: int = 0
    visibility: Visibility = Visibility.PUBLIC
    metadata: CommercialMetadata = field(default_factory=CommercialMetadata)


@dataclass(frozen=True, slots=True)
class CreateBundle(_Command):
    code: str = ""
    display_name: str = ""
    product_codes: tuple[str, ...] = ()
    bundle_codes: tuple[str, ...] = ()
    feature_codes: tuple[str, ...] = ()
    visibility: Visibility = Visibility.PUBLIC
    audience: TargetAudience = TargetAudience.SELF_SERVE
    metadata: CommercialMetadata = field(default_factory=CommercialMetadata)


@dataclass(frozen=True, slots=True)
class CreateFeature(_Command):
    code: str = ""
    display_name: str = ""
    kind: FeatureKind = FeatureKind.FLAG
    meter_code: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class CreateFeatureGroup(_Command):
    code: str = ""
    display_name: str = ""
    feature_codes: tuple[str, ...] = ()
    description: str = ""
    rank: int = 0


@dataclass(frozen=True, slots=True)
class PublishCatalog(_Command):
    notes: str = ""


@dataclass(frozen=True, slots=True)
class RollbackCatalog(_Command):
    to_version: int = 1
    notes: str = ""


ALL_CATALOG_COMMANDS = (
    CreateProduct,
    PublishProduct,
    DeprecateProduct,
    ArchiveProduct,
    RestoreProduct,
    DuplicateProduct,
    CreateCatalogPlan,
    PublishCatalogPlan,
    ArchiveCatalogPlan,
    CreateEdition,
    CreateBundle,
    CreateFeature,
    CreateFeatureGroup,
    PublishCatalog,
    RollbackCatalog,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetProduct(_Query):
    code: str = ""


@dataclass(frozen=True, slots=True)
class ListProducts(_Query):
    include_internal: bool = False


@dataclass(frozen=True, slots=True)
class SearchCatalog(_Query):
    query: CatalogSearchQuery = field(default_factory=CatalogSearchQuery)


@dataclass(frozen=True, slots=True)
class GetCatalogPlan(_Query):
    code: str = ""
    version: int | None = None


@dataclass(frozen=True, slots=True)
class ListCatalogPlans(_Query):
    edition_code: str = ""


@dataclass(frozen=True, slots=True)
class ListBundles(_Query):
    pass


@dataclass(frozen=True, slots=True)
class ListEditions(_Query):
    product_code: str = ""


@dataclass(frozen=True, slots=True)
class ListFeatureGroups(_Query):
    pass


@dataclass(frozen=True, slots=True)
class GetCatalogVersion(_Query):
    version: int | None = None  # None ⇒ latest


@dataclass(frozen=True, slots=True)
class CatalogVersionHistory(_Query):
    limit: int = 50


@dataclass(frozen=True, slots=True)
class CatalogActivityHistory(_Query):
    subject_code: str = ""
    limit: int = 100


ALL_CATALOG_QUERIES = (
    GetProduct,
    ListProducts,
    SearchCatalog,
    GetCatalogPlan,
    ListCatalogPlans,
    ListBundles,
    ListEditions,
    ListFeatureGroups,
    GetCatalogVersion,
    CatalogVersionHistory,
    CatalogActivityHistory,
)


# ── view DTOs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MetadataView:
    headline: str
    description: str
    documentation_url: str
    marketing_url: str
    icon: str
    tags: tuple[str, ...]
    highlights: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProductView:
    id: str
    code: str
    display_name: str
    kind: str
    state: str
    visibility: str
    audience: str
    edition_codes: tuple[str, ...]
    feature_codes: tuple[str, ...]
    metadata: MetadataView
    is_sellable: bool
    allowed_triggers: tuple[str, ...]
    replaced_by_code: str


@dataclass(frozen=True, slots=True)
class EditionView:
    id: str
    code: str
    display_name: str
    product_code: str
    state: str
    visibility: str
    plan_codes: tuple[str, ...]
    feature_codes: tuple[str, ...]
    rank: int


@dataclass(frozen=True, slots=True)
class BundleView:
    id: str
    code: str
    display_name: str
    state: str
    visibility: str
    audience: str
    product_codes: tuple[str, ...]
    bundle_codes: tuple[str, ...]
    feature_codes: tuple[str, ...]
    expanded_product_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FeatureGroupView:
    id: str
    code: str
    display_name: str
    feature_codes: tuple[str, ...]
    description: str
    rank: int


@dataclass(frozen=True, slots=True)
class CatalogVersionView:
    id: str
    version: int
    published_at: float
    published_by: str
    notes: str
    rolled_back_from: int | None
    entity_count: int
    product_codes: tuple[str, ...]
    plan_codes: tuple[str, ...]
    bundle_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogActivityView:
    id: str
    action: str
    subject_kind: str
    subject_code: str
    actor_id: str
    at: float
    catalog_version: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class CatalogSearchView:
    products: tuple[ProductView, ...]
    editions: tuple[EditionView, ...]
    bundles: tuple[BundleView, ...]
    plans: tuple[dict, ...]
    features: tuple[dict, ...]
    feature_groups: tuple[FeatureGroupView, ...]
    total: int


def metadata_view(meta: CommercialMetadata) -> MetadataView:
    return MetadataView(
        headline=meta.headline,
        description=meta.description,
        documentation_url=meta.documentation_url,
        marketing_url=meta.marketing_url,
        icon=meta.icon,
        tags=meta.tags,
        highlights=meta.highlights,
    )


def product_view(product: Product, allowed: tuple[CatalogTrigger, ...] = ()) -> ProductView:
    return ProductView(
        id=product.id,
        code=product.code,
        display_name=product.display_name,
        kind=product.kind.value,
        state=product.state.value,
        visibility=product.visibility.value,
        audience=product.audience.value,
        edition_codes=product.edition_codes,
        feature_codes=product.feature_codes,
        metadata=metadata_view(product.metadata),
        is_sellable=product.is_sellable,
        allowed_triggers=tuple(t.value for t in allowed),
        replaced_by_code=product.replaced_by_code,
    )


def edition_view(edition: Edition) -> EditionView:
    return EditionView(
        id=edition.id,
        code=edition.code,
        display_name=edition.display_name,
        product_code=edition.product_code,
        state=edition.state.value,
        visibility=edition.visibility.value,
        plan_codes=edition.plan_codes,
        feature_codes=edition.feature_codes,
        rank=edition.rank,
    )


def bundle_view(bundle: Bundle, expanded: tuple[str, ...] = ()) -> BundleView:
    return BundleView(
        id=bundle.id,
        code=bundle.code,
        display_name=bundle.display_name,
        state=bundle.state.value,
        visibility=bundle.visibility.value,
        audience=bundle.audience.value,
        product_codes=bundle.product_codes,
        bundle_codes=bundle.bundle_codes,
        feature_codes=bundle.feature_codes,
        expanded_product_codes=expanded,
    )


def feature_group_view(group: FeatureGroup) -> FeatureGroupView:
    return FeatureGroupView(
        id=group.id,
        code=group.code,
        display_name=group.display_name,
        feature_codes=group.feature_codes,
        description=group.description,
        rank=group.rank,
    )


def catalog_version_view(snapshot: CatalogVersion) -> CatalogVersionView:
    return CatalogVersionView(
        id=snapshot.id,
        version=snapshot.version,
        published_at=snapshot.published_at,
        published_by=snapshot.published_by,
        notes=snapshot.notes,
        rolled_back_from=snapshot.rolled_back_from,
        entity_count=snapshot.entity_count,
        product_codes=tuple(p.code for p in snapshot.products),
        plan_codes=tuple(f"{p.code}@{p.version}" for p in snapshot.plans),
        bundle_codes=tuple(b.code for b in snapshot.bundles),
    )


def activity_view(activity) -> CatalogActivityView:
    return CatalogActivityView(
        id=activity.id,
        action=activity.action.value,
        subject_kind=activity.subject_kind,
        subject_code=activity.subject_code,
        actor_id=activity.actor_id,
        at=activity.at,
        catalog_version=activity.catalog_version,
        detail=activity.detail,
    )


def _plan_dict(plan: Plan) -> dict:
    return {
        "code": plan.code,
        "version": plan.version,
        "display_name": plan.display_name,
        "tier": plan.tier.value,
        "published": plan.published,
        "deprecated": plan.deprecated,
        "feature_codes": [f.feature_code for f in plan.features],
        "metered_codes": list(plan.metered_codes),
    }


def _feature_dict(feature: Feature) -> dict:
    return {
        "code": feature.code,
        "display_name": feature.display_name,
        "kind": feature.kind.value,
        "meter_code": feature.meter_code,
        "description": feature.description,
    }


# ── use cases ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CatalogRepositories:
    """The catalog seams, injected. Interfaces only — implementations land in persistence."""

    products: object
    editions: object
    bundles: object
    feature_groups: object
    versions: object
    activity: object


class CatalogCommandUseCases:
    """Orchestration for the commercial catalog. Coordinates; decides nothing."""

    def __init__(
        self,
        *,
        context,
        catalog_repos: CatalogRepositories,
        factory,
        lifecycle,
        resolver,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        self._ctx = context
        self._repos = catalog_repos
        self._factory = factory
        self._lifecycle = lifecycle
        self._resolver = resolver
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── products ────────────────────────────────────────────────────────────────

    def create_product(self, command: CreateProduct) -> Product:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            product = self._factory.create_product(
                org_id=command.org_id,
                code=command.code,
                display_name=command.display_name,
                kind=command.kind,
                visibility=command.visibility,
                audience=command.audience,
                feature_codes=command.feature_codes,
                edition_codes=command.edition_codes,
                metadata=command.metadata,
                actor_id=command.actor_id,
            )
            stored = self._repos.products.add(product)
            self._record(command, CatalogAction.PRODUCT_CREATED, "product", stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    def publish_product(self, command: PublishProduct) -> Product:
        return self._transition(
            command, command.code, CatalogTrigger.PUBLISH, CatalogAction.PRODUCT_PUBLISHED
        )

    def deprecate_product(self, command: DeprecateProduct) -> Product:
        return self._transition(
            command,
            command.code,
            CatalogTrigger.DEPRECATE,
            CatalogAction.PRODUCT_DEPRECATED,
            replaced_by=command.replaced_by_code,
        )

    def archive_product(self, command: ArchiveProduct) -> Product:
        return self._transition(
            command, command.code, CatalogTrigger.ARCHIVE, CatalogAction.PRODUCT_ARCHIVED
        )

    def restore_product(self, command: RestoreProduct) -> Product:
        return self._transition(
            command, command.code, CatalogTrigger.RESTORE, CatalogAction.PRODUCT_RESTORED
        )

    def duplicate_product(self, command: DuplicateProduct) -> Product:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            source = self._product(command.org_id, command.source_code)
            copy = self._factory.duplicate_product(
                source,
                code=command.code,
                display_name=command.display_name,
                actor_id=command.actor_id,
            )
            stored = self._repos.products.add(copy)
            self._record(
                command,
                CatalogAction.PRODUCT_DUPLICATED,
                "product",
                stored.code,
                detail=f"copied from {source.code}",
            )
            uow.stage(self._event(command, stored.id, code=stored.code, source=source.code))
            return stored

    # ── plans ───────────────────────────────────────────────────────────────────

    def create_plan(self, command: CreateCatalogPlan) -> Plan:
        """Delegates plan construction to the M9.2 `BillingFactory` the Application already
        holds — the catalog adds association and history, never a second plan definition."""
        from billing.foundation import DefinePlanRequest, PlanFeature, Tier

        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            known_meters = frozenset(
                m.code for m in self._ctx.repositories.meters.all(org_id=command.org_id)
            )
            plan = self._ctx.factory.define_plan(
                DefinePlanRequest(
                    code=command.code,
                    display_name=command.display_name,
                    tier=Tier(command.tier),
                    features=tuple(PlanFeature(code) for code in command.feature_codes),
                    metered_codes=command.metered_codes,
                    org_id=command.org_id,
                ),
                known_meters=known_meters,
                actor_id=command.actor_id,
            )
            stored = self._ctx.repositories.plans.add(plan)
            if command.edition_code:
                edition = self._repos.editions.get(
                    command.org_id, "", command.edition_code
                ) or self._edition_by_code(command.org_id, command.edition_code)
                if edition is not None and stored.code not in edition.plan_codes:
                    self._repos.editions.replace(
                        replace(edition, plan_codes=edition.plan_codes + (stored.code,))
                    )
            self._record(command, CatalogAction.PLAN_CREATED, "plan", stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    def publish_plan(self, command: PublishCatalogPlan) -> Plan:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            plan = self._ctx.repositories.plans.get(
                command.code, command.version, org_id=command.org_id
            )
            if plan is None:
                raise _NotFound(f"plan {command.code!r} v{command.version} not found")
            published = self._ctx.repositories.plans.add(
                self._ctx.factory.publish_plan(plan, actor_id=command.actor_id)
            )
            self._record(command, CatalogAction.PLAN_PUBLISHED, "plan", published.code)
            uow.stage(self._event(command, published.id, code=published.code))
            return published

    def archive_plan(self, command: ArchiveCatalogPlan) -> Plan:
        """Archiving a plan marks it deprecated in the catalog. Existing subscriptions are
        untouched — a plan leaving the shelf is not a plan leaving the customers who hold
        it, and the Subscription domain owns that entirely."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            plan = self._ctx.repositories.plans.get(
                command.code, command.version, org_id=command.org_id
            )
            if plan is None:
                raise _NotFound(f"plan {command.code!r} v{command.version} not found")
            archived = self._ctx.repositories.plans.add(replace(plan, deprecated=True))
            self._record(command, CatalogAction.PLAN_ARCHIVED, "plan", archived.code)
            uow.stage(self._event(command, archived.id, code=archived.code))
            return archived

    # ── editions, bundles, features ─────────────────────────────────────────────

    def create_edition(self, command: CreateEdition) -> Edition:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            known = frozenset(p.code for p in self._repos.products.all(command.org_id))
            edition = self._factory.create_edition(
                org_id=command.org_id,
                code=command.code,
                display_name=command.display_name,
                product_code=command.product_code,
                plan_codes=command.plan_codes,
                feature_codes=command.feature_codes,
                rank=command.rank,
                visibility=command.visibility,
                metadata=command.metadata,
                known_products=known,
                actor_id=command.actor_id,
            )
            stored = self._repos.editions.add(edition)
            product = self._repos.products.get(command.org_id, command.product_code)
            if product is not None and stored.code not in product.edition_codes:
                self._repos.products.replace(
                    replace(
                        product,
                        edition_codes=product.edition_codes + (stored.code,),
                        updated_at=self._clock.now(),
                    )
                )
            self._record(command, CatalogAction.EDITION_CREATED, "edition", stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    def create_bundle(self, command: CreateBundle) -> Bundle:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            known = self._repos.bundles.all(command.org_id)
            bundle = self._factory.create_bundle(
                org_id=command.org_id,
                code=command.code,
                display_name=command.display_name,
                product_codes=command.product_codes,
                bundle_codes=command.bundle_codes,
                feature_codes=command.feature_codes,
                visibility=command.visibility,
                audience=command.audience,
                metadata=command.metadata,
                known_bundles=known,
                actor_id=command.actor_id,
            )
            stored = self._repos.bundles.add(bundle)
            self._record(command, CatalogAction.BUNDLE_CREATED, "bundle", stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    def create_feature(self, command: CreateFeature) -> Feature:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            feature = self._factory.create_feature(
                org_id=command.org_id,
                code=command.code,
                display_name=command.display_name,
                kind=command.kind,
                meter_code=command.meter_code,
                description=command.description,
                actor_id=command.actor_id,
            )
            stored = self._ctx.repositories.features.add(feature)
            self._record(command, CatalogAction.FEATURE_CREATED, "feature", stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    def create_feature_group(self, command: CreateFeatureGroup) -> FeatureGroup:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            group = self._factory.create_feature_group(
                org_id=command.org_id,
                code=command.code,
                display_name=command.display_name,
                feature_codes=command.feature_codes,
                description=command.description,
                rank=command.rank,
                actor_id=command.actor_id,
            )
            stored = self._repos.feature_groups.add(group)
            self._record(command, CatalogAction.FEATURE_GROUP_CREATED, "feature_group", stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code))
            return stored

    # ── publishing ──────────────────────────────────────────────────────────────

    def publish_catalog(self, command: PublishCatalog) -> CatalogVersion:
        """Freeze the whole catalog into the next immutable version."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            version = self._repos.versions.next_version(command.org_id)
            snapshot = self._factory.publish_catalog(
                org_id=command.org_id,
                version=version,
                products=self._repos.products.all(command.org_id),
                editions=self._repos.editions.all(command.org_id),
                bundles=self._repos.bundles.all(command.org_id),
                feature_groups=self._repos.feature_groups.all(command.org_id),
                plans=self._ctx.repositories.plans.published(org_id=command.org_id),
                features=self._ctx.repositories.features.all(),
                meters=self._ctx.repositories.meters.all(org_id=command.org_id),
                notes=command.notes,
                actor_id=command.actor_id,
            )
            stored = self._repos.versions.publish(snapshot)
            self._record(
                command,
                CatalogAction.CATALOG_PUBLISHED,
                "catalog",
                f"v{stored.version}",
                catalog_version=stored.version,
                detail=command.notes,
            )
            uow.stage(
                self._event(
                    command, stored.id, version=stored.version, entities=stored.entity_count
                )
            )
            return stored

    def rollback_catalog(self, command: RollbackCatalog) -> CatalogVersion:
        """Publish a **new** version carrying an older version's content. History is never
        rewritten and the intervening versions remain readable forever."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            source = self._repos.versions.version(command.org_id, command.to_version)
            if source is None:
                raise _NotFound(f"catalog version {command.to_version} not found")
            snapshot = self._factory.rollback_catalog(
                source,
                version=self._repos.versions.next_version(command.org_id),
                notes=command.notes,
                actor_id=command.actor_id,
            )
            stored = self._repos.versions.publish(snapshot)
            self._record(
                command,
                CatalogAction.CATALOG_ROLLED_BACK,
                "catalog",
                f"v{stored.version}",
                catalog_version=stored.version,
                detail=f"content restored from v{source.version}",
            )
            uow.stage(
                self._event(
                    command,
                    stored.id,
                    version=stored.version,
                    rolled_back_from=source.version,
                )
            )
            return stored

    # ── internals ───────────────────────────────────────────────────────────────

    def _transition(
        self,
        command,
        code: str,
        trigger: CatalogTrigger,
        action: CatalogAction,
        *,
        replaced_by: str = "",
    ) -> Product:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            product = self._product(command.org_id, code)
            # Legality is the Foundation's FSM decision, not this method's.
            moved = self._lifecycle.apply_product(
                product, trigger, at=self._clock.now(), replaced_by=replaced_by
            )
            stored = self._repos.products.replace(moved)
            self._record(command, action, "product", stored.code)
            uow.stage(self._event(command, stored.id, code=stored.code, state=stored.state.value))
            return stored

    def _product(self, org_id: str, code: str) -> Product:
        product = self._repos.products.get(org_id, code)
        if product is None:
            raise _NotFound(f"product {code!r} not found")
        return product

    def _edition_by_code(self, org_id: str, code: str) -> Edition | None:
        return next((e for e in self._repos.editions.all(org_id) if e.code == code), None)

    def _record(
        self,
        command,
        action: CatalogAction,
        subject_kind: str,
        subject_code: str,
        *,
        catalog_version: int | None = None,
        detail: str = "",
    ) -> None:
        """Append catalog history **inside the caller's transaction**, so the history and the
        change it describes commit or vanish together."""
        self._repos.activity.append(
            self._factory.activity(
                org_id=command.org_id,
                action=action,
                subject_kind=subject_kind,
                subject_code=subject_code,
                actor_id=command.actor_id,
                catalog_version=catalog_version,
                detail=detail,
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.CATALOG_PUBLISHED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


class CatalogQueryUseCases:
    """Read-only. No transaction, no audit, no mutation."""

    def __init__(
        self, *, context, catalog_repos: CatalogRepositories, search, resolver, visibility, authz
    ) -> None:
        self._ctx = context
        self._repos = catalog_repos
        self._search = search
        self._resolver = resolver
        self._visibility = visibility
        self._authz = authz
        from billing.foundation import CatalogLifecycleService

        self._lifecycle = CatalogLifecycleService()

    def get_product(self, query: GetProduct) -> ProductView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        product = self._repos.products.get(query.org_id, query.code)
        if product is None:
            return None
        return product_view(product, self._lifecycle.allowed(product.state))

    def list_products(self, query: ListProducts) -> tuple[ProductView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        products = self._repos.products.all(query.org_id)
        if not query.include_internal:
            products = tuple(p for p in products if p.visibility is not Visibility.INTERNAL)
        return tuple(
            product_view(p, self._lifecycle.allowed(p.state))
            for p in sorted(products, key=lambda p: p.code)
        )

    def search(self, query: SearchCatalog) -> CatalogSearchView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        result = self._search.search(
            query.query,
            products=self._repos.products.all(query.org_id),
            editions=self._repos.editions.all(query.org_id),
            bundles=self._repos.bundles.all(query.org_id),
            plans=self._ctx.repositories.plans.published(org_id=query.org_id),
            features=self._ctx.repositories.features.all(),
            feature_groups=self._repos.feature_groups.all(query.org_id),
        )
        return CatalogSearchView(
            products=tuple(product_view(p) for p in result.products),
            editions=tuple(edition_view(e) for e in result.editions),
            bundles=tuple(bundle_view(b) for b in result.bundles),
            plans=tuple(_plan_dict(p) for p in result.plans),
            features=tuple(_feature_dict(f) for f in result.features),
            feature_groups=tuple(feature_group_view(g) for g in result.feature_groups),
            total=result.total,
        )

    def get_plan(self, query: GetCatalogPlan) -> dict | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        version = query.version if query.version is not None else 1
        plan = self._ctx.repositories.plans.get(query.code, version, org_id=query.org_id)
        return _plan_dict(plan) if plan else None

    def list_plans(self, query: ListCatalogPlans) -> tuple[dict, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        plans = self._ctx.repositories.plans.published(org_id=query.org_id)
        if query.edition_code:
            edition = next(
                (e for e in self._repos.editions.all(query.org_id) if e.code == query.edition_code),
                None,
            )
            allowed = frozenset(edition.plan_codes) if edition else frozenset()
            plans = tuple(p for p in plans if p.code in allowed)
        return tuple(_plan_dict(p) for p in plans)

    def list_editions(self, query: ListEditions) -> tuple[EditionView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        editions = (
            self._repos.editions.for_product(query.org_id, query.product_code)
            if query.product_code
            else self._repos.editions.all(query.org_id)
        )
        return tuple(edition_view(e) for e in editions)

    def list_bundles(self, query: ListBundles) -> tuple[BundleView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        bundles = self._repos.bundles.all(query.org_id)
        return tuple(bundle_view(b, self._resolver.expand(b, bundles)) for b in bundles)

    def list_feature_groups(self, query: ListFeatureGroups) -> tuple[FeatureGroupView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(feature_group_view(g) for g in self._repos.feature_groups.all(query.org_id))

    def get_version(self, query: GetCatalogVersion) -> CatalogVersionView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        snapshot = (
            self._repos.versions.version(query.org_id, query.version)
            if query.version is not None
            else self._repos.versions.latest(query.org_id)
        )
        return catalog_version_view(snapshot) if snapshot else None

    def version_history(self, query: CatalogVersionHistory) -> tuple[CatalogVersionView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            catalog_version_view(s) for s in self._repos.versions.history(query.org_id, query.limit)
        )

    def activity_history(self, query: CatalogActivityHistory) -> tuple[CatalogActivityView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        entries = (
            self._repos.activity.for_subject(query.org_id, query.subject_code)
            if query.subject_code
            else self._repos.activity.history(query.org_id, query.limit)
        )
        return tuple(activity_view(a) for a in entries)


# ── wiring table ────────────────────────────────────────────────────────────────


def register_catalog_command_handlers(bus, uc: CatalogCommandUseCases) -> None:
    bus.register(CreateProduct, uc.create_product)
    bus.register(PublishProduct, uc.publish_product)
    bus.register(DeprecateProduct, uc.deprecate_product)
    bus.register(ArchiveProduct, uc.archive_product)
    bus.register(RestoreProduct, uc.restore_product)
    bus.register(DuplicateProduct, uc.duplicate_product)
    bus.register(CreateCatalogPlan, uc.create_plan)
    bus.register(PublishCatalogPlan, uc.publish_plan)
    bus.register(ArchiveCatalogPlan, uc.archive_plan)
    bus.register(CreateEdition, uc.create_edition)
    bus.register(CreateBundle, uc.create_bundle)
    bus.register(CreateFeature, uc.create_feature)
    bus.register(CreateFeatureGroup, uc.create_feature_group)
    bus.register(PublishCatalog, uc.publish_catalog)
    bus.register(RollbackCatalog, uc.rollback_catalog)


def register_catalog_query_handlers(bus, uc: CatalogQueryUseCases) -> None:
    bus.register(GetProduct, uc.get_product)
    bus.register(ListProducts, uc.list_products)
    bus.register(SearchCatalog, uc.search)
    bus.register(GetCatalogPlan, uc.get_plan)
    bus.register(ListCatalogPlans, uc.list_plans)
    bus.register(ListEditions, uc.list_editions)
    bus.register(ListBundles, uc.list_bundles)
    bus.register(ListFeatureGroups, uc.list_feature_groups)
    bus.register(GetCatalogVersion, uc.get_version)
    bus.register(CatalogVersionHistory, uc.version_history)
    bus.register(CatalogActivityHistory, uc.activity_history)

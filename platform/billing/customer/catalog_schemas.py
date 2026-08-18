"""Commercial Catalog wire DTOs (M9.6) — Pydantic v2, additive to the M9.5 schemas.

Same discipline as M9.5: a third representation, distinct from Foundation aggregates and
Application views, with `to_*()` inbound and `from_*()` outbound. Foundation enums are reused
so validation and OpenAPI enums come from the domain vocabulary.

**Not one field here carries money.** A catalog says what a customer gets; a price book
(M9.7) says what they pay. The absence is asserted by a test, because "we simply did not add
a price field" is a property that decays the moment nobody is checking.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    CatalogSearchQuery,
    CatalogState,
    CommercialMetadata,
    FeatureKind,
    ProductKind,
    TargetAudience,
    Visibility,
)


class MetadataBody(BaseModel):
    headline: str = ""
    description: str = ""
    documentation_url: str = ""
    marketing_url: str = ""
    icon: str = ""
    tags: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)

    def to_metadata(self) -> CommercialMetadata:
        return CommercialMetadata(
            headline=self.headline,
            description=self.description,
            documentation_url=self.documentation_url,
            marketing_url=self.marketing_url,
            icon=self.icon,
            tags=tuple(self.tags),
            highlights=tuple(self.highlights),
        )


class MetadataResponse(BaseModel):
    headline: str
    description: str
    documentation_url: str
    marketing_url: str
    icon: str
    tags: list[str]
    highlights: list[str]

    @classmethod
    def from_view(cls, view) -> MetadataResponse:
        return cls(
            headline=view.headline,
            description=view.description,
            documentation_url=view.documentation_url,
            marketing_url=view.marketing_url,
            icon=view.icon,
            tags=list(view.tags),
            highlights=list(view.highlights),
        )


class CreateProductBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "voice_ai",
                "display_name": "Voice AI",
                "kind": "platform",
                "audience": "self_serve",
            }
        }
    )
    code: str = Field(description="Stable, URL-safe commercial code — appears in contracts")
    display_name: str
    kind: ProductKind = ProductKind.PLATFORM
    visibility: Visibility = Visibility.PUBLIC
    audience: TargetAudience = TargetAudience.SELF_SERVE
    feature_codes: list[str] = Field(default_factory=list)
    edition_codes: list[str] = Field(default_factory=list)
    metadata: MetadataBody = Field(default_factory=MetadataBody)


class DeprecateProductBody(BaseModel):
    replaced_by_code: str = Field(
        default="", description="What existing customers should migrate to"
    )


class DuplicateProductBody(BaseModel):
    code: str
    display_name: str = ""


class ProductResponse(BaseModel):
    id: str
    code: str
    display_name: str
    kind: str
    state: str
    visibility: str
    audience: str
    edition_codes: list[str]
    feature_codes: list[str]
    metadata: MetadataResponse
    is_sellable: bool
    allowed_triggers: list[str] = Field(
        description="Lifecycle moves legal from the current state — a UI can render only "
        "buttons that cannot produce a 409"
    )
    replaced_by_code: str

    @classmethod
    def from_view(cls, view) -> ProductResponse:
        return cls(
            id=view.id,
            code=view.code,
            display_name=view.display_name,
            kind=view.kind,
            state=view.state,
            visibility=view.visibility,
            audience=view.audience,
            edition_codes=list(view.edition_codes),
            feature_codes=list(view.feature_codes),
            metadata=MetadataResponse.from_view(view.metadata),
            is_sellable=view.is_sellable,
            allowed_triggers=list(view.allowed_triggers),
            replaced_by_code=view.replaced_by_code,
        )

    @classmethod
    def from_aggregate(cls, product, allowed=()) -> ProductResponse:
        return cls(
            id=product.id,
            code=product.code,
            display_name=product.display_name,
            kind=product.kind.value,
            state=product.state.value,
            visibility=product.visibility.value,
            audience=product.audience.value,
            edition_codes=list(product.edition_codes),
            feature_codes=list(product.feature_codes),
            metadata=MetadataResponse(
                headline=product.metadata.headline,
                description=product.metadata.description,
                documentation_url=product.metadata.documentation_url,
                marketing_url=product.metadata.marketing_url,
                icon=product.metadata.icon,
                tags=list(product.metadata.tags),
                highlights=list(product.metadata.highlights),
            ),
            is_sellable=product.is_sellable,
            allowed_triggers=[t.value for t in allowed],
            replaced_by_code=product.replaced_by_code,
        )


class CreateEditionBody(BaseModel):
    code: str
    display_name: str
    product_code: str
    plan_codes: list[str] = Field(default_factory=list)
    feature_codes: list[str] = Field(default_factory=list)
    rank: int = Field(default=0, description="Commercial ordering. Not a price.")
    visibility: Visibility = Visibility.PUBLIC
    metadata: MetadataBody = Field(default_factory=MetadataBody)


class EditionResponse(BaseModel):
    id: str
    code: str
    display_name: str
    product_code: str
    state: str
    visibility: str
    plan_codes: list[str]
    feature_codes: list[str]
    rank: int

    @classmethod
    def from_view(cls, view) -> EditionResponse:
        return cls(
            id=view.id,
            code=view.code,
            display_name=view.display_name,
            product_code=view.product_code,
            state=view.state,
            visibility=view.visibility,
            plan_codes=list(view.plan_codes),
            feature_codes=list(view.feature_codes),
            rank=view.rank,
        )

    @classmethod
    def from_aggregate(cls, edition) -> EditionResponse:
        return cls(
            id=edition.id,
            code=edition.code,
            display_name=edition.display_name,
            product_code=edition.product_code,
            state=edition.state.value,
            visibility=edition.visibility.value,
            plan_codes=list(edition.plan_codes),
            feature_codes=list(edition.feature_codes),
            rank=edition.rank,
        )


class CreateBundleBody(BaseModel):
    code: str
    display_name: str
    product_codes: list[str] = Field(default_factory=list)
    bundle_codes: list[str] = Field(
        default_factory=list, description="Nested bundles. Cycles are rejected (422)."
    )
    feature_codes: list[str] = Field(default_factory=list)
    visibility: Visibility = Visibility.PUBLIC
    audience: TargetAudience = TargetAudience.SELF_SERVE
    metadata: MetadataBody = Field(default_factory=MetadataBody)


class BundleResponse(BaseModel):
    id: str
    code: str
    display_name: str
    state: str
    visibility: str
    audience: str
    product_codes: list[str]
    bundle_codes: list[str]
    feature_codes: list[str]
    expanded_product_codes: list[str] = Field(
        default_factory=list, description="Every product reachable through nesting"
    )

    @classmethod
    def from_view(cls, view) -> BundleResponse:
        return cls(
            id=view.id,
            code=view.code,
            display_name=view.display_name,
            state=view.state,
            visibility=view.visibility,
            audience=view.audience,
            product_codes=list(view.product_codes),
            bundle_codes=list(view.bundle_codes),
            feature_codes=list(view.feature_codes),
            expanded_product_codes=list(view.expanded_product_codes),
        )

    @classmethod
    def from_aggregate(cls, bundle) -> BundleResponse:
        return cls(
            id=bundle.id,
            code=bundle.code,
            display_name=bundle.display_name,
            state=bundle.state.value,
            visibility=bundle.visibility.value,
            audience=bundle.audience.value,
            product_codes=list(bundle.product_codes),
            bundle_codes=list(bundle.bundle_codes),
            feature_codes=list(bundle.feature_codes),
        )


class CreateFeatureBody(BaseModel):
    code: str
    display_name: str
    kind: FeatureKind = FeatureKind.FLAG
    meter_code: str = ""
    description: str = ""


class CreateFeatureGroupBody(BaseModel):
    code: str
    display_name: str
    feature_codes: list[str] = Field(default_factory=list)
    description: str = ""
    rank: int = 0


class FeatureGroupResponse(BaseModel):
    id: str
    code: str
    display_name: str
    feature_codes: list[str]
    description: str
    rank: int

    @classmethod
    def from_view(cls, view) -> FeatureGroupResponse:
        return cls(
            id=view.id,
            code=view.code,
            display_name=view.display_name,
            feature_codes=list(view.feature_codes),
            description=view.description,
            rank=view.rank,
        )

    @classmethod
    def from_aggregate(cls, group) -> FeatureGroupResponse:
        return cls(
            id=group.id,
            code=group.code,
            display_name=group.display_name,
            feature_codes=list(group.feature_codes),
            description=group.description,
            rank=group.rank,
        )


class CreateCatalogPlanBody(BaseModel):
    code: str
    display_name: str
    tier: str = "free"
    edition_code: str = ""
    feature_codes: list[str] = Field(default_factory=list)
    metered_codes: list[str] = Field(default_factory=list)
    description: str = ""


class PublishCatalogBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"notes": "Q3 packaging: adds the Enterprise edition"}}
    )
    notes: str = Field(default="", description="Why this version exists — kept forever")


class RollbackCatalogBody(BaseModel):
    to_version: int = Field(
        description="The version whose content to restore. A **new** version is published; "
        "history is never rewritten."
    )
    notes: str = ""


class CatalogVersionResponse(BaseModel):
    id: str
    version: int
    published_at: float
    published_by: str
    notes: str
    rolled_back_from: int | None
    entity_count: int
    product_codes: list[str]
    plan_codes: list[str]
    bundle_codes: list[str]

    @classmethod
    def from_view(cls, view) -> CatalogVersionResponse:
        return cls(
            id=view.id,
            version=view.version,
            published_at=view.published_at,
            published_by=view.published_by,
            notes=view.notes,
            rolled_back_from=view.rolled_back_from,
            entity_count=view.entity_count,
            product_codes=list(view.product_codes),
            plan_codes=list(view.plan_codes),
            bundle_codes=list(view.bundle_codes),
        )

    @classmethod
    def from_aggregate(cls, snapshot) -> CatalogVersionResponse:
        return cls(
            id=snapshot.id,
            version=snapshot.version,
            published_at=snapshot.published_at,
            published_by=snapshot.published_by,
            notes=snapshot.notes,
            rolled_back_from=snapshot.rolled_back_from,
            entity_count=snapshot.entity_count,
            product_codes=[p.code for p in snapshot.products],
            plan_codes=[f"{p.code}@{p.version}" for p in snapshot.plans],
            bundle_codes=[b.code for b in snapshot.bundles],
        )


class CatalogActivityResponse(BaseModel):
    id: str
    action: str
    subject_kind: str
    subject_code: str
    actor_id: str
    at: float
    catalog_version: int | None
    detail: str

    @classmethod
    def from_view(cls, view) -> CatalogActivityResponse:
        return cls(
            id=view.id,
            action=view.action,
            subject_kind=view.subject_kind,
            subject_code=view.subject_code,
            actor_id=view.actor_id,
            at=view.at,
            catalog_version=view.catalog_version,
            detail=view.detail,
        )


class CatalogSearchResponse(BaseModel):
    products: list[ProductResponse]
    editions: list[EditionResponse]
    bundles: list[BundleResponse]
    plans: list[dict[str, Any]]
    features: list[dict[str, Any]]
    feature_groups: list[FeatureGroupResponse]
    total: int

    @classmethod
    def from_view(cls, view) -> CatalogSearchResponse:
        return cls(
            products=[ProductResponse.from_view(p) for p in view.products],
            editions=[EditionResponse.from_view(e) for e in view.editions],
            bundles=[BundleResponse.from_view(b) for b in view.bundles],
            plans=list(view.plans),
            features=list(view.features),
            feature_groups=[FeatureGroupResponse.from_view(g) for g in view.feature_groups],
            total=view.total,
        )


def build_search_query(
    *,
    text: str = "",
    kinds: list[ProductKind] | None = None,
    states: list[CatalogState] | None = None,
    visibilities: list[Visibility] | None = None,
    audiences: list[TargetAudience] | None = None,
    tags: list[str] | None = None,
    feature_code: str = "",
    product_code: str = "",
    limit: int = 100,
    offset: int = 0,
) -> CatalogSearchQuery:
    """Query-string → Foundation search query. Field mapping only; the filtering itself is
    the Foundation's `CatalogSearchService`, so the API cannot drift into its own semantics."""
    return CatalogSearchQuery(
        text=text,
        kinds=tuple(kinds or ()),
        states=tuple(states or ()),
        visibilities=tuple(visibilities or ()),
        audiences=tuple(audiences or ()),
        tags=tuple(tags or ()),
        feature_code=feature_code,
        product_code=product_code,
        limit=limit,
        offset=offset,
    )

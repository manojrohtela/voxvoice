"""Commercial Catalog routes (M9.6) — transport only.

Additive to the M9.5 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No catalog rule is
evaluated here — duplicate detection, bundle-cycle detection, lifecycle legality, snapshot
assembly, and search filtering are all Foundation behaviour reached through the Application.

**There is no price endpoint in this file and no money field in its schemas.** The catalog
describes what is sold; M9.7 will describe what it costs, on its own surface.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    ArchiveCatalogPlan,
    ArchiveProduct,
    BillingApplication,
    CatalogActivityHistory,
    CatalogVersionHistory,
    CreateBundle,
    CreateCatalogPlan,
    CreateEdition,
    CreateFeature,
    CreateFeatureGroup,
    CreateProduct,
    DeprecateProduct,
    DuplicateProduct,
    GetCatalogPlan,
    GetCatalogVersion,
    GetProduct,
    ListBundles,
    ListEditions,
    ListFeatureGroups,
    ListProducts,
    PublishCatalog,
    PublishCatalogPlan,
    PublishProduct,
    RestoreProduct,
    RollbackCatalog,
    SearchCatalog,
)
from billing.foundation import (
    BillingNotFound,
    CatalogState,
    ProductKind,
    TargetAudience,
    Visibility,
)

from ..auth import require_org
from ..catalog_schemas import (
    BundleResponse,
    CatalogActivityResponse,
    CatalogSearchResponse,
    CatalogVersionResponse,
    CreateBundleBody,
    CreateCatalogPlanBody,
    CreateEditionBody,
    CreateFeatureBody,
    CreateFeatureGroupBody,
    CreateProductBody,
    DeprecateProductBody,
    DuplicateProductBody,
    EditionResponse,
    FeatureGroupResponse,
    ProductResponse,
    PublishCatalogBody,
    RollbackCatalogBody,
    build_search_query,
)
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/catalog", tags=["catalog"], responses=ERROR_RESPONSES)


def _product_response(app: BillingApplication, ctx: BillingRequestContext, code: str):
    """Re-read the product through the query surface after a command.

    A command returns the aggregate, but the response also carries `allowed_triggers` —
    which lifecycle moves are legal next — and that is a Foundation decision. Reading it
    back through `GetProduct` keeps the computation below this layer instead of importing
    the state machine into a controller."""
    view = app.queries.dispatch(
        GetProduct(actor_id=ctx.actor(), org_id=ctx.organization(), code=code)
    )
    if view is None:  # pragma: no cover — a just-written product is always readable
        raise BillingNotFound(f"product {code!r} not found")
    return ProductResponse.from_view(view)


# ── products ────────────────────────────────────────────────────────────────────


@router.post(
    "/products", response_model=ProductResponse, status_code=201, summary="Create a product"
)
def create_product(
    body: CreateProductBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProductResponse:
    """Everything starts as a draft — a product nobody has been sold cannot be published by
    accident."""
    app.commands.dispatch(
        CreateProduct(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            kind=body.kind,
            visibility=body.visibility,
            audience=body.audience,
            feature_codes=tuple(body.feature_codes),
            edition_codes=tuple(body.edition_codes),
            metadata=body.metadata.to_metadata(),
        )
    )
    return _product_response(app, ctx, body.code)


@router.get("/products", response_model=list[ProductResponse], summary="List products")
def list_products(
    include_internal: bool = Query(default=False),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ProductResponse]:
    views = app.queries.dispatch(
        ListProducts(
            actor_id=ctx.actor(), org_id=ctx.organization(), include_internal=include_internal
        )
    )
    return [ProductResponse.from_view(v) for v in views]


@router.get("/products/{code}", response_model=ProductResponse, summary="Get a product")
def get_product(
    code: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProductResponse:
    view = app.queries.dispatch(
        GetProduct(actor_id=ctx.actor(), org_id=ctx.organization(), code=code)
    )
    if view is None:
        raise BillingNotFound(f"product {code!r} not found")
    return ProductResponse.from_view(view)


@router.post(
    "/products/{code}/publish", response_model=ProductResponse, summary="Publish a product"
)
def publish_product(
    code: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProductResponse:
    app.commands.dispatch(
        PublishProduct(actor_id=ctx.actor(), org_id=ctx.organization(), code=code)
    )
    return _product_response(app, ctx, code)


@router.post(
    "/products/{code}/deprecate",
    response_model=ProductResponse,
    summary="Deprecate a product (existing customers keep it)",
)
def deprecate_product(
    code: str,
    body: DeprecateProductBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProductResponse:
    app.commands.dispatch(
        DeprecateProduct(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=code,
            replaced_by_code=body.replaced_by_code,
        )
    )
    return _product_response(app, ctx, code)


@router.post(
    "/products/{code}/archive", response_model=ProductResponse, summary="Archive a product"
)
def archive_product(
    code: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProductResponse:
    app.commands.dispatch(
        ArchiveProduct(actor_id=ctx.actor(), org_id=ctx.organization(), code=code)
    )
    return _product_response(app, ctx, code)


@router.post(
    "/products/{code}/restore", response_model=ProductResponse, summary="Restore a product"
)
def restore_product(
    code: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProductResponse:
    app.commands.dispatch(
        RestoreProduct(actor_id=ctx.actor(), org_id=ctx.organization(), code=code)
    )
    return _product_response(app, ctx, code)


@router.post(
    "/products/{code}/duplicate",
    response_model=ProductResponse,
    status_code=201,
    summary="Duplicate a product into a new draft",
)
def duplicate_product(
    code: str,
    body: DuplicateProductBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ProductResponse:
    app.commands.dispatch(
        DuplicateProduct(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            source_code=code,
            code=body.code,
            display_name=body.display_name,
        )
    )
    return _product_response(app, ctx, body.code)


# ── editions, bundles, features ─────────────────────────────────────────────────


@router.post(
    "/editions", response_model=EditionResponse, status_code=201, summary="Create an edition"
)
def create_edition(
    body: CreateEditionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> EditionResponse:
    edition = app.commands.dispatch(
        CreateEdition(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            product_code=body.product_code,
            plan_codes=tuple(body.plan_codes),
            feature_codes=tuple(body.feature_codes),
            rank=body.rank,
            visibility=body.visibility,
            metadata=body.metadata.to_metadata(),
        )
    )
    return EditionResponse.from_aggregate(edition)


@router.get("/editions", response_model=list[EditionResponse], summary="List editions")
def list_editions(
    product_code: str = Query(default=""),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[EditionResponse]:
    views = app.queries.dispatch(
        ListEditions(actor_id=ctx.actor(), org_id=ctx.organization(), product_code=product_code)
    )
    return [EditionResponse.from_view(v) for v in views]


@router.post("/bundles", response_model=BundleResponse, status_code=201, summary="Create a bundle")
def create_bundle(
    body: CreateBundleBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> BundleResponse:
    """A nesting cycle is rejected with 422 and the offending path — validation names the
    edge to cut rather than reporting that something is wrong."""
    bundle = app.commands.dispatch(
        CreateBundle(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            product_codes=tuple(body.product_codes),
            bundle_codes=tuple(body.bundle_codes),
            feature_codes=tuple(body.feature_codes),
            visibility=body.visibility,
            audience=body.audience,
            metadata=body.metadata.to_metadata(),
        )
    )
    return BundleResponse.from_aggregate(bundle)


@router.get("/bundles", response_model=list[BundleResponse], summary="List bundles (expanded)")
def list_bundles(
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[BundleResponse]:
    views = app.queries.dispatch(ListBundles(actor_id=ctx.actor(), org_id=ctx.organization()))
    return [BundleResponse.from_view(v) for v in views]


@router.post("/features", status_code=201, summary="Create a feature")
def create_feature(
    body: CreateFeatureBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> dict:
    feature = app.commands.dispatch(
        CreateFeature(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            kind=body.kind,
            meter_code=body.meter_code,
            description=body.description,
        )
    )
    return {
        "id": feature.id,
        "code": feature.code,
        "display_name": feature.display_name,
        "kind": feature.kind.value,
        "meter_code": feature.meter_code,
    }


@router.post(
    "/feature-groups",
    response_model=FeatureGroupResponse,
    status_code=201,
    summary="Create a feature group",
)
def create_feature_group(
    body: CreateFeatureGroupBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> FeatureGroupResponse:
    group = app.commands.dispatch(
        CreateFeatureGroup(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            feature_codes=tuple(body.feature_codes),
            description=body.description,
            rank=body.rank,
        )
    )
    return FeatureGroupResponse.from_aggregate(group)


@router.get(
    "/feature-groups", response_model=list[FeatureGroupResponse], summary="List feature groups"
)
def list_feature_groups(
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[FeatureGroupResponse]:
    views = app.queries.dispatch(ListFeatureGroups(actor_id=ctx.actor(), org_id=ctx.organization()))
    return [FeatureGroupResponse.from_view(v) for v in views]


# ── plans (catalog-side) ────────────────────────────────────────────────────────
#
# `POST /catalog/plans` and `GET /catalog/plans` already exist from M9.5 and are **reused
# unchanged** — adding a second endpoint that defines a plan would have given the surface two
# ways to do one thing. What lands here is what M9.5 could not express: associating a plan
# with an edition at creation, a standalone publish (M9.5 had to fold publishing into
# creation because no query could re-read a plan; `GetCatalogPlan` now can — closing that
# documented limitation), and archival.


@router.post(
    "/editions/{edition_code}/plans",
    status_code=201,
    summary="Create a plan and attach it to an edition",
)
def create_edition_plan(
    edition_code: str,
    body: CreateCatalogPlanBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> dict:
    """A plan says what a customer *gets*. What they pay is a price book (M9.7), published
    and versioned separately so repricing never requires republishing the product."""
    plan = app.commands.dispatch(
        CreateCatalogPlan(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            tier=body.tier,
            edition_code=edition_code,
            feature_codes=tuple(body.feature_codes),
            metered_codes=tuple(body.metered_codes),
            description=body.description,
        )
    )
    return {"code": plan.code, "version": plan.version, "tier": plan.tier.value}


@router.get("/plans/{code}", summary="Get a catalog plan version")
def get_plan(
    code: str,
    version: int = Query(default=1, ge=1),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> dict:
    plan = app.queries.dispatch(
        GetCatalogPlan(actor_id=ctx.actor(), org_id=ctx.organization(), code=code, version=version)
    )
    if plan is None:
        raise BillingNotFound(f"plan {code!r} v{version} not found")
    return plan


@router.post("/plans/{code}/versions/{version}/publish", summary="Publish a plan version")
def publish_plan(
    code: str,
    version: int,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> dict:
    plan = app.commands.dispatch(
        PublishCatalogPlan(
            actor_id=ctx.actor(), org_id=ctx.organization(), code=code, version=version
        )
    )
    return {"code": plan.code, "version": plan.version, "published": plan.published}


@router.post(
    "/plans/{code}/versions/{version}/archive",
    summary="Archive a plan version (existing subscriptions are untouched)",
)
def archive_plan(
    code: str,
    version: int,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> dict:
    """A plan leaving the shelf is not a plan leaving the customers who hold it — the
    Subscription domain owns that entirely, and this endpoint touches none of it."""
    plan = app.commands.dispatch(
        ArchiveCatalogPlan(
            actor_id=ctx.actor(), org_id=ctx.organization(), code=code, version=version
        )
    )
    return {"code": plan.code, "version": plan.version, "deprecated": plan.deprecated}


# ── search ──────────────────────────────────────────────────────────────────────


@router.get("/search", response_model=CatalogSearchResponse, summary="Search the catalog")
def search_catalog(
    text: str = Query(default="", description="Case-insensitive substring; no relevance ranking"),
    kinds: list[ProductKind] = Query(default=[]),
    states: list[CatalogState] = Query(default=[]),
    visibilities: list[Visibility] = Query(default=[]),
    audiences: list[TargetAudience] = Query(default=[]),
    tags: list[str] = Query(default=[]),
    feature_code: str = Query(default=""),
    product_code: str = Query(default=""),
    limit: int = Query(default=100, ge=0, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CatalogSearchResponse:
    """Deterministic filtering: the same catalog and the same query always return the same
    rows in the same order. No full-text engine, no scoring."""
    view = app.queries.dispatch(
        SearchCatalog(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            query=build_search_query(
                text=text,
                kinds=kinds,
                states=states,
                visibilities=visibilities,
                audiences=audiences,
                tags=tags,
                feature_code=feature_code,
                product_code=product_code,
                limit=limit,
                offset=offset,
            ),
        )
    )
    return CatalogSearchResponse.from_view(view)


# ── versions + history ──────────────────────────────────────────────────────────


@router.post(
    "/versions",
    response_model=CatalogVersionResponse,
    status_code=201,
    summary="Publish an immutable catalog version",
)
def publish_catalog(
    body: PublishCatalogBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CatalogVersionResponse:
    """Freezes the whole catalog. The version is immutable and readable forever; there is no
    PUT and no DELETE on this resource."""
    snapshot = app.commands.dispatch(
        PublishCatalog(actor_id=ctx.actor(), org_id=ctx.organization(), notes=body.notes)
    )
    return CatalogVersionResponse.from_aggregate(snapshot)


@router.post(
    "/versions/rollback",
    response_model=CatalogVersionResponse,
    status_code=201,
    summary="Roll back by publishing a new version with older content",
)
def rollback_catalog(
    body: RollbackCatalogBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CatalogVersionResponse:
    snapshot = app.commands.dispatch(
        RollbackCatalog(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            to_version=body.to_version,
            notes=body.notes,
        )
    )
    return CatalogVersionResponse.from_aggregate(snapshot)


@router.get(
    "/versions", response_model=list[CatalogVersionResponse], summary="Catalog version history"
)
def version_history(
    limit: int = Query(default=50, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CatalogVersionResponse]:
    views = app.queries.dispatch(
        CatalogVersionHistory(actor_id=ctx.actor(), org_id=ctx.organization(), limit=limit)
    )
    return [CatalogVersionResponse.from_view(v) for v in views]


@router.get(
    "/versions/latest",
    response_model=CatalogVersionResponse,
    summary="The current published catalog version",
)
def latest_version(
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CatalogVersionResponse:
    view = app.queries.dispatch(GetCatalogVersion(actor_id=ctx.actor(), org_id=ctx.organization()))
    if view is None:
        raise BillingNotFound("no catalog version has been published yet")
    return CatalogVersionResponse.from_view(view)


@router.get(
    "/versions/{version}",
    response_model=CatalogVersionResponse,
    summary="A historical catalog version (readable forever)",
)
def get_version(
    version: int,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CatalogVersionResponse:
    view = app.queries.dispatch(
        GetCatalogVersion(actor_id=ctx.actor(), org_id=ctx.organization(), version=version)
    )
    if view is None:
        raise BillingNotFound(f"catalog version {version} not found")
    return CatalogVersionResponse.from_view(view)


@router.get(
    "/activity", response_model=list[CatalogActivityResponse], summary="Catalog activity history"
)
def activity_history(
    subject_code: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CatalogActivityResponse]:
    views = app.queries.dispatch(
        CatalogActivityHistory(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subject_code=subject_code,
            limit=limit,
        )
    )
    return [CatalogActivityResponse.from_view(v) for v in views]

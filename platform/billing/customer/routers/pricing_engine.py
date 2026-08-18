"""Pricing Engine routes (M9.7) — transport only.

Additive to the M9.5/M9.6 transport layer and identical in discipline: parse, take identity
from the verified token, construct a command or query, dispatch, serialize. No pricing rule is
evaluated here — price validity, effective-date collision, discount eligibility and ordering,
book selection, and lifecycle legality are all Foundation behaviour reached through the
Application.

**No controller in this file performs arithmetic.** A controller that multiplied a quantity by
a unit amount would have made rating a transport concern, which is precisely the drift
ADR-0023 forbids — and a test asserts the absence rather than trusting it.

The existing M9.5 `/pricing/price-books` routes are untouched and not duplicated. What lands
here is the Pricing Engine proper, under `/pricing/books`, `/pricing/discounts`,
`/pricing/promotions`, `/pricing/coupons`, `/pricing/resolve`, and `/pricing/versions`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    ArchivePriceBook,
    BillingApplication,
    CreateCoupon,
    CreateDiscountPolicy,
    CreatePrice,
    CreatePriceBook,
    CreatePromotion,
    DuplicatePriceBook,
    GetPricingBook,
    GetPricingVersion,
    ListCoupons,
    ListDiscountPolicies,
    ListPromotions,
    PriceVersionHistory,
    PricingHistory,
    PricingVersionHistory,
    PublishPricing,
    PublishPricingBook,
    ResolvePrice,
    RestorePriceBook,
    RollbackPricing,
    SearchPriceBooks,
    UpdateDraftPrice,
)
from billing.foundation import (
    BillingNotFound,
    CommercialChannel,
    Currency,
    CustomerTier,
    PriceBookAudience,
    PricingState,
    Region,
)

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..pricing_schemas import (
    CouponResponse,
    CreateCouponBody,
    CreateDiscountBody,
    CreatePriceBody,
    CreatePriceBookBody,
    CreatePromotionBody,
    DiscountResponse,
    DuplicatePriceBookBody,
    PriceBookSearchResponse,
    PriceBookVersionResponse,
    PricingActivityResponse,
    PricingBookResponse,
    PricingVersionResponse,
    PromotionResponse,
    PublishPriceBookBody,
    PublishPricingBody,
    ResolvedPriceResponse,
    ResolvePriceBody,
    RollbackPricingBody,
    UpdateDraftPriceBody,
    build_price_book_query,
    to_money,
)
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/pricing", tags=["pricing"], responses=ERROR_RESPONSES)


def _book_response(app: BillingApplication, ctx: BillingRequestContext, code: str):
    """Re-read the book through the query surface after a command.

    A command returns the aggregate, but the response also carries `allowed_triggers` — which
    lifecycle moves are legal next — and that is a Foundation decision. Reading it back through
    `GetPricingBook` keeps the computation below this layer instead of importing the state
    machine into a controller."""
    view = app.queries.dispatch(
        GetPricingBook(actor_id=ctx.actor(), org_id=ctx.organization(), code=code)
    )
    if view is None:  # pragma: no cover — a just-written book is always readable
        raise BillingNotFound(f"price book {code!r} not found")
    return PricingBookResponse.from_view(view)


# ── price books ─────────────────────────────────────────────────────────────────


@router.post(
    "/books",
    response_model=PricingBookResponse,
    status_code=201,
    summary="Create a draft price book",
)
def create_book(
    body: CreatePriceBookBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PricingBookResponse:
    """Every book starts as a draft — a price nobody has been quoted cannot go live by
    accident."""
    app.commands.dispatch(
        CreatePriceBook(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            currency=body.currency,
            region=body.scope.region,
            channels=tuple(body.scope.channels),
            customer_tiers=tuple(body.scope.customer_tiers),
            audience=body.scope.audience,
            catalog_version=body.catalog_version,
            effective_from=body.effective_from,
            effective_until=body.effective_until,
            notes=body.notes,
        )
    )
    return _book_response(app, ctx, body.code)


@router.get(
    "/books",
    response_model=PriceBookSearchResponse,
    summary="Search price books (deterministic filtering)",
)
def search_books(
    text: str = Query(default="", description="Case-insensitive substring; no relevance ranking"),
    currencies: list[Currency] = Query(default=[]),
    regions: list[Region] = Query(default=[]),
    channels: list[CommercialChannel] = Query(default=[]),
    customer_tiers: list[CustomerTier] = Query(default=[]),
    states: list[PricingState] = Query(default=[]),
    audiences: list[PriceBookAudience] = Query(default=[]),
    price_code: str = Query(default=""),
    catalog_version: int | None = Query(default=None),
    active_at: float | None = Query(default=None, description="Only books live at this instant"),
    limit: int = Query(default=100, ge=0, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PriceBookSearchResponse:
    """The same books and the same query always return the same rows in the same order."""
    view = app.queries.dispatch(
        SearchPriceBooks(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            query=build_price_book_query(
                text=text,
                currencies=currencies,
                regions=regions,
                channels=channels,
                customer_tiers=customer_tiers,
                states=states,
                audiences=audiences,
                price_code=price_code,
                catalog_version=catalog_version,
                active_at=active_at,
                limit=limit,
                offset=offset,
            ),
        )
    )
    return PriceBookSearchResponse.from_view(view)


@router.get("/books/{code}", response_model=PricingBookResponse, summary="Get a price book")
def get_book(
    code: str,
    revision: int | None = Query(default=None, description="Omit for the working revision"),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PricingBookResponse:
    view = app.queries.dispatch(
        GetPricingBook(
            actor_id=ctx.actor(), org_id=ctx.organization(), code=code, revision=revision
        )
    )
    if view is None:
        raise BillingNotFound(f"price book {code!r} not found")
    return PricingBookResponse.from_view(view)


@router.post(
    "/books/{code}/publish",
    response_model=PriceBookVersionResponse,
    status_code=201,
    summary="Freeze a draft book into an immutable version",
)
def publish_book(
    code: str,
    body: PublishPriceBookBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PriceBookVersionResponse:
    """The version is immutable and readable forever; there is no PUT and no DELETE on it."""
    version = app.commands.dispatch(
        PublishPricingBook(
            actor_id=ctx.actor(), org_id=ctx.organization(), code=code, notes=body.notes
        )
    )
    return PriceBookVersionResponse.from_aggregate(version)


@router.post(
    "/books/{code}/archive", response_model=PricingBookResponse, summary="Archive a price book"
)
def archive_book(
    code: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PricingBookResponse:
    app.commands.dispatch(
        ArchivePriceBook(actor_id=ctx.actor(), org_id=ctx.organization(), code=code)
    )
    return _book_response(app, ctx, code)


@router.post(
    "/books/{code}/restore", response_model=PricingBookResponse, summary="Restore a price book"
)
def restore_book(
    code: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PricingBookResponse:
    app.commands.dispatch(
        RestorePriceBook(actor_id=ctx.actor(), org_id=ctx.organization(), code=code)
    )
    return _book_response(app, ctx, code)


@router.post(
    "/books/{code}/duplicate",
    response_model=PricingBookResponse,
    status_code=201,
    summary="Duplicate a book into a new draft revision",
)
def duplicate_book(
    code: str,
    body: DuplicatePriceBookBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PricingBookResponse:
    """The reprice flow: a published book is immutable, so a new price is a new revision."""
    copy = app.commands.dispatch(
        DuplicatePriceBook(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            source_code=code,
            code=body.code,
            display_name=body.display_name,
        )
    )
    return _book_response(app, ctx, copy.code)


@router.get(
    "/books/{code}/versions",
    response_model=list[PriceBookVersionResponse],
    summary="Price-book version history (readable forever)",
)
def book_version_history(
    code: str,
    limit: int = Query(default=50, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[PriceBookVersionResponse]:
    views = app.queries.dispatch(
        PriceVersionHistory(actor_id=ctx.actor(), org_id=ctx.organization(), code=code, limit=limit)
    )
    return [PriceBookVersionResponse.from_view(v) for v in views]


# ── prices ──────────────────────────────────────────────────────────────────────


@router.post(
    "/books/{code}/prices",
    response_model=PricingBookResponse,
    status_code=201,
    summary="Add a price to a draft book",
)
def create_price(
    code: str,
    body: CreatePriceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PricingBookResponse:
    """A price inherits the book's currency: a book is single-currency by construction, so a
    cross-currency line cannot be authored."""
    app.commands.dispatch(
        CreatePrice(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            book_code=code,
            code=body.code,
            shape=body.shape,
            version=body.version,
            amount=to_money(body.amount),
            unit_amount=to_money(body.unit_amount),
            interval=body.interval,
            meter_code=body.meter_code,
            included_units=body.included_units,
            package_size=body.package_size,
            tiers=tuple(t.to_tier() for t in body.tiers),
            credit_units_per_amount=body.credit_units_per_amount,
            rounding=body.rounding,
            effective_from=body.effective_from,
            effective_until=body.effective_until,
            description=body.description,
        )
    )
    return _book_response(app, ctx, code)


@router.patch(
    "/books/{code}/prices/{price_code}",
    response_model=PricingBookResponse,
    summary="Amend a price in a draft book",
)
def update_price(
    code: str,
    price_code: str,
    body: UpdateDraftPriceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PricingBookResponse:
    """Rejected with 409 on a published book — repricing duplicates it into a new revision."""
    app.commands.dispatch(
        UpdateDraftPrice(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            book_code=code,
            code=price_code,
            version=body.version,
            amount=to_money(body.amount),
            unit_amount=to_money(body.unit_amount),
            included_units=body.included_units,
            package_size=body.package_size,
            tiers=tuple(t.to_tier() for t in body.tiers) if body.tiers is not None else None,
            effective_from=body.effective_from,
            effective_until=body.effective_until,
            description=body.description,
        )
    )
    return _book_response(app, ctx, code)


# ── discounts, promotions, coupons ──────────────────────────────────────────────


@router.post(
    "/discounts", response_model=DiscountResponse, status_code=201, summary="Create a discount"
)
def create_discount(
    body: CreateDiscountBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> DiscountResponse:
    policy = app.commands.dispatch(
        CreateDiscountPolicy(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            kind=body.kind,
            percentage_bp=body.percentage_bp,
            amount=to_money(body.amount),
            priority=body.priority,
            stackable=body.stackable,
            price_codes=tuple(body.price_codes),
            bundle_codes=tuple(body.bundle_codes),
            min_quantity=body.min_quantity,
            min_commitment=to_money(body.min_commitment),
            region=body.scope.region,
            channels=tuple(body.scope.channels),
            customer_tiers=tuple(body.scope.customer_tiers),
            rounding=body.rounding,
            effective_from=body.effective_from,
            effective_until=body.effective_until,
            description=body.description,
        )
    )
    return DiscountResponse.from_aggregate(policy)


@router.get("/discounts", response_model=list[DiscountResponse], summary="List discount policies")
def list_discounts(
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[DiscountResponse]:
    views = app.queries.dispatch(
        ListDiscountPolicies(actor_id=ctx.actor(), org_id=ctx.organization())
    )
    return [DiscountResponse.from_view(v) for v in views]


@router.post(
    "/promotions", response_model=PromotionResponse, status_code=201, summary="Create a promotion"
)
def create_promotion(
    body: CreatePromotionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PromotionResponse:
    promotion = app.commands.dispatch(
        CreatePromotion(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            kind=body.kind,
            percentage_bp=body.percentage_bp,
            amount=to_money(body.amount),
            priority=body.priority,
            stackable=body.stackable,
            exclusive=body.exclusive,
            campaign=body.campaign,
            price_codes=tuple(body.price_codes),
            region=body.scope.region,
            channels=tuple(body.scope.channels),
            customer_tiers=tuple(body.scope.customer_tiers),
            effective_from=body.effective_from,
            effective_until=body.effective_until,
            description=body.description,
        )
    )
    return PromotionResponse.from_aggregate(promotion)


@router.get("/promotions", response_model=list[PromotionResponse], summary="List promotions")
def list_promotions(
    live_at: float | None = Query(default=None, description="Only promotions live at this instant"),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[PromotionResponse]:
    views = app.queries.dispatch(
        ListPromotions(actor_id=ctx.actor(), org_id=ctx.organization(), live_at=live_at)
    )
    return [PromotionResponse.from_view(v) for v in views]


@router.post("/coupons", response_model=CouponResponse, status_code=201, summary="Create a coupon")
def create_coupon(
    body: CreateCouponBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> CouponResponse:
    """Eligibility only — this endpoint creates no redemption, counts none, and enforces no
    redemption limit. Whoever records a redemption enforces it."""
    coupon = app.commands.dispatch(
        CreateCoupon(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            kind=body.kind,
            percentage_bp=body.percentage_bp,
            amount=to_money(body.amount),
            priority=body.priority,
            stackable=body.stackable,
            price_codes=tuple(body.price_codes),
            promotion_code=body.promotion_code,
            single_use=body.single_use,
            max_redemptions=body.max_redemptions,
            effective_from=body.effective_from,
            effective_until=body.effective_until,
            description=body.description,
        )
    )
    return CouponResponse.from_aggregate(coupon)


@router.get("/coupons", response_model=list[CouponResponse], summary="List coupons")
def list_coupons(
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[CouponResponse]:
    views = app.queries.dispatch(ListCoupons(actor_id=ctx.actor(), org_id=ctx.organization()))
    return [CouponResponse.from_view(v) for v in views]


# ── resolution ──────────────────────────────────────────────────────────────────


@router.post(
    "/resolve",
    response_model=ResolvedPriceResponse,
    summary="Resolve a price (a read; POST only because the request is structured)",
)
def resolve_price(
    body: ResolvePriceBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ResolvedPriceResponse:
    """Deterministic: the same books, the same discounts, and the same request always produce
    the same answer, and `resolution_path` records how it was reached.

    404 when nothing prices the request — an unpriced code is a fact the caller must handle,
    never a zero this API invents."""
    view = app.queries.dispatch(
        ResolvePrice(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            request=body.to_request(ctx.organization()),
        )
    )
    if view is None:
        raise BillingNotFound(
            f"no price book prices {body.price_code!r} in {body.currency.value} at {body.at}"
        )
    return ResolvedPriceResponse.from_view(view)


# ── pricing versions + history ──────────────────────────────────────────────────


@router.post(
    "/versions",
    response_model=PricingVersionResponse,
    status_code=201,
    summary="Publish an immutable pricing version",
)
def publish_pricing(
    body: PublishPricingBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PricingVersionResponse:
    """Freezes the whole pricing surface, pinned to a published catalog version. Publishing
    pricing does not republish the catalog — the two version independently."""
    snapshot = app.commands.dispatch(
        PublishPricing(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            catalog_version=body.catalog_version,
            notes=body.notes,
            require_priced_codes=tuple(body.require_priced_codes),
        )
    )
    return PricingVersionResponse.from_aggregate(snapshot)


@router.post(
    "/versions/rollback",
    response_model=PricingVersionResponse,
    status_code=201,
    summary="Roll back by publishing a new version with older prices",
)
def rollback_pricing(
    body: RollbackPricingBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PricingVersionResponse:
    snapshot = app.commands.dispatch(
        RollbackPricing(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            to_version=body.to_version,
            notes=body.notes,
        )
    )
    return PricingVersionResponse.from_aggregate(snapshot)


@router.get(
    "/versions", response_model=list[PricingVersionResponse], summary="Pricing version history"
)
def pricing_version_history(
    limit: int = Query(default=50, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[PricingVersionResponse]:
    """The org-wide version line. `GET /pricing/books/{code}/versions` is the per-book one —
    the two version independently, and both are readable forever."""
    views = app.queries.dispatch(
        PricingVersionHistory(actor_id=ctx.actor(), org_id=ctx.organization(), limit=limit)
    )
    return [PricingVersionResponse.from_view(v) for v in views]


@router.get(
    "/versions/latest",
    response_model=PricingVersionResponse,
    summary="The current published pricing version",
)
def latest_pricing_version(
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PricingVersionResponse:
    view = app.queries.dispatch(GetPricingVersion(actor_id=ctx.actor(), org_id=ctx.organization()))
    if view is None:
        raise BillingNotFound("no pricing version has been published yet")
    return PricingVersionResponse.from_view(view)


@router.get(
    "/versions/{version}",
    response_model=PricingVersionResponse,
    summary="A historical pricing version (readable forever)",
)
def get_pricing_version(
    version: int,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> PricingVersionResponse:
    view = app.queries.dispatch(
        GetPricingVersion(actor_id=ctx.actor(), org_id=ctx.organization(), version=version)
    )
    if view is None:
        raise BillingNotFound(f"pricing version {version} not found")
    return PricingVersionResponse.from_view(view)


@router.get(
    "/activity", response_model=list[PricingActivityResponse], summary="Pricing activity history"
)
def pricing_activity(
    subject_code: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[PricingActivityResponse]:
    views = app.queries.dispatch(
        PricingHistory(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            subject_code=subject_code,
            limit=limit,
        )
    )
    return [PricingActivityResponse.from_view(v) for v in views]

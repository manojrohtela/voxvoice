"""Pricing routes (M9.5) — price books. Transport only.

**No price is ever calculated here.** This endpoint publishes a price *book*; rating a
charge is Foundation behaviour arriving with M9.7. A controller that multiplied a quantity
by a unit amount would have made pricing a transport concern, which is precisely the drift
ADR-0023 forbids.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing.application import BillingApplication, GetPriceBook, PublishPriceBook
from billing.foundation import BillingNotFound

from ..auth import authenticate
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES, PriceBookResponse, PublishPriceBookBody

router = APIRouter(prefix="/pricing", tags=["pricing"], responses=ERROR_RESPONSES)


@router.post(
    "/price-books",
    response_model=PriceBookResponse,
    status_code=201,
    summary="Publish a price book",
)
def publish_price_book(
    body: PublishPriceBookBody,
    ctx: BillingRequestContext = Depends(authenticate),
    app: BillingApplication = Depends(get_application),
) -> PriceBookResponse:
    book = app.commands.dispatch(
        PublishPriceBook(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            code=body.code,
            display_name=body.display_name,
            currency=body.currency,
        )
    )
    return PriceBookResponse.from_aggregate(book)


@router.get(
    "/price-books/{price_book_id}/versions/{version}",
    response_model=PriceBookResponse,
    summary="Get a price-book version",
)
def get_price_book(
    price_book_id: str,
    version: int,
    ctx: BillingRequestContext = Depends(authenticate),
    app: BillingApplication = Depends(get_application),
) -> PriceBookResponse:
    """Price versions are immutable — an existing version always resolves to the same
    prices, which is what makes a historical invoice reproducible."""
    view = app.queries.dispatch(
        GetPriceBook(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            price_book_id=price_book_id,
            version=version,
        )
    )
    if view is None:
        raise BillingNotFound(f"price book {price_book_id!r} version {version} not found")
    return PriceBookResponse.from_view(view)

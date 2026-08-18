"""Tax routes (M9.5) — transport only.

**No tax is calculated here.** This API stores a tax *profile* (the evidence inputs: regime,
place of supply, registrations) and reads back an immutable *determination* that the tax
engine produced. Determination itself is a rented edge arriving at M9.15 (ADR-0023 TX2/TX4);
a rate computed in a controller would be an unauditable tax decision.

One transport wrinkle worth naming: `UpsertTaxProfile` takes a fully-formed Foundation
aggregate, so this controller must name it — it uses the container's injected `IdGenerator`
and `Clock`, the same seams every layer below was wired with, rather than inventing an id or
reading the wall clock. A `TaxProfileRequest` DTO with a Foundation factory method (the shape
every other aggregate has) removes even that at M9.15; recorded as a known limitation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing.application import (
    BillingApplication,
    GetTaxDetermination,
    GetTaxProfile,
    UpsertTaxProfile,
)
from billing.foundation import BillingNotFound, Clock, IdGenerator
from billing.foundation.seams import PREFIX_TAX_PROFILE

from ..auth import require_org
from ..container import get_application, get_clock, get_ids
from ..context import BillingRequestContext
from ..schemas import (
    ERROR_RESPONSES,
    TaxDeterminationResponse,
    TaxProfileResponse,
    UpsertTaxProfileBody,
)

router = APIRouter(prefix="/taxes", tags=["taxes"], responses=ERROR_RESPONSES)


@router.put(
    "/profiles",
    response_model=TaxProfileResponse,
    summary="Create or replace an account's tax profile",
)
def upsert_tax_profile(
    body: UpsertTaxProfileBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
    ids: IdGenerator = Depends(get_ids),
    clock: Clock = Depends(get_clock),
) -> TaxProfileResponse:
    profile = body.to_profile(
        profile_id=ids.new(PREFIX_TAX_PROFILE),
        org_id=ctx.organization(),
        updated_at=clock.now(),
    )
    stored = app.commands.dispatch(
        UpsertTaxProfile(actor_id=ctx.actor(), org_id=ctx.organization(), profile=profile)
    )
    return TaxProfileResponse.from_aggregate(stored)


@router.get(
    "/profiles/{account_id}", response_model=TaxProfileResponse, summary="Get a tax profile"
)
def get_tax_profile(
    account_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxProfileResponse:
    view = app.queries.dispatch(
        GetTaxProfile(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id)
    )
    if view is None:
        raise BillingNotFound(f"no tax profile for account {account_id!r}")
    return TaxProfileResponse.from_view(view)


@router.get(
    "/determinations/{invoice_id}",
    response_model=TaxDeterminationResponse,
    summary="Get the immutable tax determination for an invoice",
)
def get_determination(
    invoice_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> TaxDeterminationResponse:
    """A determination is evidence: it is recorded once and never recomputed, so this always
    returns what was actually applied at issuance, not what today's rates would produce."""
    view = app.queries.dispatch(
        GetTaxDetermination(actor_id=ctx.actor(), org_id=ctx.organization(), invoice_id=invoice_id)
    )
    if view is None:
        raise BillingNotFound(f"no tax determination for invoice {invoice_id!r}")
    return TaxDeterminationResponse.from_view(view)

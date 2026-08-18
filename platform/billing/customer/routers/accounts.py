"""Billing account routes (M9.5) — transport only."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing.application import (
    BillingApplication,
    CloseBillingAccount,
    GetAccount,
    GetAccountHierarchy,
    OpenBillingAccount,
    UpdateBillingProfile,
)
from billing.foundation import BillingNotFound

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import (
    ERROR_RESPONSES,
    AccountHierarchyResponse,
    AccountResponse,
    CloseAccountBody,
    OpenAccountBody,
    UpdateProfileBody,
)

router = APIRouter(prefix="/accounts", tags=["accounts"], responses=ERROR_RESPONSES)


@router.post("", response_model=AccountResponse, status_code=201, summary="Open a billing account")
def open_account(
    body: OpenAccountBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AccountResponse:
    account = app.commands.dispatch(
        OpenBillingAccount(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            display_name=body.display_name,
            currency=body.currency,
            kind=body.kind,
            parent_account_id=body.parent_account_id,
            contact=body.contact.to_contact(),
            address=body.address.to_address(),
        )
    )
    return AccountResponse.from_aggregate(account)


@router.get("/{account_id}", response_model=AccountResponse, summary="Get a billing account")
def get_account(
    account_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AccountResponse:
    view = app.queries.dispatch(
        GetAccount(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id)
    )
    if view is None:
        raise BillingNotFound(f"billing account {account_id!r} not found")
    return AccountResponse.from_view(view)


@router.get(
    "/{account_id}/hierarchy",
    response_model=AccountHierarchyResponse,
    summary="Get the billing hierarchy for an account",
)
def get_hierarchy(
    account_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AccountHierarchyResponse:
    view = app.queries.dispatch(
        GetAccountHierarchy(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id)
    )
    if view is None:
        raise BillingNotFound(f"billing account {account_id!r} not found")
    return AccountHierarchyResponse.from_view(view)


@router.patch("/{account_id}", response_model=AccountResponse, summary="Update the billing profile")
def update_profile(
    account_id: str,
    body: UpdateProfileBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AccountResponse:
    account = app.commands.dispatch(
        UpdateBillingProfile(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            contact=body.contact.to_contact(),
            address=body.address.to_address(),
        )
    )
    return AccountResponse.from_aggregate(account)


@router.post(
    "/{account_id}/close",
    response_model=AccountResponse,
    summary="Close a billing account (records are retained)",
)
def close_account(
    account_id: str,
    body: CloseAccountBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> AccountResponse:
    """Closing ends commercial activity; it never deletes revenue records (ADR-0023 RS4).
    There is deliberately no DELETE on this resource."""
    account = app.commands.dispatch(
        CloseBillingAccount(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=account_id,
            reason=body.reason,
        )
    )
    return AccountResponse.from_aggregate(account)

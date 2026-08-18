"""License routes (M9.5) — transport only."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing.application import (
    BillingApplication,
    IssueLicense,
    ListLicenses,
    RevokeLicense,
)

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..schemas import ERROR_RESPONSES, IssueLicenseBody, LicenseResponse, RevokeLicenseBody

router = APIRouter(prefix="/licenses", tags=["licenses"], responses=ERROR_RESPONSES)


@router.post("", response_model=LicenseResponse, status_code=201, summary="Issue a license")
def issue_license(
    body: IssueLicenseBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> LicenseResponse:
    license_ = app.commands.dispatch(
        IssueLicense(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            account_id=body.account_id,
            kind=body.kind,
            valid_from=body.valid_from,
            valid_until=body.valid_until,
            entitlement_codes=tuple(body.entitlement_codes),
            contract_id=body.contract_id,
        )
    )
    return LicenseResponse.from_aggregate(license_)


@router.get("", response_model=list[LicenseResponse], summary="List an account's licenses")
def list_licenses(
    account_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[LicenseResponse]:
    views = app.queries.dispatch(
        ListLicenses(actor_id=ctx.actor(), org_id=ctx.organization(), account_id=account_id)
    )
    return [LicenseResponse.from_view(v) for v in views]


@router.post("/{license_id}/revoke", response_model=LicenseResponse, summary="Revoke a license")
def revoke_license(
    license_id: str,
    body: RevokeLicenseBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> LicenseResponse:
    """Revocation is a recorded state transition, not a delete — the license and the reason
    it ended both remain readable."""
    license_ = app.commands.dispatch(
        RevokeLicense(
            actor_id=ctx.actor(),
            org_id=ctx.organization(),
            license_id=license_id,
            reason=body.reason,
        )
    )
    return LicenseResponse.from_aggregate(license_)

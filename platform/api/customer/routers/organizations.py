"""Organization routes (M8.5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.application import Application, CreateOrganization, GetOrganization
from api.foundation import NotFoundError

from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import ERROR_RESPONSES, CreateOrganizationBody, OrganizationResponse

router = APIRouter(prefix="/v1/organizations", tags=["organizations"])


@router.post("", response_model=OrganizationResponse, status_code=201, responses=ERROR_RESPONSES)
def create_organization(
    body: CreateOrganizationBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> OrganizationResponse:
    """Create an organization; the authenticated caller becomes its owner (bootstrap)."""
    view = app.commands.dispatch(
        CreateOrganization(
            name=body.name, owner_user_id=ctx.actor(), plan=body.plan, region=body.region
        )
    )
    return OrganizationResponse.from_view(view)


@router.get("/{org_id}", response_model=OrganizationResponse, responses=ERROR_RESPONSES)
def get_organization(
    org_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> OrganizationResponse:
    view = app.queries.dispatch(GetOrganization(org_id))
    if view is None:
        raise NotFoundError(f"organization {org_id!r} not found")
    return OrganizationResponse.from_view(view)

"""`/v1/console/me` and `/v1/console/organization(s)` — who is signed in, which organization
they are working in, and its members (M12.2, LLD §3).

The active organization lives on the **server-side session**. Every read below is scoped by it
(S3). An organization the user does not belong to is indistinguishable from one that does not
exist (S4).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session as DBSession

from ...db import get_session
from ..identity import service
from ..identity.deps import (
    Identity,
    Member,
    current_identity,
    require_console_header,
    require_role,
)
from .views import MemberView, MeView, OrganizationView, me_view

router = APIRouter(
    prefix="/v1/console", tags=["console"], dependencies=[Depends(require_console_header)]
)


class CreateOrganizationBody(BaseModel):
    name: str


class SelectOrganizationBody(BaseModel):
    organization_id: str


class RenameOrganizationBody(BaseModel):
    name: str


@router.get("/me", response_model=MeView)
def me(
    identity: Identity = Depends(current_identity), db: DBSession = Depends(get_session)
) -> MeView:
    return me_view(db, identity.user, identity.session.org_id)


@router.post("/organizations", response_model=MeView, status_code=201)
def create_organization(
    body: CreateOrganizationBody,
    identity: Identity = Depends(current_identity),
    db: DBSession = Depends(get_session),
) -> MeView:
    """Create an organization owned by the caller, and switch the session to it."""
    org = service.create_organization(db, owner=identity.user, name=body.name)
    service.select_organization(
        db, session=identity.session, user_id=identity.user.id, org_id=org.id
    )
    db.commit()
    return me_view(db, identity.user, org.id)


@router.post("/session/organization", response_model=MeView)
def select_organization(
    body: SelectOrganizationBody,
    identity: Identity = Depends(current_identity),
    db: DBSession = Depends(get_session),
) -> MeView:
    service.select_organization(
        db, session=identity.session, user_id=identity.user.id, org_id=body.organization_id
    )
    db.commit()
    return me_view(db, identity.user, body.organization_id)


@router.get("/organization", response_model=OrganizationView)
def get_organization(member: Member = Depends(require_role("viewer"))) -> OrganizationView:
    return OrganizationView(id=member.org.id, name=member.org.name, role=member.role)


@router.patch("/organization", response_model=OrganizationView)
def rename_organization(
    body: RenameOrganizationBody,
    member: Member = Depends(require_role("owner")),
    db: DBSession = Depends(get_session),
) -> OrganizationView:
    org = member.org
    org.name = service.clean_name(body.name, "Organization name")
    db.add(org)
    db.commit()
    return OrganizationView(id=org.id, name=org.name, role=member.role)


@router.get("/organization/members", response_model=list[MemberView])
def list_members(
    member: Member = Depends(require_role("viewer")), db: DBSession = Depends(get_session)
) -> list[MemberView]:
    return [
        MemberView(
            user_id=m.user_id,
            email=m.email,
            display_name=m.display_name,
            role=m.role,
            joined_at=m.joined_at,
        )
        for m in service.members_of(db, member.org.id)
    ]

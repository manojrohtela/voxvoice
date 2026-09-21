"""Response models shared by the identity routers (M12.2). Nothing secret is ever a field:
no password hash, no session token, no token hash, no API key."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from sqlmodel import Session as DBSession

from ..identity import service
from ..persistence import UserRow


class UserView(BaseModel):
    id: str
    email: str
    display_name: str


class OrganizationView(BaseModel):
    id: str
    name: str
    role: str


class MeView(BaseModel):
    user: UserView
    organization: OrganizationView | None
    memberships: list[OrganizationView]


class MemberView(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str
    joined_at: datetime


def me_view(db: DBSession, user: UserRow, active_org_id: str | None) -> MeView:
    memberships = [
        OrganizationView(id=m.org.id, name=m.org.name, role=m.role)
        for m in service.memberships(db, user.id)
    ]
    active = next((m for m in memberships if m.id == active_org_id), None)
    return MeView(
        user=UserView(id=user.id, email=user.email, display_name=user.display_name),
        organization=active,
        memberships=memberships,
    )

"""FastAPI dependencies that authenticate console requests (M12.2, LLD §3, security model §4).

* `require_console_header` — the CSRF guard on every mutating console route.
* `current_identity` — the signed-in user and their console session, or 401.
* `current_member` — the user's role in the session's active organization, or 409.
* `require_role(minimum)` — `current_member` plus the role matrix, or 403.

Later milestones scope every tenant query by `Member.org.id`, which is resolved server-side from
the session and never taken from the request (S3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Request
from sqlmodel import Session as DBSession

from ...db import get_session
from ...models import Org
from ..config import ConsoleSettings
from ..errors import Conflict, Forbidden, Unauthenticated
from ..persistence import ConsoleSessionRow, UserRow
from . import service

SESSION_COOKIE = "vv_console"
CONSOLE_HEADER = "x-voxvoice-console"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def console_settings(request: Request) -> ConsoleSettings:
    return request.app.state.console_settings


def require_console_header(request: Request) -> None:
    """Mutations must carry `x-voxvoice-console: 1`. A cross-site form cannot set a custom
    header, and a cross-site `fetch` cannot without a CORS preflight the API never grants."""
    if request.method not in _SAFE_METHODS and request.headers.get(CONSOLE_HEADER) != "1":
        raise Forbidden("This request must come from the VoxVoice console")


@dataclass(frozen=True)
class Identity:
    user: UserRow
    session: ConsoleSessionRow


def current_identity(
    request: Request,
    db: DBSession = Depends(get_session),
    settings: ConsoleSettings = Depends(console_settings),
) -> Identity:
    token = request.cookies.get(SESSION_COOKIE)
    resolved = (
        service.resolve_session(
            db, token, idle=timedelta(minutes=settings.session_idle_minutes)
        )
        if token
        else None
    )
    if resolved is None:
        raise Unauthenticated("Sign in to continue")
    db.commit()  # persist the refreshed idle clock
    session, user = resolved
    return Identity(user=user, session=session)


@dataclass(frozen=True)
class Member:
    user: UserRow
    org: Org
    role: str
    session: ConsoleSessionRow


def current_member(
    identity: Identity = Depends(current_identity),
    db: DBSession = Depends(get_session),
) -> Member:
    org_id = identity.session.org_id
    membership = service.membership_in(db, identity.user.id, org_id) if org_id else None
    if membership is None:
        raise Conflict("Select or create an organization first")
    return Member(
        user=identity.user, org=membership.org, role=membership.role, session=identity.session
    )


def require_role(minimum: str) -> Callable[..., Member]:
    if minimum not in service.ROLES:
        raise ValueError(f"unknown role {minimum!r}")

    def dependency(member: Member = Depends(current_member)) -> Member:
        if not service.role_at_least(member.role, minimum):
            raise Forbidden(f"This action needs the {minimum} role or higher")
        return member

    return dependency

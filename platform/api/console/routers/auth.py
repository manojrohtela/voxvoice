"""`/v1/console/auth/*` — sign-up, sign-in and sign-out for the console (M12.2, LLD §3).

Credentials are verified, always: there is no endpoint that issues a session for an identifier
alone (the Epic 8 `/v1/auth/token` behaviour is not reproduced, and that app is not served here).
The session token only ever travels in an `HttpOnly` cookie; no response body contains it.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlmodel import Session as DBSession

from ...db import get_session
from ..config import ConsoleSettings
from ..errors import Forbidden, RateLimited, Unauthenticated
from ..identity import service
from ..identity.deps import SESSION_COOKIE, console_settings, require_console_header
from .views import MeView, me_view

router = APIRouter(
    prefix="/v1/console/auth",
    tags=["console"],
    dependencies=[Depends(require_console_header)],
)


class SignupBody(BaseModel):
    email: str
    password: str
    display_name: str = ""
    organization_name: str


class LoginBody(BaseModel):
    email: str
    password: str


def signup_available(db: DBSession, settings: ConsoleSettings) -> bool:
    if settings.signup == "open":
        return True
    if settings.signup == "first_run":
        return service.user_count(db) == 0
    return False


def _set_session_cookie(
    request: Request, response: Response, token: str, settings: ConsoleSettings
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_absolute_hours * 3600,
        path="/",
        httponly=True,
        samesite="strict",
        secure=settings.secure_cookie(request.app.state.console_app_env),
    )


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/signup", response_model=MeView, status_code=201)
def signup(
    body: SignupBody,
    request: Request,
    response: Response,
    db: DBSession = Depends(get_session),
    settings: ConsoleSettings = Depends(console_settings),
) -> MeView:
    """Create an account and its first organization, and sign in."""
    if not signup_available(db, settings):
        raise Forbidden("Sign-up is closed on this server. Ask an owner for an account.")
    user = service.create_user(
        db, email=body.email, password=body.password, display_name=body.display_name
    )
    org = service.create_organization(db, owner=user, name=body.organization_name)
    token = service.start_session(
        db, user=user, org_id=org.id, absolute=timedelta(hours=settings.session_absolute_hours)
    )
    db.commit()
    _set_session_cookie(request, response, token, settings)
    return me_view(db, user, org.id)


@router.post("/login", response_model=MeView)
def login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: DBSession = Depends(get_session),
    settings: ConsoleSettings = Depends(console_settings),
) -> MeView:
    throttle = request.app.state.console_login_throttle
    key = (body.email.strip().lower(), _client(request))
    if throttle.blocked(*key):
        raise RateLimited("Too many failed sign-in attempts. Try again later.")
    user = service.authenticate(db, email=body.email, password=body.password)
    if user is None:
        throttle.record_failure(*key)
        raise Unauthenticated("Email or password is incorrect")
    throttle.clear(*key)
    org_id = service.default_org_id(db, user.id)
    token = service.start_session(
        db, user=user, org_id=org_id, absolute=timedelta(hours=settings.session_absolute_hours)
    )
    db.commit()
    _set_session_cookie(request, response, token, settings)
    return me_view(db, user, org_id)


@router.post("/logout", status_code=204)
def logout(request: Request, db: DBSession = Depends(get_session)) -> Response:
    """Revoke the current session. Idempotent: signing out twice is not an error."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        service.revoke_session(db, token)
        db.commit()
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response

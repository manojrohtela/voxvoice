"""Authentication routes (M8.5): register, dev token issuance, identity."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.application import Application, RegisterUser

from ..auth import authenticate, issue_token
from ..config import CustomerApiSettings
from ..container import get_application, get_settings
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    IdentityResponse,
    RegisterUserBody,
    TokenBody,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=201, responses=ERROR_RESPONSES)
def register(body: RegisterUserBody, app: Application = Depends(get_application)) -> UserResponse:
    """Public self-service signup — creates a user (no organization yet)."""
    view = app.commands.dispatch(RegisterUser(email=body.email, display_name=body.display_name))
    return UserResponse.from_view(view)


@router.post("/token", response_model=TokenResponse, responses=ERROR_RESPONSES)
def token(body: TokenBody, settings: CustomerApiSettings = Depends(get_settings)) -> TokenResponse:
    """Dev/test token issuance — mints an identity JWT for an existing user id. A real
    deployment replaces this with an IdP / credential check (no credential store yet)."""
    access = issue_token(settings, user_id=body.user_id, org_id=body.org_id)
    return TokenResponse(access_token=access, expires_in=settings.token_ttl_seconds)


@router.get("/me", response_model=IdentityResponse, responses=ERROR_RESPONSES)
def me(ctx: RequestContext = Depends(authenticate)) -> IdentityResponse:
    """Return the identity resolved from the bearer token."""
    return IdentityResponse(user_id=ctx.actor(), org_id=ctx.org_id, workspace_id=ctx.workspace_id)

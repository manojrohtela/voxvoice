"""Authentication (M8.5) — bearer/JWT identity resolution. No authorization.

Validates a `Authorization: Bearer <jwt>` header, verifies the HS256 signature + expiry,
and resolves the caller's identity (user id, optional org/workspace claims) into the
`RequestContext`. It makes **no** permission decision — authorization is orchestrated by
the Application Layer against the Foundation authorizer (ADR-0021). Controllers depend on
`authenticate` to obtain an authenticated context; they never parse tokens themselves.

Token issuance here is a **development/testing** convenience (`issue_token`) — a real
deployment replaces it with an IdP / credential verification. It mints identity only; it
never grants a role.
"""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import CustomerApiSettings
from .container import get_settings
from .context import RequestContext
from .jwt import JWTError, decode_any, encode

_bearer = HTTPBearer(auto_error=False, description="HS256 JWT bearer token")


def issue_token(settings: CustomerApiSettings, *, user_id: str, org_id: str | None = None) -> str:
    """Mint a signed identity token (dev/test issuance — not a credential check)."""
    claims: dict[str, object] = {"sub": user_id}
    if org_id:
        claims["org"] = org_id
    return encode(claims, settings.jwt_secret, ttl_seconds=settings.token_ttl_seconds)


def authenticate(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: CustomerApiSettings = Depends(get_settings),
) -> RequestContext:
    """Resolve the authenticated `RequestContext`, or raise `JWTError` (→ 401)."""
    if credentials is None or not credentials.credentials:
        raise JWTError("missing bearer token")
    # Verify against all accepted secrets (primary + rotated) — the M8.18 key-rotation seam.
    payload = decode_any(credentials.credentials, settings.verification_secrets)
    user_id = payload.get("sub")
    if not user_id:
        raise JWTError("token has no subject")
    base: RequestContext = request.state.context  # set by middleware
    ctx = base.with_identity(user_id).scoped(org_id=payload.get("org"))
    request.state.context = ctx  # enrich the shared context with identity
    return ctx

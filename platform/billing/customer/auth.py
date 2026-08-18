"""Authentication (M9.5) — identity resolution. **No authorization.**

Validates `Authorization: Bearer <jwt>`, verifies the HS256 signature and expiry against the
configured secrets, and resolves the caller's identity into the `BillingRequestContext`.

That is the entire job. Billing does not authenticate users (it verifies a token the platform
identity provider issued), does not own RBAC, and never evaluates a permission (ADR-0023
§5.3). Controllers depend on `authenticate` to obtain identity and forward it; the decision is
made below, by the Application's `BillingAuthorizer` seam.

There is no `/auth` route in this API, no token issuance, and no login. If those existed here,
Billing would have become an identity system — the exact ownership drift ADR-0023 forbids.
"""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import BillingApiSettings
from .container import get_settings
from .context import BillingRequestContext
from .jwt import BillingJWTError, decode_any

_bearer = HTTPBearer(auto_error=False, description="HS256 identity token (platform-issued)")


def authenticate(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: BillingApiSettings = Depends(get_settings),
) -> BillingRequestContext:
    """Resolve the authenticated context, or raise `BillingJWTError` (→ 401)."""
    if credentials is None or not credentials.credentials:
        raise BillingJWTError("missing bearer token")
    payload = decode_any(credentials.credentials, settings.verification_secrets)
    user_id = payload.get("sub")
    if not user_id:
        raise BillingJWTError("token has no subject")
    base: BillingRequestContext = request.state.context  # established by middleware
    ctx = base.with_identity(str(user_id)).scoped(org_id=payload.get("org"))
    request.state.context = ctx
    return ctx


def require_org(ctx: BillingRequestContext = Depends(authenticate)) -> BillingRequestContext:
    """Identity **and** an organization claim, for the org-owned routes.

    This is a shape check on the request, not a permission check: a command that acts on an
    organization needs one to act on. Whether this subject may do so is decided below, and the
    absence of a claim is a malformed request (401), not a denial."""
    if not ctx.organization():
        raise BillingJWTError("token carries no organization claim")
    return ctx

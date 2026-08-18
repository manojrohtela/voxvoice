"""Billing Control Plane API (M9.5) — the HTTP transport for the revenue plane.

    HTTP → command/query → Application → Foundation → Persistence

Nothing bypasses the Application. Controllers parse, authenticate, construct a message,
dispatch it, and serialize the result; they never authorize, validate a business rule, touch
a repository, open a transaction, calculate a price, or evaluate an entitlement.

Layering (ADR-0031 §8, extended additively at M9.5):

    billing.customer → billing.application → billing.foundation → core

and never the reverse. This package imports no persistence model and no SQLModel type; the
lower layers do not import it at all.
"""

from __future__ import annotations

from .app import create_billing_app
from .auth import authenticate, require_org
from .config import OPENAPI_TAGS, BillingApiSettings
from .container import (
    BillingContainer,
    get_application,
    get_clock,
    get_container,
    get_ids,
    get_settings,
)
from .context import BillingRequestContext
from .errors import register_exception_handlers, status_for
from .jwt import BillingJWTError
from .middleware import (
    BillingContextMiddleware,
    BillingPayloadLimitMiddleware,
    BillingRequestMetrics,
)
from .routers import ALL_ROUTERS, DOMAIN_ROUTERS, SYSTEM_ROUTER

__all__ = [
    "ALL_ROUTERS",
    "DOMAIN_ROUTERS",
    "OPENAPI_TAGS",
    "SYSTEM_ROUTER",
    "BillingApiSettings",
    "BillingContainer",
    "BillingContextMiddleware",
    "BillingJWTError",
    "BillingPayloadLimitMiddleware",
    "BillingRequestContext",
    "BillingRequestMetrics",
    "authenticate",
    "create_billing_app",
    "get_application",
    "get_clock",
    "get_container",
    "get_ids",
    "get_settings",
    "register_exception_handlers",
    "require_org",
    "status_for",
]

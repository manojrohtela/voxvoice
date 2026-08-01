"""customer — the Customer Platform Control Plane HTTP API (Epic 8, M8.5).

A **thin** FastAPI transport boundary over the M8.3 Application buses, wired to the M8.4
persistence composition root. It owns only transport concerns: HTTP parsing/serialization,
dependency injection, authentication (bearer/JWT identity resolution — no authorization),
request context, centralized error translation, middleware, OpenAPI, versioning, and
health. **All** business logic, validation, authorization, transactions, and persistence
live below it (M8.2–M8.4) and are unchanged.

Boundary: `api.customer → api.application, api.persistence, api.foundation (types), core,
tenant, fastapi/pydantic`. Controllers never touch a repository, a transaction, or a
permission check — they dispatch Application commands/queries. Foundation, Application, and
Persistence layers are byte-for-byte unchanged; frozen Epics 5/6/7 untouched.
"""

from __future__ import annotations

from .app import create_app
from .auth import authenticate, issue_token
from .config import CustomerApiSettings
from .container import Container, get_application, get_container, get_database, get_settings
from .context import RequestContext
from .jwt import JWTError

__all__ = [
    "create_app",
    "CustomerApiSettings",
    "Container",
    "RequestContext",
    "authenticate",
    "issue_token",
    "get_application",
    "get_container",
    "get_database",
    "get_settings",
    "JWTError",
]

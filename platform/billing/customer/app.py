"""Billing Control Plane API application factory (M9.5).

`create_billing_app(...)` assembles the thin FastAPI transport boundary over the Billing
Application: lifespan (validate configuration, build the DI container unless one is injected
for tests, verify dependencies, close the database on shutdown), middleware, the centralized
exception handlers, the routers, and OpenAPI metadata.

It owns transport only. No business rule, no pricing, no entitlement decision, no
authorization policy, no transaction, and no repository access lives in this package — all of
it is below, in layers this one cannot even import a row type from.

**This is a separate ASGI application from the Epic 8 Control Plane, by design.** ADR-0023
gives revenue a distinct failure domain: a Billing deployment that is down, degraded, or
mid-migration must not take conversations or deployments with it, and two apps in two
processes is the cheapest possible way to guarantee that. Mounting it under the Customer
Platform would have re-coupled exactly what the ownership boundary separates.
"""

from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .config import OPENAPI_TAGS, BillingApiSettings
from .container import BillingContainer
from .errors import register_exception_handlers
from .middleware import (
    BillingContextMiddleware,
    BillingPayloadLimitMiddleware,
    BillingRequestMetrics,
)
from .routers import DOMAIN_ROUTERS, SYSTEM_ROUTER

_DESCRIPTION = (
    "The VoxVoice **Billing Control Plane API** — a thin transport boundary over the Billing "
    "Application layer (Epic 9 M9.3) and, beneath it, the Billing Foundation (M9.2) and "
    "durable persistence (M9.4).\n\n"
    "Billing owns revenue and nothing else. This API therefore never authenticates a user, "
    "never evaluates a permission, never calculates a price or a tax, never enforces an "
    "entitlement, and never moves money: it parses requests, dispatches Application "
    "commands and queries, and serializes responses.\n\n"
    "**Money is always integer minor units plus an explicit currency** — never a float.\n\n"
    "Authentication is a platform-issued HS256 bearer token; authorization is decided below "
    "this layer and is **deny-by-default**."
)


def _verify_dependencies(container: BillingContainer) -> None:
    """Startup verification (§A10): the database must be reachable and the Application wired
    before the process accepts traffic. Fails fast — a broken deployment never serves."""
    if not container.check_database():
        raise RuntimeError("startup verification failed: billing database unreachable")
    if container.application is None or container.application.commands is None:
        raise RuntimeError("startup verification failed: Billing Application not wired")


def create_billing_app(
    *,
    settings: BillingApiSettings | None = None,
    container: BillingContainer | None = None,
) -> FastAPI:
    settings = settings or (container.settings if container else BillingApiSettings.load())
    metrics = BillingRequestMetrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.validate()  # fail fast on invalid configuration (§A2.6)
        app.state.container = container or BillingContainer.build(settings)
        app.state.metrics = metrics
        _verify_dependencies(app.state.container)
        yield
        try:  # graceful shutdown: release the connection (idempotent)
            app.state.container.database.close()
        except Exception:  # noqa: BLE001 — shutdown must never raise
            pass

    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        description=_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )

    # Middleware, outermost first: CORS → GZip → payload limit → request context.
    app.add_middleware(BillingContextMiddleware, metrics=metrics)
    app.add_middleware(BillingPayloadLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(GZipMiddleware, minimum_size=512)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    # Probes at the root; every domain route under the configured version prefix, so the
    # whole surface can be versioned in one place rather than fourteen.
    app.include_router(SYSTEM_ROUTER)
    for router in DOMAIN_ROUTERS:
        app.include_router(router, prefix=settings.api_prefix)

    # M9.6 — the Catalog Studio, served as static assets at /studio. ADR-0021: the Studio is
    # a **Consumer** that drives this API over HTTP; it owns no state and holds no rule, so
    # it is a file this app hands out, never a layer this app depends on.
    studio_dir = pathlib.Path(__file__).parent / "studio"
    if studio_dir.is_dir():
        app.mount("/studio", StaticFiles(directory=str(studio_dir), html=True), name="studio")

    return app

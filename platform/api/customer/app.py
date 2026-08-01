"""Control Plane API application factory (M8.5).

`create_app(...)` assembles the thin FastAPI transport boundary over the Customer Platform:
lifecycle (build the DI `Container` at startup unless one is injected for tests; close the
database at shutdown), middleware (context/correlation/timing/metrics/security headers +
GZip + CORS), the centralized exception handlers, the domain routers, and OpenAPI metadata
(tags, version, bearer security scheme). It owns transport only — no business logic,
persistence, transactions, or permission evaluation lives here.
"""

from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .config import OPENAPI_TAGS, CustomerApiSettings
from .container import Container
from .errors import register_exception_handlers
from .middleware import (
    ContextMiddleware,
    PayloadLimitMiddleware,
    RateLimitMiddleware,
    RequestMetrics,
)
from .routers import ALL_ROUTERS

_DESCRIPTION = (
    "The VoxVoice Control Plane API — a thin transport boundary over the Customer Platform "
    "Application Layer. Business logic, authorization, validation, transactions, and "
    "persistence live below this layer (Epic 8 M8.2–M8.4); this API only parses requests, "
    "dispatches Application commands/queries, and serializes responses."
)


def _verify_dependencies(container: Container) -> None:
    """Startup dependency verification (M8.18 hardening): the database must be reachable and the
    Application wired before the process accepts traffic. Fails fast (§A10) — a broken deployment
    never serves. Cheap + deterministic; the deeper checks live in the `/ready` probe."""
    from sqlalchemy import text

    container.database.session.exec(text("SELECT 1"))
    if container.application is None or container.application.commands is None:
        raise RuntimeError("startup verification failed: Application not wired")


def create_app(
    *,
    settings: CustomerApiSettings | None = None,
    container: Container | None = None,
) -> FastAPI:
    settings = settings or (container.settings if container else CustomerApiSettings.load())
    metrics = RequestMetrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: validate configuration (fail fast, §A2.6), then wire the container (build
        # persistence + Application) unless injected, and verify core dependencies are reachable.
        settings.validate()
        app.state.container = container or Container.build(settings)
        app.state.metrics = metrics
        _verify_dependencies(app.state.container)
        yield
        # Graceful shutdown: release the database connection (idempotent).
        try:
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

    # Middleware (outermost first): CORS → GZip → rate-limit → payload-limit → context. The
    # rate/payload guards run before request context so an abusive caller is rejected cheaply; both
    # are transport-only and disabled by default (dev/test) — a deployment enables them via config.
    app.add_middleware(ContextMiddleware, metrics=metrics)
    app.add_middleware(PayloadLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_minute)
    app.add_middleware(GZipMiddleware, minimum_size=512)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    for router in ALL_ROUTERS:
        app.include_router(router)

    # M8.6 — serve the self-contained Studio UI (static assets) at /studio. Transport
    # only: the Studio is a Consumer (ADR-0021) that drives this API over HTTP.
    studio_dir = pathlib.Path(__file__).parent / "studio"
    if studio_dir.is_dir():
        app.mount("/studio", StaticFiles(directory=str(studio_dir), html=True), name="studio")

    return app

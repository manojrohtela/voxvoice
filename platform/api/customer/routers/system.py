"""System routes (M8.5): health, readiness, version.

Liveness/readiness/version only — **no runtime checks** (never touches the Epic 5/6/7
runtime). Readiness verifies configuration, database connectivity, and Application wiring.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.application import Application
from api.persistence import Database

from ..config import CustomerApiSettings
from ..container import get_application, get_database, get_settings
from ..schemas import HealthResponse, ReadyResponse, VersionResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(settings: CustomerApiSettings = Depends(get_settings)) -> HealthResponse:
    """Liveness — the process is up and configured."""
    return HealthResponse(status="ok", environment=settings.environment)


@router.get("/ready", response_model=ReadyResponse)
def ready(
    settings: CustomerApiSettings = Depends(get_settings),
    database: Database = Depends(get_database),
    app: Application = Depends(get_application),
) -> JSONResponse:
    """Readiness — configuration valid, database reachable, Application wired."""
    checks = {"configuration": True, "database": False, "application": False}
    try:
        settings.validate()
        checks["configuration"] = True
    except Exception:  # noqa: BLE001
        checks["configuration"] = False
    try:
        database.session.exec(text("SELECT 1"))
        checks["database"] = True
    except Exception:  # noqa: BLE001
        checks["database"] = False
    checks["application"] = app is not None and app.commands is not None
    ready_ = all(checks.values())
    return JSONResponse(
        status_code=200 if ready_ else 503,
        content=ReadyResponse(ready=ready_, checks=checks).model_dump(),
    )


@router.get("/version", response_model=VersionResponse)
def version(settings: CustomerApiSettings = Depends(get_settings)) -> VersionResponse:
    return VersionResponse(api=settings.api_version, title=settings.api_title)

"""System routes (M9.5): health, readiness, version.

Liveness, readiness, and version only. **No runtime checks** — this API never touches the
Epic 5/6/7 runtime, and a Billing readiness failure must never be read as a platform outage
(ADR-0023: Billing never blocks Runtime or Deployment).

These are the only unauthenticated routes in the plane: a probe has no identity.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from billing.application import BillingApplication

from ..config import BillingApiSettings
from ..container import BillingContainer, get_application, get_container, get_settings
from ..schemas import HealthResponse, ReadyResponse, VersionResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Liveness")
def health(settings: BillingApiSettings = Depends(get_settings)) -> HealthResponse:
    """The process is up and configured."""
    return HealthResponse(status="ok", environment=settings.environment)


@router.get("/ready", response_model=ReadyResponse, summary="Readiness")
def ready(
    settings: BillingApiSettings = Depends(get_settings),
    container: BillingContainer = Depends(get_container),
    app: BillingApplication = Depends(get_application),
) -> JSONResponse:
    """Configuration valid, database reachable, Application wired. 503 when not ready."""
    checks = {"configuration": False, "database": False, "application": False}
    try:
        settings.validate()
        checks["configuration"] = True
    except Exception:  # noqa: BLE001 — a probe reports, it never raises
        checks["configuration"] = False
    checks["database"] = container.check_database()
    checks["application"] = app is not None and app.commands is not None
    ready_ = all(checks.values())
    return JSONResponse(
        status_code=200 if ready_ else 503,
        content=ReadyResponse(ready=ready_, checks=checks).model_dump(),
    )


@router.get("/version", response_model=VersionResponse, summary="API version")
def version(settings: BillingApiSettings = Depends(get_settings)) -> VersionResponse:
    return VersionResponse(
        api=settings.api_version, title=settings.api_title, prefix=settings.api_prefix
    )

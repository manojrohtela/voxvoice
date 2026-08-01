"""Observability routes (M8.17) — additive, thin.

The Observability surface under an Organization: metrics / logs / traces / health / usage / cost /
performance / dashboards (read-model projections over the durable audit stream) + alert-rule
config (create/list/update/delete/evaluate/history). Every controller is transport-only: it maps
the request to an Application query/command and serializes the result — **no monitoring logic, no
repository, no aggregation, no permission evaluation** (all of that is the Foundation's
`ObservabilityService`). Reads are org-scoped (a viewer may observe); alert-rule writes require
`ORG_MANAGE` (enforced in the Foundation). Optional `workspace_id`/`project_id` query params narrow
the scope.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.application import (
    Application,
    CreateAlertRule,
    DeleteAlertRule,
    EvaluateAlerts,
    GetAlertHistory,
    GetCost,
    GetDashboard,
    GetHealth,
    GetHealthHistory,
    GetLogs,
    GetMetricDefinitions,
    GetMetricReport,
    GetMetricSeries,
    GetPerformance,
    GetTrace,
    GetTraces,
    GetUsage,
    ListAlertRules,
    UpdateAlertRule,
)

from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import ERROR_RESPONSES, CreateAlertRuleBody, UpdateAlertRuleBody

router = APIRouter(prefix="/v1/organizations/{org_id}/observability", tags=["observability"])


# ── metrics ─────────────────────────────────────────────────────────────────────


@router.get("/metrics", responses=ERROR_RESPONSES)
def metric_report(
    org_id: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
    since: float | None = None,
    until: float | None = None,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetMetricReport(org_id, workspace_id, project_id, since, until))


@router.get("/metrics/definitions", responses=ERROR_RESPONSES)
def metric_definitions(
    org_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetMetricDefinitions(org_id))


@router.get("/metrics/{metric_key}/series", responses=ERROR_RESPONSES)
def metric_series(
    org_id: str,
    metric_key: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
    bucket_seconds: float = 60.0,
    since: float | None = None,
    until: float | None = None,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(
        GetMetricSeries(org_id, metric_key, workspace_id, project_id, bucket_seconds, since, until)
    )


# ── logs / traces ─────────────────────────────────────────────────────────────────


@router.get("/logs", responses=ERROR_RESPONSES)
def logs(
    org_id: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
    level: str | None = None,
    category: str | None = None,
    actor: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = 100,
    offset: int = 0,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(
        GetLogs(
            org_id, workspace_id, project_id, level, category, actor, since, until, limit, offset
        )
    )


@router.get("/traces", responses=ERROR_RESPONSES)
def traces(
    org_id: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetTraces(org_id, workspace_id, project_id, None, None, limit))


@router.get("/traces/{trace_id}", responses=ERROR_RESPONSES)
def trace(
    org_id: str,
    trace_id: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetTrace(org_id, trace_id, workspace_id, project_id))


# ── health / usage / cost / performance ──────────────────────────────────────────


@router.get("/health", responses=ERROR_RESPONSES)
def health(
    org_id: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
    since: float | None = None,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetHealth(org_id, workspace_id, project_id, since, None))


@router.get("/health/{component}/history", responses=ERROR_RESPONSES)
def health_history(
    org_id: str,
    component: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetHealthHistory(org_id, component, workspace_id, project_id))


@router.get("/usage", responses=ERROR_RESPONSES)
def usage(
    org_id: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
    since: float | None = None,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetUsage(org_id, workspace_id, project_id, since, None))


@router.get("/cost", responses=ERROR_RESPONSES)
def cost(
    org_id: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
    since: float | None = None,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetCost(org_id, workspace_id, project_id, since, None))


@router.get("/performance", responses=ERROR_RESPONSES)
def performance(
    org_id: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
    since: float | None = None,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetPerformance(org_id, workspace_id, project_id, since, None))


# ── dashboards ────────────────────────────────────────────────────────────────────


@router.get("/dashboards/{kind}", responses=ERROR_RESPONSES)
def dashboard(
    org_id: str,
    kind: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetDashboard(org_id, kind, workspace_id, project_id, agent_id))


# ── alerts (config + evaluation) ─────────────────────────────────────────────────


@router.get("/alerts", responses=ERROR_RESPONSES)
def list_alerts(
    org_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[dict]:
    return app.queries.dispatch(ListAlertRules(org_id))


@router.post("/alerts", status_code=201, responses=ERROR_RESPONSES)
def create_alert(
    org_id: str,
    body: CreateAlertRuleBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.commands.dispatch(
        CreateAlertRule(
            actor_user_id=ctx.actor(),
            org_id=org_id,
            name=body.name,
            metric_key=body.metric_key,
            comparator=body.comparator,
            threshold=body.threshold,
            window_seconds=body.window_seconds,
            severity=body.severity,
            project_id=body.project_id,
        )
    )


@router.get("/alerts/evaluate", responses=ERROR_RESPONSES)
def evaluate_alerts(
    org_id: str,
    project_id: str | None = None,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(EvaluateAlerts(org_id, project_id))


@router.get("/alerts/{rule_id}/history", responses=ERROR_RESPONSES)
def alert_history(
    org_id: str,
    rule_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetAlertHistory(org_id, rule_id))


@router.patch("/alerts/{rule_id}", responses=ERROR_RESPONSES)
def update_alert(
    org_id: str,
    rule_id: str,
    body: UpdateAlertRuleBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.commands.dispatch(
        UpdateAlertRule(
            actor_user_id=ctx.actor(), org_id=org_id, rule_id=rule_id, changes=body.changes
        )
    )


@router.delete("/alerts/{rule_id}", responses=ERROR_RESPONSES)
def delete_alert(
    org_id: str,
    rule_id: str,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.commands.dispatch(
        DeleteAlertRule(actor_user_id=ctx.actor(), org_id=org_id, rule_id=rule_id)
    )

"""Deployment Actuator routes (M8.16, ADR-0022) — additive, thin.

The deployment surface under a Project: plan (read-only validation), submit, and the lifecycle
verbs (approve / reject / cancel / retry / rollback), plus status / history / timeline / logs /
health / targets. Every controller is transport-only: it maps the request to an Application
command/query and serializes the result — **no deployment logic, no repository, no transaction, no
permission evaluation** (all of that is the Actuator / Foundation service). Studio *requests*
deployment; the Actuator *executes* it (ADR-0021 / ADR-0022 — Studio owns no execution state).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.application import (
    Application,
    ApproveDeployment,
    CancelDeployment,
    GetDeploymentHealth,
    GetDeploymentLogs,
    GetDeploymentStatus,
    GetDeploymentTimeline,
    ListDeploymentHistory,
    ListDeploymentTargets,
    PlanDeployment,
    RejectDeployment,
    RetryDeployment,
    RollbackDeployment,
    SubmitDeployment,
)

from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    DeploymentActionBody,
    PlanDeploymentBody,
    SubmitDeploymentBody,
)

router = APIRouter(
    prefix="/v1/organizations/{org_id}/workspaces/{workspace_id}/projects/{project_id}/deployments",
    tags=["deployment"],
)


# ── plan (read-only) + submit ─────────────────────────────────────────────────────


@router.post("/plan", responses=ERROR_RESPONSES)
def plan_deployment(
    org_id: str,
    workspace_id: str,
    project_id: str,
    body: PlanDeploymentBody,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    """Validate + return a read-only Deployment Plan (the planning step). No execution."""
    return app.queries.dispatch(
        PlanDeployment(
            org_id=org_id,
            workspace_id=workspace_id,
            project_id=project_id,
            agent_id=body.agent_id,
            environment=body.environment,
            strategy=body.strategy,
            requires_approval=body.requires_approval,
        )
    )


@router.post("", status_code=201, responses=ERROR_RESPONSES)
def submit_deployment(
    org_id: str,
    workspace_id: str,
    project_id: str,
    body: SubmitDeploymentBody,
    ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.commands.dispatch(
        SubmitDeployment(
            actor_user_id=ctx.actor(),
            org_id=org_id,
            workspace_id=workspace_id,
            project_id=project_id,
            agent_id=body.agent_id,
            environment=body.environment,
            strategy=body.strategy,
            requires_approval=body.requires_approval,
            auto_approve=body.auto_approve,
        )
    )


@router.get("", responses=ERROR_RESPONSES)
def list_history(
    org_id: str,
    workspace_id: str,
    project_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> list[dict]:
    return app.queries.dispatch(ListDeploymentHistory(org_id, project_id))


@router.get("/targets", responses=ERROR_RESPONSES)
def list_targets(
    org_id: str,
    workspace_id: str,
    project_id: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(ListDeploymentTargets(org_id))


@router.get("/{deployment_ref}", responses=ERROR_RESPONSES)
def get_status(
    org_id: str,
    workspace_id: str,
    project_id: str,
    deployment_ref: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetDeploymentStatus(org_id, project_id, deployment_ref))


@router.get("/{deployment_ref}/timeline", responses=ERROR_RESPONSES)
def get_timeline(
    org_id: str,
    workspace_id: str,
    project_id: str,
    deployment_ref: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetDeploymentTimeline(org_id, project_id, deployment_ref))


@router.get("/{deployment_ref}/logs", responses=ERROR_RESPONSES)
def get_logs(
    org_id: str,
    workspace_id: str,
    project_id: str,
    deployment_ref: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetDeploymentLogs(org_id, project_id, deployment_ref))


@router.get("/{deployment_ref}/health", responses=ERROR_RESPONSES)
def get_health(
    org_id: str,
    workspace_id: str,
    project_id: str,
    deployment_ref: str,
    _ctx: RequestContext = Depends(authenticate),
    app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(GetDeploymentHealth(org_id, project_id, deployment_ref))


# ── lifecycle verbs ─────────────────────────────────────────────────────────────


def _action(cmd_cls, with_reason: bool):
    def handler(
        org_id: str,
        workspace_id: str,
        project_id: str,
        deployment_ref: str,
        body: DeploymentActionBody | None = None,
        ctx: RequestContext = Depends(authenticate),
        app: Application = Depends(get_application),
    ) -> dict:
        reason = (body.reason if body else "") if with_reason else ""
        kwargs = {"reason": reason} if with_reason else {}
        return app.commands.dispatch(
            cmd_cls(ctx.actor(), org_id, workspace_id, project_id, deployment_ref, **kwargs)
        )

    return handler


router.add_api_route(
    "/{deployment_ref}/approve",
    _action(ApproveDeployment, True),
    methods=["POST"],
    responses=ERROR_RESPONSES,
)
router.add_api_route(
    "/{deployment_ref}/reject",
    _action(RejectDeployment, True),
    methods=["POST"],
    responses=ERROR_RESPONSES,
)
router.add_api_route(
    "/{deployment_ref}/rollback",
    _action(RollbackDeployment, True),
    methods=["POST"],
    responses=ERROR_RESPONSES,
)
router.add_api_route(
    "/{deployment_ref}/cancel",
    _action(CancelDeployment, False),
    methods=["POST"],
    responses=ERROR_RESPONSES,
)
router.add_api_route(
    "/{deployment_ref}/retry",
    _action(RetryDeployment, False),
    methods=["POST"],
    responses=ERROR_RESPONSES,
)

"""Deployment Actuator control-plane service (Epic 8, M8.16) — ADR-0022. Additive.

`DeploymentActuatorService` is the control-plane seam onto the **Deployment Actuator** (the
top-level `actuator/` package — the sole owner of deployment execution). It derives a **read-only**
Epic 7 `DeploymentPlan` from a *published* Agent Version (reusing the frozen `product.deployment`
value objects — Product Runtime is never modified), hands it to the Actuator, and mirrors the
Actuator's typed `deployment.*` facts into the durable Audit Platform so Studio can render an
honest, read-only timeline (ADR-0021 — Studio owns no execution state).

Boundaries held: **no deployment logic lives here** (it lives in `actuator/`); this service only
authorizes (writes via the workspace-scoped `PROJECT_MANAGE`; reads via project scoping — no new
RBAC scope), builds the plan, enforces org/workspace/project isolation, and audits. Only the
**Local** adapter executes; production adapters are declared (ADR-0022 §Provider Model).
"""

from __future__ import annotations

from actuator import (
    ActuatorError,
    ApprovalError,
    DeploymentActuator,
    DeploymentEngine,
    DeploymentEventType,
    DeploymentExecution,
    DeploymentExecutionRepository,
    DeploymentJob,
    DeploymentNotFoundError,
    IllegalTransitionError,
    InfrastructureError,
    InfrastructureRegistry,
    InvalidDeploymentPlanError,
    PlanRef,
    ProductionAdapterUnavailableError,
    build_deployment_plan,
    is_production_environment,
)

from .errors import ConflictError, NotFoundError
from .events import FoundationEventType
from .rbac import Permission
from .repositories import AgentRepository, AgentVersionRepository, ProjectRepository
from .services import _Base

# ── deployment.* (Actuator) → foundation audit event mapping ──────────────────────
_EVENT_MAP: dict[DeploymentEventType, FoundationEventType] = {
    DeploymentEventType.REQUESTED: FoundationEventType.DEPLOYMENT_REQUESTED,
    DeploymentEventType.VALIDATED: FoundationEventType.DEPLOYMENT_VALIDATED,
    DeploymentEventType.PENDING_APPROVAL: FoundationEventType.DEPLOYMENT_PENDING_APPROVAL,
    DeploymentEventType.APPROVED: FoundationEventType.DEPLOYMENT_APPROVED,
    DeploymentEventType.REJECTED: FoundationEventType.DEPLOYMENT_REJECTED,
    DeploymentEventType.QUEUED: FoundationEventType.DEPLOYMENT_QUEUED,
    DeploymentEventType.STARTED: FoundationEventType.DEPLOYMENT_STARTED,
    DeploymentEventType.VERIFYING: FoundationEventType.DEPLOYMENT_VERIFYING,
    DeploymentEventType.COMPLETED: FoundationEventType.DEPLOYMENT_COMPLETED,
    DeploymentEventType.FAILED: FoundationEventType.DEPLOYMENT_FAILED,
    DeploymentEventType.ROLLING_BACK: FoundationEventType.DEPLOYMENT_ROLLING_BACK,
    DeploymentEventType.ROLLED_BACK: FoundationEventType.DEPLOYMENT_ROLLED_BACK,
    DeploymentEventType.CANCELLED: FoundationEventType.DEPLOYMENT_CANCELLED,
    DeploymentEventType.HEALTH_FAILED: FoundationEventType.DEPLOYMENT_HEALTH_FAILED,
}


class DeploymentActuatorService(_Base):
    """The control-plane facade onto the Deployment Actuator. Constructs its own `DeploymentEngine`
    + `DeploymentActuator` over the injected (durable) execution repository, wiring an audit sink
    that mirrors every `deployment.*` fact into the Foundation event stream."""

    def __init__(
        self,
        *,
        projects: ProjectRepository,
        agents: AgentRepository,
        agent_versions: AgentVersionRepository,
        executions: DeploymentExecutionRepository,
        registry: InfrastructureRegistry | None = None,
        **base,
    ) -> None:
        super().__init__(**base)
        self._projects = projects
        self._agents = agents
        self._versions = agent_versions
        self._executions = executions
        self._registry = registry or InfrastructureRegistry()
        self._engine = DeploymentEngine(
            repository=executions,
            registry=self._registry,
            clock=self._clock,
            ids=self._ids,
            emit=self._audit,
        )
        self._actuator = DeploymentActuator(self._engine)

    # ── error translation (actuator typed errors → HTTP-mapped foundation errors) ──
    @staticmethod
    def _run(fn):
        try:
            return fn()
        except DeploymentNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
        except (
            ApprovalError,
            IllegalTransitionError,
            InfrastructureError,
            InvalidDeploymentPlanError,
            ProductionAdapterUnavailableError,
            ActuatorError,
        ) as exc:
            raise ConflictError(str(exc)) from exc

    # ── audit sink (Actuator → durable audit) ─────────────────────────────────────
    def _audit(
        self, event: DeploymentEventType, execution: DeploymentExecution, data: dict
    ) -> None:
        self._emit(
            _EVENT_MAP[event],
            org_id=execution.org_id,
            actor=data.get("actor") or execution.job.requested_by,
            resource=execution.id,
            workspace_id=execution.job.workspace_id,
            project_id=execution.project_id,
            deployment_id=execution.deployment_id,
            environment=execution.job.environment,
            state=execution.state.value,
            **{k: v for k, v in data.items() if k != "actor"},
        )

    # ── plan derivation (read-only reuse of Epic 7 types) ──────────────────────────
    def _load_published(self, org_id, workspace_id, project_id, agent_id):
        project = self._projects.get(org_id, workspace_id, project_id)
        if project is None:
            raise NotFoundError(f"project {project_id!r} not found in workspace {workspace_id!r}")
        agent = self._agents.get(org_id, project_id, agent_id)
        if agent is None:
            raise NotFoundError(f"agent {agent_id!r} not found in project {project_id!r}")
        if agent.published_version <= 0:
            raise ConflictError(
                f"agent {agent_id!r} has no published version; only a published agent deploys"
            )
        version = self._versions.get(org_id, agent_id, agent.published_version)
        if version is None:
            raise NotFoundError(
                f"published version {agent.published_version} of agent {agent_id!r} not found"
            )
        return agent, version

    def _build_plan(self, agent, version, environment, strategy, requires_approval):
        """Delegate to the Actuator's plan adapter (the only place Epic 7's `DeploymentPlan` is
        reused). The Foundation imports **only** `actuator` — never `product` — preserving its
        frozen-Epic import boundary."""
        return build_deployment_plan(
            PlanRef(
                agent_id=agent.id,
                agent_name=agent.name,
                version_id=version.id,
                version_number=version.number,
            ),
            environment=environment,
            strategy=strategy,
            requires_approval=requires_approval,
        )

    def _job_from_plan(
        self, plan, org, ws, project, actor, environment, requires_approval, source_ref
    ):
        return DeploymentJob(
            id=self._ids.new("dpj"),
            deployment_id=plan.deployment_id,
            org_id=org,
            workspace_id=ws,
            project_id=project,
            source_ref=source_ref,
            environment=environment,
            profile=plan.descriptor.profile.value,
            strategy=plan.descriptor.deployment_strategy.value,
            target="local",  # only the Local adapter executes in this build
            requires_approval=requires_approval,
            rollback_mode=plan.descriptor.rollback_policy.mode.value,
            rollback_version=str(plan.package_ref.package_version),
            requested_by=actor,
            requested_at=self._clock.now(),
            plan_content_hash=plan.content_hash,
        )

    def _requires_approval(self, environment, requested) -> bool:
        # Promotion to Production is always gated by approval (ADR-0022 §Environment/Security).
        return bool(requested) or is_production_environment(environment)

    # ── public API ─────────────────────────────────────────────────────────────────
    def plan(
        self,
        org,
        ws,
        project,
        agent_id,
        *,
        environment="development",
        strategy="rolling",
        requires_approval=False,
    ) -> dict:
        """Validate + return a read-only Deployment Plan report (no execution). Reuses Product
        Runtime's declarative types (the planning step ADR-0022 leaves with Product Runtime)."""
        agent, version = self._load_published(org, ws, project, agent_id)
        needs = self._requires_approval(environment, requires_approval)
        plan = self._build_plan(agent, version, environment, strategy, needs)
        return {
            "deployment_id": plan.deployment_id,
            "package_id": plan.package_ref.package_id,
            "content_hash": plan.content_hash,
            "environment": environment,
            "profile": plan.descriptor.profile.value,
            "strategy": plan.descriptor.deployment_strategy.value,
            "target": "local",
            "requires_approval": needs,
            "valid": plan.is_valid,
            "source_ref": version.id,
            "capabilities": self._registry.capabilities("local").as_dict(),
        }

    def submit(
        self,
        actor,
        org,
        ws,
        project,
        agent_id,
        *,
        environment="development",
        strategy="rolling",
        requires_approval=False,
        auto_approve=False,
    ) -> DeploymentExecution:
        self._require(actor, Permission.PROJECT_MANAGE, org_id=org, workspace_id=ws)
        agent, version = self._load_published(org, ws, project, agent_id)
        needs = self._requires_approval(environment, requires_approval)
        plan = self._build_plan(agent, version, environment, strategy, needs)
        job = self._job_from_plan(plan, org, ws, project, actor, environment, needs, version.id)
        return self._run(
            lambda: self._actuator.deploy(job, plan_valid=plan.is_valid, auto_approve=auto_approve)
        )

    def _scoped(self, org, project, ref) -> DeploymentExecution:
        execution = self._executions.get(org, ref)
        if execution is None or execution.project_id != project:
            raise NotFoundError(f"deployment {ref!r} not found in project {project!r}")
        return execution

    def approve(self, actor, org, ws, project, ref, reason="") -> DeploymentExecution:
        self._require(actor, Permission.PROJECT_MANAGE, org_id=org, workspace_id=ws)
        self._scoped(org, project, ref)
        return self._run(lambda: self._actuator.approve(org, ref, actor, reason))

    def reject(self, actor, org, ws, project, ref, reason="") -> DeploymentExecution:
        self._require(actor, Permission.PROJECT_MANAGE, org_id=org, workspace_id=ws)
        self._scoped(org, project, ref)
        return self._run(lambda: self._actuator.reject(org, ref, actor, reason))

    def cancel(self, actor, org, ws, project, ref) -> DeploymentExecution:
        self._require(actor, Permission.PROJECT_MANAGE, org_id=org, workspace_id=ws)
        self._scoped(org, project, ref)
        return self._run(lambda: self._actuator.cancel(org, ref, actor))

    def retry(self, actor, org, ws, project, ref) -> DeploymentExecution:
        self._require(actor, Permission.PROJECT_MANAGE, org_id=org, workspace_id=ws)
        self._scoped(org, project, ref)
        return self._run(lambda: self._actuator.retry(org, ref, actor))

    def rollback(self, actor, org, ws, project, ref, reason="") -> DeploymentExecution:
        self._require(actor, Permission.PROJECT_MANAGE, org_id=org, workspace_id=ws)
        self._scoped(org, project, ref)
        return self._run(lambda: self._actuator.rollback(org, ref, actor, reason))

    # ── reads (project-scoped; a viewer may read, not mutate) ──────────────────────
    def status(self, org, project, ref) -> DeploymentExecution:
        return self._scoped(org, project, ref)

    def history(self, org, project) -> tuple[DeploymentExecution, ...]:
        return self._engine.list_for_project(org, project)

    def timeline(self, org, project, ref) -> dict:
        return self._scoped(org, project, ref).as_history().as_dict()

    def logs(self, org, project, ref) -> tuple[dict, ...]:
        self._scoped(org, project, ref)
        return self._engine.logs(org, ref)

    def health(self, org, project, ref) -> dict:
        self._scoped(org, project, ref)
        return self._engine.live_health(org, ref)

    def targets(self) -> dict:
        return InfrastructureRegistry.describe()

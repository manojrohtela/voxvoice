"""Deployment Actuator application layer (Epic 8, M8.16) — ADR-0022. Additive.

Commands + queries + use cases for the Deployment Actuator vertical slice, in a separate module so
the prior M8.x modules stay untouched (mirrors `knowledge.py`). Use cases coordinate the additive
Foundation `deployment` service (the control-plane seam onto the top-level `actuator/` package) —
no deployment logic is duplicated here. Mutations run inside a Unit of Work so the durable execution
rows + the mirrored audit facts commit atomically. Reads are project-scoped (a viewer may read).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from api.foundation import Foundation

from .authorization import AuthorizationOrchestrator
from .dispatcher import CommandBus, QueryBus
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory
from .use_cases import _UseCaseBase

# ── commands ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SubmitDeployment:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    agent_id: str
    environment: str = "development"
    strategy: str = "rolling"
    requires_approval: bool = False
    auto_approve: bool = False


@dataclass(frozen=True, slots=True)
class _DeploymentOp:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    deployment_ref: str
    reason: str = ""


class ApproveDeployment(_DeploymentOp): ...


class RejectDeployment(_DeploymentOp): ...


class CancelDeployment(_DeploymentOp): ...


class RetryDeployment(_DeploymentOp): ...


class RollbackDeployment(_DeploymentOp): ...


# ── queries ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PlanDeployment:
    org_id: str
    workspace_id: str
    project_id: str
    agent_id: str
    environment: str = "development"
    strategy: str = "rolling"
    requires_approval: bool = False


@dataclass(frozen=True, slots=True)
class GetDeploymentStatus:
    org_id: str
    project_id: str
    deployment_ref: str


@dataclass(frozen=True, slots=True)
class ListDeploymentHistory:
    org_id: str
    project_id: str


@dataclass(frozen=True, slots=True)
class GetDeploymentTimeline:
    org_id: str
    project_id: str
    deployment_ref: str


@dataclass(frozen=True, slots=True)
class GetDeploymentLogs:
    org_id: str
    project_id: str
    deployment_ref: str


@dataclass(frozen=True, slots=True)
class GetDeploymentHealth:
    org_id: str
    project_id: str
    deployment_ref: str


@dataclass(frozen=True, slots=True)
class ListDeploymentTargets:
    org_id: str


# ── use cases (write side) ─────────────────────────────────────────────────────────


class DeploymentUseCases(_UseCaseBase):
    def _stage(self, uow, use_case, *, org_id, actor, **data):
        uow.stage(
            ApplicationEvent(
                id=self._ids.new(PREFIX_APP_EVENT),
                type=ApplicationEventType.DEPLOYMENT_CHANGED,
                ts=self._clock.now(),
                use_case=use_case,
                outcome="completed",
                org_id=org_id,
                actor=actor,
                data=dict(data),
            )
        )

    def submit(self, cmd: SubmitDeployment) -> dict:
        with self._uow.new() as uow:
            execution = self._f.deployment.submit(
                cmd.actor_user_id,
                cmd.org_id,
                cmd.workspace_id,
                cmd.project_id,
                cmd.agent_id,
                environment=cmd.environment,
                strategy=cmd.strategy,
                requires_approval=cmd.requires_approval,
                auto_approve=cmd.auto_approve,
            )
            self._stage(
                uow,
                "submit_deployment",
                org_id=cmd.org_id,
                actor=cmd.actor_user_id,
                deployment=execution.id,
                state=execution.state.value,
            )
        return asdict(execution.as_view())

    def _commit(self, cmd: _DeploymentOp, execution, use_case) -> dict:
        with self._uow.new() as uow:
            self._stage(
                uow,
                use_case,
                org_id=cmd.org_id,
                actor=cmd.actor_user_id,
                deployment=execution.id,
                state=execution.state.value,
            )
        return asdict(execution.as_view())

    def approve(self, cmd: ApproveDeployment) -> dict:
        execution = self._f.deployment.approve(
            cmd.actor_user_id,
            cmd.org_id,
            cmd.workspace_id,
            cmd.project_id,
            cmd.deployment_ref,
            cmd.reason,
        )
        return self._commit(cmd, execution, "approve_deployment")

    def reject(self, cmd: RejectDeployment) -> dict:
        execution = self._f.deployment.reject(
            cmd.actor_user_id,
            cmd.org_id,
            cmd.workspace_id,
            cmd.project_id,
            cmd.deployment_ref,
            cmd.reason,
        )
        return self._commit(cmd, execution, "reject_deployment")

    def cancel(self, cmd: CancelDeployment) -> dict:
        execution = self._f.deployment.cancel(
            cmd.actor_user_id,
            cmd.org_id,
            cmd.workspace_id,
            cmd.project_id,
            cmd.deployment_ref,
        )
        return self._commit(cmd, execution, "cancel_deployment")

    def retry(self, cmd: RetryDeployment) -> dict:
        execution = self._f.deployment.retry(
            cmd.actor_user_id,
            cmd.org_id,
            cmd.workspace_id,
            cmd.project_id,
            cmd.deployment_ref,
        )
        return self._commit(cmd, execution, "retry_deployment")

    def rollback(self, cmd: RollbackDeployment) -> dict:
        execution = self._f.deployment.rollback(
            cmd.actor_user_id,
            cmd.org_id,
            cmd.workspace_id,
            cmd.project_id,
            cmd.deployment_ref,
            cmd.reason,
        )
        return self._commit(cmd, execution, "rollback_deployment")


# ── queries (read side) ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeploymentQueries:
    foundation: Foundation

    def plan(self, q: PlanDeployment) -> dict:
        return self.foundation.deployment.plan(
            q.org_id,
            q.workspace_id,
            q.project_id,
            q.agent_id,
            environment=q.environment,
            strategy=q.strategy,
            requires_approval=q.requires_approval,
        )

    def status(self, q: GetDeploymentStatus) -> dict:
        return asdict(
            self.foundation.deployment.status(q.org_id, q.project_id, q.deployment_ref).as_view()
        )

    def history(self, q: ListDeploymentHistory) -> list[dict]:
        return [
            e.as_summary().as_dict()
            for e in self.foundation.deployment.history(q.org_id, q.project_id)
        ]

    def timeline(self, q: GetDeploymentTimeline) -> dict:
        return self.foundation.deployment.timeline(q.org_id, q.project_id, q.deployment_ref)

    def logs(self, q: GetDeploymentLogs) -> dict:
        return {
            "logs": list(self.foundation.deployment.logs(q.org_id, q.project_id, q.deployment_ref))
        }

    def health(self, q: GetDeploymentHealth) -> dict:
        return self.foundation.deployment.health(q.org_id, q.project_id, q.deployment_ref)

    def targets(self, q: ListDeploymentTargets) -> dict:
        return self.foundation.deployment.targets()


def build_and_register(
    *,
    foundation,
    authz: AuthorizationOrchestrator,
    uow_factory: UnitOfWorkFactory,
    ids,
    clock,
    command_bus: CommandBus,
    query_bus: QueryBus,
) -> None:
    """Wire the M8.16 use cases + handlers into the existing buses (additive)."""
    uc = DeploymentUseCases(
        foundation=foundation, authz=authz, uow_factory=uow_factory, ids=ids, clock=clock
    )
    q = DeploymentQueries(foundation=foundation)
    command_bus.register(SubmitDeployment, uc.submit)
    command_bus.register(ApproveDeployment, uc.approve)
    command_bus.register(RejectDeployment, uc.reject)
    command_bus.register(CancelDeployment, uc.cancel)
    command_bus.register(RetryDeployment, uc.retry)
    command_bus.register(RollbackDeployment, uc.rollback)
    query_bus.register(PlanDeployment, q.plan)
    query_bus.register(GetDeploymentStatus, q.status)
    query_bus.register(ListDeploymentHistory, q.history)
    query_bus.register(GetDeploymentTimeline, q.timeline)
    query_bus.register(GetDeploymentLogs, q.logs)
    query_bus.register(GetDeploymentHealth, q.health)
    query_bus.register(ListDeploymentTargets, q.targets)

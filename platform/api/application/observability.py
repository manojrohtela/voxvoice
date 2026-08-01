"""Observability application layer (Epic 8, M8.17) — additive.

Queries (metrics / logs / traces / health / usage / cost / performance / dashboards / alerts) and
the alert-rule commands (create/update/delete), in a separate module so prior M8.x modules stay
untouched (mirrors `deployment.py`). Reads dispatch to the Foundation's `ObservabilityService` —
a passive read-model over the durable audit stream; alert-rule mutations run in a Unit of Work
(durable config row + the audit fact commit atomically). No monitoring logic here — pure wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from api.foundation import Foundation, LogLevel, LogQuery

from .authorization import AuthorizationOrchestrator
from .dispatcher import CommandBus, QueryBus
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory
from .use_cases import _UseCaseBase

# ── commands (alert-rule config — the only Observability writes) ───────────────────


@dataclass(frozen=True, slots=True)
class CreateAlertRule:
    actor_user_id: str
    org_id: str
    name: str
    metric_key: str
    comparator: str
    threshold: float
    window_seconds: float = 3600.0
    severity: str = "warning"
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateAlertRule:
    actor_user_id: str
    org_id: str
    rule_id: str
    changes: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeleteAlertRule:
    actor_user_id: str
    org_id: str
    rule_id: str


# ── queries (read-model projections) ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Scope:
    org_id: str
    workspace_id: str | None = None
    project_id: str | None = None
    since: float | None = None
    until: float | None = None


@dataclass(frozen=True, slots=True)
class GetMetricReport(_Scope): ...


@dataclass(frozen=True, slots=True)
class GetMetricDefinitions:
    org_id: str


@dataclass(frozen=True, slots=True)
class GetMetricSeries:
    org_id: str
    metric_key: str
    workspace_id: str | None = None
    project_id: str | None = None
    bucket_seconds: float = 60.0
    since: float | None = None
    until: float | None = None


@dataclass(frozen=True, slots=True)
class GetLogs:
    org_id: str
    workspace_id: str | None = None
    project_id: str | None = None
    level: str | None = None
    category: str | None = None
    actor: str | None = None
    since: float | None = None
    until: float | None = None
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class GetTraces(_Scope):
    limit: int = 50


@dataclass(frozen=True, slots=True)
class GetTrace:
    org_id: str
    trace_id: str
    workspace_id: str | None = None
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class GetHealth(_Scope): ...


@dataclass(frozen=True, slots=True)
class GetHealthHistory:
    org_id: str
    component: str
    workspace_id: str | None = None
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class GetUsage(_Scope): ...


@dataclass(frozen=True, slots=True)
class GetCost(_Scope): ...


@dataclass(frozen=True, slots=True)
class GetPerformance(_Scope): ...


@dataclass(frozen=True, slots=True)
class GetDashboard:
    org_id: str
    kind: str = "organization"
    workspace_id: str | None = None
    project_id: str | None = None
    agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class ListAlertRules:
    org_id: str


@dataclass(frozen=True, slots=True)
class EvaluateAlerts:
    org_id: str
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class GetAlertHistory:
    org_id: str
    rule_id: str


# ── use cases (write side) ─────────────────────────────────────────────────────────


class ObservabilityUseCases(_UseCaseBase):
    def _stage(self, uow, use_case, *, org_id, actor, **data):
        uow.stage(
            ApplicationEvent(
                id=self._ids.new(PREFIX_APP_EVENT),
                type=ApplicationEventType.OBSERVABILITY_ALERT_CHANGED,
                ts=self._clock.now(),
                use_case=use_case,
                outcome="completed",
                org_id=org_id,
                actor=actor,
                data=dict(data),
            )
        )

    def create_rule(self, cmd: CreateAlertRule) -> dict:
        with self._uow.new() as uow:
            rule = self._f.observability.alerts.create_rule(
                cmd.actor_user_id,
                cmd.org_id,
                name=cmd.name,
                metric_key=cmd.metric_key,
                comparator=cmd.comparator,
                threshold=cmd.threshold,
                window_seconds=cmd.window_seconds,
                severity=cmd.severity,
                project_id=cmd.project_id,
            )
            self._stage(
                uow, "create_alert_rule", org_id=cmd.org_id, actor=cmd.actor_user_id, rule=rule.id
            )
        return rule.as_dict()

    def update_rule(self, cmd: UpdateAlertRule) -> dict:
        with self._uow.new() as uow:
            rule = self._f.observability.alerts.update_rule(
                cmd.actor_user_id, cmd.org_id, cmd.rule_id, **cmd.changes
            )
            self._stage(
                uow, "update_alert_rule", org_id=cmd.org_id, actor=cmd.actor_user_id, rule=rule.id
            )
        return rule.as_dict()

    def delete_rule(self, cmd: DeleteAlertRule) -> dict:
        with self._uow.new() as uow:
            rule = self._f.observability.alerts.delete_rule(
                cmd.actor_user_id, cmd.org_id, cmd.rule_id
            )
            self._stage(
                uow, "delete_alert_rule", org_id=cmd.org_id, actor=cmd.actor_user_id, rule=rule.id
            )
        return rule.as_dict()


# ── queries (read side) ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ObservabilityQueries:
    foundation: Foundation

    @property
    def _o(self):
        return self.foundation.observability

    def metric_report(self, q: GetMetricReport) -> dict:
        return self._o.metrics.report(
            q.org_id,
            workspace_id=q.workspace_id,
            project_id=q.project_id,
            since=q.since,
            until=q.until,
        ).as_dict()

    def metric_definitions(self, q: GetMetricDefinitions) -> dict:
        return {"metrics": [d.as_dict() for d in self._o.metric_definitions()]}

    def metric_series(self, q: GetMetricSeries) -> dict:
        return self._o.metrics.series(
            q.org_id,
            q.metric_key,
            workspace_id=q.workspace_id,
            project_id=q.project_id,
            bucket_seconds=q.bucket_seconds,
            since=q.since,
            until=q.until,
        ).as_dict()

    def logs(self, q: GetLogs) -> dict:
        from api.foundation import LogCategory

        return self._o.logs.query(
            LogQuery(
                org_id=q.org_id,
                workspace_id=q.workspace_id,
                project_id=q.project_id,
                level=LogLevel(q.level) if q.level else None,
                category=LogCategory(q.category) if q.category else None,
                actor=q.actor,
                since=q.since,
                until=q.until,
                limit=q.limit,
                offset=q.offset,
            )
        ).as_dict()

    def traces(self, q: GetTraces) -> dict:
        return self._o.traces.traces(
            q.org_id, workspace_id=q.workspace_id, project_id=q.project_id, limit=q.limit
        ).as_dict()

    def trace(self, q: GetTrace) -> dict:
        return self._o.traces.trace(
            q.org_id, q.trace_id, workspace_id=q.workspace_id, project_id=q.project_id
        ).as_dict()

    def health(self, q: GetHealth) -> dict:
        return self._o.health.snapshot(
            q.org_id, workspace_id=q.workspace_id, project_id=q.project_id, since=q.since
        ).as_dict()

    def health_history(self, q: GetHealthHistory) -> dict:
        return self._o.health.history(
            q.org_id, q.component, workspace_id=q.workspace_id, project_id=q.project_id
        ).as_dict()

    def usage(self, q: GetUsage) -> dict:
        return self._o.usage.summary(
            q.org_id, workspace_id=q.workspace_id, project_id=q.project_id, since=q.since
        )

    def cost(self, q: GetCost) -> dict:
        return self._o.cost.estimate(
            q.org_id, workspace_id=q.workspace_id, project_id=q.project_id, since=q.since
        )

    def performance(self, q: GetPerformance) -> dict:
        return self._o.performance.summary(
            q.org_id, workspace_id=q.workspace_id, project_id=q.project_id, since=q.since
        )

    def dashboard(self, q: GetDashboard) -> dict:
        d = self._o.dashboards
        match q.kind:
            case "workspace":
                return d.workspace(q.org_id, q.workspace_id)
            case "project":
                return d.project(q.org_id, q.project_id)
            case "agent":
                return d.agent(q.org_id, q.project_id, q.agent_id)
            case "deployment":
                return d.deployment(q.org_id, q.project_id)
            case "knowledge":
                return d.knowledge(q.org_id, q.project_id)
            case "provider":
                return d.provider(q.org_id)
            case "system":
                return d.system(q.org_id)
            case _:
                return d.organization(q.org_id)

    def alert_rules(self, q: ListAlertRules) -> list[dict]:
        return [r.as_dict() for r in self._o.alerts.list_rules(q.org_id)]

    def evaluate_alerts(self, q: EvaluateAlerts) -> dict:
        return {
            "alerts": [
                e.as_dict() for e in self._o.alerts.evaluate(q.org_id, project_id=q.project_id)
            ]
        }

    def alert_history(self, q: GetAlertHistory) -> dict:
        return self._o.alerts.history(q.org_id, q.rule_id).as_dict()


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
    """Wire the M8.17 use cases + handlers into the existing buses (additive)."""
    uc = ObservabilityUseCases(
        foundation=foundation, authz=authz, uow_factory=uow_factory, ids=ids, clock=clock
    )
    q = ObservabilityQueries(foundation=foundation)
    command_bus.register(CreateAlertRule, uc.create_rule)
    command_bus.register(UpdateAlertRule, uc.update_rule)
    command_bus.register(DeleteAlertRule, uc.delete_rule)
    query_bus.register(GetMetricReport, q.metric_report)
    query_bus.register(GetMetricDefinitions, q.metric_definitions)
    query_bus.register(GetMetricSeries, q.metric_series)
    query_bus.register(GetLogs, q.logs)
    query_bus.register(GetTraces, q.traces)
    query_bus.register(GetTrace, q.trace)
    query_bus.register(GetHealth, q.health)
    query_bus.register(GetHealthHistory, q.health_history)
    query_bus.register(GetUsage, q.usage)
    query_bus.register(GetCost, q.cost)
    query_bus.register(GetPerformance, q.performance)
    query_bus.register(GetDashboard, q.dashboard)
    query_bus.register(ListAlertRules, q.alert_rules)
    query_bus.register(EvaluateAlerts, q.evaluate_alerts)
    query_bus.register(GetAlertHistory, q.alert_history)

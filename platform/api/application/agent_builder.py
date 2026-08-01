"""Agent Builder application layer (M8.10) — additive.

New commands, queries, use cases and handler registration for the Agent Builder vertical
slice, in a separate module so the M8.3 use-case/command/query modules stay untouched
(mirrors `project_lifecycle.py`). Use cases coordinate the additive Foundation
`agent_builder` service — no domain logic is duplicated. Authorization, transaction
boundaries and audit are handled exactly as the M8.6–M8.8 use cases do (open a Unit of Work,
call the Foundation service which validates + persists + emits its domain event, map to a
view DTO, stage the application event, commit).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from api.foundation import (
    AgentConfig,
    AgentSummaryView,
    AgentVersionView,
    AgentView,
    Foundation,
)

from .activity import ActivityFilter
from .authorization import AuthorizationOrchestrator
from .dispatcher import CommandBus, QueryBus
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory
from .use_cases import _UseCaseBase

# ── commands ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CreateAgent:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    name: str
    description: str = ""
    icon: str = ""
    category: str = ""
    tags: tuple[str, ...] = ()
    language: str = "en"
    greeting: str = ""
    config: dict | None = None


@dataclass(frozen=True, slots=True)
class UpdateAgent:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    agent_id: str
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    category: str | None = None
    tags: tuple[str, ...] | None = None
    language: str | None = None
    greeting: str | None = None
    config_patch: dict | None = None
    metadata: dict | None = None


@dataclass(frozen=True, slots=True)
class _AgentOp:
    """Shared shape for the simple agent operations keyed by id."""

    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    agent_id: str


class ArchiveAgent(_AgentOp): ...
class RestoreAgent(_AgentOp): ...
class DeleteAgent(_AgentOp): ...
class UnpublishAgent(_AgentOp): ...


@dataclass(frozen=True, slots=True)
class DuplicateAgent(_AgentOp):
    name: str | None = None


@dataclass(frozen=True, slots=True)
class PublishAgent(_AgentOp):
    description: str = ""


@dataclass(frozen=True, slots=True)
class RollbackAgent(_AgentOp):
    target_number: int = 0


# ── queries ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetAgent:
    org_id: str
    project_id: str
    agent_id: str


@dataclass(frozen=True, slots=True)
class ListAgents:
    org_id: str
    project_id: str
    include_archived: bool = True


@dataclass(frozen=True, slots=True)
class SearchAgents:
    org_id: str
    project_id: str
    term: str


@dataclass(frozen=True, slots=True)
class ListAgentVersions:
    org_id: str
    agent_id: str


@dataclass(frozen=True, slots=True)
class GetAgentVersion:
    org_id: str
    agent_id: str
    number: int


@dataclass(frozen=True, slots=True)
class CompareAgentVersions:
    org_id: str
    agent_id: str
    from_number: int
    to_number: int


@dataclass(frozen=True, slots=True)
class GetAgentActivity:
    org_id: str
    agent_id: str
    filter: ActivityFilter = field(default_factory=ActivityFilter)


# ── use cases (write side) ─────────────────────────────────────────────────────────


class AgentBuilderUseCases(_UseCaseBase):
    """Write side of the Agent Builder — one Unit of Work per mutation."""

    def _stage(self, uow, type_, use_case, *, org_id, actor, **data):
        uow.stage(
            ApplicationEvent(
                id=self._ids.new(PREFIX_APP_EVENT), type=type_, ts=self._clock.now(),
                use_case=use_case, outcome="completed", org_id=org_id, actor=actor,
                data=dict(data),
            )
        )

    def create_agent(self, cmd: CreateAgent) -> AgentView:
        with self._uow.new() as uow:
            agent = self._f.agent_builder.create_agent(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                name=cmd.name, description=cmd.description, icon=cmd.icon,
                category=cmd.category, tags=tuple(cmd.tags), language=cmd.language,
                greeting=cmd.greeting,
                config=AgentConfig.from_dict(cmd.config) if cmd.config else None,
            )
            self._stage(
                uow, ApplicationEventType.AGENT_CREATED, "create_agent",
                org_id=cmd.org_id, actor=cmd.actor_user_id, agent_id=agent.id,
            )
        return AgentView.of(agent)

    def update_agent(self, cmd: UpdateAgent) -> AgentView:
        with self._uow.new() as uow:
            agent = self._f.agent_builder.update_agent(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.agent_id,
                name=cmd.name, description=cmd.description, icon=cmd.icon,
                category=cmd.category,
                tags=tuple(cmd.tags) if cmd.tags is not None else None,
                language=cmd.language, greeting=cmd.greeting, config_patch=cmd.config_patch,
                metadata=cmd.metadata,
            )
            self._stage(
                uow, ApplicationEventType.AGENT_UPDATED, "update_agent",
                org_id=cmd.org_id, actor=cmd.actor_user_id, agent_id=agent.id,
            )
        return AgentView.of(agent)

    def archive_agent(self, cmd: ArchiveAgent) -> AgentView:
        return self._simple(
            cmd, self._f.agent_builder.archive_agent,
            ApplicationEventType.AGENT_ARCHIVED, "archive_agent",
        )

    def restore_agent(self, cmd: RestoreAgent) -> AgentView:
        return self._simple(
            cmd, self._f.agent_builder.restore_agent,
            ApplicationEventType.AGENT_RESTORED, "restore_agent",
        )

    def delete_agent(self, cmd: DeleteAgent) -> AgentView:
        return self._simple(
            cmd, self._f.agent_builder.delete_agent,
            ApplicationEventType.AGENT_DELETED, "delete_agent",
        )

    def unpublish_agent(self, cmd: UnpublishAgent) -> AgentView:
        return self._simple(
            cmd, self._f.agent_builder.unpublish_agent,
            ApplicationEventType.AGENT_UNPUBLISHED, "unpublish_agent",
        )

    def _simple(self, cmd: _AgentOp, fn, event_type, use_case) -> AgentView:
        with self._uow.new() as uow:
            agent = fn(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.agent_id
            )
            self._stage(
                uow, event_type, use_case, org_id=cmd.org_id, actor=cmd.actor_user_id,
                agent_id=agent.id,
            )
        return AgentView.of(agent)

    def duplicate_agent(self, cmd: DuplicateAgent) -> AgentView:
        with self._uow.new() as uow:
            agent = self._f.agent_builder.duplicate_agent(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                cmd.agent_id, name=cmd.name,
            )
            self._stage(
                uow, ApplicationEventType.AGENT_DUPLICATED, "duplicate_agent",
                org_id=cmd.org_id, actor=cmd.actor_user_id, agent_id=agent.id,
            )
        return AgentView.of(agent)

    def publish_agent(self, cmd: PublishAgent) -> AgentView:
        with self._uow.new() as uow:
            agent, _version = self._f.agent_builder.publish_agent(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                cmd.agent_id, description=cmd.description,
            )
            self._stage(
                uow, ApplicationEventType.AGENT_PUBLISHED, "publish_agent",
                org_id=cmd.org_id, actor=cmd.actor_user_id, agent_id=agent.id,
                version=agent.version,
            )
        return AgentView.of(agent)

    def rollback_agent(self, cmd: RollbackAgent) -> AgentView:
        with self._uow.new() as uow:
            agent, _version = self._f.agent_builder.rollback_agent(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                cmd.agent_id, cmd.target_number,
            )
            self._stage(
                uow, ApplicationEventType.AGENT_ROLLED_BACK, "rollback_agent",
                org_id=cmd.org_id, actor=cmd.actor_user_id, agent_id=agent.id,
                version=agent.version, rollback_target=cmd.target_number,
            )
        return AgentView.of(agent)


# ── queries (read side) ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AgentBuilderQueries:
    """Read side — agents, versions, comparison, and durable per-agent activity."""

    foundation: Foundation

    def get(self, query: GetAgent) -> dict:
        from dataclasses import asdict

        agent = self.foundation.agent_builder.get_agent(
            query.org_id, query.project_id, query.agent_id
        )
        return asdict(AgentView.of(agent))

    def list(self, query: ListAgents) -> list[dict]:
        from dataclasses import asdict

        return [
            asdict(AgentSummaryView.of(a))
            for a in self.foundation.agent_builder.list_agents(
                query.org_id, query.project_id, include_archived=query.include_archived
            )
        ]

    def search(self, query: SearchAgents) -> list[dict]:
        from dataclasses import asdict

        return [
            asdict(AgentSummaryView.of(a))
            for a in self.foundation.agent_builder.search_agents(
                query.org_id, query.project_id, query.term
            )
        ]

    def versions(self, query: ListAgentVersions) -> list[dict]:
        from dataclasses import asdict

        return [
            asdict(AgentVersionView.of(v))
            for v in self.foundation.agent_builder.list_versions(
                query.org_id, query.agent_id
            )
        ]

    def version(self, query: GetAgentVersion) -> dict:
        from dataclasses import asdict

        v = self.foundation.agent_builder.get_version(
            query.org_id, query.agent_id, query.number
        )
        return asdict(AgentVersionView.of(v))

    def compare(self, query: CompareAgentVersions) -> dict:
        return self.foundation.agent_builder.compare_versions(
            query.org_id, query.agent_id, query.from_number, query.to_number
        )

    def activity(self, query: GetAgentActivity) -> dict:
        # The agent's history is a projection over the durable audit stream (M8.9),
        # filtered to this agent id — versioning reuses the one audit platform.
        return self.foundation.activity.resource_activity(
            query.org_id, query.agent_id, **query.filter.kwargs()
        )


# ── registration ───────────────────────────────────────────────────────────────────


def build_and_register(
    *,
    foundation: Foundation,
    authz: AuthorizationOrchestrator,
    uow_factory: UnitOfWorkFactory,
    ids,
    clock,
    command_bus: CommandBus,
    query_bus: QueryBus,
) -> None:
    """Wire the M8.10 use cases + handlers into the existing buses (additive)."""
    uc = AgentBuilderUseCases(
        foundation=foundation, authz=authz, uow_factory=uow_factory, ids=ids, clock=clock
    )
    q = AgentBuilderQueries(foundation=foundation)
    command_bus.register(CreateAgent, uc.create_agent)
    command_bus.register(UpdateAgent, uc.update_agent)
    command_bus.register(ArchiveAgent, uc.archive_agent)
    command_bus.register(RestoreAgent, uc.restore_agent)
    command_bus.register(DeleteAgent, uc.delete_agent)
    command_bus.register(DuplicateAgent, uc.duplicate_agent)
    command_bus.register(PublishAgent, uc.publish_agent)
    command_bus.register(UnpublishAgent, uc.unpublish_agent)
    command_bus.register(RollbackAgent, uc.rollback_agent)
    query_bus.register(GetAgent, q.get)
    query_bus.register(ListAgents, q.list)
    query_bus.register(SearchAgents, q.search)
    query_bus.register(ListAgentVersions, q.versions)
    query_bus.register(GetAgentVersion, q.version)
    query_bus.register(CompareAgentVersions, q.compare)
    query_bus.register(GetAgentActivity, q.activity)

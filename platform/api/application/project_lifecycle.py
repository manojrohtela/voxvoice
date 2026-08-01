"""Project Lifecycle application layer (M8.8) — additive.

New commands, queries, use cases, and handler registration for the Project Lifecycle vertical
slice, delivered as a separate module so the M8.3 `use_cases.py`/`commands.py`/`queries.py` are
left untouched (mirrors `org_lifecycle.py`/`workspace_lifecycle.py`). Use cases coordinate the
additive Foundation services (`project_lifecycle`/`project_members`/`project_environments`) —
no domain logic is duplicated. Authorization, transaction boundaries, and audit are handled
exactly as the M8.3/M8.6/M8.7 use cases do.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from api.foundation import Foundation, ProjectMemberView, ProjectRole, ProjectView

from .activity import ActivityFilter
from .authorization import AuthorizationOrchestrator
from .dispatcher import CommandBus, QueryBus
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory
from .use_cases import _UseCaseBase

# ── commands ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UpdateProject:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    metadata: dict | None = None


@dataclass(frozen=True, slots=True)
class ArchiveProject:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str


@dataclass(frozen=True, slots=True)
class RestoreProject:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str


@dataclass(frozen=True, slots=True)
class AddProjectMember:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    user_id: str
    role: ProjectRole = ProjectRole.MEMBER


@dataclass(frozen=True, slots=True)
class RemoveProjectMember:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class SwitchProjectEnvironment:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    environment: str


# ── queries ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ListProjectMembers:
    org_id: str
    project_id: str


@dataclass(frozen=True, slots=True)
class GetProjectActivity:
    org_id: str
    workspace_id: str
    project_id: str
    # M8.9 — additive pagination/filter/time-range (defaults reproduce prior behaviour).
    filter: ActivityFilter = field(default_factory=ActivityFilter)


@dataclass(frozen=True, slots=True)
class GetProjectEnvironment:
    actor_user_id: str
    org_id: str
    project_id: str


@dataclass(frozen=True, slots=True)
class SearchProjects:
    org_id: str
    workspace_id: str
    term: str


# ── use cases ────────────────────────────────────────────────────────────────────


class ProjectLifecycleUseCases(_UseCaseBase):
    """Write side of the Project Lifecycle."""

    def _stage(self, uow, type_, use_case, *, org_id, actor, **data):
        uow.stage(
            ApplicationEvent(
                id=self._ids.new(PREFIX_APP_EVENT), type=type_, ts=self._clock.now(),
                use_case=use_case, outcome="completed", org_id=org_id, actor=actor,
                data=dict(data),
            )
        )

    def update_project(self, cmd: UpdateProject) -> ProjectView:
        with self._uow.new() as uow:
            p = self._f.project_lifecycle.update_project(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                name=cmd.name, description=cmd.description, icon=cmd.icon,
                metadata=cmd.metadata,
            )
            self._stage(
                uow, ApplicationEventType.PROJECT_UPDATED, "update_project",
                org_id=cmd.org_id, actor=cmd.actor_user_id, project_id=cmd.project_id,
            )
        return ProjectView.of(p)

    def archive_project(self, cmd: ArchiveProject) -> ProjectView:
        with self._uow.new() as uow:
            p = self._f.project_lifecycle.archive_project(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id
            )
            self._stage(
                uow, ApplicationEventType.PROJECT_ARCHIVED, "archive_project",
                org_id=cmd.org_id, actor=cmd.actor_user_id, project_id=cmd.project_id,
            )
        return ProjectView.of(p)

    def restore_project(self, cmd: RestoreProject) -> ProjectView:
        with self._uow.new() as uow:
            p = self._f.project_lifecycle.restore_project(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id
            )
            self._stage(
                uow, ApplicationEventType.PROJECT_RESTORED, "restore_project",
                org_id=cmd.org_id, actor=cmd.actor_user_id, project_id=cmd.project_id,
            )
        return ProjectView.of(p)

    def add_member(self, cmd: AddProjectMember) -> ProjectMemberView:
        with self._uow.new() as uow:
            pm = self._f.project_members.add_member(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id,
                cmd.user_id, role=cmd.role,
            )
            self._stage(
                uow, ApplicationEventType.PROJECT_MEMBER_ADDED, "add_project_member",
                org_id=cmd.org_id, actor=cmd.actor_user_id,
                project_id=cmd.project_id, user_id=cmd.user_id,
            )
        return ProjectMemberView.of(pm)

    def remove_member(self, cmd: RemoveProjectMember) -> ProjectMemberView:
        with self._uow.new() as uow:
            pm = self._f.project_members.remove_member(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.user_id
            )
            self._stage(
                uow, ApplicationEventType.PROJECT_MEMBER_REMOVED, "remove_project_member",
                org_id=cmd.org_id, actor=cmd.actor_user_id,
                project_id=cmd.project_id, user_id=cmd.user_id,
            )
        return ProjectMemberView.of(pm)

    def switch_environment(self, cmd: SwitchProjectEnvironment) -> dict:
        with self._uow.new():
            env = self._f.project_environments.set_environment(
                cmd.org_id, cmd.actor_user_id, cmd.workspace_id, cmd.project_id,
                cmd.environment,
            )
        return {"project_id": cmd.project_id, "environment": env}


@dataclass(frozen=True, slots=True)
class ProjectLifecycleQueries:
    """Read side — members, activity (summary + audit feed filtered to the project), the
    per-user environment selector, and search. No transactions, no mutation."""

    foundation: Foundation

    def members(self, query: ListProjectMembers) -> list[dict]:
        return [
            asdict(ProjectMemberView.of(pm))
            for pm in self.foundation.project_members.list_members(
                query.org_id, query.project_id
            )
        ]

    def activity(self, query: GetProjectActivity) -> dict:
        # Summary is domain state; the feed is a durable projection over the audit stream
        # (M8.9), filtered to this project.
        summary = self.foundation.project_lifecycle.activity_summary(
            query.org_id, query.workspace_id, query.project_id
        )
        page = self.foundation.activity.project_activity(
            query.org_id, query.workspace_id, query.project_id, **query.filter.kwargs()
        )
        return {"summary": summary, **page}

    def environment(self, query: GetProjectEnvironment) -> dict:
        return self.foundation.project_environments.selector(
            query.org_id, query.actor_user_id, query.project_id
        )

    def search(self, query: SearchProjects) -> list[dict]:
        return [
            asdict(ProjectView.of(p))
            for p in self.foundation.project_lifecycle.search(
                query.org_id, query.workspace_id, query.term
            )
        ]


# ── registration (dependency injection) ──────────────────────────────────────────


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
    """Wire the M8.8 use cases + handlers into the existing buses (additive)."""
    uc = ProjectLifecycleUseCases(
        foundation=foundation, authz=authz, uow_factory=uow_factory, ids=ids, clock=clock
    )
    q = ProjectLifecycleQueries(foundation=foundation)
    command_bus.register(UpdateProject, uc.update_project)
    command_bus.register(ArchiveProject, uc.archive_project)
    command_bus.register(RestoreProject, uc.restore_project)
    command_bus.register(AddProjectMember, uc.add_member)
    command_bus.register(RemoveProjectMember, uc.remove_member)
    command_bus.register(SwitchProjectEnvironment, uc.switch_environment)
    query_bus.register(ListProjectMembers, q.members)
    query_bus.register(GetProjectActivity, q.activity)
    query_bus.register(GetProjectEnvironment, q.environment)
    query_bus.register(SearchProjects, q.search)

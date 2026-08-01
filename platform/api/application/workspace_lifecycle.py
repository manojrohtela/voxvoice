"""Workspace Lifecycle application layer (M8.7) — additive.

New commands, queries, use cases, and handler registration for the Workspace Lifecycle
vertical slice, delivered as a separate module so the M8.3 `use_cases.py`/`commands.py`/
`queries.py` are left untouched (mirrors `org_lifecycle.py`). Use cases coordinate the
additive Foundation services (`workspace_lifecycle`/`workspace_members`/
`workspace_navigation`) — no domain logic is duplicated. Authorization, transaction
boundaries, and audit are handled exactly as the M8.3/M8.6 use cases do.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from api.foundation import Foundation, WorkspaceMemberView, WorkspaceView, WsRole

from .activity import ActivityFilter
from .authorization import AuthorizationOrchestrator
from .dispatcher import CommandBus, QueryBus
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory
from .use_cases import _UseCaseBase

# ── commands ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UpdateWorkspace:
    actor_user_id: str
    org_id: str
    workspace_id: str
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    metadata: dict | None = None


@dataclass(frozen=True, slots=True)
class ArchiveWorkspace:
    actor_user_id: str
    org_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class RestoreWorkspace:
    actor_user_id: str
    org_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class AddWorkspaceMember:
    actor_user_id: str
    org_id: str
    workspace_id: str
    user_id: str
    role: WsRole = WsRole.MEMBER


@dataclass(frozen=True, slots=True)
class RemoveWorkspaceMember:
    actor_user_id: str
    org_id: str
    workspace_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class SetDefaultWorkspace:
    actor_user_id: str
    org_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class FavoriteWorkspace:
    actor_user_id: str
    org_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class RecordRecentWorkspace:
    actor_user_id: str
    org_id: str
    workspace_id: str


# ── queries ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ListWorkspaceMembers:
    org_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class GetWorkspaceActivity:
    org_id: str
    workspace_id: str
    # M8.9 — additive pagination/filter/time-range (defaults reproduce prior behaviour).
    filter: ActivityFilter = field(default_factory=ActivityFilter)


@dataclass(frozen=True, slots=True)
class GetWorkspaceNavigation:
    org_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class SearchWorkspaces:
    org_id: str
    term: str


# ── use cases ────────────────────────────────────────────────────────────────────


class WorkspaceLifecycleUseCases(_UseCaseBase):
    """Write side of the Workspace Lifecycle."""

    def _stage(self, uow, type_, use_case, *, org_id, actor, **data):
        uow.stage(
            ApplicationEvent(
                id=self._ids.new(PREFIX_APP_EVENT), type=type_, ts=self._clock.now(),
                use_case=use_case, outcome="completed", org_id=org_id, actor=actor,
                data=dict(data),
            )
        )

    def update_workspace(self, cmd: UpdateWorkspace) -> WorkspaceView:
        with self._uow.new() as uow:
            ws = self._f.workspace_lifecycle.update_workspace(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, name=cmd.name,
                description=cmd.description, icon=cmd.icon, color=cmd.color,
                metadata=cmd.metadata,
            )
            self._stage(
                uow, ApplicationEventType.WORKSPACE_UPDATED, "update_workspace",
                org_id=cmd.org_id, actor=cmd.actor_user_id, workspace_id=cmd.workspace_id,
            )
        return WorkspaceView.of(ws)

    def archive_workspace(self, cmd: ArchiveWorkspace) -> WorkspaceView:
        with self._uow.new() as uow:
            ws = self._f.workspace_lifecycle.archive_workspace(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id
            )
            self._stage(
                uow, ApplicationEventType.WORKSPACE_ARCHIVED, "archive_workspace",
                org_id=cmd.org_id, actor=cmd.actor_user_id, workspace_id=cmd.workspace_id,
            )
        return WorkspaceView.of(ws)

    def restore_workspace(self, cmd: RestoreWorkspace) -> WorkspaceView:
        with self._uow.new() as uow:
            ws = self._f.workspace_lifecycle.restore_workspace(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id
            )
            self._stage(
                uow, ApplicationEventType.WORKSPACE_RESTORED, "restore_workspace",
                org_id=cmd.org_id, actor=cmd.actor_user_id, workspace_id=cmd.workspace_id,
            )
        return WorkspaceView.of(ws)

    def add_member(self, cmd: AddWorkspaceMember) -> WorkspaceMemberView:
        with self._uow.new() as uow:
            wm = self._f.workspace_members.add_member(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.user_id, role=cmd.role
            )
            self._stage(
                uow, ApplicationEventType.WORKSPACE_MEMBER_ADDED, "add_workspace_member",
                org_id=cmd.org_id, actor=cmd.actor_user_id,
                workspace_id=cmd.workspace_id, user_id=cmd.user_id,
            )
        return WorkspaceMemberView.of(wm)

    def remove_member(self, cmd: RemoveWorkspaceMember) -> WorkspaceMemberView:
        with self._uow.new() as uow:
            wm = self._f.workspace_members.remove_member(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.user_id
            )
            self._stage(
                uow, ApplicationEventType.WORKSPACE_MEMBER_REMOVED, "remove_workspace_member",
                org_id=cmd.org_id, actor=cmd.actor_user_id,
                workspace_id=cmd.workspace_id, user_id=cmd.user_id,
            )
        return WorkspaceMemberView.of(wm)

    def set_default(self, cmd: SetDefaultWorkspace) -> dict:
        with self._uow.new():
            self._f.workspace_navigation.set_default(
                cmd.org_id, cmd.actor_user_id, cmd.workspace_id
            )
        return {"default_workspace_id": cmd.workspace_id}

    def favorite(self, cmd: FavoriteWorkspace) -> dict:
        with self._uow.new():
            favored = self._f.workspace_navigation.toggle_favorite(
                cmd.org_id, cmd.actor_user_id, cmd.workspace_id
            )
        return {"workspace_id": cmd.workspace_id, "favorited": favored}

    def record_recent(self, cmd: RecordRecentWorkspace) -> dict:
        with self._uow.new():
            self._f.workspace_navigation.record_recent(
                cmd.org_id, cmd.actor_user_id, cmd.workspace_id
            )
        return {"workspace_id": cmd.workspace_id}


@dataclass(frozen=True, slots=True)
class WorkspaceLifecycleQueries:
    """Read side — members, activity (summary + audit feed filtered to the workspace),
    per-user navigation, and search. No transactions, no mutation."""

    foundation: Foundation

    def members(self, query: ListWorkspaceMembers) -> list[dict]:
        return [
            asdict(WorkspaceMemberView.of(wm))
            for wm in self.foundation.workspace_members.list_members(
                query.org_id, query.workspace_id
            )
        ]

    def activity(self, query: GetWorkspaceActivity) -> dict:
        # Summary is domain state; the feed is a durable projection over the audit stream
        # (M8.9), filtered to this workspace (workspace-level + its projects' events).
        summary = self.foundation.workspace_lifecycle.activity_summary(
            query.org_id, query.workspace_id
        )
        page = self.foundation.activity.workspace_activity(
            query.org_id, query.workspace_id, **query.filter.kwargs()
        )
        return {"summary": summary, **page}

    def navigation(self, query: GetWorkspaceNavigation) -> dict:
        nav = self.foundation.workspace_navigation.navigation(query.org_id, query.user_id)
        return {
            "org_id": nav["org_id"],
            "workspaces": [asdict(WorkspaceView.of(w)) for w in nav["workspaces"]],
            "favorites": nav["favorites"],
            "recent": nav["recent"],
            "default_workspace_id": nav["default_workspace_id"],
        }

    def search(self, query: SearchWorkspaces) -> list[dict]:
        return [
            asdict(WorkspaceView.of(w))
            for w in self.foundation.workspace_lifecycle.search(query.org_id, query.term)
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
    """Wire the M8.7 use cases + handlers into the existing buses (additive)."""
    uc = WorkspaceLifecycleUseCases(
        foundation=foundation, authz=authz, uow_factory=uow_factory, ids=ids, clock=clock
    )
    q = WorkspaceLifecycleQueries(foundation=foundation)
    command_bus.register(UpdateWorkspace, uc.update_workspace)
    command_bus.register(ArchiveWorkspace, uc.archive_workspace)
    command_bus.register(RestoreWorkspace, uc.restore_workspace)
    command_bus.register(AddWorkspaceMember, uc.add_member)
    command_bus.register(RemoveWorkspaceMember, uc.remove_member)
    command_bus.register(SetDefaultWorkspace, uc.set_default)
    command_bus.register(FavoriteWorkspace, uc.favorite)
    command_bus.register(RecordRecentWorkspace, uc.record_recent)
    query_bus.register(ListWorkspaceMembers, q.members)
    query_bus.register(GetWorkspaceActivity, q.activity)
    query_bus.register(GetWorkspaceNavigation, q.navigation)
    query_bus.register(SearchWorkspaces, q.search)

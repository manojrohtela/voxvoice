"""Application use cases (M8.3).

Each use case coordinates — never re-implements — the Foundation. The pipeline per
mutating use case is uniform:

    authorize (Foundation authorizer) → open Unit of Work → call the Foundation domain
    service (which validates + persists + emits its own domain event) → map the domain
    result to a view DTO → stage the application-level (composite) event → commit.

No domain logic is duplicated: validation lives in the Foundation validator, persistence
in the Foundation repositories, permission logic in `api.foundation.rbac`. The use case
owns *orchestration* only: authorization sequencing, the transaction boundary, DTO
mapping, and application-event emission. Query use cases are read-only projections.
"""

from __future__ import annotations

from api.foundation import (
    Clock,
    EnvironmentView,
    Foundation,
    IdGenerator,
    MemberView,
    OrganizationView,
    Permission,
    PreferencesView,
    ProjectView,
    UserView,
    WorkspaceView,
)

from . import commands as c
from . import mapping as m
from . import queries as q
from .authorization import AuthorizationOrchestrator
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory


class _UseCaseBase:
    def __init__(
        self,
        *,
        foundation: Foundation,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._f = foundation
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    def _event(
        self,
        type_: ApplicationEventType,
        use_case: str,
        *,
        org_id: str,
        actor: str,
        **data: object,
    ) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=type_,
            ts=self._clock.now(),
            use_case=use_case,
            outcome="completed",
            org_id=org_id,
            actor=actor,
            data=dict(data),
        )


class CommandUseCases(_UseCaseBase):
    """The write side. One method per command; each returns the response view DTO."""

    def register_user(self, cmd: c.RegisterUser) -> UserView:
        with self._uow.new() as uow:
            user = self._f.identity.register_user(m.to_register_request(cmd))
            uow.stage(
                self._event(
                    ApplicationEventType.MEMBER_ONBOARDED, "register_user",
                    org_id="", actor=user.id, user_id=user.id,
                )
            )
        return m.user_view(user)

    def create_organization(self, cmd: c.CreateOrganization) -> OrganizationView:
        # Bootstrap: the creator becomes owner; no pre-existing permission required.
        with self._uow.new() as uow:
            org = self._f.identity.create_organization(m.to_create_org_request(cmd))
            uow.stage(
                self._event(
                    ApplicationEventType.ORGANIZATION_PROVISIONED, "create_organization",
                    org_id=org.id, actor=cmd.owner_user_id, name=org.name,
                )
            )
        return m.organization_view(org)

    def create_workspace(self, cmd: c.CreateWorkspace) -> WorkspaceView:
        self._authz.require(cmd.actor_user_id, cmd.org_id, Permission.WORKSPACE_MANAGE)
        with self._uow.new() as uow:
            ws = self._f.workspaces.create_workspace(
                cmd.actor_user_id, m.to_create_workspace_request(cmd)
            )
            uow.stage(
                self._event(
                    ApplicationEventType.WORKSPACE_PROVISIONED, "create_workspace",
                    org_id=cmd.org_id, actor=cmd.actor_user_id, workspace_id=ws.id,
                )
            )
        return m.workspace_view(ws)

    def create_environment(self, cmd: c.CreateEnvironment) -> EnvironmentView:
        self._authz.require(
            cmd.actor_user_id, cmd.org_id, Permission.ENVIRONMENT_MANAGE,
            workspace_id=cmd.workspace_id,
        )
        with self._uow.new():
            env = self._f.workspaces.create_environment(
                cmd.actor_user_id, m.to_create_environment_request(cmd)
            )
        return m.environment_view(env)

    def invite_member(self, cmd: c.InviteMember) -> MemberView:
        self._authz.require(cmd.actor_user_id, cmd.org_id, Permission.MEMBER_MANAGE)
        with self._uow.new() as uow:
            membership = self._f.membership.invite_member(
                cmd.actor_user_id, m.to_invite_request(cmd)
            )
            uow.stage(
                self._event(
                    ApplicationEventType.MEMBER_ONBOARDED, "invite_member",
                    org_id=cmd.org_id, actor=cmd.actor_user_id, user_id=cmd.user_id,
                )
            )
        return m.member_view(membership)

    def activate_member(self, cmd: c.ActivateMember) -> MemberView:
        self._authz.require(cmd.actor_user_id, cmd.org_id, Permission.MEMBER_MANAGE)
        with self._uow.new():
            membership = self._f.membership.activate_member(
                cmd.actor_user_id, cmd.org_id, cmd.user_id
            )
        return m.member_view(membership)

    def remove_member(self, cmd: c.RemoveMember) -> MemberView:
        # Soft-remove = SUSPENDED (the frozen Foundation's removal model).
        self._authz.require(cmd.actor_user_id, cmd.org_id, Permission.MEMBER_MANAGE)
        with self._uow.new() as uow:
            membership = self._f.membership.suspend_member(
                cmd.actor_user_id, cmd.org_id, cmd.user_id
            )
            uow.stage(
                self._event(
                    ApplicationEventType.MEMBER_OFFBOARDED, "remove_member",
                    org_id=cmd.org_id, actor=cmd.actor_user_id, user_id=cmd.user_id,
                )
            )
        return m.member_view(membership)

    def grant_role(self, cmd: c.GrantRole) -> MemberView:
        self._authz.require(cmd.actor_user_id, cmd.org_id, Permission.ROLE_MANAGE)
        with self._uow.new() as uow:
            self._f.access.grant_role(cmd.actor_user_id, m.to_grant_role_request(cmd))
            uow.stage(
                self._event(
                    ApplicationEventType.ROLE_ASSIGNMENT_CHANGED, "grant_role",
                    org_id=cmd.org_id, actor=cmd.actor_user_id,
                    user_id=cmd.user_id, role=cmd.role.value,
                )
            )
        membership = self._f.membership.list_members(cmd.org_id)
        current = next(m_ for m_ in membership if m_.user_id == cmd.user_id)
        return m.member_view(current, self._f.access.roles_of(cmd.org_id, cmd.user_id))

    def revoke_role(self, cmd: c.RevokeRole) -> None:
        self._authz.require(cmd.actor_user_id, cmd.org_id, Permission.ROLE_MANAGE)
        with self._uow.new() as uow:
            self._f.access.revoke_role(cmd.actor_user_id, cmd.org_id, cmd.binding_id)
            uow.stage(
                self._event(
                    ApplicationEventType.ROLE_ASSIGNMENT_CHANGED, "revoke_role",
                    org_id=cmd.org_id, actor=cmd.actor_user_id, binding_id=cmd.binding_id,
                )
            )

    def create_project(self, cmd: c.CreateProject) -> ProjectView:
        self._authz.require(
            cmd.actor_user_id, cmd.org_id, Permission.PROJECT_MANAGE,
            workspace_id=cmd.workspace_id,
        )
        with self._uow.new() as uow:
            project = self._f.projects.create_project(
                cmd.actor_user_id, m.to_create_project_request(cmd)
            )
            uow.stage(
                self._event(
                    ApplicationEventType.PROJECT_PROVISIONED, "create_project",
                    org_id=cmd.org_id, actor=cmd.actor_user_id, project_id=project.id,
                )
            )
        return m.project_view(project)

    def update_preferences(self, cmd: c.UpdatePreferences) -> PreferencesView:
        # Self-service: a user edits their own preferences (Consumer-owned, ADR-0021).
        with self._uow.new() as uow:
            prefs = self._f.configuration.update_preferences(
                m.to_update_preferences_request(cmd)
            )
            uow.stage(
                self._event(
                    ApplicationEventType.PREFERENCES_SAVED, "update_preferences",
                    org_id=cmd.org_id, actor=cmd.actor_user_id,
                )
            )
        return PreferencesView.of(prefs)


class QueryUseCases:
    """The read side — projections only; no events, no transactions, org-scoped."""

    def __init__(self, *, foundation: Foundation) -> None:
        self._f = foundation

    def get_user(self, query: q.GetUser) -> UserView | None:
        user = self._f.identity.get_user(query.user_id)
        return m.user_view(user) if user else None

    def get_organization(self, query: q.GetOrganization) -> OrganizationView | None:
        org = self._f.identity.get_organization(query.org_id)
        return m.organization_view(org) if org else None

    def get_workspace(self, query: q.GetWorkspace) -> WorkspaceView | None:
        for ws in self._f.workspaces.list_workspaces(query.org_id):
            if ws.id == query.workspace_id:
                return m.workspace_view(ws)
        return None

    def list_workspaces(self, query: q.ListWorkspaces) -> tuple[WorkspaceView, ...]:
        return tuple(m.workspace_view(w) for w in self._f.workspaces.list_workspaces(query.org_id))

    def list_members(self, query: q.ListMembers) -> tuple[MemberView, ...]:
        return tuple(
            m.member_view(mem, self._f.access.roles_of(query.org_id, mem.user_id))
            for mem in self._f.membership.list_members(query.org_id)
        )

    def list_projects(self, query: q.ListProjects) -> tuple[ProjectView, ...]:
        return tuple(
            m.project_view(p)
            for p in self._f.projects.list_projects(query.org_id, query.workspace_id)
        )

    def list_environments(self, query: q.ListEnvironments) -> tuple[EnvironmentView, ...]:
        return tuple(
            m.environment_view(e)
            for e in self._f.workspaces.list_environments(query.org_id, query.workspace_id)
        )

    def get_preferences(self, query: q.GetPreferences) -> PreferencesView:
        return PreferencesView.of(
            self._f.configuration.get_preferences(query.org_id, query.user_id)
        )

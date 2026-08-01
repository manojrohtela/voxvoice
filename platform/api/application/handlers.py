"""Command & Query handler registration (M8.3).

Handlers are thin CQRS adapters: each maps one message type to the corresponding use-case
method. They contain **no** logic beyond that routing (the use cases own orchestration,
the Foundation owns domain behaviour) — so the handler layer is pure wiring and stays
trivially correct. `register_handlers` performs the dependency injection into the buses.
"""

from __future__ import annotations

from . import commands as c
from . import queries as q
from .dispatcher import CommandBus, QueryBus
from .use_cases import CommandUseCases, QueryUseCases


def register_command_handlers(bus: CommandBus, uc: CommandUseCases) -> None:
    bus.register(c.RegisterUser, uc.register_user)
    bus.register(c.CreateOrganization, uc.create_organization)
    bus.register(c.CreateWorkspace, uc.create_workspace)
    bus.register(c.CreateEnvironment, uc.create_environment)
    bus.register(c.InviteMember, uc.invite_member)
    bus.register(c.ActivateMember, uc.activate_member)
    bus.register(c.RemoveMember, uc.remove_member)
    bus.register(c.GrantRole, uc.grant_role)
    bus.register(c.RevokeRole, uc.revoke_role)
    bus.register(c.CreateProject, uc.create_project)
    bus.register(c.UpdatePreferences, uc.update_preferences)


def register_query_handlers(bus: QueryBus, uc: QueryUseCases) -> None:
    bus.register(q.GetUser, uc.get_user)
    bus.register(q.GetOrganization, uc.get_organization)
    bus.register(q.GetWorkspace, uc.get_workspace)
    bus.register(q.ListWorkspaces, uc.list_workspaces)
    bus.register(q.ListMembers, uc.list_members)
    bus.register(q.ListProjects, uc.list_projects)
    bus.register(q.ListEnvironments, uc.list_environments)
    bus.register(q.GetPreferences, uc.get_preferences)

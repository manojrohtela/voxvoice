"""Project Lifecycle + Membership + Environment services (M8.8) — additive.

New domain services in a separate module so the M8.2 `services.py` stays untouched (mirrors
`org_lifecycle.py`/`workspace_lifecycle.py`). Projects are the primary execution boundary
inside a workspace; this slice owns rename/settings/archive/restore, project membership, and
per-user environment selection. Project **creation** stays in the frozen
`ProjectService.create_project`.

Reuse-first (per the M8.8 architecture rule): **no new RBAC scope** is introduced — project
operations authorize via the existing **workspace-scoped** `PROJECT_MANAGE`/`PROJECT_READ`
permissions; a project member must already be an ACTIVE **org** member (reusing org
membership); and environment selection is Consumer-owned state persisted in the existing
`UserPreferences` (reusing `EnvironmentKind` for the standard dev/staging/prod set — no new
Environment persistence, no deployment logic).
"""

from __future__ import annotations

from .diagnostics import (
    Diagnostic,
    FoundationDiagnosticCode,
    FoundationDiagnostics,
    Severity,
)
from .domain import (
    EnvironmentKind,
    MembershipStatus,
    ProjectMembership,
    ProjectMembershipStatus,
    ProjectRole,
    ProjectStatus,
    UserPreferences,
)
from .errors import (
    ConflictError,
    FoundationValidationError,
    NotFoundError,
    OrganizationIsolationError,
)
from .events import FoundationEventType
from .rbac import Permission
from .repositories import (
    MembershipRepository,
    OrganizationRepository,
    PreferencesRepository,
    ProjectMembershipRepository,
    ProjectRepository,
    UserRepository,
    WorkspaceRepository,
)
from .seams import PREFIX_PROJECT_MEMBERSHIP
from .services import _Base
from .validation import FoundationValidator

# The standard project environments (M8.8). A fixed, deployment-free set built from the
# existing EnvironmentKind vocabulary — no new persistence.
STANDARD_ENVIRONMENTS: tuple[str, ...] = (
    EnvironmentKind.DEVELOPMENT.value,
    EnvironmentKind.STAGING.value,
    EnvironmentKind.PRODUCTION.value,
)
_DEFAULT_ENVIRONMENT = EnvironmentKind.DEVELOPMENT.value
# Preference-metadata key holding a {project_id: environment_kind} map (Consumer-owned).
_ENV_KEY = "project_environments"


class ProjectLifecycleService(_Base):
    """Project rename, settings (description/icon/metadata), archive/restore, activity
    summary, and search. Name uniqueness within a workspace is enforced on rename."""

    def __init__(
        self,
        *,
        workspaces: WorkspaceRepository,
        projects: ProjectRepository,
        project_memberships: ProjectMembershipRepository,
        validator: FoundationValidator,
        **base,
    ) -> None:
        super().__init__(**base)
        self._workspaces = workspaces
        self._projects = projects
        self._project_memberships = project_memberships
        self._validator = validator

    def _load(self, org_id: str, workspace_id: str, project_id: str):
        p = self._projects.get(org_id, workspace_id, project_id)
        if p is None:
            raise NotFoundError(
                f"project {project_id!r} not found in workspace {workspace_id!r}"
            )
        return p

    def _require_unique_name(
        self, org_id: str, workspace_id: str, project_id: str, name: str
    ) -> None:
        norm = name.strip().casefold()
        for p in self._projects.list(org_id, workspace_id):
            if p.id != project_id and p.name.strip().casefold() == norm:
                raise ConflictError(
                    f"project name {name!r} already exists in workspace {workspace_id!r}"
                )

    def update_project(
        self,
        actor_user_id: str,
        org_id: str,
        workspace_id: str,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        metadata: dict | None = None,
    ):
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        p = self._load(org_id, workspace_id, project_id)
        if name is not None:
            diagnostics = self._validator.validate_project(name=name, slug=p.slug)
            if diagnostics.has_errors:
                raise FoundationValidationError(diagnostics)
            self._require_unique_name(org_id, workspace_id, project_id, name)
        updated = p.with_updates(
            name=name, description=description, icon=icon, metadata=metadata,
            updated_at=self._clock.now(),
        )
        self._projects.update(updated)
        settings_touched = any(v is not None for v in (description, icon, metadata))
        self._emit(
            FoundationEventType.PROJECT_SETTINGS_CHANGED
            if settings_touched and name is None
            else FoundationEventType.PROJECT_UPDATED,
            org_id=org_id, actor=actor_user_id, resource=project_id,
            workspace_id=workspace_id,
        )
        return updated

    def archive_project(
        self, actor_user_id: str, org_id: str, workspace_id: str, project_id: str
    ):
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        p = self._load(org_id, workspace_id, project_id)
        if p.status is ProjectStatus.ARCHIVED:
            raise ConflictError(f"project {project_id!r} is already archived")
        updated = self._projects.update(
            p.with_updates(status=ProjectStatus.ARCHIVED, updated_at=self._clock.now())
        )
        self._emit(
            FoundationEventType.PROJECT_ARCHIVED,
            org_id=org_id, actor=actor_user_id, resource=project_id,
            workspace_id=workspace_id,
        )
        return updated

    def restore_project(
        self, actor_user_id: str, org_id: str, workspace_id: str, project_id: str
    ):
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        p = self._load(org_id, workspace_id, project_id)
        if p.status is not ProjectStatus.ARCHIVED:
            raise ConflictError(f"project {project_id!r} is not archived")
        updated = self._projects.update(
            p.with_updates(status=ProjectStatus.ACTIVE, updated_at=self._clock.now())
        )
        self._emit(
            FoundationEventType.PROJECT_RESTORED,
            org_id=org_id, actor=actor_user_id, resource=project_id,
            workspace_id=workspace_id,
        )
        return updated

    def activity_summary(self, org_id: str, workspace_id: str, project_id: str) -> dict:
        """A deterministic current-state rollup (member counts + project status)."""
        p = self._projects.get(org_id, workspace_id, project_id)
        members = self._project_memberships.list(org_id, project_id)
        active = [m for m in members if m.status is ProjectMembershipStatus.ACTIVE]
        by_role: dict[str, int] = {}
        for m in active:
            by_role[m.role] = by_role.get(m.role, 0) + 1
        return {
            "org_id": org_id,
            "workspace_id": workspace_id,
            "project_id": project_id,
            "status": p.status.value if p else "unknown",
            "total_members": len(active),
            "members_by_role": by_role,
        }

    def search(
        self, org_id: str, workspace_id: str, term: str, *, include_archived: bool = False
    ):
        """Case-insensitive substring match on name/slug, workspace-scoped."""
        needle = term.strip().casefold()
        out = []
        for p in self._projects.list(org_id, workspace_id):
            if not include_archived and p.status is ProjectStatus.ARCHIVED:
                continue
            if needle in p.name.casefold() or needle in p.slug.casefold():
                out.append(p)
        return tuple(out)


class ProjectMembershipService(_Base):
    """Add/remove/list project members. Only existing ACTIVE org members may join a project;
    the tier (Admin/Member/Viewer) is recorded on the membership (no new RBAC scope — project
    authorization stays on the existing workspace-scoped PROJECT_MANAGE/PROJECT_READ)."""

    def __init__(
        self,
        *,
        orgs: OrganizationRepository,
        users: UserRepository,
        workspaces: WorkspaceRepository,
        projects: ProjectRepository,
        memberships: MembershipRepository,
        project_memberships: ProjectMembershipRepository,
        **base,
    ) -> None:
        super().__init__(**base)
        self._orgs = orgs
        self._users = users
        self._workspaces = workspaces
        self._projects = projects
        self._memberships = memberships
        self._project_memberships = project_memberships

    def _load_project(self, org_id: str, workspace_id: str, project_id: str):
        p = self._projects.get(org_id, workspace_id, project_id)
        if p is None:
            raise NotFoundError(
                f"project {project_id!r} not found in workspace {workspace_id!r}"
            )
        return p

    def add_member(
        self,
        actor_user_id: str,
        org_id: str,
        workspace_id: str,
        project_id: str,
        user_id: str,
        *,
        role: ProjectRole = ProjectRole.MEMBER,
    ) -> ProjectMembership:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        self._load_project(org_id, workspace_id, project_id)
        # Isolation: the user must be an ACTIVE org member (reuse org membership).
        org_membership = self._memberships.get(org_id, user_id)
        if org_membership is None or org_membership.status is not MembershipStatus.ACTIVE:
            raise OrganizationIsolationError(
                f"user {user_id!r} is not an active member of org {org_id!r}"
            )
        existing = self._project_memberships.get(org_id, project_id, user_id)
        if existing is not None and existing.status is ProjectMembershipStatus.ACTIVE:
            raise ConflictError(
                f"user {user_id!r} is already a member of project {project_id!r}"
            )
        membership = ProjectMembership(
            id=existing.id if existing is not None else self._ids.new(PREFIX_PROJECT_MEMBERSHIP),
            org_id=org_id, workspace_id=workspace_id, project_id=project_id, user_id=user_id,
            role=role.value, status=ProjectMembershipStatus.ACTIVE, created_at=self._clock.now(),
        )
        if existing is not None:
            self._project_memberships.update(membership)
        else:
            self._project_memberships.add(membership)
        self._emit(
            FoundationEventType.PROJECT_MEMBER_ADDED,
            org_id=org_id, actor=actor_user_id, resource=membership.id,
            workspace_id=workspace_id, project_id=project_id, user_id=user_id,
            role=role.value,
        )
        return membership

    def remove_member(
        self,
        actor_user_id: str,
        org_id: str,
        workspace_id: str,
        project_id: str,
        user_id: str,
    ) -> ProjectMembership:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        pm = self._project_memberships.get(org_id, project_id, user_id)
        if pm is None or pm.status is not ProjectMembershipStatus.ACTIVE:
            raise NotFoundError(
                f"user {user_id!r} is not a member of project {project_id!r}"
            )
        removed = self._project_memberships.update(
            pm.with_status(ProjectMembershipStatus.REMOVED)
        )
        self._emit(
            FoundationEventType.PROJECT_MEMBER_REMOVED,
            org_id=org_id, actor=actor_user_id, resource=removed.id,
            workspace_id=workspace_id, project_id=project_id, user_id=user_id,
        )
        return removed

    def list_members(
        self, org_id: str, project_id: str
    ) -> tuple[ProjectMembership, ...]:
        return tuple(
            pm
            for pm in self._project_memberships.list(org_id, project_id)
            if pm.status is ProjectMembershipStatus.ACTIVE
        )


class ProjectEnvironmentService(_Base):
    """Per-user project environment selection (M8.8). The standard dev/staging/prod set is
    fixed (no deployment logic); the selected/default environment is Consumer-owned state
    persisted in the user's `UserPreferences` metadata (reusing the preferences repository)."""

    def __init__(
        self,
        *,
        projects: ProjectRepository,
        preferences: PreferencesRepository,
        **base,
    ) -> None:
        super().__init__(**base)
        self._projects = projects
        self._preferences = preferences

    @staticmethod
    def environments() -> tuple[str, ...]:
        return STANDARD_ENVIRONMENTS

    def _prefs(self, org_id: str, user_id: str) -> UserPreferences:
        return self._preferences.get(org_id, user_id) or UserPreferences(
            user_id=user_id, org_id=org_id
        )

    def get_environment(self, org_id: str, user_id: str, project_id: str) -> str:
        prefs = self._prefs(org_id, user_id)
        return prefs.metadata.get(_ENV_KEY, {}).get(project_id, _DEFAULT_ENVIRONMENT)

    def set_environment(
        self, org_id: str, user_id: str, workspace_id: str, project_id: str, environment: str
    ) -> str:
        if environment not in STANDARD_ENVIRONMENTS:
            raise FoundationValidationError(
                FoundationDiagnostics.of([
                    Diagnostic(
                        "environment",
                        FoundationDiagnosticCode.INVALID_ENVIRONMENT_KIND,
                        Severity.ERROR,
                        f"unknown environment {environment!r}",
                        f"use one of {', '.join(STANDARD_ENVIRONMENTS)}",
                    )
                ])
            )
        # Confirm the project exists in this workspace (isolation).
        if self._projects.get(org_id, workspace_id, project_id) is None:
            raise NotFoundError(
                f"project {project_id!r} not found in workspace {workspace_id!r}"
            )
        prefs = self._prefs(org_id, user_id)
        meta = dict(prefs.metadata)
        env_map = dict(meta.get(_ENV_KEY, {}))
        env_map[project_id] = environment
        meta[_ENV_KEY] = env_map
        self._preferences.put(
            UserPreferences(
                user_id=prefs.user_id, org_id=prefs.org_id, theme=prefs.theme,
                locale=prefs.locale,
                keyboard_shortcuts_enabled=prefs.keyboard_shortcuts_enabled,
                favorites=prefs.favorites, metadata=meta,
            )
        )
        self._emit(
            FoundationEventType.PROJECT_ENVIRONMENT_SWITCHED,
            org_id=org_id, actor=user_id, resource=project_id,
            workspace_id=workspace_id, environment=environment,
        )
        return environment

    def selector(self, org_id: str, user_id: str, project_id: str) -> dict:
        """The environment selector payload: the fixed set + the user's current selection."""
        return {
            "project_id": project_id,
            "environments": list(STANDARD_ENVIRONMENTS),
            "current": self.get_environment(org_id, user_id, project_id),
        }

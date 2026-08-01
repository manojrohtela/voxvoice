"""Domain services (M8.2).

Where behaviour lives: each service validates its input (fail-fast, raising
`FoundationValidationError` with full diagnostics), enforces organization isolation and
RBAC authorization, persists through repository interfaces, and emits a typed domain
event (Constitution §A2.5 — one writer per fact; §A2.6 — accepted config acts or is
rejected; §A11.2 — every mutation is authorized and audited). Services never touch a
frozen Epic and never actuate anything — they own control-plane authoring/management
state only (ADR-0021).

Determinism (§A2.4): all behaviour is deterministic; the only non-deterministic edges
(id minting, wall clock) are the injected `IdGenerator`/`Clock` seams.
"""

from __future__ import annotations

from .config import FeatureFlag, FeatureFlagResolver, FeatureFlagState
from .domain import (
    Environment,
    EnvironmentKind,
    Membership,
    MembershipStatus,
    Organization,
    Project,
    Role,
    RoleBinding,
    RoleScope,
    User,
    UserPreferences,
    Workspace,
)
from .dto import (
    CreateEnvironmentRequest,
    CreateOrganizationRequest,
    CreateProjectRequest,
    CreateWorkspaceRequest,
    GrantRoleRequest,
    InviteMemberRequest,
    RegisterUserRequest,
    UpdatePreferencesRequest,
)
from .errors import (
    AuthorizationError,
    ConflictError,
    FoundationValidationError,
    NotFoundError,
    OrganizationIsolationError,
)
from .events import EventEmitter, FoundationEvent, FoundationEventType
from .rbac import Permission, PermissionEvaluator
from .repositories import (
    EnvironmentRepository,
    MembershipRepository,
    OrganizationRepository,
    PreferencesRepository,
    ProjectRepository,
    RoleBindingRepository,
    UserRepository,
    WorkspaceRepository,
)
from .seams import (
    PREFIX_ENVIRONMENT,
    PREFIX_EVENT,
    PREFIX_MEMBERSHIP,
    PREFIX_ORG,
    PREFIX_PROJECT,
    PREFIX_ROLE_BINDING,
    PREFIX_USER,
    PREFIX_WORKSPACE,
    Clock,
    IdGenerator,
)
from .validation import FoundationValidator


class _Base:
    """Shared plumbing: id/clock seams, event emission, and isolation/authorization
    helpers used by every service."""

    def __init__(
        self,
        *,
        ids: IdGenerator,
        clock: Clock,
        emitter: EventEmitter,
        evaluator: PermissionEvaluator,
        role_bindings: RoleBindingRepository,
    ) -> None:
        self._ids = ids
        self._clock = clock
        self._emitter = emitter
        self._evaluator = evaluator
        self._role_bindings = role_bindings

    def _emit(
        self,
        type_: FoundationEventType,
        *,
        org_id: str,
        actor: str,
        resource: str,
        **data: object,
    ) -> None:
        self._emitter.emit(
            FoundationEvent(
                id=self._ids.new(PREFIX_EVENT),
                type=type_,
                ts=self._clock.now(),
                org_id=org_id,
                actor=actor,
                resource=resource,
                data=dict(data),
            )
        )

    def _require(
        self,
        actor_user_id: str,
        permission: Permission,
        *,
        org_id: str,
        workspace_id: str | None = None,
    ) -> None:
        """Authorize an actor for a permission at a target scope, or raise. Emits an
        `access.denied` audit event on failure (never silent)."""
        bindings = self._role_bindings.for_user(org_id, actor_user_id)
        if not self._evaluator.can(
            bindings, permission, org_id=org_id, workspace_id=workspace_id
        ):
            self._emit(
                FoundationEventType.ACCESS_DENIED,
                org_id=org_id,
                actor=actor_user_id,
                resource=workspace_id or org_id,
                permission=permission.value,
            )
            raise AuthorizationError(
                f"user {actor_user_id} lacks {permission.value} in org {org_id}"
            )


class IdentityService(_Base):
    """Users + organizations. `create_organization` auto-provisions the owner
    (an ACTIVE membership + an org-scoped ORG_OWNER role binding) so a new org is never
    left without an administrator — the "sign up → organization" step (Blueprint §7)."""

    def __init__(
        self,
        *,
        users: UserRepository,
        orgs: OrganizationRepository,
        memberships: MembershipRepository,
        validator: FoundationValidator,
        **base,
    ) -> None:
        super().__init__(**base)
        self._users = users
        self._orgs = orgs
        self._memberships = memberships
        self._validator = validator

    def register_user(self, req: RegisterUserRequest) -> User:
        diagnostics = self._validator.validate_user(
            email=req.email, display_name=req.display_name
        )
        if diagnostics.has_errors:
            raise FoundationValidationError(diagnostics)
        if self._users.by_email(req.email):
            raise ConflictError(f"a user with email {req.email!r} already exists")
        user = User(
            id=self._ids.new(PREFIX_USER),
            email=req.email,
            display_name=req.display_name,
            created_at=self._clock.now(),
        )
        return self._users.add(user)

    def create_organization(self, req: CreateOrganizationRequest) -> Organization:
        diagnostics = self._validator.validate_organization(name=req.name)
        if diagnostics.has_errors:
            raise FoundationValidationError(diagnostics)
        if self._users.get(req.owner_user_id) is None:
            raise NotFoundError(f"owner user {req.owner_user_id!r} does not exist")
        org = Organization(
            id=self._ids.new(PREFIX_ORG),
            name=req.name,
            plan=req.plan,
            region=req.region,
            created_at=self._clock.now(),
        )
        self._orgs.add(org)
        # Auto-provision the owner: ACTIVE membership + org-scoped ORG_OWNER binding.
        membership = Membership(
            id=self._ids.new(PREFIX_MEMBERSHIP),
            org_id=org.id,
            user_id=req.owner_user_id,
            status=MembershipStatus.ACTIVE,
            created_at=self._clock.now(),
        )
        self._memberships.add(membership)
        self._role_bindings.add(
            RoleBinding(
                id=self._ids.new(PREFIX_ROLE_BINDING),
                org_id=org.id,
                user_id=req.owner_user_id,
                role=Role.ORG_OWNER,
                scope=RoleScope.ORGANIZATION,
                scope_id=org.id,
                created_at=self._clock.now(),
            )
        )
        self._emit(
            FoundationEventType.ORGANIZATION_CREATED,
            org_id=org.id,
            actor=req.owner_user_id,
            resource=org.id,
            name=org.name,
        )
        return org

    def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def get_organization(self, org_id: str) -> Organization | None:
        return self._orgs.get(org_id)


class MembershipService(_Base):
    """Org membership lifecycle: invite → activate/suspend."""

    def __init__(
        self,
        *,
        orgs: OrganizationRepository,
        users: UserRepository,
        memberships: MembershipRepository,
        **base,
    ) -> None:
        super().__init__(**base)
        self._orgs = orgs
        self._users = users
        self._memberships = memberships

    def invite_member(self, actor_user_id: str, req: InviteMemberRequest) -> Membership:
        self._require(actor_user_id, Permission.MEMBER_MANAGE, org_id=req.org_id)
        if self._orgs.get(req.org_id) is None:
            raise NotFoundError(f"organization {req.org_id!r} does not exist")
        if self._users.get(req.user_id) is None:
            raise NotFoundError(f"user {req.user_id!r} does not exist")
        if self._memberships.get(req.org_id, req.user_id) is not None:
            raise ConflictError(
                f"user {req.user_id!r} is already a member of org {req.org_id!r}"
            )
        membership = Membership(
            id=self._ids.new(PREFIX_MEMBERSHIP),
            org_id=req.org_id,
            user_id=req.user_id,
            status=MembershipStatus.INVITED,
            created_at=self._clock.now(),
        )
        self._memberships.add(membership)
        self._emit(
            FoundationEventType.MEMBER_INVITED,
            org_id=req.org_id,
            actor=actor_user_id,
            resource=membership.id,
            user_id=req.user_id,
        )
        return membership

    def activate_member(self, actor_user_id: str, org_id: str, user_id: str) -> Membership:
        self._require(actor_user_id, Permission.MEMBER_MANAGE, org_id=org_id)
        return self._transition(
            actor_user_id, org_id, user_id, MembershipStatus.ACTIVE,
            FoundationEventType.MEMBER_ACTIVATED,
        )

    def suspend_member(self, actor_user_id: str, org_id: str, user_id: str) -> Membership:
        self._require(actor_user_id, Permission.MEMBER_MANAGE, org_id=org_id)
        return self._transition(
            actor_user_id, org_id, user_id, MembershipStatus.SUSPENDED,
            FoundationEventType.MEMBER_SUSPENDED,
        )

    def list_members(self, org_id: str) -> tuple[Membership, ...]:
        return self._memberships.list(org_id)

    def _transition(
        self,
        actor_user_id: str,
        org_id: str,
        user_id: str,
        status: MembershipStatus,
        event: FoundationEventType,
    ) -> Membership:
        membership = self._memberships.get(org_id, user_id)
        if membership is None:
            raise NotFoundError(f"user {user_id!r} is not a member of org {org_id!r}")
        updated = self._memberships.update(membership.with_status(status))
        self._emit(event, org_id=org_id, actor=actor_user_id, resource=updated.id,
                   status=status.value)
        return updated


class AccessService(_Base):
    """Role grants + permission queries — the RBAC control surface."""

    def __init__(
        self,
        *,
        orgs: OrganizationRepository,
        workspaces: WorkspaceRepository,
        memberships: MembershipRepository,
        validator: FoundationValidator,
        **base,
    ) -> None:
        super().__init__(**base)
        self._orgs = orgs
        self._workspaces = workspaces
        self._memberships = memberships
        self._validator = validator

    def grant_role(self, actor_user_id: str, req: GrantRoleRequest) -> RoleBinding:
        self._require(actor_user_id, Permission.ROLE_MANAGE, org_id=req.org_id)
        diagnostics = self._validator.validate_role_binding(
            role=req.role, scope=req.scope, scope_id=req.scope_id
        )
        if diagnostics.has_errors:
            raise FoundationValidationError(diagnostics)
        if self._memberships.get(req.org_id, req.user_id) is None:
            raise NotFoundError(
                f"user {req.user_id!r} must be a member before a role is granted"
            )
        # Isolation: a workspace-scoped grant must target a workspace *in this org*.
        if req.scope is RoleScope.WORKSPACE:
            if self._workspaces.get(req.org_id, req.scope_id) is None:
                raise OrganizationIsolationError(
                    f"workspace {req.scope_id!r} is not in org {req.org_id!r}"
                )
        elif req.scope_id != req.org_id:
            raise OrganizationIsolationError(
                "an ORGANIZATION-scoped binding must target its own org id"
            )
        binding = RoleBinding(
            id=self._ids.new(PREFIX_ROLE_BINDING),
            org_id=req.org_id,
            user_id=req.user_id,
            role=req.role,
            scope=req.scope,
            scope_id=req.scope_id,
            created_at=self._clock.now(),
        )
        self._role_bindings.add(binding)
        self._emit(
            FoundationEventType.ROLE_GRANTED,
            org_id=req.org_id,
            actor=actor_user_id,
            resource=binding.id,
            user_id=req.user_id,
            role=req.role.value,
        )
        return binding

    def revoke_role(self, actor_user_id: str, org_id: str, binding_id: str) -> None:
        self._require(actor_user_id, Permission.ROLE_MANAGE, org_id=org_id)
        self._role_bindings.remove(org_id, binding_id)
        self._emit(
            FoundationEventType.ROLE_REVOKED,
            org_id=org_id,
            actor=actor_user_id,
            resource=binding_id,
        )

    def roles_of(self, org_id: str, user_id: str) -> tuple[RoleBinding, ...]:
        return self._role_bindings.for_user(org_id, user_id)

    def can(
        self,
        user_id: str,
        permission: Permission,
        *,
        org_id: str,
        workspace_id: str | None = None,
    ) -> bool:
        """Read-only permission check (no event; used by callers to gate UI/actions)."""
        bindings = self._role_bindings.for_user(org_id, user_id)
        return self._evaluator.can(
            bindings, permission, org_id=org_id, workspace_id=workspace_id
        )


class WorkspaceService(_Base):
    """Workspaces + environments. Creating a workspace provisions a default `dev`
    environment so a builder has somewhere to work immediately (Blueprint §7)."""

    def __init__(
        self,
        *,
        orgs: OrganizationRepository,
        workspaces: WorkspaceRepository,
        environments: EnvironmentRepository,
        validator: FoundationValidator,
        **base,
    ) -> None:
        super().__init__(**base)
        self._orgs = orgs
        self._workspaces = workspaces
        self._environments = environments
        self._validator = validator

    def create_workspace(self, actor_user_id: str, req: CreateWorkspaceRequest) -> Workspace:
        self._require(actor_user_id, Permission.WORKSPACE_MANAGE, org_id=req.org_id)
        diagnostics = self._validator.validate_workspace(name=req.name, slug=req.slug)
        if diagnostics.has_errors:
            raise FoundationValidationError(diagnostics)
        if self._orgs.get(req.org_id) is None:
            raise NotFoundError(f"organization {req.org_id!r} does not exist")
        if self._workspaces.by_slug(req.org_id, req.slug) is not None:
            raise ConflictError(
                f"workspace slug {req.slug!r} already exists in org {req.org_id!r}"
            )
        workspace = Workspace(
            id=self._ids.new(PREFIX_WORKSPACE),
            org_id=req.org_id,
            name=req.name,
            slug=req.slug,
            created_at=self._clock.now(),
        )
        self._workspaces.add(workspace)
        self._environments.add(
            Environment(
                id=self._ids.new(PREFIX_ENVIRONMENT),
                org_id=req.org_id,
                workspace_id=workspace.id,
                name="dev",
                kind=EnvironmentKind.DEVELOPMENT,
                created_at=self._clock.now(),
            )
        )
        self._emit(
            FoundationEventType.WORKSPACE_CREATED,
            org_id=req.org_id,
            actor=actor_user_id,
            resource=workspace.id,
            slug=workspace.slug,
        )
        return workspace

    def create_environment(
        self, actor_user_id: str, req: CreateEnvironmentRequest
    ) -> Environment:
        self._require(
            actor_user_id, Permission.ENVIRONMENT_MANAGE,
            org_id=req.org_id, workspace_id=req.workspace_id,
        )
        if self._workspaces.get(req.org_id, req.workspace_id) is None:
            raise NotFoundError(
                f"workspace {req.workspace_id!r} is not in org {req.org_id!r}"
            )
        environment = Environment(
            id=self._ids.new(PREFIX_ENVIRONMENT),
            org_id=req.org_id,
            workspace_id=req.workspace_id,
            name=req.name,
            kind=req.kind,
            created_at=self._clock.now(),
        )
        self._environments.add(environment)
        self._emit(
            FoundationEventType.ENVIRONMENT_CREATED,
            org_id=req.org_id,
            actor=actor_user_id,
            resource=environment.id,
            kind=req.kind.value,
        )
        return environment

    def list_workspaces(self, org_id: str) -> tuple[Workspace, ...]:
        return self._workspaces.list(org_id)

    def list_environments(self, org_id: str, workspace_id: str) -> tuple[Environment, ...]:
        return self._environments.list(org_id, workspace_id)


class ProjectService(_Base):
    """Projects within a workspace."""

    def __init__(
        self,
        *,
        workspaces: WorkspaceRepository,
        projects: ProjectRepository,
        validator: FoundationValidator,
        **base,
    ) -> None:
        super().__init__(**base)
        self._workspaces = workspaces
        self._projects = projects
        self._validator = validator

    def create_project(self, actor_user_id: str, req: CreateProjectRequest) -> Project:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE,
            org_id=req.org_id, workspace_id=req.workspace_id,
        )
        diagnostics = self._validator.validate_project(name=req.name, slug=req.slug)
        if diagnostics.has_errors:
            raise FoundationValidationError(diagnostics)
        if self._workspaces.get(req.org_id, req.workspace_id) is None:
            raise NotFoundError(
                f"workspace {req.workspace_id!r} is not in org {req.org_id!r}"
            )
        if self._projects.by_slug(req.org_id, req.workspace_id, req.slug) is not None:
            raise ConflictError(f"project slug {req.slug!r} already exists in workspace")
        project = Project(
            id=self._ids.new(PREFIX_PROJECT),
            org_id=req.org_id,
            workspace_id=req.workspace_id,
            name=req.name,
            slug=req.slug,
            created_at=self._clock.now(),
        )
        self._projects.add(project)
        self._emit(
            FoundationEventType.PROJECT_CREATED,
            org_id=req.org_id,
            actor=actor_user_id,
            resource=project.id,
            slug=project.slug,
        )
        return project

    def list_projects(self, org_id: str, workspace_id: str) -> tuple[Project, ...]:
        return self._projects.list(org_id, workspace_id)


class ConfigurationService(_Base):
    """User preferences + feature-flag resolution. Preferences are Consumer-owned
    (ADR-0021); flags resolve deterministically under fixed precedence."""

    def __init__(
        self,
        *,
        preferences: PreferencesRepository,
        resolver: FeatureFlagResolver,
        **base,
    ) -> None:
        super().__init__(**base)
        self._preferences = preferences
        self._resolver = resolver

    def update_preferences(self, req: UpdatePreferencesRequest) -> UserPreferences:
        prefs = UserPreferences(
            user_id=req.user_id,
            org_id=req.org_id,
            theme=req.theme,
            locale=req.locale,
            keyboard_shortcuts_enabled=req.keyboard_shortcuts_enabled,
            favorites=req.favorites,
        )
        self._preferences.put(prefs)
        self._emit(
            FoundationEventType.PREFERENCES_UPDATED,
            org_id=req.org_id,
            actor=req.user_id,
            resource=req.user_id,
        )
        return prefs

    def get_preferences(self, org_id: str, user_id: str) -> UserPreferences:
        existing = self._preferences.get(org_id, user_id)
        return existing or UserPreferences(user_id=user_id, org_id=org_id)

    def resolve_flag(
        self, key: str, flags: tuple[FeatureFlag, ...], *, default: bool = False
    ) -> FeatureFlagState:
        return self._resolver.resolve(key, flags, default=default)

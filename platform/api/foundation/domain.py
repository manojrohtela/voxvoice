"""Customer Platform Foundation domain model (M8.2).

The identity + platform aggregates every future Epic 8 feature builds on, as immutable,
slotted value objects (the same frozen-dataclass discipline as `product.domain`). These
are pure data — no I/O, no behaviour beyond derived read-only properties. Services
(`services.py`) own construction/transition; repositories own persistence.

Ownership (ADR-0021): these are authoring/management records the Control Plane owns.
Every org-owned aggregate carries `org_id` so isolation is enforceable on every query
(Constitution §A11.2). Aggregates are identified, not content-addressed (unlike Product
Runtime artifacts) — an organization gets a fresh id, it is not derived from its content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ── enumerations ────────────────────────────────────────────────────────────────


class Role(StrEnum):
    """The 11 RBAC roles ratified in ADR-0021 §5. Permissions per role live in
    `rbac.ROLE_PERMISSIONS` (declared, extensible), never on the enum."""

    PLATFORM_SUPER_ADMIN = "platform_super_admin"
    ORG_OWNER = "org_owner"
    BILLING_ADMIN = "billing_admin"
    WORKSPACE_ADMIN = "workspace_admin"
    DEVELOPER = "developer"
    AGENT_DESIGNER = "agent_designer"
    QA_REVIEWER = "qa_reviewer"
    HR_RECRUITER = "hr_recruiter"
    ANALYST = "analyst"
    APPROVER = "approver"
    READ_ONLY = "read_only"
    # M8.6 — org-membership tiers (additive built-in roles, not user-defined; the
    # Organization Lifecycle's Owner/Admin/Member/Viewer bindings). ORG_OWNER above is
    # reused for the Owner tier.
    ORG_ADMIN = "org_admin"
    ORG_MEMBER = "org_member"
    ORG_VIEWER = "org_viewer"
    # M8.7 — workspace-membership tiers (additive built-in roles). WORKSPACE_ADMIN above
    # is reused for the workspace Admin tier; Member/Viewer are new workspace-scoped roles.
    WS_MEMBER = "ws_member"
    WS_VIEWER = "ws_viewer"


class RoleScope(StrEnum):
    """The altitude a role binding applies at. An ORGANIZATION binding covers every
    workspace in the org; a WORKSPACE binding covers exactly one workspace."""

    ORGANIZATION = "organization"
    WORKSPACE = "workspace"


class MembershipStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    # M8.6 — invitation lifecycle terminal states (additive; an invitation is a
    # pending membership, so its lifecycle is modelled as membership status).
    DECLINED = "declined"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class OrganizationStatus(StrEnum):
    """Organization lifecycle status (M8.6). `DELETED` is a soft-delete marker — the row
    is retained (audit/isolation), never hard-deleted."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class OrgRole(StrEnum):
    """The four org-membership tiers (M8.6) — a curated, non-custom set surfaced by the
    invitation flow. They map to `Role` bindings via `ORG_ROLE_BINDING` in `rbac`."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class WsRole(StrEnum):
    """The three workspace-membership tiers (M8.7) — a curated, non-custom set surfaced by
    the workspace member flow. They map to `Role` bindings via `WS_ROLE_BINDING` in `rbac`."""

    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class WorkspaceStatus(StrEnum):
    """Workspace lifecycle status (M8.7). `ARCHIVED` is a soft-archive marker — the row is
    retained (and restorable), never hard-deleted."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class WorkspaceMembershipStatus(StrEnum):
    """A user's belonging to a workspace (M8.7)."""

    ACTIVE = "active"
    REMOVED = "removed"


class ProjectRole(StrEnum):
    """The three project-membership tiers (M8.8) — a curated, non-custom set surfaced by
    the project member flow (Admin/Member/Viewer)."""

    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class ProjectStatus(StrEnum):
    """Project lifecycle status (M8.8). `ARCHIVED` is a soft-archive marker — the row is
    retained (and restorable), never hard-deleted."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ProjectMembershipStatus(StrEnum):
    """A user's belonging to a project (M8.8)."""

    ACTIVE = "active"
    REMOVED = "removed"


class EnvironmentKind(StrEnum):
    """Deployment target profiles (mirrors Epic 7's environment vocabulary; the
    Actuator, ADR-0022, consumes these later — the foundation only records them)."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"
    CUSTOM = "custom"


# ── aggregates ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class User:
    """A person. Platform-global identity (not org-scoped); joins organizations via
    a `Membership`."""

    id: str
    email: str
    display_name: str = ""
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Organization:
    """The top tenant boundary — the root of identity, billing, and isolation. Field
    shape is intentionally compatible with `tenant.OrganizationContext`."""

    id: str
    name: str
    plan: str = "free"
    region: str = "us"
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    # M8.6 — organization settings + lifecycle status (all additive, defaulted, so
    # every existing construction remains valid and existing behaviour is unchanged).
    status: OrganizationStatus = OrganizationStatus.ACTIVE
    timezone: str = "UTC"
    language: str = "en"
    logo_url: str | None = None
    updated_at: float = 0.0

    def with_updates(
        self,
        *,
        name: str | None = None,
        timezone: str | None = None,
        language: str | None = None,
        logo_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: OrganizationStatus | None = None,
        updated_at: float | None = None,
    ) -> Organization:
        """Immutable partial update — only provided fields change (services validate)."""
        return Organization(
            id=self.id,
            name=name if name is not None else self.name,
            plan=self.plan,
            region=self.region,
            created_at=self.created_at,
            metadata=metadata if metadata is not None else dict(self.metadata),
            status=status if status is not None else self.status,
            timezone=timezone if timezone is not None else self.timezone,
            language=language if language is not None else self.language,
            logo_url=logo_url if logo_url is not None else self.logo_url,
            updated_at=updated_at if updated_at is not None else self.updated_at,
        )


@dataclass(frozen=True, slots=True)
class Workspace:
    """A team/BU boundary within an org; the scope for environments, secrets, and
    projects."""

    id: str
    org_id: str
    name: str
    slug: str
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    # M8.7 — workspace settings + lifecycle status (all additive, defaulted, so every
    # existing construction remains valid and existing behaviour is unchanged).
    description: str = ""
    icon: str = ""
    color: str = ""
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    updated_at: float = 0.0

    def with_updates(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: WorkspaceStatus | None = None,
        updated_at: float | None = None,
    ) -> Workspace:
        """Immutable partial update — only provided fields change (services validate)."""
        return Workspace(
            id=self.id,
            org_id=self.org_id,
            name=name if name is not None else self.name,
            slug=self.slug,
            created_at=self.created_at,
            metadata=metadata if metadata is not None else dict(self.metadata),
            description=description if description is not None else self.description,
            icon=icon if icon is not None else self.icon,
            color=color if color is not None else self.color,
            status=status if status is not None else self.status,
            updated_at=updated_at if updated_at is not None else self.updated_at,
        )


@dataclass(frozen=True, slots=True)
class WorkspaceMembership:
    """A user's belonging to a workspace within an org (M8.7). Role tier is carried inline
    (Admin/Member/Viewer); the concrete workspace-scoped `RoleBinding` is granted alongside
    so RBAC evaluation is unchanged."""

    id: str
    org_id: str
    workspace_id: str
    user_id: str
    role: str = WsRole.MEMBER.value
    status: WorkspaceMembershipStatus = WorkspaceMembershipStatus.ACTIVE
    created_at: float = 0.0

    def with_role(self, role: str) -> WorkspaceMembership:
        return WorkspaceMembership(
            id=self.id, org_id=self.org_id, workspace_id=self.workspace_id,
            user_id=self.user_id, role=role, status=self.status, created_at=self.created_at,
        )

    def with_status(self, status: WorkspaceMembershipStatus) -> WorkspaceMembership:
        return WorkspaceMembership(
            id=self.id, org_id=self.org_id, workspace_id=self.workspace_id,
            user_id=self.user_id, role=self.role, status=status, created_at=self.created_at,
        )


@dataclass(frozen=True, slots=True)
class Membership:
    """A user's belonging to an organization, with a lifecycle status. Role grants are
    separate (`RoleBinding`) so a member can hold several scoped roles."""

    id: str
    org_id: str
    user_id: str
    status: MembershipStatus = MembershipStatus.INVITED
    created_at: float = 0.0
    # M8.6 — invitation metadata (additive, defaulted). `invited_role` is the org tier
    # the invite carries; `expires_at` drives invitation expiry (0.0 = no expiry).
    invited_role: str = OrgRole.MEMBER.value
    expires_at: float = 0.0

    def with_status(
        self, status: MembershipStatus, *, expires_at: float | None = None
    ) -> Membership:
        """Return a copy in a new status (immutable transition; services validate)."""
        return Membership(
            id=self.id,
            org_id=self.org_id,
            user_id=self.user_id,
            status=status,
            created_at=self.created_at,
            invited_role=self.invited_role,
            expires_at=expires_at if expires_at is not None else self.expires_at,
        )


@dataclass(frozen=True, slots=True)
class RoleBinding:
    """Grants one `Role` to one user at one `RoleScope`. `scope_id` is the org id (for
    ORGANIZATION bindings) or a workspace id (for WORKSPACE bindings)."""

    id: str
    org_id: str
    user_id: str
    role: Role
    scope: RoleScope
    scope_id: str
    created_at: float = 0.0


@dataclass(frozen=True, slots=True)
class Environment:
    """A deployment target profile within a workspace (dev/staging/prod/...)."""

    id: str
    org_id: str
    workspace_id: str
    name: str
    kind: EnvironmentKind = EnvironmentKind.DEVELOPMENT
    created_at: float = 0.0


@dataclass(frozen=True, slots=True)
class Project:
    """A logical grouping of related work within a workspace — the primary execution
    boundary (M8.8; future Agent Builder/Knowledge/Deployment/Analytics operate here)."""

    id: str
    org_id: str
    workspace_id: str
    name: str
    slug: str
    created_at: float = 0.0
    # M8.8 — project settings + lifecycle status (all additive, defaulted, so every
    # existing construction remains valid and existing behaviour is unchanged).
    description: str = ""
    icon: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    updated_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_updates(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: ProjectStatus | None = None,
        updated_at: float | None = None,
    ) -> Project:
        """Immutable partial update — only provided fields change (services validate)."""
        return Project(
            id=self.id,
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            name=name if name is not None else self.name,
            slug=self.slug,
            created_at=self.created_at,
            description=description if description is not None else self.description,
            icon=icon if icon is not None else self.icon,
            status=status if status is not None else self.status,
            updated_at=updated_at if updated_at is not None else self.updated_at,
            metadata=metadata if metadata is not None else dict(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class ProjectMembership:
    """A user's belonging to a project within a workspace/org (M8.8). Role tier is carried
    inline (Admin/Member/Viewer). Reuses org/workspace membership for eligibility — a
    project member must already belong to the org; no new RBAC scope is introduced (project
    operations authorize via the existing workspace-scoped PROJECT_MANAGE/PROJECT_READ)."""

    id: str
    org_id: str
    workspace_id: str
    project_id: str
    user_id: str
    role: str = ProjectRole.MEMBER.value
    status: ProjectMembershipStatus = ProjectMembershipStatus.ACTIVE
    created_at: float = 0.0

    def with_role(self, role: str) -> ProjectMembership:
        return ProjectMembership(
            id=self.id, org_id=self.org_id, workspace_id=self.workspace_id,
            project_id=self.project_id, user_id=self.user_id, role=role,
            status=self.status, created_at=self.created_at,
        )

    def with_status(self, status: ProjectMembershipStatus) -> ProjectMembership:
        return ProjectMembership(
            id=self.id, org_id=self.org_id, workspace_id=self.workspace_id,
            project_id=self.project_id, user_id=self.user_id, role=self.role,
            status=status, created_at=self.created_at,
        )


@dataclass(frozen=True, slots=True)
class UserPreferences:
    """Per-user, per-org Studio preferences. Owned by the Consumer surface (ADR-0021);
    holds nothing platform-authoritative."""

    user_id: str
    org_id: str
    theme: str = "dark"  # dark default (Blueprint §19)
    locale: str = "en"
    keyboard_shortcuts_enabled: bool = True
    favorites: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

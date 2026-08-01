"""Workspace Lifecycle + Membership + Navigation services (M8.7) — additive.

New domain services delivered as a separate module so the M8.2 `services.py` is left
untouched (mirrors how `org_lifecycle.py` extended the Organization surface). They reuse the
same `_Base` plumbing (ids/clock/emitter/authorization/audit) and the existing repositories
— no repository redesign beyond the additive `WorkspaceRepository.update` and the new
`WorkspaceMembershipRepository`. Workspace *creation* stays in the frozen
`WorkspaceService.create_workspace`; this module owns rename/settings/archive/restore,
workspace membership, and per-user navigation (switcher/recent/favorite/default/search).

Isolation is structural: every operation is org- **and** workspace-scoped, a workspace
member must already be an org member, and workspace role bindings are granted at WORKSPACE
scope so RBAC evaluation stays exactly as `rbac` defines it.
"""

from __future__ import annotations

from .domain import (
    MembershipStatus,
    RoleBinding,
    RoleScope,
    UserPreferences,
    WorkspaceMembership,
    WorkspaceMembershipStatus,
    WorkspaceStatus,
    WsRole,
)
from .errors import (
    ConflictError,
    FoundationValidationError,
    NotFoundError,
    OrganizationIsolationError,
)
from .events import FoundationEventType
from .rbac import Permission, role_for_ws_tier
from .repositories import (
    MembershipRepository,
    OrganizationRepository,
    PreferencesRepository,
    UserRepository,
    WorkspaceMembershipRepository,
    WorkspaceRepository,
)
from .seams import PREFIX_ROLE_BINDING, PREFIX_WS_MEMBERSHIP
from .services import _Base
from .validation import FoundationValidator

# Preference metadata keys the navigation service owns (Consumer-owned state, ADR-0021).
_DEFAULT_KEY = "default_workspace_id"
_RECENT_KEY = "recent_workspace_ids"
_RECENT_LIMIT = 8


class WorkspaceLifecycleService(_Base):
    """Workspace rename, settings (description/icon/color/metadata), archive/restore, and
    activity summary. Name uniqueness within an org is enforced on rename."""

    def __init__(
        self,
        *,
        orgs: OrganizationRepository,
        workspaces: WorkspaceRepository,
        ws_memberships: WorkspaceMembershipRepository,
        validator: FoundationValidator,
        **base,
    ) -> None:
        super().__init__(**base)
        self._orgs = orgs
        self._workspaces = workspaces
        self._ws_memberships = ws_memberships
        self._validator = validator

    def _load(self, org_id: str, workspace_id: str):
        ws = self._workspaces.get(org_id, workspace_id)
        if ws is None:
            raise NotFoundError(f"workspace {workspace_id!r} not found in org {org_id!r}")
        return ws

    def _require_unique_name(self, org_id: str, workspace_id: str, name: str) -> None:
        norm = name.strip().casefold()
        for ws in self._workspaces.list(org_id):
            if ws.id != workspace_id and ws.name.strip().casefold() == norm:
                raise ConflictError(
                    f"workspace name {name!r} already exists in org {org_id!r}"
                )

    def update_workspace(
        self,
        actor_user_id: str,
        org_id: str,
        workspace_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        metadata: dict | None = None,
    ):
        self._require(
            actor_user_id, Permission.WORKSPACE_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        ws = self._load(org_id, workspace_id)
        if name is not None:
            diagnostics = self._validator.validate_workspace(name=name, slug=ws.slug)
            if diagnostics.has_errors:
                raise FoundationValidationError(diagnostics)
            self._require_unique_name(org_id, workspace_id, name)
        updated = ws.with_updates(
            name=name, description=description, icon=icon, color=color,
            metadata=metadata, updated_at=self._clock.now(),
        )
        self._workspaces.update(updated)
        settings_touched = any(v is not None for v in (description, icon, color, metadata))
        self._emit(
            FoundationEventType.WORKSPACE_SETTINGS_CHANGED
            if settings_touched and name is None
            else FoundationEventType.WORKSPACE_UPDATED,
            org_id=org_id, actor=actor_user_id, resource=workspace_id,
        )
        return updated

    def archive_workspace(self, actor_user_id: str, org_id: str, workspace_id: str):
        self._require(
            actor_user_id, Permission.WORKSPACE_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        ws = self._load(org_id, workspace_id)
        if ws.status is WorkspaceStatus.ARCHIVED:
            raise ConflictError(f"workspace {workspace_id!r} is already archived")
        updated = self._workspaces.update(
            ws.with_updates(status=WorkspaceStatus.ARCHIVED, updated_at=self._clock.now())
        )
        self._emit(
            FoundationEventType.WORKSPACE_ARCHIVED,
            org_id=org_id, actor=actor_user_id, resource=workspace_id,
        )
        return updated

    def restore_workspace(self, actor_user_id: str, org_id: str, workspace_id: str):
        self._require(
            actor_user_id, Permission.WORKSPACE_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        ws = self._load(org_id, workspace_id)
        if ws.status is not WorkspaceStatus.ARCHIVED:
            raise ConflictError(f"workspace {workspace_id!r} is not archived")
        updated = self._workspaces.update(
            ws.with_updates(status=WorkspaceStatus.ACTIVE, updated_at=self._clock.now())
        )
        self._emit(
            FoundationEventType.WORKSPACE_RESTORED,
            org_id=org_id, actor=actor_user_id, resource=workspace_id,
        )
        return updated

    def activity_summary(self, org_id: str, workspace_id: str) -> dict:
        """A deterministic current-state rollup (member counts + workspace status)."""
        ws = self._workspaces.get(org_id, workspace_id)
        members = self._ws_memberships.list(org_id, workspace_id)
        active = [wm for wm in members if wm.status is WorkspaceMembershipStatus.ACTIVE]
        by_role: dict[str, int] = {}
        for wm in active:
            by_role[wm.role] = by_role.get(wm.role, 0) + 1
        return {
            "org_id": org_id,
            "workspace_id": workspace_id,
            "status": ws.status.value if ws else "unknown",
            "total_members": len(active),
            "members_by_role": by_role,
        }

    def search(self, org_id: str, term: str, *, include_archived: bool = False):
        """Case-insensitive substring match on name/slug, org-scoped."""
        needle = term.strip().casefold()
        out = []
        for ws in self._workspaces.list(org_id):
            if not include_archived and ws.status is WorkspaceStatus.ARCHIVED:
                continue
            if needle in ws.name.casefold() or needle in ws.slug.casefold():
                out.append(ws)
        return tuple(out)


class WorkspaceMembershipService(_Base):
    """Add/remove/list workspace members. Only existing org members may join a workspace;
    each add grants a workspace-scoped role binding for the tier (Admin/Member/Viewer)."""

    def __init__(
        self,
        *,
        orgs: OrganizationRepository,
        users: UserRepository,
        workspaces: WorkspaceRepository,
        memberships: MembershipRepository,
        ws_memberships: WorkspaceMembershipRepository,
        **base,
    ) -> None:
        super().__init__(**base)
        self._orgs = orgs
        self._users = users
        self._workspaces = workspaces
        self._memberships = memberships
        self._ws_memberships = ws_memberships

    def _load_workspace(self, org_id: str, workspace_id: str):
        ws = self._workspaces.get(org_id, workspace_id)
        if ws is None:
            raise NotFoundError(f"workspace {workspace_id!r} not found in org {org_id!r}")
        return ws

    def add_member(
        self,
        actor_user_id: str,
        org_id: str,
        workspace_id: str,
        user_id: str,
        *,
        role: WsRole = WsRole.MEMBER,
    ) -> WorkspaceMembership:
        self._require(
            actor_user_id, Permission.WORKSPACE_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        self._load_workspace(org_id, workspace_id)
        # Isolation: the user must be an ACTIVE org member (cannot add a stranger / cross-org).
        org_membership = self._memberships.get(org_id, user_id)
        if org_membership is None or org_membership.status is not MembershipStatus.ACTIVE:
            raise OrganizationIsolationError(
                f"user {user_id!r} is not an active member of org {org_id!r}"
            )
        existing = self._ws_memberships.get(org_id, workspace_id, user_id)
        if existing is not None and existing.status is WorkspaceMembershipStatus.ACTIVE:
            raise ConflictError(
                f"user {user_id!r} is already a member of workspace {workspace_id!r}"
            )
        membership = WorkspaceMembership(
            id=existing.id if existing is not None else self._ids.new(PREFIX_WS_MEMBERSHIP),
            org_id=org_id, workspace_id=workspace_id, user_id=user_id, role=role.value,
            status=WorkspaceMembershipStatus.ACTIVE, created_at=self._clock.now(),
        )
        if existing is not None:
            self._ws_memberships.update(membership)
        else:
            self._ws_memberships.add(membership)
        # Grant the workspace-scoped role binding for the tier.
        self._role_bindings.add(
            RoleBinding(
                id=self._ids.new(PREFIX_ROLE_BINDING),
                org_id=org_id, user_id=user_id, role=role_for_ws_tier(role.value),
                scope=RoleScope.WORKSPACE, scope_id=workspace_id,
                created_at=self._clock.now(),
            )
        )
        self._emit(
            FoundationEventType.WORKSPACE_MEMBER_ADDED,
            org_id=org_id, actor=actor_user_id, resource=membership.id,
            workspace_id=workspace_id, user_id=user_id, role=role.value,
        )
        return membership

    def remove_member(
        self, actor_user_id: str, org_id: str, workspace_id: str, user_id: str
    ) -> WorkspaceMembership:
        self._require(
            actor_user_id, Permission.WORKSPACE_MANAGE,
            org_id=org_id, workspace_id=workspace_id,
        )
        wm = self._ws_memberships.get(org_id, workspace_id, user_id)
        if wm is None or wm.status is not WorkspaceMembershipStatus.ACTIVE:
            raise NotFoundError(
                f"user {user_id!r} is not a member of workspace {workspace_id!r}"
            )
        removed = self._ws_memberships.update(
            wm.with_status(WorkspaceMembershipStatus.REMOVED)
        )
        # Revoke the workspace-scoped bindings for this user in this workspace.
        for b in self._role_bindings.for_user(org_id, user_id):
            if b.scope is RoleScope.WORKSPACE and b.scope_id == workspace_id:
                self._role_bindings.remove(org_id, b.id)
        self._emit(
            FoundationEventType.WORKSPACE_MEMBER_REMOVED,
            org_id=org_id, actor=actor_user_id, resource=removed.id,
            workspace_id=workspace_id, user_id=user_id,
        )
        return removed

    def list_members(
        self, org_id: str, workspace_id: str
    ) -> tuple[WorkspaceMembership, ...]:
        return tuple(
            wm
            for wm in self._ws_memberships.list(org_id, workspace_id)
            if wm.status is WorkspaceMembershipStatus.ACTIVE
        )


class WorkspaceNavigationService(_Base):
    """Per-user workspace navigation: switcher/recent/favorite/default/search. State is
    Consumer-owned (ADR-0021) — persisted in the user's `UserPreferences` (favorites tuple
    + metadata for default/recent), reusing the existing preferences repository."""

    def __init__(
        self,
        *,
        workspaces: WorkspaceRepository,
        preferences: PreferencesRepository,
        **base,
    ) -> None:
        super().__init__(**base)
        self._workspaces = workspaces
        self._preferences = preferences

    def _prefs(self, org_id: str, user_id: str) -> UserPreferences:
        return self._preferences.get(org_id, user_id) or UserPreferences(
            user_id=user_id, org_id=org_id
        )

    def _save(self, prefs: UserPreferences, **changes) -> UserPreferences:
        merged = UserPreferences(
            user_id=prefs.user_id, org_id=prefs.org_id, theme=prefs.theme,
            locale=prefs.locale,
            keyboard_shortcuts_enabled=prefs.keyboard_shortcuts_enabled,
            favorites=changes.get("favorites", prefs.favorites),
            metadata=changes.get("metadata", dict(prefs.metadata)),
        )
        return self._preferences.put(merged)

    def set_default(self, org_id: str, user_id: str, workspace_id: str) -> None:
        prefs = self._prefs(org_id, user_id)
        meta = dict(prefs.metadata)
        meta[_DEFAULT_KEY] = workspace_id
        self._save(prefs, metadata=meta)

    def toggle_favorite(self, org_id: str, user_id: str, workspace_id: str) -> bool:
        prefs = self._prefs(org_id, user_id)
        favorites = list(prefs.favorites)
        if workspace_id in favorites:
            favorites.remove(workspace_id)
            favored = False
        else:
            favorites.append(workspace_id)
            favored = True
        self._save(prefs, favorites=tuple(favorites))
        return favored

    def record_recent(self, org_id: str, user_id: str, workspace_id: str) -> None:
        prefs = self._prefs(org_id, user_id)
        meta = dict(prefs.metadata)
        recent = [w for w in meta.get(_RECENT_KEY, []) if w != workspace_id]
        recent.insert(0, workspace_id)
        meta[_RECENT_KEY] = recent[:_RECENT_LIMIT]
        self._save(prefs, metadata=meta)

    def navigation(self, org_id: str, user_id: str) -> dict:
        """The switcher payload: all active workspaces + favorites/default/recent ids."""
        prefs = self._prefs(org_id, user_id)
        active = [
            ws
            for ws in self._workspaces.list(org_id)
            if ws.status is not WorkspaceStatus.ARCHIVED
        ]
        ids = {ws.id for ws in active}
        return {
            "org_id": org_id,
            "workspaces": active,
            "favorites": [w for w in prefs.favorites if w in ids],
            "recent": [w for w in prefs.metadata.get(_RECENT_KEY, []) if w in ids],
            "default_workspace_id": prefs.metadata.get(_DEFAULT_KEY),
        }

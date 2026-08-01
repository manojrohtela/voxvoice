"""Organization Lifecycle + Invitation services (M8.6) — additive.

New domain services delivered as a separate module so the M8.2 `services.py` is left
untouched. They reuse the same `_Base` plumbing (ids/clock/emitter/authorization/audit) and
the existing repositories — no repository redesign beyond the additive
`OrganizationRepository.update`. An **invitation is a pending membership** (an INVITED
`Membership` with `invited_role` + `expires_at`), so the invitation lifecycle is modelled on
the existing membership aggregate rather than a parallel one.
"""

from __future__ import annotations

from .domain import (
    Membership,
    MembershipStatus,
    OrganizationStatus,
    OrgRole,
    RoleBinding,
    RoleScope,
)
from .errors import (
    ConflictError,
    FoundationValidationError,
    NotFoundError,
)
from .events import FoundationEventType
from .rbac import Permission, role_for_org_tier
from .repositories import (
    MembershipRepository,
    OrganizationRepository,
    UserRepository,
)
from .seams import PREFIX_MEMBERSHIP, PREFIX_ROLE_BINDING
from .services import _Base
from .validation import FoundationValidator


class OrganizationLifecycleService(_Base):
    """Organization settings, rename, status, soft-delete, and activity summary."""

    def __init__(
        self,
        *,
        orgs: OrganizationRepository,
        memberships: MembershipRepository,
        validator: FoundationValidator,
        **base,
    ) -> None:
        super().__init__(**base)
        self._orgs = orgs
        self._memberships = memberships
        self._validator = validator

    def _load(self, org_id: str):
        org = self._orgs.get(org_id)
        if org is None or org.status is OrganizationStatus.DELETED:
            raise NotFoundError(f"organization {org_id!r} not found")
        return org

    def update_organization(
        self,
        actor_user_id: str,
        org_id: str,
        *,
        name: str | None = None,
        timezone: str | None = None,
        language: str | None = None,
        logo_url: str | None = None,
        metadata: dict | None = None,
    ):
        self._require(actor_user_id, Permission.ORG_MANAGE, org_id=org_id)
        org = self._load(org_id)
        if name is not None:
            diagnostics = self._validator.validate_organization(name=name)
            if diagnostics.has_errors:
                raise FoundationValidationError(diagnostics)
        updated = org.with_updates(
            name=name, timezone=timezone, language=language, logo_url=logo_url,
            metadata=metadata, updated_at=self._clock.now(),
        )
        self._orgs.update(updated)
        self._emit(
            FoundationEventType.ORGANIZATION_UPDATED,
            org_id=org_id, actor=actor_user_id, resource=org_id,
        )
        return updated

    def set_status(self, actor_user_id: str, org_id: str, status: OrganizationStatus):
        self._require(actor_user_id, Permission.ORG_MANAGE, org_id=org_id)
        org = self._load(org_id)
        updated = self._orgs.update(
            org.with_updates(status=status, updated_at=self._clock.now())
        )
        self._emit(
            FoundationEventType.ORGANIZATION_STATUS_CHANGED,
            org_id=org_id, actor=actor_user_id, resource=org_id, status=status.value,
        )
        return updated

    def soft_delete_organization(self, actor_user_id: str, org_id: str):
        """Soft-delete: mark DELETED (row retained for audit/isolation), never removed."""
        self._require(actor_user_id, Permission.ORG_MANAGE, org_id=org_id)
        org = self._load(org_id)
        updated = self._orgs.update(
            org.with_updates(status=OrganizationStatus.DELETED, updated_at=self._clock.now())
        )
        self._emit(
            FoundationEventType.ORGANIZATION_DELETED,
            org_id=org_id, actor=actor_user_id, resource=org_id,
        )
        return updated

    def activity_summary(self, org_id: str) -> dict:
        """A deterministic current-state summary (member counts by status + org status).
        The event stream (audit trail) is the detailed activity; this is the rollup."""
        org = self._orgs.get(org_id)
        members = self._memberships.list(org_id)
        counts: dict[str, int] = {}
        for m in members:
            counts[m.status.value] = counts.get(m.status.value, 0) + 1
        return {
            "org_id": org_id,
            "status": org.status.value if org else "unknown",
            "total_members": len(members),
            "members_by_status": counts,
        }


class InvitationService(_Base):
    """Invitation lifecycle over the membership aggregate: send/accept/decline/cancel/
    resend/expire, each authorized and audited. Invitation = a pending membership."""

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

    def _membership(self, org_id: str, user_id: str) -> Membership:
        m = self._memberships.get(org_id, user_id)
        if m is None:
            raise NotFoundError(f"no invitation/membership for {user_id!r} in {org_id!r}")
        return m

    def invite(
        self,
        actor_user_id: str,
        org_id: str,
        user_id: str,
        *,
        tier: OrgRole = OrgRole.MEMBER,
        ttl_seconds: float = 0.0,
    ) -> Membership:
        self._require(actor_user_id, Permission.MEMBER_MANAGE, org_id=org_id)
        if self._orgs.get(org_id) is None:
            raise NotFoundError(f"organization {org_id!r} does not exist")
        if self._users.get(user_id) is None:
            raise NotFoundError(f"user {user_id!r} does not exist")
        existing = self._memberships.get(org_id, user_id)
        if existing is not None and existing.status in (
            MembershipStatus.ACTIVE, MembershipStatus.INVITED
        ):
            raise ConflictError(f"user {user_id!r} already invited or active in {org_id!r}")
        expires_at = (self._clock.now() + ttl_seconds) if ttl_seconds > 0 else 0.0
        # A fresh invite for a previously-declined/cancelled/expired user reuses the same
        # membership row (one row per (org,user)); a brand-new invite adds a row.
        membership = Membership(
            id=existing.id if existing is not None else self._ids.new(PREFIX_MEMBERSHIP),
            org_id=org_id, user_id=user_id, status=MembershipStatus.INVITED,
            created_at=self._clock.now(), invited_role=tier.value, expires_at=expires_at,
        )
        if existing is not None:
            self._memberships.update(membership)
        else:
            self._memberships.add(membership)
        self._emit(
            FoundationEventType.MEMBER_INVITED,
            org_id=org_id, actor=actor_user_id, resource=membership.id,
            user_id=user_id, tier=tier.value,
        )
        return membership

    def accept(self, actor_user_id: str, org_id: str, user_id: str) -> Membership:
        # The invited user accepts their own invite; an admin may accept on their behalf.
        if actor_user_id != user_id:
            self._require(actor_user_id, Permission.MEMBER_MANAGE, org_id=org_id)
        m = self._membership(org_id, user_id)
        if m.status is not MembershipStatus.INVITED:
            raise ConflictError(f"invitation is not pending (status={m.status.value})")
        if m.expires_at and self._clock.now() >= m.expires_at:
            self._memberships.update(m.with_status(MembershipStatus.EXPIRED))
            raise ConflictError("invitation has expired")
        activated = self._memberships.update(m.with_status(MembershipStatus.ACTIVE))
        # Grant the org-tier role binding on acceptance.
        self._role_bindings.add(
            RoleBinding(
                id=self._ids.new(PREFIX_ROLE_BINDING),
                org_id=org_id, user_id=user_id,
                role=role_for_org_tier(m.invited_role),
                scope=RoleScope.ORGANIZATION, scope_id=org_id,
                created_at=self._clock.now(),
            )
        )
        self._emit(
            FoundationEventType.MEMBER_ACTIVATED,
            org_id=org_id, actor=actor_user_id, resource=activated.id, user_id=user_id,
        )
        return activated

    def decline(self, actor_user_id: str, org_id: str, user_id: str) -> Membership:
        if actor_user_id != user_id:
            self._require(actor_user_id, Permission.MEMBER_MANAGE, org_id=org_id)
        m = self._membership(org_id, user_id)
        if m.status is not MembershipStatus.INVITED:
            raise ConflictError("invitation is not pending")
        declined = self._memberships.update(m.with_status(MembershipStatus.DECLINED))
        self._emit(
            FoundationEventType.INVITATION_DECLINED,
            org_id=org_id, actor=actor_user_id, resource=declined.id, user_id=user_id,
        )
        return declined

    def cancel(self, actor_user_id: str, org_id: str, user_id: str) -> Membership:
        self._require(actor_user_id, Permission.MEMBER_MANAGE, org_id=org_id)
        m = self._membership(org_id, user_id)
        if m.status is not MembershipStatus.INVITED:
            raise ConflictError("invitation is not pending")
        cancelled = self._memberships.update(m.with_status(MembershipStatus.CANCELLED))
        self._emit(
            FoundationEventType.INVITATION_CANCELLED,
            org_id=org_id, actor=actor_user_id, resource=cancelled.id, user_id=user_id,
        )
        return cancelled

    def resend(
        self, actor_user_id: str, org_id: str, user_id: str, *, ttl_seconds: float = 0.0
    ) -> Membership:
        self._require(actor_user_id, Permission.MEMBER_MANAGE, org_id=org_id)
        m = self._membership(org_id, user_id)
        if m.status is not MembershipStatus.INVITED:
            raise ConflictError("invitation is not pending")
        expires_at = (self._clock.now() + ttl_seconds) if ttl_seconds > 0 else m.expires_at
        refreshed = self._memberships.update(
            m.with_status(MembershipStatus.INVITED, expires_at=expires_at)
        )
        self._emit(
            FoundationEventType.INVITATION_RESENT,
            org_id=org_id, actor=actor_user_id, resource=refreshed.id, user_id=user_id,
        )
        return refreshed

    def expire_due(self, org_id: str) -> int:
        """Sweep pending invitations past their expiry → EXPIRED. Returns the count."""
        now = self._clock.now()
        expired = 0
        for m in self._memberships.list(org_id):
            if (
                m.status is MembershipStatus.INVITED
                and m.expires_at
                and now >= m.expires_at
            ):
                self._memberships.update(m.with_status(MembershipStatus.EXPIRED))
                self._emit(
                    FoundationEventType.INVITATION_EXPIRED,
                    org_id=org_id, actor="system", resource=m.id, user_id=m.user_id,
                )
                expired += 1
        return expired

    def list_pending(self, org_id: str) -> tuple[Membership, ...]:
        return tuple(
            m for m in self._memberships.list(org_id)
            if m.status is MembershipStatus.INVITED
        )

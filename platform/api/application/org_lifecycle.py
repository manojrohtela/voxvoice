"""Organization Lifecycle + Invitation application layer (M8.6) — additive.

New commands, queries, use cases, and handler registration for the Organization Lifecycle
vertical slice, delivered as a separate module so the M8.3 `use_cases.py`/`commands.py`/
`queries.py` are left untouched. Use cases coordinate the additive Foundation services
(`org_lifecycle`/`invitations`) — no domain logic is duplicated. Authorization, transaction
boundaries, and audit are handled exactly as the M8.3 use cases do.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from api.foundation import Foundation, OrganizationView, OrgRole

from .activity import ActivityFilter
from .authorization import AuthorizationOrchestrator
from .dispatcher import CommandBus, QueryBus
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory
from .use_cases import _UseCaseBase

# ── commands ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UpdateOrganization:
    actor_user_id: str
    org_id: str
    name: str | None = None
    timezone: str | None = None
    language: str | None = None
    logo_url: str | None = None
    metadata: dict | None = None


@dataclass(frozen=True, slots=True)
class DeleteOrganization:
    actor_user_id: str
    org_id: str


@dataclass(frozen=True, slots=True)
class InviteToOrganization:
    actor_user_id: str
    org_id: str
    user_id: str
    tier: OrgRole = OrgRole.MEMBER
    ttl_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class AcceptInvitation:
    actor_user_id: str
    org_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class DeclineInvitation:
    actor_user_id: str
    org_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class CancelInvitation:
    actor_user_id: str
    org_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class ResendInvitation:
    actor_user_id: str
    org_id: str
    user_id: str
    ttl_seconds: float = 0.0


# ── queries ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetOrganizationActivity:
    org_id: str
    # M8.9 — additive pagination/filter/time-range (defaults reproduce prior behaviour).
    filter: ActivityFilter = field(default_factory=ActivityFilter)


@dataclass(frozen=True, slots=True)
class ListPendingInvitations:
    org_id: str


# ── use cases ────────────────────────────────────────────────────────────────────


def _member_view(m) -> dict:
    return {
        "membership_id": m.id, "org_id": m.org_id, "user_id": m.user_id,
        "status": m.status.value, "invited_role": m.invited_role,
        "expires_at": m.expires_at,
    }


class OrgLifecycleUseCases(_UseCaseBase):
    """Write side of the Organization Lifecycle."""

    def _stage(self, uow, type_, use_case, *, org_id, actor, **data):
        uow.stage(
            ApplicationEvent(
                id=self._ids.new(PREFIX_APP_EVENT), type=type_, ts=self._clock.now(),
                use_case=use_case, outcome="completed", org_id=org_id, actor=actor,
                data=dict(data),
            )
        )

    def update_organization(self, cmd: UpdateOrganization) -> OrganizationView:
        with self._uow.new() as uow:
            org = self._f.org_lifecycle.update_organization(
                cmd.actor_user_id, cmd.org_id, name=cmd.name, timezone=cmd.timezone,
                language=cmd.language, logo_url=cmd.logo_url, metadata=cmd.metadata,
            )
            self._stage(
                uow, ApplicationEventType.ORGANIZATION_UPDATED, "update_organization",
                org_id=cmd.org_id, actor=cmd.actor_user_id,
            )
        return OrganizationView.of(org)

    def delete_organization(self, cmd: DeleteOrganization) -> OrganizationView:
        with self._uow.new() as uow:
            org = self._f.org_lifecycle.soft_delete_organization(cmd.actor_user_id, cmd.org_id)
            self._stage(
                uow, ApplicationEventType.ORGANIZATION_DELETED, "delete_organization",
                org_id=cmd.org_id, actor=cmd.actor_user_id,
            )
        return OrganizationView.of(org)

    def invite(self, cmd: InviteToOrganization) -> dict:
        with self._uow.new() as uow:
            m = self._f.invitations.invite(
                cmd.actor_user_id, cmd.org_id, cmd.user_id,
                tier=cmd.tier, ttl_seconds=cmd.ttl_seconds,
            )
            self._stage(
                uow, ApplicationEventType.MEMBER_ONBOARDED, "invite_to_organization",
                org_id=cmd.org_id, actor=cmd.actor_user_id, user_id=cmd.user_id,
            )
        return _member_view(m)

    def accept(self, cmd: AcceptInvitation) -> dict:
        with self._uow.new() as uow:
            m = self._f.invitations.accept(cmd.actor_user_id, cmd.org_id, cmd.user_id)
            self._stage(
                uow, ApplicationEventType.MEMBER_ONBOARDED, "accept_invitation",
                org_id=cmd.org_id, actor=cmd.actor_user_id, user_id=cmd.user_id,
            )
        return _member_view(m)

    def decline(self, cmd: DeclineInvitation) -> dict:
        with self._uow.new():
            m = self._f.invitations.decline(cmd.actor_user_id, cmd.org_id, cmd.user_id)
        return _member_view(m)

    def cancel(self, cmd: CancelInvitation) -> dict:
        with self._uow.new():
            m = self._f.invitations.cancel(cmd.actor_user_id, cmd.org_id, cmd.user_id)
        return _member_view(m)

    def resend(self, cmd: ResendInvitation) -> dict:
        with self._uow.new():
            m = self._f.invitations.resend(
                cmd.actor_user_id, cmd.org_id, cmd.user_id, ttl_seconds=cmd.ttl_seconds
            )
        return _member_view(m)


@dataclass(frozen=True, slots=True)
class OrgLifecycleQueries:
    """Read side — activity summary + feed (from the Foundation audit event stream) and
    pending invitations. No transactions, no mutation."""

    foundation: Foundation

    def activity(self, query: GetOrganizationActivity) -> dict:
        # Summary is domain state (member counts + status); the feed is a durable projection
        # over the append-only audit stream (M8.9), paginated + filterable.
        summary = self.foundation.org_lifecycle.activity_summary(query.org_id)
        page = self.foundation.activity.organization_activity(
            query.org_id, **query.filter.kwargs()
        )
        return {"summary": summary, **page}

    def pending_invitations(self, query: ListPendingInvitations) -> list[dict]:
        return [_member_view(m) for m in self.foundation.invitations.list_pending(query.org_id)]


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
    """Wire the M8.6 use cases + handlers into the existing buses (additive)."""
    uc = OrgLifecycleUseCases(
        foundation=foundation, authz=authz, uow_factory=uow_factory, ids=ids, clock=clock
    )
    q = OrgLifecycleQueries(foundation=foundation)
    command_bus.register(UpdateOrganization, uc.update_organization)
    command_bus.register(DeleteOrganization, uc.delete_organization)
    command_bus.register(InviteToOrganization, uc.invite)
    command_bus.register(AcceptInvitation, uc.accept)
    command_bus.register(DeclineInvitation, uc.decline)
    command_bus.register(CancelInvitation, uc.cancel)
    command_bus.register(ResendInvitation, uc.resend)
    query_bus.register(GetOrganizationActivity, q.activity)
    query_bus.register(ListPendingInvitations, q.pending_invitations)

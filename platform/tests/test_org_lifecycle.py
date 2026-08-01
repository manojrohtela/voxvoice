"""Organization Lifecycle vertical slice (Epic 8, M8.6) tests.

Covers every layer end-to-end: Foundation unit behaviour (settings/soft-delete/invitation
lifecycle/expiry over the in-memory wiring), and the full Control Plane API + persistence
integration — the Definition-of-Done flow (sign in → create org → configure settings →
invite → accept → activity → manage members) plus authorization failures, cross-org
isolation, invitation expiry, and soft-delete behaviour. The Studio UI is a static bundle
served at /studio (verified reachable); its behaviour is exercised through these API paths.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import (
    ConflictError,
    CreateOrganizationRequest,
    FixedClock,
    FoundationFactory,
    MembershipStatus,
    OrganizationStatus,
    OrgRole,
    RegisterUserRequest,
    SequentialIdGenerator,
)

# ── Foundation unit (in-memory wiring) ───────────────────────────────────────────


def _foundation():
    return FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())


def _user(f, email):
    return f.identity.register_user(RegisterUserRequest(email, "U"))


def _org(f):
    owner = _user(f, "owner@acme.io")
    org = f.identity.create_organization(
        CreateOrganizationRequest(name="Acme", owner_user_id=owner.id)
    )
    return owner, org


def test_update_and_soft_delete_organization():
    f = _foundation()
    owner, org = _org(f)
    updated = f.org_lifecycle.update_organization(
        owner.id, org.id, name="Acme Corp", timezone="Asia/Kolkata", language="hi",
        logo_url="https://x/y.png",
    )
    assert updated.name == "Acme Corp" and updated.timezone == "Asia/Kolkata"
    # regression: soft-delete marks DELETED; the row is retained (get raises NotFound in
    # the lifecycle service, but the underlying org still exists via the base repo)
    f.org_lifecycle.soft_delete_organization(owner.id, org.id)
    assert f.identity.get_organization(org.id).status is OrganizationStatus.DELETED


def test_invitation_lifecycle_accept_grants_tier_role():
    f = _foundation()
    owner, org = _org(f)
    invitee = _user(f, "dev@acme.io")
    f.invitations.invite(owner.id, org.id, invitee.id, tier=OrgRole.ADMIN)
    pending = f.invitations.list_pending(org.id)
    assert [m.user_id for m in pending] == [invitee.id]
    # the invitee accepts their own invite → ACTIVE + org_admin binding
    m = f.invitations.accept(invitee.id, org.id, invitee.id)
    assert m.status is MembershipStatus.ACTIVE
    assert [b.role.value for b in f.access.roles_of(org.id, invitee.id)] == ["org_admin"]


def test_invitation_decline_and_cancel():
    f = _foundation()
    owner, org = _org(f)
    u = _user(f, "x@acme.io")
    f.invitations.invite(owner.id, org.id, u.id)
    m = f.invitations.decline(u.id, org.id, u.id)
    assert m.status is MembershipStatus.DECLINED
    # re-invite then cancel (admin action)
    f.invitations.invite(owner.id, org.id, u.id)
    m2 = f.invitations.cancel(owner.id, org.id, u.id)
    assert m2.status is MembershipStatus.CANCELLED


def test_invitation_expiry():
    f = _foundation()
    owner, org = _org(f)
    u = _user(f, "e@acme.io")
    # ttl in the past (FixedClock advances by 1.0 per call, so a tiny ttl expires quickly)
    f.invitations.invite(owner.id, org.id, u.id, ttl_seconds=0.001)
    expired = f.invitations.expire_due(org.id)
    assert expired == 1
    # accepting an expired invitation is rejected
    with pytest.raises(ConflictError):
        f.invitations.accept(u.id, org.id, u.id)


# ── API + persistence integration (end-to-end) ───────────────────────────────────


def _client() -> TestClient:
    settings = CustomerApiSettings(environment="testing", database_url="sqlite://")
    container = Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
    return TestClient(create_app(container=container))


def _register(c, email):
    return c.post("/v1/auth/register", json={"email": email, "display_name": "U"}).json()["id"]


def _auth(c, uid):
    tok = c.post("/v1/auth/token", json={"user_id": uid}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_definition_of_done_end_to_end():
    with _client() as c:
        # 1. sign in
        owner = _register(c, "founder@acme.io")
        oh = _auth(c, owner)
        # 2. create org
        org = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()
        oid = org["id"]
        # 3. configure settings
        upd = c.patch(f"/v1/organizations/{oid}",
                      json={"name": "Acme Corp", "timezone": "Asia/Kolkata", "language": "hi"},
                      headers=oh)
        assert upd.status_code == 200 and upd.json()["timezone"] == "Asia/Kolkata"
        # 4. invite another member
        member = _register(c, "teammate@acme.io")
        inv = c.post(f"/v1/organizations/{oid}/invitations",
                     json={"user_id": member, "tier": "admin"}, headers=oh)
        assert inv.status_code == 201
        pend = c.get(f"/v1/organizations/{oid}/invitations", headers=oh).json()
        assert pend[0]["user_id"] == member
        # 5. accept the invitation (as the invited member)
        mh = _auth(c, member)
        acc = c.post(f"/v1/organizations/{oid}/invitations/{member}/accept", headers=mh)
        assert acc.status_code == 200 and acc.json()["status"] == "active"
        # 6. view organization activity
        act = c.get(f"/v1/organizations/{oid}/activity", headers=oh).json()
        types = [f["type"] for f in act["feed"]]
        assert "organization.created" in types and "member.activated" in types
        assert act["summary"]["total_members"] == 2
        # 7. manage members
        members = c.get(f"/v1/organizations/{oid}/members", headers=oh).json()
        assert {m["user_id"] for m in members} == {owner, member}
        rm = c.delete(f"/v1/organizations/{oid}/members/{member}", headers=oh)
        assert rm.status_code == 200 and rm.json()["status"] == "suspended"


def test_authorization_failure_on_update():
    with _client() as c:
        owner = _register(c, "o2@acme.io")
        oh = _auth(c, owner)
        oid = c.post("/v1/organizations", json={"name": "A"}, headers=oh).json()["id"]
        outsider = _register(c, "out@acme.io")
        c.post(f"/v1/organizations/{oid}/invitations",
               json={"user_id": outsider, "tier": "viewer"}, headers=oh)
        # accept as viewer, then try to update org settings (viewer lacks ORG_MANAGE)
        vh = _auth(c, outsider)
        c.post(f"/v1/organizations/{oid}/invitations/{outsider}/accept", headers=vh)
        r = c.patch(f"/v1/organizations/{oid}", json={"name": "Hacked"}, headers=vh)
        assert r.status_code == 403


def test_cross_org_isolation():
    with _client() as c:
        a = _register(c, "a@x.io")
        ah = _auth(c, a)
        org_a = c.post("/v1/organizations", json={"name": "A"}, headers=ah).json()["id"]
        b = _register(c, "b@x.io")
        bh = _auth(c, b)
        org_b = c.post("/v1/organizations", json={"name": "B"}, headers=bh).json()["id"]
        # user A cannot update org B (not a member/authorized) → 403
        r = c.patch(f"/v1/organizations/{org_b}", json={"name": "X"}, headers=ah)
        assert r.status_code == 403
        # and A's org is unaffected
        assert c.get(f"/v1/organizations/{org_a}", headers=ah).json()["name"] == "A"


def test_soft_delete_hides_org():
    with _client() as c:
        owner = _register(c, "d@acme.io")
        oh = _auth(c, owner)
        oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
        assert c.delete(f"/v1/organizations/{oid}", headers=oh).json()["status"] == "deleted"
        # a soft-deleted org reads as not-found through the lifecycle-guarded update path
        r = c.patch(f"/v1/organizations/{oid}", json={"name": "Y"}, headers=oh)
        assert r.status_code == 404


def test_studio_ui_is_served():
    with _client() as c:
        r = c.get("/studio/")
        assert r.status_code == 200 and "VoxVoice Studio" in r.text

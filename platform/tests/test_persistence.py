"""Customer Platform Persistence Layer (Epic 8, M8.4) tests.

Persistence-backed integration tests: the same Foundation + Application behaviour, now over
SQLModel repositories and the transactional `SqlUnitOfWork`, wired by
`build_persistent_application`. Verifies: full command/query round-trips are durable; the
SQL repositories satisfy the Foundation interfaces (RBAC + org isolation still hold);
**real atomic rollback** (a failed Unit of Work undoes the DB writes — the capability the
M8.3 in-memory Unit of Work could not provide); commit persists; cross-instance durability
(data survives a fresh Application over the same database file); the migration structure
(create/upgrade/downgrade); row↔domain mapping keeps persistence types out of the domain;
and the dependency boundary (persistence imports Foundation/Application but neither imports
persistence, no FastAPI/HTTP/frozen-runtime).

The existing in-memory unit tests (`test_foundation.py`, `test_application.py`) are kept —
they exercise the default in-memory wiring, which is byte-for-byte unchanged.
"""

from __future__ import annotations

import ast
import pathlib
import tempfile

import pytest

from api.application import ApplicationEventType as AET
from api.application import (
    CreateOrganization,
    CreateWorkspace,
    GetOrganization,
    GrantRole,
    InviteMember,
    ListMembers,
    ListWorkspaces,
    RecordingApplicationEventEmitter,
    RegisterUser,
)
from api.foundation import (
    ConflictError,
    FixedClock,
    MembershipStatus,
    Permission,
    Role,
    RoleScope,
    SequentialIdGenerator,
    User,
)
from api.persistence import (
    Database,
    SqlUnitOfWorkFactory,
    SqlUserRepository,
    build_persistent_application,
)
from api.persistence.migrations import initial


def _persistent_app(url: str = "sqlite://"):
    return build_persistent_application(
        url=url, ids=SequentialIdGenerator(), clock=FixedClock()
    )


def _boot(app):
    owner = app.commands.dispatch(RegisterUser("owner@acme.test", "Owner"))
    org = app.commands.dispatch(CreateOrganization(name="Acme", owner_user_id=owner.id))
    return owner, org


# ── durable round-trip through the persistent Application ─────────────────────────


def test_full_roundtrip_is_persisted():
    pa = _persistent_app()
    app = pa.application
    owner, org = _boot(app)
    ws = app.commands.dispatch(CreateWorkspace(owner.id, org.id, "Web", "web"))
    # queries read back from the database
    assert app.queries.dispatch(GetOrganization(org.id)).id == org.id
    assert [w.id for w in app.queries.dispatch(ListWorkspaces(org.id))] == [ws.id]
    # the owner was auto-provisioned (ACTIVE member + ORG_OWNER binding) — over SQL
    members = app.queries.dispatch(ListMembers(org.id))
    assert len(members) == 1 and members[0].status == MembershipStatus.ACTIVE.value
    pa.database.close()


def test_rbac_and_isolation_hold_over_sql():
    pa = _persistent_app()
    app = pa.application
    owner, org = _boot(app)
    dev = app.commands.dispatch(RegisterUser("dev@acme.test", "Dev"))
    app.commands.dispatch(InviteMember(owner.id, org.id, dev.id))
    ws_a = app.commands.dispatch(CreateWorkspace(owner.id, org.id, "A", "a"))
    ws_b = app.commands.dispatch(CreateWorkspace(owner.id, org.id, "B", "b"))
    app.commands.dispatch(
        GrantRole(owner.id, org.id, dev.id, Role.DEVELOPER, RoleScope.WORKSPACE, ws_a.id)
    )
    # workspace-scoped grant authorizes ws_a but not ws_b (RBAC over SQL bindings)
    assert app.foundation.access.can(
        dev.id, Permission.PROJECT_MANAGE, org_id=org.id, workspace_id=ws_a.id
    )
    assert not app.foundation.access.can(
        dev.id, Permission.PROJECT_MANAGE, org_id=org.id, workspace_id=ws_b.id
    )
    pa.database.close()


# ── real atomic rollback (the new capability) ────────────────────────────────────


def test_unit_of_work_rolls_back_db_writes_on_error():
    pa = _persistent_app()
    db = pa.database
    repo = SqlUserRepository(db)
    uow_factory = SqlUnitOfWorkFactory(db, RecordingApplicationEventEmitter())
    # regression: a write inside a Unit of Work that raises is UNDONE in the database
    with pytest.raises(RuntimeError):
        with uow_factory.new():
            repo.add(User(id="usr_rollback", email="x@y.test"))
            raise RuntimeError("boom")
    assert repo.get("usr_rollback") is None  # rolled back — real atomicity
    db.close()


def test_unit_of_work_commit_persists_db_writes():
    pa = _persistent_app()
    db = pa.database
    repo = SqlUserRepository(db)
    uow_factory = SqlUnitOfWorkFactory(db, RecordingApplicationEventEmitter())
    with uow_factory.new():
        repo.add(User(id="usr_commit", email="c@d.test"))
    # regression: a clean Unit of Work commits the write durably
    assert repo.get("usr_commit") is not None
    db.close()


def test_failed_command_leaves_no_partial_data_and_audits_rejection():
    pa = _persistent_app()
    app = pa.application
    owner, org = _boot(app)
    app.commands.dispatch(CreateWorkspace(owner.id, org.id, "W", "dup"))
    with pytest.raises(ConflictError):
        app.commands.dispatch(CreateWorkspace(owner.id, org.id, "W2", "dup"))
    # only the first workspace exists; the failed command persisted nothing
    assert len(app.queries.dispatch(ListWorkspaces(org.id))) == 1
    # ...and the rejection was still audited
    assert app.emitter.of_type(AET.USE_CASE_REJECTED)  # type: ignore[attr-defined]
    pa.database.close()


# ── cross-instance durability (real persistence, not a per-run cache) ─────────────


def test_data_survives_a_fresh_application_over_the_same_database():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        url = f"sqlite:///{tmp.name}"
    pa1 = _persistent_app(url)
    owner, org = _boot(pa1.application)
    pa1.application.commands.dispatch(CreateWorkspace(owner.id, org.id, "W", "w"))
    pa1.database.session.commit()  # ensure flushed to file
    pa1.database.close()
    # a brand-new Application over the same file sees the committed data
    pa2 = build_persistent_application(
        url=url, ids=SequentialIdGenerator(), clock=FixedClock(), create_all=False
    )
    got = pa2.application.queries.dispatch(GetOrganization(org.id))
    assert got is not None and got.name == "Acme"
    assert len(pa2.application.queries.dispatch(ListWorkspaces(org.id))) == 1
    pa2.database.close()


# ── migration structure ──────────────────────────────────────────────────────────


def test_migration_upgrade_and_downgrade():
    db = Database("sqlite://")
    initial.upgrade(db)  # creates cp_* tables
    repo = SqlUserRepository(db)
    with SqlUnitOfWorkFactory(db, RecordingApplicationEventEmitter()).new():
        repo.add(User(id="usr_m", email="m@n.test"))
    assert repo.get("usr_m") is not None
    assert initial.VERSION == "0001"
    initial.downgrade(db)  # drops cp_* tables (declarative teardown)
    db.close()


# ── mapping keeps persistence types out of the domain ────────────────────────────


def test_repositories_return_frozen_domain_aggregates_not_rows():
    pa = _persistent_app()
    app = pa.application
    owner, org = _boot(app)
    got = app.foundation.identity.get_organization(org.id)
    # regression: the repo returns the frozen domain aggregate, never a SQLModel row
    assert type(got).__name__ == "Organization"
    assert "Row" not in type(got).__name__
    pa.database.close()


# ── dependency boundary ──────────────────────────────────────────────────────────


def _imported_modules(pkg_path: str) -> set[str]:
    """Full dotted module paths imported anywhere under `pkg_path` (AST-precise —
    never matches the word inside a docstring or comment)."""
    mods: set[str] = set()
    for p in pathlib.Path(pkg_path).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                mods.add(n.module)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    mods.add(a.name)
    return mods


def test_persistence_boundary_and_layers_unchanged():
    used_tops = {m.split(".")[0] for m in _imported_modules("api/persistence")}
    forbidden = {
        "fastapi", "starlette", "httpx",
        "runtime", "gateway", "replay", "memory", "knowledge", "context", "prompt",
        "reasoning", "provider", "providers", "bridge", "pipeline", "product", "tool",
        "skill", "workflow", "agent", "loop", "mcp", "hooks", "integration",
        "observability", "voicebot",
    }
    # regression: persistence has no HTTP/frozen-runtime import...
    assert not (used_tops & forbidden), used_tops & forbidden
    # ...and it does import the layers it wires + the ORM
    assert {"api", "sqlmodel"} <= used_tops
    # regression: neither Foundation nor Application imports persistence (direction kept)
    for layer in ("api/foundation", "api/application"):
        offenders = {m for m in _imported_modules(layer) if "persistence" in m}
        assert not offenders, f"{layer} imports persistence: {offenders}"

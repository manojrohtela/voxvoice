# VoxVoice — Customer Platform Persistence Layer (Epic 8, M8.4)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/customer-platform-persistence-layer.md` |
| **Status** | 🟢 **Implemented (M8.4)** — third Epic 8 implementation milestone. Component specification per Constitution §A11.3. |
| **Date** | 2026-07-27 |
| **Package** | `platform/api/persistence/` (persistence composition root over the M8.2 Foundation + M8.3 Application; subpackage of the Control Plane — no new top-level package, no placement ADR) |
| **Scope** | SQLModel-backed repository implementations of the Foundation's 8 interfaces; a production transactional `UnitOfWork`; row↔domain mapping; query helpers; a migration structure. **Out of scope:** FastAPI/HTTP, frontend, agents, deployment. **No interface changes.** |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) M8.4 under ADR-0021/0022. No new ADR, no redesign, no ownership change. The [M8.2 Foundation](customer-platform-foundation.md) and [M8.3 Application](customer-platform-application-layer.md) are **byte-for-byte unchanged**; frozen Epics 5/6/7 untouched. |
| **Related** | [Foundation (M8.2)](customer-platform-foundation.md) · [Application (M8.3)](customer-platform-application-layer.md) · [Implementation Plan](../../roadmap/epic8-implementation-plan.md) · Constitution §A2.4/§A7/§A9/§A11.2/§A11.3 · existing `api/db.py`+`api/models.py` · [INDEX](../../INDEX.md) |

## Responsibilities

The **only production implementation** of the Foundation's repository interfaces and the
Application's `UnitOfWork` abstraction — SQLModel-backed, transactional, swapped in at a
composition root. It replaces the in-memory defaults for production **without any interface
change**, so the Foundation and Application never learn a database exists.

## Dependencies

`api.persistence → api.foundation, api.application, core, tenant, sqlmodel/sqlalchemy` —
test-enforced. It is the Customer-Platform **composition root** (the only such package that
imports both the Foundation and the Application, mirroring `pipeline`/`integration`). **No**
FastAPI/HTTP; **no** frozen Epic 5/6/7 runtime. Crucially, **neither the Foundation nor the
Application imports persistence** (AST-verified) — dependency direction preserved, both
layers byte-for-byte unchanged.

## Module map

| Module | Responsibility |
|---|---|
| `models.py` | SQLModel tables (`UserRow`…`PreferencesRow`), `cp_*` namespaced, JSON columns; `CP_TABLES` |
| `database.py` | `Database` — engine + the shared `Session` the repos use and the Unit of Work commits; `create_all` |
| `mapping.py` | Pure row ↔ frozen-domain-aggregate translation (no persistence leakage into the domain) |
| `queries.py` | Reusable org-scoped SELECT helpers (`get_by_pk`/`first_where`/`all_where`) — no business logic |
| `repositories.py` | `Sql*Repository` — production impls of the 8 Foundation interfaces; read/write the session, **never commit** |
| `unit_of_work.py` | `SqlUnitOfWork`/`SqlUnitOfWorkFactory` — the production `UnitOfWork`: DB commit/rollback + event outbox |
| `composition.py` | `build_persistent_application(...)` / `build_foundation(...)` — wire SQL repos + Unit of Work into Foundation/Application |
| `migrations/` | Migration structure (`initial.py` = `0001`, ordered `MIGRATIONS`, README) — declarative schema only, no app logic |

## Repository implementations

Eight `Sql*Repository` classes (User/Organization/Workspace/Membership/RoleBinding/
Environment/Project/UserPreferences) satisfy the Foundation interfaces exactly (no interface
change). Each reads/writes the shared `Database.session`, maps row↔domain at the boundary,
and **org-scopes every read** by construction (§A11.2). Repositories `flush()` but **never
commit or rollback** — the Unit of Work owns the transaction boundary.

## Unit of Work (transactions)

`SqlUnitOfWork` implements the **existing** `api.application.UnitOfWork` abstraction (no
interface change). Over a real DB transaction (commit-as-you-go on the shared session):

- **commit** → `session.commit()` **then** publish the staged application events (events
  observable only after durable success — the §A5 outbox shape);
- **rollback** (on any raised exception) → `session.rollback()` **and** discard staged events.

Repository writes inside a `with uow:` block are therefore **atomic** — durable together on
commit, gone together on rollback. **This closes the honest limitation M8.3 documented**: the
in-memory Unit of Work rolled back events but not data; the SQL Unit of Work rolls back both.

## Mapping strategy

`Domain → Persistence Model → Domain`, isolated in `mapping.py`. Repositories accept/return
**frozen Foundation domain aggregates**; `*Row` SQLModel types never escape the persistence
package (the Foundation/Application never import `sqlmodel` or a `*Row`). Enum values
round-trip through their string form; `metadata`/`favorites` via JSON columns. Pure, total,
deterministic — verified by a test asserting repos return `Organization`, never a `*Row`.

## Migrations

`migrations/` provides the **structure** only (no application logic): ordered, idempotent
modules exposing `VERSION`/`DESCRIPTION`/`upgrade`/`downgrade` performing declarative schema
ops on the `cp_*` tables (`0001` = `initial.py`). The bootstrap today is
`Database.create_all()`; the append-only `MIGRATIONS` registry lets a runner (e.g. Alembic —
not yet a dependency) adopt the same shape later.

## Determinism & scale

Behaviour is deterministic (§A2.4) via the shared `Clock`/`IdGenerator` seams. A single
shared session is correct for the single-worker Control Plane (§A9 Stage 1–2); per-request
`scoped_session` is the documented Stage-3+ upgrade for multi-worker deployments. SQLite
(dev/test) and Postgres/Supabase (prod) run unchanged (JSON columns portable).

## Tests

`tests/test_persistence.py` — 9 persistence-backed integration tests: durable round-trip;
RBAC + org isolation over SQL; **real atomic rollback** (write in a failing Unit of Work is
undone in the DB) + commit persists; a failed command leaves no partial data yet still
audits the rejection; **cross-instance durability** (data survives a fresh Application over
the same database file); migration upgrade/downgrade; repos return frozen domain aggregates
(no row leakage); and the dependency boundary (persistence imports Foundation/Application +
ORM but neither imports persistence; no FastAPI/HTTP/frozen-runtime). The existing in-memory
unit tests (`test_foundation.py`/`test_application.py`) are **kept** (default wiring
unchanged). **9/9 pass; 1595/1595 repository-wide; ruff clean; Foundation, Application, and
Epics 5/6/7 byte-for-byte unchanged.**

## Non-goals (later milestones)

The FastAPI router/HTTP layer (M8.5+); per-request session scoping / connection pooling for
multi-worker; a full migration runner; FK constraints (a later migration).

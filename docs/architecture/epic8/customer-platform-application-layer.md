# VoxVoice — Customer Platform Application Layer (Epic 8, M8.3)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/customer-platform-application-layer.md` |
| **Status** | 🟢 **Implemented (M8.3)** — the second Epic 8 implementation milestone. Component specification per Constitution §A11.3. |
| **Date** | 2026-07-27 |
| **Package** | `platform/api/application/` (a subpackage of the Control Plane; orchestration layer over the M8.2 Foundation — **not** a new top-level package, no placement ADR) |
| **Scope** | Use cases, command/query handlers (CQRS), transactions (Unit of Work), authorization orchestration, audit orchestration, validation orchestration, DTO mapping, application events. **Out of scope:** FastAPI/HTTP/WebSocket, frontend, database, deployment, agents, knowledge. |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) M8.3 under [ADR-0021](../../adr/ADR-0021-studio-consumer-and-management-surface.md)/[ADR-0022](../../adr/ADR-0022-deployment-actuator-runtime-actuation.md). No new ADR, no redesign, no ownership change. The [M8.2 Foundation](customer-platform-foundation.md) and frozen Epics 5/6/7 are **byte-for-byte unchanged**. |
| **Related** | [Foundation (M8.2)](customer-platform-foundation.md) · [Blueprint](product-blueprint.md) · [Implementation Plan](../../roadmap/epic8-implementation-plan.md) · Constitution §A2.4/§A2.5/§A5/§A11.2/§A11.3/§A11.4 · [INDEX](../../INDEX.md) |

## Responsibilities

The orchestration layer between a future API/transport layer and the Foundation. It owns
**use cases, CQRS command/query dispatch, transaction boundaries, authorization/audit/
validation orchestration, DTO mapping, and application-level events** — and nothing else.
The **Foundation remains responsible only for domain behaviour**; the Application never
duplicates validation, permission logic, or persistence — it coordinates them.

## Inputs / Outputs

- **Inputs:** immutable command/query messages dispatched through the `CommandBus`/
  `QueryBus`.
- **Outputs:** Foundation **view DTOs** (response projections) from handlers; **application
  events** (`use_case.*` audit + composite business events) via the emitter; typed errors
  (Foundation's, propagated unchanged; plus `ApplicationError`/`NoHandlerError`).

## Dependencies

`api.application → api.foundation, core, tenant` only — test-enforced
(`test_application.py::test_application_imports_only_foundation_core_tenant`). **No**
FastAPI/Starlette/httpx/SQLModel/Pydantic, **no** database, **no** frozen Epic 5/6/7
runtime; the API/transport layer does not exist yet (also test-asserted). Authorization is
orchestrated by calling the Foundation's `tenant.access.Authorizer` seam — no permission
logic lives in the Application (or, later, the API).

## Module map

| Module | Responsibility |
|---|---|
| `commands.py` | Immutable write-intent messages (RegisterUser/CreateOrganization/CreateWorkspace/CreateEnvironment/InviteMember/ActivateMember/RemoveMember/GrantRole/RevokeRole/CreateProject/UpdatePreferences) |
| `queries.py` | Immutable read messages (GetUser/GetOrganization/GetWorkspace/ListWorkspaces/ListMembers/ListProjects/ListEnvironments/GetPreferences) |
| `use_cases.py` | `CommandUseCases`/`QueryUseCases` — coordinate authorize→transaction→Foundation→map→event; no domain-logic duplication |
| `dispatcher.py` | `CommandBus` (routes + cross-cutting audit) / `QueryBus` (pure routing); DI by message type |
| `handlers.py` | Thin CQRS adapters mapping each message type to a use-case method; `register_*_handlers` DI |
| `authorization.py` | `AuthorizationOrchestrator` over the Foundation authorizer seam + `tenant_for` |
| `unit_of_work.py` | `UnitOfWork` transaction abstraction + in-memory impl (application-event outbox) + factory |
| `mapping.py` | Pure command→Foundation-request and domain→view-DTO translation |
| `events.py` | `ApplicationEventType`/`ApplicationEvent` (disjoint from Foundation vocabulary) + emitter seam |
| `errors.py` | `ApplicationError`/`NoHandlerError`/`UnsupportedOperationError` (rooted in `core.errors.PlatformError`) |
| `application.py` | `ApplicationFactory` composition root + `Application` facade; `.default()` = in-memory, deterministic |

## Key behaviours

- **Use-case pipeline (write):** authorize (Foundation authorizer) → open Unit of Work →
  Foundation domain call (validates + persists + emits its **own** domain event) → map to a
  view DTO → stage the application (composite) event → commit.
- **Transactions:** the Application owns the boundary. Application events stage in the Unit
  of Work and publish atomically **on commit**; on **rollback** (any raised error) they are
  discarded. *Honest limitation, documented:* with the in-memory Foundation repositories the
  Unit of Work provides the boundary + event outbox but does not roll back in-memory writes;
  durable repository-level atomicity arrives with the DB-backed Unit of Work (interface-first).
- **Audit orchestration (cross-cutting):** the `CommandBus` emits exactly one
  `use_case.completed` (success) or `use_case.rejected` (any error) per dispatch — the
  rejection audit is emitted directly, surviving the rolled-back transaction. Auditing can
  never be forgotten per handler.
- **No event duplication:** Foundation domain events (`organization.created`, …) and
  Application events (`organization.provisioned`, `use_case.*`, …) are **disjoint
  vocabularies at different altitudes** — asserted disjoint in tests.

## Deferred (would require a Foundation change — out of scope this milestone)

- **`UpdateOrganization`** — mutating an org needs an additive Foundation capability
  (`OrganizationRepository.update` + an `Organization` transition); the frozen Foundation has
  none, so the command is intentionally absent.
- **Hard member delete** — `RemoveMember` maps to the Foundation's `SUSPENDED` transition
  (soft-remove); a hard delete would need an additive Foundation capability.

## Tests

`tests/test_application.py` — 13 tests: bus dispatch returns view DTOs; workspace
provisioning + mapping; authorization orchestration (deny → reject + audit); grant-then-
authorized; cross-cutting completion audit; Unit-of-Work commit publishes / rollback
discards + rejection audit survives; Foundation domain events still fire and are disjoint
from application events; soft-remove via suspend; preferences round-trip; deterministic
dispatch under shared seams; and the acyclic `api.application → api.foundation, core,
tenant` boundary + "no API layer yet" assertion. **13/13 pass; 1586/1586 repository-wide;
ruff clean; Foundation and Epics 5/6/7 byte-for-byte unchanged.**

## Non-goals (later milestones)

The FastAPI router/HTTP/WebSocket layer over the buses (M8.4+); a durable DB-backed Unit of
Work + repositories; agents/knowledge/deployment/analytics surfaces.

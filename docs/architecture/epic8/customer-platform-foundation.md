# VoxVoice — Customer Platform Foundation (Epic 8, M8.2)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/customer-platform-foundation.md` |
| **Status** | 🟢 **Implemented (M8.2)** — the first Epic 8 implementation milestone. Component specification per Constitution §A11.3. |
| **Date** | 2026-07-27 |
| **Package** | `platform/api/foundation/` (a subpackage of the Control Plane; **not** a new top-level package — no placement ADR required) |
| **Scope** | Identity (User/Organization/Workspace/Membership/RoleBinding), Platform (Environment/Project/UserPreferences), Security (RBAC foundation + permission evaluation + organization isolation), Configuration (settings + feature flags), and Infrastructure (repository/service/event seams, validation, DTOs, factory). **Explicitly out of scope:** Agents, Prompts, Knowledge, Testing, Deployment, Billing, Marketplace, Coach, Analytics, Sessions. |
| **Authority** | Realizes the [Epic 8 Implementation Plan](../../roadmap/epic8-implementation-plan.md) M8.2 milestone under [ADR-0021](../../adr/ADR-0021-studio-consumer-and-management-surface.md) (Studio owns no runtime state) and [ADR-0022](../../adr/ADR-0022-deployment-actuator-runtime-actuation.md). No new ADR, no architecture redesign, no ownership change. Frozen Epics 5/6/7 untouched. |
| **Related** | [Product Blueprint](product-blueprint.md) · [Implementation Plan](../../roadmap/epic8-implementation-plan.md) · [ADR-0021](../../adr/ADR-0021-studio-consumer-and-management-surface.md) · Constitution §A2.4/§A2.5/§A2.6/§A11.2/§A11.3/§A11.4/§A12 · [INDEX](../../INDEX.md) |

## Responsibilities

The base every future Epic 8 feature builds on. It owns **control-plane
identity/tenancy/authoring/management state only** (ADR-0021): registering users,
creating organizations (auto-provisioning the owner), managing workspaces/projects/
environments, org membership lifecycle, RBAC role grants + deterministic permission
evaluation, organization isolation, user preferences, and deterministic feature-flag
resolution. It emits typed domain events (the in-memory audit trail).

It does **not** own or touch: runtime/session/execution/provider state, deployment
execution (ADR-0022's Actuator), or any frozen Epic. It never actuates anything.

## Inputs / Outputs

- **Inputs:** request DTOs (`RegisterUserRequest`, `CreateOrganizationRequest`,
  `CreateWorkspaceRequest`, `InviteMemberRequest`, `GrantRoleRequest`,
  `CreateProjectRequest`, `CreateEnvironmentRequest`, `UpdatePreferencesRequest`) + an
  `actor_user_id` for authorized mutations; an injected `TenantContext` at the
  `Authorizer` bridge.
- **Outputs:** immutable domain aggregates + view DTOs (read projections for the Consumer
  surface), `FoundationDiagnostics` (deterministic finding sets), and `FoundationEvent`s.

## Dependencies

`api.foundation → core, tenant` only — test-enforced
(`test_foundation.py::test_foundation_imports_only_core_and_tenant`). It imports **no**
frozen runtime/execution/product package and **no** web/ORM framework (FastAPI/SQLModel/
Pydantic) in the domain layer — the domain stays pure and independently testable; a later
milestone adds the FastAPI router layer over these services. Reuses the frozen
`tenant.access.Authorizer`/`AccessDecision`/`Effect` seam (implemented, not duplicated)
and `tenant.context.TenantContext`.

## Module map (the closed-package shape)

| Module | Responsibility |
|---|---|
| `diagnostics.py` | `Diagnostic`/`Severity`/`FoundationDiagnosticCode`/`FoundationDiagnostics` — deterministic, ordered finding sets (mirrors `product.diagnostics`) |
| `errors.py` | Typed error hierarchy rooted in `core.errors.PlatformError` (Validation/NotFound/Conflict/OrganizationIsolation/Authorization) |
| `seams.py` | Deterministic seams: `Clock` (System/Fixed), `IdGenerator` (Default/Sequential), id prefixes |
| `events.py` | `FoundationEventType`/`FoundationEvent` (control-plane domain envelope) + `EventEmitter` seam + recording/null sinks |
| `domain.py` | Immutable aggregates + enums (`Role`, `RoleScope`, `MembershipStatus`, `EnvironmentKind`) |
| `config.py` | `OrganizationSettings`/`WorkspaceSettings`/`FeatureFlag` + deterministic `FeatureFlagResolver` |
| `rbac.py` | `Permission`, `ROLE_PERMISSIONS` (all 11 ADR-0021 roles), pure `PermissionEvaluator`, `RbacAuthorizer` (tenant-seam bridge) |
| `validation.py` | Stateless `FoundationValidator` (never raises; returns diagnostics) |
| `dto.py` | Request + view DTOs (transport-agnostic; no FastAPI) |
| `repositories.py` | Repository **interfaces** + deterministic in-memory implementations (org-scoped by construction) |
| `services.py` | Six domain services (Identity/Membership/Access/Workspace/Project/Configuration): validate → isolate → authorize → persist → emit |
| `factory.py` | `FoundationFactory` composition root + the `Foundation` facade; `.default()` = fully in-memory, deterministic |

## Lifecycle / key behaviours

- **Bootstrap:** `create_organization` auto-provisions an **ACTIVE owner membership + an
  org-scoped `ORG_OWNER` role binding** — a new org is never left without an admin
  (Blueprint §7). `create_workspace` auto-provisions a default `dev` environment.
- **RBAC:** an ORGANIZATION binding covers every workspace in the org; a WORKSPACE binding
  covers exactly its workspace — verified not to leak across workspaces.
- **Isolation (§A11.2):** every repository lookup is org-scoped; cross-org reads return
  `None` and cross-org role grants raise `OrganizationIsolationError`.

## Emitted events

`organization.created`, `workspace.created`, `project.created`, `environment.created`,
`member.invited|activated|suspended`, `role.granted|revoked`, `settings.updated`,
`feature_flag.updated`, `preferences.updated`, `access.granted|denied` — one producer per
fact (§A5); a denied action is always audited, never silent.

## Failure modes → recovery

Invalid input → `FoundationValidationError` carrying full diagnostics (fail-fast, §A2.6).
Missing aggregate → `NotFoundError`. Uniqueness/lifecycle violation → `ConflictError`.
Cross-org access → `OrganizationIsolationError`. Missing permission → `AuthorizationError`
(+ `access.denied` audit event). All rooted in `core.errors.PlatformError`; no raw error
crosses a boundary.

## Determinism

All behaviour (validation, permission evaluation, isolation, flag resolution) is
deterministic; the only non-deterministic edges — id minting and the wall clock — are the
injected `IdGenerator`/`Clock` seams (§A2.4). `test_construction_is_deterministic_under_
injected_seams` pins that identical seams yield identical ids and event streams.

## Tests

`tests/test_foundation.py` — 17 tests: bootstrap/auto-provisioning, workspace default
environment, project slug/uniqueness, RBAC scope non-leak, service authorization denial,
org-owner org-wide authorization, cross-org grant + lookup isolation, typed-event/audit
emission, deterministic flag precedence, the `tenant.access.Authorizer` bridge,
dark-default preferences round-trip, immutability, deterministic construction, and the
acyclic `api.foundation → core, tenant` dependency boundary. **17/17 pass; 1573/1573
repository-wide; ruff clean; zero frozen-Epic files modified.**

## Non-goals (deferred to later Epic 8 milestones)

Agents/Prompts/Knowledge/Testing/Deployment/Billing/Marketplace/Coach/Analytics/Sessions;
the FastAPI router/HTTP layer over these services; a durable System-of-Record backing
(the repository interfaces are the seam — in-memory today, per M8.1's staged plan).

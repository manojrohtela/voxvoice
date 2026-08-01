# VoxVoice — Customer Platform Control Plane API (Epic 8, M8.5)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/customer-platform-control-plane-api.md` |
| **Status** | 🟢 **Implemented (M8.5)** — fourth Epic 8 implementation milestone. Component specification (§A11.3) + API architecture + OpenAPI documentation. |
| **Date** | 2026-07-27 |
| **Package** | `platform/api/customer/` (thin FastAPI transport boundary; subpackage of the Control Plane — no new top-level package, no placement ADR; its own `create_app()`, so the legacy `api/main.py` app is untouched) |
| **Scope** | HTTP transport, request parsing/serialization, DI, authentication (bearer/JWT identity — no authorization), request context, centralized error translation, middleware, health, OpenAPI, versioning. **Out of scope:** Studio, Agents, Knowledge, Deployment. |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) M8.5 under ADR-0021/0022. No new ADR, no redesign, no ownership change. The [M8.2 Foundation](customer-platform-foundation.md), [M8.3 Application](customer-platform-application-layer.md), and [M8.4 Persistence](customer-platform-persistence-layer.md) layers are **byte-for-byte unchanged**; frozen Epics 5/6/7 untouched. |
| **Related** | [Foundation](customer-platform-foundation.md) · [Application](customer-platform-application-layer.md) · [Persistence](customer-platform-persistence-layer.md) · [Implementation Plan](../../roadmap/epic8-implementation-plan.md) · Constitution §A2.6/§A4/§A11.2/§A11.3/§A11.4 · [INDEX](../../INDEX.md) |

## Responsibilities

The **thin transport boundary** exposing the Customer Platform over HTTP. It owns **only**:
HTTP transport, request parsing, response serialization, dependency injection,
authentication (identity resolution), request context, error translation, OpenAPI, and
versioning. It **does not** own business rules, validation, authorization logic,
transactions, or persistence — those live in M8.2–M8.4 and are unchanged. Controllers map
an HTTP request to an Application command/query, dispatch it, and serialize the result.

## Dependencies

`api.customer → api.application, api.persistence, api.foundation (types), core, tenant,
fastapi/pydantic/starlette` — test-enforced. No frozen Epic 5/6/7 runtime import. The lower
layers never import `api.customer` (AST-verified) — dependency direction preserved.

## Module map

| Module | Responsibility |
|---|---|
| `app.py` | `create_app()` — assembles lifecycle, middleware, exception handlers, routers, OpenAPI |
| `config.py` | `CustomerApiSettings` — env-driven, validated (prod ≠ default secret); OpenAPI tags |
| `container.py` | DI `Container` (holds the wired `PersistentApplication`) + FastAPI providers; fully replaceable for tests |
| `context.py` | `RequestContext` (request/correlation ids, user/org/workspace, trace) |
| `jwt.py` | Dependency-free HS256 JWT encode/decode (stdlib only) |
| `auth.py` | Bearer/JWT `authenticate` dependency + dev `issue_token`; identity only, **no authorization** |
| `middleware.py` | `ContextMiddleware` (correlation id, timing, metrics, structured log, security headers) + `RequestMetrics` |
| `errors.py` | Centralized exception handlers: typed error → HTTP status + uniform envelope |
| `schemas.py` | Pydantic HTTP request/response models + `from_view` mappers + `ERROR_RESPONSES` |
| `routers/` | Domain routers (auth, organizations, workspaces, members, projects, environments, preferences, system) |

## Dependency injection

`Container.build(settings)` calls the M8.4 `build_persistent_application(...)` to wire SQL
repositories + the transactional `SqlUnitOfWork` into the Foundation/Application, and holds
the result on `app.state.container`. FastAPI providers (`get_application`/`get_database`/
`get_settings`) read it. Everything is replaceable: a test builds a `Container` over an
in-memory database with deterministic seams and passes it to `create_app(container=...)` —
no production wiring runs, nothing else is overridden.

## Authentication flow

1. `POST /v1/auth/register` (public) → creates a user.
2. `POST /v1/auth/token` (public, **dev/test issuance**) → mints an HS256 identity JWT
   (`sub`=user id, optional `org`). A production deployment replaces this with an IdP /
   credential check; it mints identity only, never a role.
3. Client sends `Authorization: Bearer <jwt>`. The `authenticate` dependency verifies the
   signature + expiry and resolves identity into the `RequestContext`. **No permission
   decision is made here** — authorization is orchestrated by the Application against the
   Foundation authorizer (ADR-0021). An org-scoped route takes `org_id` from the path; the
   controller forwards `actor=context.user_id` + `org_id` into the command, and the
   Application denies (→ 403) if the caller is not authorized in that org.

## Request lifecycle

```
HTTP request
  → ContextMiddleware: assign request_id + correlation_id, start timer, set RequestContext
  → CORS / GZip
  → route: parse body (Pydantic HTTP DTO) + path params
  → authenticate dependency: verify bearer JWT → identity into RequestContext
  → controller: build Application command/query (actor from context, org from path)
  → Application bus.dispatch → (authorize → UnitOfWork → Foundation → persistence)
  → controller: map returned view DTO → Pydantic response
  → ContextMiddleware: record metric, stamp X-Request-ID/X-Correlation-ID/X-Response-Time,
    security headers, structured access log
HTTP response
```
On any raised typed error the centralized handlers translate it (below); controllers never
catch.

## Middleware

Structured access logging, correlation ids (honour inbound `X-Correlation-ID`), request
timing (`X-Response-Time-ms`), in-process request metrics (`RequestMetrics`), security
headers (`X-Content-Type-Options`/`X-Frame-Options`/`Referrer-Policy`), GZip compression,
and CORS. Transport concerns only.

## Error handling

Centralized handlers (`register_exception_handlers`) map typed errors to HTTP + a uniform
`{error:{type,message,request_id,...}}` envelope; 5xx never leak internal detail.

| Error (source layer) | HTTP |
|---|---|
| `FoundationValidationError` (+ diagnostics) | 422 |
| `AuthorizationError` / `OrganizationIsolationError` | 403 |
| `NotFoundError` | 404 |
| `ConflictError` | 409 |
| `JWTError` (auth) | 401 |
| `NoHandlerError` / `ApplicationError` | 400 |
| `PlatformError` (typed root) / unexpected | 500 |

## Endpoints (OpenAPI)

Auto-generated at `/openapi.json` (+ `/docs`, `/redoc`), versioned `1.0.0`, with tags,
descriptions, examples, per-route error responses, and an HTTP **bearer** security scheme.

| Method & path | Tag | Application message |
|---|---|---|
| `POST /v1/auth/register` | auth | `RegisterUser` |
| `POST /v1/auth/token` | auth | (dev token issuance) |
| `GET /v1/auth/me` | auth | (identity from context) |
| `POST /v1/organizations` | organizations | `CreateOrganization` |
| `GET /v1/organizations/{org_id}` | organizations | `GetOrganization` |
| `POST /v1/organizations/{org_id}/workspaces` | workspaces | `CreateWorkspace` |
| `GET /v1/organizations/{org_id}/workspaces` | workspaces | `ListWorkspaces` |
| `GET /v1/organizations/{org_id}/workspaces/{workspace_id}` | workspaces | `GetWorkspace` |
| `POST …/workspaces/{workspace_id}/environments` | environments | `CreateEnvironment` |
| `GET …/workspaces/{workspace_id}/environments` | environments | `ListEnvironments` |
| `POST /v1/organizations/{org_id}/members` | members | `InviteMember` |
| `GET /v1/organizations/{org_id}/members` | members | `ListMembers` |
| `POST …/members/{user_id}/roles` | members | `GrantRole` |
| `DELETE …/members/{user_id}` | members | `RemoveMember` (soft-remove) |
| `POST …/workspaces/{workspace_id}/projects` | projects | `CreateProject` |
| `GET …/workspaces/{workspace_id}/projects` | projects | `ListProjects` |
| `GET /v1/organizations/{org_id}/preferences` | preferences | `GetPreferences` |
| `PUT /v1/organizations/{org_id}/preferences` | preferences | `UpdatePreferences` |
| `GET /health`, `/ready`, `/version` | system | (health/readiness/version) |

## Health

`/health` (liveness — up + configured), `/ready` (readiness — configuration valid,
**database reachable** via `SELECT 1`, Application wired; 503 if not), `/version`. **No
runtime checks** (never touches the Epic 5/6/7 runtime).

## Configuration

`CustomerApiSettings` (frozen) loads from `VOXVOICE_API_*` env vars with dev defaults and
validates at startup: environment ∈ {development, testing, production}; production may not
run on the default JWT secret; positive token TTL (fail fast, §A2.6).

## Tests

`tests/test_customer_api.py` — 15 integration tests: full HTTP flow (register→token→org→
workspace→member→role→project), preferences round-trip, authentication (missing/invalid/
expired → 401, `me`), DI replaceability, error translation (422+diagnostics / 403 / 409 /
404), health/ready/version, OpenAPI generation (schema/tags/security/version), middleware
(correlation + security headers), and architecture compliance (domain controllers contain
no repository/transaction/permission symbols; lower layers never import the API). **15/15
pass; 1610/1610 repository-wide; ruff clean; Foundation, Application, Persistence, and Epics
5/6/7 byte-for-byte unchanged.**

## Architecture compliance

- Controllers own no business logic, no direct repository usage, no transaction logic, no
  permission evaluation (test-asserted on the domain routers).
- Authorization is forwarded to the Application; never evaluated in controllers.
- No domain object is exposed directly (HTTP DTO → Application command → view DTO → response
  DTO).
- Dependency direction preserved; lower layers unchanged; frozen Epics untouched.

## Non-goals (later milestones)

Studio frontend (its own placement ADR); Agents/Knowledge/Deployment routes; a real IdP /
credential store replacing dev token issuance; per-request `scoped_session` + connection
pooling for multi-worker (the §A9 Stage-3+ upgrade noted in M8.4).

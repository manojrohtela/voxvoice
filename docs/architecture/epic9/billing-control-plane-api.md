# Billing Control Plane API — Component Specification (M9.5)

**Status:** 🟢 Implemented · **Date:** 2026-08-04 · **Milestone:** M9.5
**Package:** `platform/billing/customer/`
**Authority:** Engineering Constitution v2 §A2.6, §A9, §A10, §A11.2, §A11.3, §A12 · ADR-0023 · ADR-0031
**Rationale:** DL-0115 · **Amends** ADR-0031 IR1 (new **IR1b**, see §12)

---

## 1. Purpose

The thin HTTP transport for the Billing Platform. Every request follows one path:

```
HTTP → command/query → Billing Application → Billing Foundation → Persistence
```

**Nothing bypasses the Application** — asserted by an AST test that every route body reaches
`commands.dispatch` or `queries.dispatch`.

This layer owns transport and nothing else. It never authenticates a user, evaluates a
permission, calculates a price or a tax, enforces an entitlement, opens a transaction, touches
a repository, or moves money. Each of those is asserted at source level, not just intended.

---

## 2. Components Implemented

| Component | Module |
|---|---|
| Application factory (`create_billing_app`) + lifespan | `app.py` |
| Environment-driven settings + OpenAPI tags | `config.py` |
| DI container + FastAPI providers | `container.py` |
| Per-request context (identity + tracing) | `context.py` |
| HS256 identity **verification** (no issuance) | `jwt.py` |
| Authentication dependency (`authenticate`, `require_org`) | `auth.py` |
| Centralized error → HTTP translation | `errors.py` |
| Middleware: context, correlation, timing, metrics, logging, security headers, payload limit | `middleware.py` |
| Pydantic request/response DTOs | `schemas.py` |
| 15 routers (14 bounded contexts + system) | `routers/` |

**39 paths / 46 operations.** Domain routes are mounted under a single configurable prefix
(`/v1/billing`); probes live at the root.

---

## 3. API Package Structure

```
platform/billing/customer/
├── __init__.py      # public surface
├── app.py           # create_billing_app(...) — lifespan, middleware, routers, OpenAPI
├── config.py        # BillingApiSettings (VOXVOICE_BILLING_*) + OPENAPI_TAGS
├── container.py     # BillingContainer + get_container/settings/application/ids/clock
├── context.py       # BillingRequestContext — carries identity, decides nothing
├── jwt.py           # HS256 verify + decode_any (rotation). No encode, deliberately.
├── auth.py          # authenticate / require_org
├── errors.py        # the one translation table
├── middleware.py    # BillingContextMiddleware, PayloadLimit, RequestMetrics
├── schemas.py       # wire DTOs — to_*() inbound, from_view()/from_aggregate() outbound
└── routers/
    ├── system.py    accounts.py  catalog.py     pricing.py    subscriptions.py
    ├── entitlements.py  metering.py  ledger.py  credits.py    invoices.py
    └── payments.py  refunds.py   taxes.py       contracts.py  licenses.py
```

### 3.1 Why a separate ASGI application

This is **not** mounted inside the Epic 8 Control Plane. ADR-0023 §4 gives revenue a distinct
failure domain: a Billing deployment that is down, degraded, or mid-migration must not take
conversations or deployments with it. Two apps in two processes is the cheapest possible way
to guarantee that, and mounting would have re-coupled precisely what the ownership boundary
separates. Billing also remains absent from `platform/bootstrap.py` (ADR-0031 CR2) — still
asserted.

---

## 4. Dependency Injection Architecture

`BillingContainer` holds the wired `PersistentBillingApplication` from the M9.4 composition
root, plus the settings and the two seams (`IdGenerator`, `Clock`). The API **constructs
nothing below itself** — no repository, no Unit of Work, no Foundation factory, no database
(source-asserted).

Every dependency is replaceable. A test builds a container over an in-memory database and
passes it to `create_billing_app(container=...)`, overriding nothing else; individual providers
are also swappable through `dependency_overrides`.

**The authorizer is an injected dependency with no default.** Billing owns no RBAC, so this
layer cannot supply one. Omitting it leaves the Application's `DenyAllAuthorizer` in place and
every command returns 403 — the intended posture for a deployment that has not yet wired the
Control Plane's §A11.2 evaluator (M9.18): **inert, not open.** Asserted.

---

## 5. Authentication Flow

```
Authorization: Bearer <HS256 JWT>
        │
        ├─ verify signature against primary + rotated secrets
        ├─ enforce expiry (expired fails *as expired*, never as "invalid signature")
        ├─ require a subject claim
        └─ resolve BillingRequestContext(user_id, org_id)
```

Identity only. **Billing never authenticates a user, issues a token, or grants a role**
(ADR-0023 §5.3): there is no `/auth` route, no login, and no `encode` in the plane's JWT
module — all three asserted.

`jwt.py` is a stdlib re-implementation of the Epic 8 wire format, **not an import**: ADR-0031
IR3 forbids `billing` importing `api`, and an import would also make revenue's availability
depend on the Customer Platform's package. Twelve lines of stdlib HMAC is the cheaper price.
The shared contract is the token format and the signing secret — configuration, not a code
edge. `VOXVOICE_BILLING_JWT_SECRET` falls back to `VOXVOICE_API_JWT_SECRET` so a deployment
configures one identity secret rather than two that can silently diverge.

**Interop is proven, not assumed:** every authenticated test mints its token with Epic 8's own
`api.customer.jwt.encode`.

**The organization always comes from the verified token, never from the request body** — a
caller cannot name a tenant it did not authenticate as. Asserted by posting usage whose body
scope names a foreign org and showing the record lands under the token's org and the named org
sees nothing.

`require_org` additionally demands an org claim on org-owned routes. That is a *shape* check
(a command acting on an organization needs one to act on), so its absence is 401 — not 403,
which would falsely imply a permission decision was made here.

---

## 6. Request Lifecycle

```
CORS → GZip → payload limit → request context ──► auth dependency ──► controller
                                                                          │
                                              command/query ──► CommandBus/QueryBus
                                                                          │
                                        authorize → transaction → Foundation → repositories
                                                                          │
   response DTO ◄── from_aggregate()/from_view() ◄────────── aggregate/view
```

Controllers do exactly five things: parse, take identity, construct the message, dispatch,
serialize. They never catch an exception — translation is centralized, and a router that
caught one would translate it twice, differently. Asserted on the AST with docstrings stripped.

### 6.1 Two return shapes, made explicit

Application **commands** return immutable Foundation aggregates; **queries** return view DTOs.
The wire models therefore carry two constructors — `from_aggregate()` and `from_view()` — and
each call site uses the right one. Collapsing them would have "worked" wherever the two shapes
coincidentally share field names and silently dropped fields wherever they do not; this was a
real defect during implementation, found because the shapes genuinely differ.

---

## 7. Middleware

Correlation ids (inbound `X-Correlation-ID` honoured so a trace survives the hop from the
Customer Platform; generated otherwise) · request ids · timing (`X-Response-Time-ms`) ·
in-process request metrics · structured access logging · security headers (nosniff, DENY,
no-referrer, no-store, Permissions-Policy, HSTS — suppressed over plain HTTP) · GZip
compression · CORS · payload limit (413).

**The access log carries no body and no token** — Billing request bodies carry money and tax
identifiers, and those do not belong in a log line. Asserted.

The request metrics are *transport* metrics (requests, statuses, latency). They are
emphatically not `metric.*` business measurements: Billing consumes those and never produces
one (ADR-0023 M8 / IR9), and a test now proves the transport layer cannot emit any event at
all — it has no emitter and stages nothing.

---

## 8. Error Handling

One table, in `errors.py`:

| Error | Status |
|---|---|
| `BillingValidationError` | **422** + every diagnostic |
| `InvalidMoney` / `CurrencyMismatch` | 422 |
| `BillingAuthorizationError` / `OrganizationIsolationError` | 403 |
| `BillingNotFound` | 404 |
| `BillingConflict` / `InvalidBillingTransition` / `ImmutabilityViolation` / `EntitlementNotDecidable` | 409 |
| `BillingJWTError` | 401 |
| `NoHandlerError` / `BillingApplicationError` | 400 |
| anything else | 500, detail withheld |

Three deliberate choices:

* **`ImmutabilityViolation` is 409, not 500.** Refusing to alter an issued invoice or a sealed
  usage period is the system working correctly; a caller must be able to tell that apart from a
  fault.
* **422 carries every finding**, so a caller fixing a request needs one round trip, not five.
* **5xx never carries the exception message.** A stack detail on an internal fault is an
  information leak; every 4xx carries its typed message because the caller can act on it.
  Asserted with a deliberately leaky exception.

Every response, including errors, carries `X-Request-ID`, and the envelope repeats it so a
support conversation and a log line join up.

---

## 9. OpenAPI

Auto-generated and versioned; 15 tags, one per bounded context, with **every declared tag used
and every used tag declared**; bearer security scheme declared and required on every non-probe
operation; request examples on the schemas a caller most needs (`MoneyBody`, `PeriodBody`,
`OpenAccountBody`, `RecordUsageBody`, `DefineMeterBody`, `ErrorEnvelope`); and **per-route
error responses (401/403/404/409/422/500) on every domain route** — all asserted rather than
hand-maintained.

`MoneyBody.minor` is typed `integer` in the published schema: the Foundation's integer
minor-unit discipline reaches the wire, because a rounding decision made in a JSON serializer
is a rounding decision nobody reviewed (RS5).

---

## 10. Testing

**74 integration tests**, all driving the real ASGI app over HTTP through real middleware, real
auth, the real Application and the real M9.4 database. There are no controller unit tests — a
controller that is only transport has nothing to unit test; what needs proving is that the
whole path behaves.

| Group | Coverage |
|---|---|
| Wiring | health / ready (3 checks) / version; probes unauthenticated; startup fails fast on bad config; production refuses default secret, wildcard CORS, in-memory DB; Billing is a separate app |
| Authentication | missing / malformed / foreign-signed / expired / subject-less tokens; org claim required; **key rotation**; Epic 8 token interop; no token issuance anywhere; org comes from the token not the body |
| Authorization forwarding | deny → 403; default wiring denies; a recording authorizer receives the token's subject, org, and a `billing.*` permission; no controller names a permission |
| Full flow | accounts (incl. hierarchy, close-retains), catalog, pricing, subscriptions (incl. an illegal trigger → 409), entitlements + snapshot classifications + quota-as-information, idempotent usage ingestion, quarantine, ledger + credit balances, invoice draft → issue → 409 on re-issue, payment intent with no provider call, refund idempotency **and separation of duties**, tax profile, read-only contracts, license issue/revoke |
| Errors | 404/422-with-diagnostics/409/500-without-detail; the translation table verified directly; no controller catches |
| Middleware | correlation echo + generation, security headers, HSTS suppression, metrics, 413, CORS preflight, error responses carry correlation, logs carry no body or token |
| OpenAPI | generation, versioning, tags, bearer scheme, security on every non-probe route, per-route errors, examples, `/docs` |
| DI | container replaceable, provider overrides, deterministic seams, API builds nothing below itself |
| Architecture | import allow/deny lists, no SQLModel, no persistence models, lower layers never import `customer`, controllers touch no repository/transaction/pricing/entitlement logic, every route dispatches through a bus, no domain type in the published schema, frozen surfaces unchanged, Billing absent from `bootstrap.py` |

**Repository-wide: 2089/2089 pass** (2012 prior + 74 new + 3 added to the boundary suite, zero
regression). `ruff check` and `ruff format --check` clean.

---

## 11. Defect Found and Fixed in M9.4

`SqlPlanRepository.add` raised `IntegrityError` when the Application stored a published plan.
`PlanRepository` declares `add` and no `replace`, and `publish_plan` legitimately calls `add` a
second time for the same `(org, code, version)` — flipping `published` and re-storing. Against
the in-memory fakes M9.3 tested with, the second call overwrote; against a real table with the
uniqueness constraint the catalog needs, **the first HTTP request to publish a plan returned
500.**

Fixed in `billing/persistence/repositories.py`: `add` upserts on the natural key. A plan version
is mutable until published and the contract offers no other write, so `add` means *store this
version*. Fixing it there keeps the Foundation contract and the frozen Application untouched;
adding a `replace` to the contract would have changed both.

This is the milestone's clearest argument for integration tests over layer-local ones: three
green suites and 2012 passing tests did not surface it, and the first real HTTP request did.

---

## 12. Amendment to ADR-0031 — IR1b

**IR1b: `billing/customer/**` may import `fastapi`, `starlette`, and `pydantic`.**

IR1 confines the plane to `core` + `billing` + stdlib. An HTTP transport cannot be implemented
under that rule. Exactly as with **IR1a** at M9.4 (`sqlmodel` in `persistence`), the exception
is **scoped to one sub-package**, has its own assertion, and leaves the original rule at full
strength for `foundation` and `application` — both are asserted to import no web framework and
to be unaware the API exists. Three M9.2 boundary assertions were **tightened into per-layer
checks rather than relaxed**, and two new ones were added (`test_domain_layers_read_no_ambient_clock`,
`test_transport_emits_no_events`), so the suite is stricter after this milestone than before it.

Related scoping, same principle: the clock-seam rule now permits `time` in exactly two transport
modules — a request stopwatch and a token-expiry check, neither of which is a domain clock read
— and pins that with a dedicated test. IR9 is now checked on the layers that can emit, with a
stronger assertion that the transport layer has no emitter at all; the `metric.*` strings in
`schemas.py` are OpenAPI examples of an event a caller *supplies*, which documents consumption.

---

## 13. Known Limitations (stated, not hidden)

1. **`POST /catalog/plans?publish=true` defines and publishes in one request.** `PublishPlan`
   takes the aggregate `DefinePlan` returned, so a standalone publish endpoint would have to
   re-read the plan — which a controller may not do. The controller sequences two dispatches
   and decides nothing. A `GetPlan` query in a later Application milestone gives publish its own
   endpoint.
2. **`PUT /taxes/profiles` names the aggregate.** `UpsertTaxProfile` takes a fully-formed
   `TaxProfile`, so the controller supplies an id and timestamp from the container's injected
   `IdGenerator`/`Clock` — the same seams every layer below was wired with, never an invented id
   or a wall-clock read. A `TaxProfileRequest` DTO plus a Foundation factory method (the shape
   every other aggregate has) removes it at M9.15.
3. **Contracts are read-only.** `RecordContract` takes a whole `Contract` aggregate; building
   one from a body would put a nested enterprise-agreement schema in transport with no
   Foundation factory to validate it. Enterprise contracts are also signed offline — a
   self-serve mint endpoint is the wrong product shape, not just the wrong layering. Creation
   lands with M9.21.
4. **No rate limiting.** Epic 8's limiter is a single-node in-process throttle; rather than copy
   it, Billing relies on the deployment edge until a shared store exists. `max_request_bytes` is
   enforced.
5. **Not wired into any deployment entrypoint yet.** There is no `main.py`; M9.18 adds the
   process, the real RBAC authorizer, and the deployment configuration.
6. **One shared database session** (inherited from M9.4) — correct for the single-worker Control
   Plane (§A9 Stage 1–2); `scoped_session` is the Stage-3+ upgrade.

---

## 14. Next

**M9.6+ per the Epic 9 implementation plan.** The commercial spine is now reachable end to end
over HTTP — every subsequent domain milestone extends a live surface rather than a library, and
the four gates (G1 shadow → G4) have something to run against.

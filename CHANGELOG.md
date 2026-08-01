# Changelog

All notable releases of the VoxVoice platform. Versioning follows SemVer at the
platform-baseline level (see `docs/releases/`).

## Unreleased — Epic 8 (Customer Platform) · 🟡 Design only (M8.0A)

**Product Blueprint & Customer Platform Architecture** — the master, design-only
blueprint for **VoxVoice Studio**, the customer-facing surface over the frozen Runtime
(Epic 5) / Execution (Epic 6) / Product Runtime (Epic 7) stacks. No code, no APIs, no
database tables, and no changes to any frozen Epic (blueprint:
[`docs/architecture/epic8/product-blueprint.md`](docs/architecture/epic8/product-blueprint.md)).

### Added

- **Epic 8 Product Blueprint (M8.0A)** — `docs/architecture/epic8/product-blueprint.md`.
  Delivers all 24 requested artifacts (vision/personas/RBAC/24-module breakdown/customer
  journey/navigation/screen inventory/product hierarchy/SaaS/AI-platform/deployment/
  analytics/billing/security/marketplace/AI Product Manager/UI/competitive analysis/
  roadmap/risks/recommendation). Organizing invariant: **Studio is a Constitution-§A0
  Consumer + a management/authoring surface that owns no runtime state** — every module
  is traced to the frozen capability that owns its data. Names the one honest gap (a new
  additive **Deployment Actuator** is required because Epic 7 froze at deployment
  *intent*). Recommends — does not ratify — **ADR-0021** (Studio-owns-no-state) plus
  ADR-0022/0023/0024. Indexed in `docs/INDEX.md`; rationale **DL-0086**.
- **ADR-0021 ratified (M8.0B)** — `docs/adr/ADR-0021-studio-consumer-and-management-surface.md`.
  Ratifies the Studio ownership model: **VoxVoice Studio is a Consumer + Management
  Surface that owns no runtime state** — it owns only UI, user workflows, the
  authoring/management experience, orchestration/deploy *requests*, presentation,
  projections, and navigation; it never mutates execution/conversation/memory/knowledge/
  deployment/session/provider state, the billing ledger, or the event stream. Fixes the
  `Studio → Platform`, never `Platform → Studio` dependency direction (acyclic, AR-15),
  an Ownership Matrix mapping every Blueprint module to its authoritative owner, and the
  Allowed/Forbidden Writes lists. Mandatory on every future Epic 8 milestone; any
  deviation requires a superseding ADR. Verified consistent with the Constitution
  (§A2.5 one-writer-per-fact) and ADR-0018/0019/0020 (no conflict). Register/INDEX
  updated; cross-referenced to the Blueprint. Rationale **DL-0087**.
- **ADR-0022 ratified (M8.0C)** — `docs/adr/ADR-0022-deployment-actuator-runtime-actuation.md`.
  Defines the **Deployment Actuator**: a new component (a new top-level `platform/actuator/`
  package, peer of `product/`) that is the **sole owner of deployment execution**. It
  consumes the frozen Epic 7 `DeploymentPlan` **read-only**, drives real infrastructure
  only through pluggable **Infrastructure Adapters** (§A6 pattern — Kubernetes/Docker/
  GitOps/ECS/Azure/GCP/Nomad each = one adapter + one registry entry, additive; a Local
  adapter is the deterministic offline default), and owns execution/orchestration/runtime
  deployment state/lifecycle FSM/retries/rollback/approvals — while **Product Runtime stays
  frozen and purely declarative**. Deterministic behaviour over a non-deterministic infra
  edge (§A2.4); one-writer-per-fact for deployment state (§A2.5); full §A10 failure ladder
  (retry/rollback/partial/timeout/idempotency/cancellation — no orphaned deploys); §A11.2
  org-scoped RBAC + separation-of-duties approvals + audit; `deployment.*` events on the
  §A5 envelope. Dependency direction frozen `actuator → product` (read-only), never
  reverse (acyclic, AR-15). Verified consistent with the Constitution and
  ADR-0018/0019/0020/0021 (no conflict; realizes the "future Deployment Actuator" ADR-0021's
  Ownership Matrix already named). Register/INDEX updated; cross-referenced to the Epic 7
  Freeze, the Blueprint, and ADR-0021. Rationale **DL-0088**.
- **Epic 8 Implementation Plan (M8.1)** — `docs/roadmap/epic8-implementation-plan.md`. The
  final planning milestone before implementation: converts the Product Blueprint into an
  executable roadmap with **no new architecture and no new ADR** (realizes ADR-0021/0022).
  Delivers a 9-phase plan (M8.2 Foundation → … → M8.10 Marketplace) on an activation spine
  (signup→org→workspace→agent→prompt→test→deploy→monitor); 7 parallel engineering streams
  coordinated by OpenAPI + event-schema contracts (the S3 Deployment Actuator + Local
  adapter is isolated and parallelizable from day 1); 8 bounded contexts; aggregate & API-
  domain identification (no tables/endpoints); a Must/Should/Future UI map; a dependency
  graph; a per-milestone breakdown (complexity/risk/test/DoD); the MVP (spine =
  M8.2/8.3/8.4/8.6/8.7/8.8); a 4-gate release strategy (Alpha→Private Beta→Public Beta→GA);
  and a 12-item risk register. Names the one governance action: a `studio/` placement ADR
  as an M8.2 kickoff entry-criterion (CODING_STANDARDS §2). Indexed in `docs/INDEX.md`;
  rationale **DL-0089**. **Implementation begins at M8.2 on acceptance.**

### Added

- **Customer Platform Foundation (M8.2)** — `platform/api/foundation/`, the first Epic 8
  implementation and the base every later Epic 8 feature builds on. A **subpackage of the
  Control Plane** (not a new top-level package, so no placement ADR is required — the
  M8.1-flagged obligation applies only to `studio/`), with a pure domain layer:
  `api.foundation → core, tenant` only, importing no FastAPI and no frozen runtime
  (test-enforced, acyclic). Ships **Identity** (User/Organization/Workspace/Membership/
  RoleBinding), **Platform** (Environment/Project/UserPreferences), **Security** (11-role
  RBAC per ADR-0021 §5, a pure deterministic `PermissionEvaluator`, org/workspace scoping,
  `OrganizationIsolationError` enforcement, and an `RbacAuthorizer` implementing the frozen
  `tenant.access.Authorizer` seam), **Configuration** (settings + deterministic
  `FeatureFlagResolver`), and **Infrastructure** (repository interfaces + in-memory impls,
  six domain services, typed `FoundationEvent` audit events, validation, DTOs, a
  composition `FoundationFactory`). `create_organization` auto-provisions the owner;
  `create_workspace` auto-provisions a default `dev` environment; denied actions are
  audited, never silent. Determinism via injected `Clock`/`IdGenerator` seams (§A2.4).
  Component spec: [`docs/architecture/epic8/customer-platform-foundation.md`](docs/architecture/epic8/customer-platform-foundation.md).
  17 tests; **1573/1573 repository-wide; ruff clean; zero Epic 5/6/7 files modified.**
  Out of scope (later milestones): Agents/Prompts/Knowledge/Testing/Deployment/Billing/
  Marketplace/Coach/Analytics/Sessions + the FastAPI router layer + a durable SoR backing.
  Rationale **DL-0090**.
- **Customer Platform Application Layer (M8.3)** — `platform/api/application/`, the CQRS
  orchestration layer between a future API and the M8.2 Foundation. `api.application →
  api.foundation, core, tenant` only (no FastAPI/HTTP/WebSocket/DB/frozen-runtime —
  test-enforced; the API/transport layer does not exist yet). Ships **commands**, **queries**,
  **use cases** (authorize→transaction→Foundation→map→event, no domain-logic duplication),
  **CommandBus/QueryBus** with DI + thin handlers, a **Unit of Work** transaction abstraction
  (application-event outbox — publish on commit, discard on rollback), **authorization
  orchestration** via the Foundation `Authorizer` seam (no permission logic upward),
  **cross-cutting audit** (`use_case.completed`/`use_case.rejected`, rejection surviving
  rollback), **DTO mapping**, and **application events disjoint from the Foundation domain
  vocabulary** (no duplication). Deferred honestly (Foundation unchanged): `UpdateOrganization`
  (absent) and hard member delete (`RemoveMember` → `SUSPENDED` soft-remove). Component spec:
  [`docs/architecture/epic8/customer-platform-application-layer.md`](docs/architecture/epic8/customer-platform-application-layer.md).
  13 tests; **1586/1586 repository-wide; ruff clean; M8.2 Foundation and Epics 5/6/7
  byte-for-byte unchanged.** Rationale **DL-0091**.
- **Customer Platform Persistence Layer (M8.4)** — `platform/api/persistence/`, the only
  production implementation of the M8.2 Foundation repository interfaces and the M8.3
  Application `UnitOfWork` abstraction, swapped in at a composition root with **no interface
  change**. SQLModel-backed (`cp_*` tables). Ships 8 `Sql*Repository` implementations (org-
  scoped reads, row↔frozen-domain mapping, never commit), a production **`SqlUnitOfWork`**
  giving **real atomic rollback of DB writes** (closing the M8.3 in-memory limitation — data
  and events now roll back together), pure row↔domain **mapping** (no persistence leakage),
  org-scoped **query helpers**, a declarative **migration structure** (`migrations/`, no app
  logic), and a **composition root** (`build_persistent_application`) wiring SQL repos + Unit
  of Work into the Foundation/Application via their existing seams. `api.persistence →
  api.foundation, api.application, core, tenant, sqlmodel` only — no FastAPI/HTTP/frozen-
  runtime; neither Foundation nor Application imports persistence (AST-verified). Component
  spec: [`docs/architecture/epic8/customer-platform-persistence-layer.md`](docs/architecture/epic8/customer-platform-persistence-layer.md).
  9 persistence-backed integration tests (existing in-memory unit tests kept); **1595/1595
  repository-wide; ruff clean; Foundation, Application, and Epics 5/6/7 byte-for-byte
  unchanged.** Rationale **DL-0092**.
- **Customer Platform Control Plane API (M8.5)** — `platform/api/customer/`, a **thin**
  FastAPI transport boundary over the M8.3 Application buses wired to M8.4 persistence, with
  its own `create_app()` (the legacy `api/main.py` app is untouched). Owns transport only:
  HTTP parse/serialize, dependency injection (`Container` + FastAPI providers, replaceable
  for tests), authentication (dependency-free HS256 **bearer/JWT identity resolution — no
  authorization**), `RequestContext`, centralized **error translation** (422+diagnostics /
  403 / 404 / 409 / 401 / 500, uniform envelope), **middleware** (correlation ids / timing /
  metrics / structured logs / security headers / GZip / CORS), **health** (`/health`,`/ready`
  with DB `SELECT 1`,`/version` — no runtime checks), auto **OpenAPI** (tags/examples/error-
  responses/bearer scheme, v1.0.0), and `/v1` versioning. Thin domain routers (auth/orgs/
  workspaces/members/projects/environments/preferences/system) dispatch Application commands/
  queries; controllers own **no** business logic/repository/transaction/permission-evaluation
  (test-asserted); authorization forwarded to the Application; no domain object exposed
  directly. `api.customer → api.application, api.persistence, api.foundation, core, tenant,
  fastapi/pydantic`; lower layers never import the API (AST-verified). Component spec + API/
  OpenAPI docs: [`docs/architecture/epic8/customer-platform-control-plane-api.md`](docs/architecture/epic8/customer-platform-control-plane-api.md).
  15 API integration tests; **1610/1610 repository-wide; ruff clean; Foundation, Application,
  Persistence, and Epics 5/6/7 byte-for-byte unchanged.** Rationale **DL-0093**.
- **Organization Lifecycle vertical slice (M8.6)** — the first complete customer-facing
  feature, end-to-end across all Epic 8 layers, delivered as **additive, non-breaking
  extensions** (no new ADR, no redesign; existing contracts + all 1610 prior tests
  preserved; frozen Epics 5/6/7 untouched). Adds organization settings/rename/logo/timezone/
  language/metadata, soft-delete + status, activity summary + feed, membership tiers
  (Owner/Admin/Member/Viewer via additive `ORG_ADMIN/ORG_MEMBER/ORG_VIEWER` roles; no custom
  roles), and the full invitation lifecycle (invite/accept/decline/resend/cancel/expiry +
  audit) modelled as a pending membership. Layers extended additively: Foundation
  (`org_lifecycle.py` services + domain fields/enums/rbac/events/`OrganizationRepository.update`),
  Application (`org_lifecycle.py` commands/queries/use cases registered into the existing
  buses), Persistence (additive `cp_*` columns + `SqlOrganizationRepository.update` +
  migration `0002`), Control Plane API (thin `PATCH/DELETE /v1/organizations/{id}`,
  `.../activity`, `.../invitations` routers). Plus a self-contained **Studio UI** served at
  `/studio` (dashboard/settings/members/invite/pending/activity; responsive; loading/empty/
  error states) — served inside `api/customer/studio/` (subpackage; no new-top-level-dir
  placement ADR needed). Component/API/Studio spec:
  [`docs/architecture/epic8/organization-lifecycle.md`](docs/architecture/epic8/organization-lifecycle.md).
  9 tests incl. the full Definition-of-Done flow, authz failure, cross-org isolation,
  invitation expiry, soft-delete, Studio served. **1619/1619 repository-wide; ruff clean;
  frozen Epics 5/6/7 untouched.** Rationale **DL-0094**.
- **Workspace Lifecycle vertical slice (M8.7)** — the second complete customer-facing
  feature, end-to-end across all Epic 8 layers, delivered as **additive, non-breaking
  extensions** (no new ADR, no redesign; existing contracts + all 1619 prior tests preserved;
  frozen Epics 5/6/7 untouched). Adds workspace rename/settings (description/icon/color/
  metadata), soft-archive + restore + status, activity summary + feed (filtered to the
  workspace), workspace membership (Admin/Member/Viewer via additive `WS_MEMBER/WS_VIEWER`
  roles + reused `WORKSPACE_ADMIN`; no custom roles) with workspace-scoped role bindings, and
  navigation (switcher/recent/favorite/default/search) backed by the existing `UserPreferences`.
  Layers extended additively: Foundation (`workspace_lifecycle.py` services + domain fields/
  enums/rbac/events/`WorkspaceRepository.update` + new `WorkspaceMembershipRepository`),
  Application (`workspace_lifecycle.py` commands/queries/use cases registered into the existing
  buses), Persistence (additive `cp_workspace` columns + new `cp_workspace_membership` table +
  `SqlWorkspaceMembershipRepository` + migration `0003`), Control Plane API (thin
  `PATCH …/workspaces/{id}`, `.../archive`, `.../restore`, `.../activity`, `.../search`,
  workspace `.../members`, and `workspace-navigation` routers). Plus the **Studio UI** Workspaces
  section (list/switcher with favorite/default + search, create, dashboard/settings, archive/
  restore, members, activity) added to the existing `api/customer/studio/` bundle. Only ACTIVE
  org members may join a workspace (org+workspace isolation); workspace name uniqueness enforced
  on rename. Component/API/Studio spec:
  [`docs/architecture/epic8/workspace-lifecycle.md`](docs/architecture/epic8/workspace-lifecycle.md).
  11 tests incl. the full Definition-of-Done flow, authz failure, cross-org + cross-workspace
  isolation, archive-hides-from-switcher/search, Studio served. **1630/1630 repository-wide;
  ruff clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0095**.
- **Project Lifecycle vertical slice (M8.8)** — the third complete customer-facing feature,
  end-to-end across all Epic 8 layers, delivered **reuse-first** with a **minimal additive
  Foundation change** (no new ADR, no redesign; existing contracts + all 1630 prior tests
  preserved; frozen Epics 5/6/7 untouched). Adds project rename/settings (description/icon/
  metadata), soft-archive + restore + status, activity summary + feed (filtered to the
  project), project membership (Admin/Member/Viewer), environment management (fixed dev/
  staging/prod set + per-user default/switch, **no deployment**), and search. Reuse decisions:
  project **creation** stays on the frozen M8.2 service/M8.5 route; **no new RBAC scope** —
  project ops authorize via the existing workspace-scoped `PROJECT_MANAGE` (`rbac.py`
  unchanged; PROJECT-granular enforcement deferred, flagged honestly); **environments reuse
  `UserPreferences` + `EnvironmentKind`** (no new persistence). Layers: Foundation
  (`project_lifecycle.py` services + additive `Project` fields/enums/events/`ProjectRepository.get`/
  `update` + new `ProjectMembershipRepository`; `services.py`/`rbac.py` untouched), Application
  (`project_lifecycle.py` commands/queries/use cases), Persistence (additive `cp_project`
  columns + new `cp_project_membership` table + `SqlProjectMembershipRepository` + migration
  `0004`), Control Plane API (thin `PATCH …/projects/{id}`, `.../archive`, `.../restore`,
  `.../activity`, `.../search`, project `.../members`, `.../environment` routers). Plus the
  **Studio UI** Projects section (list/search, create, dashboard/settings, archive/restore,
  environment selector, members, activity) added to the existing `api/customer/studio/` bundle.
  Only ACTIVE org members may join a project (org+workspace+project isolation); project name
  uniqueness enforced on rename. Component/API/Studio spec:
  [`docs/architecture/epic8/project-lifecycle.md`](docs/architecture/epic8/project-lifecycle.md).
  11 tests incl. the full Definition-of-Done flow, authz failure, cross-org/cross-workspace/
  cross-project isolation, archive-hides-from-search, Studio served. **1641/1641
  repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0096**.
- **Epic 8 Architecture Freeze — Customer Platform v1.0 (M8.20)** — governance milestone; **no
  implementation, no redesign, no new features, no ADR**. Freezes the Epic 8 Customer Platform
  **architecture** (M8.2–M8.18): its public interfaces, ownership boundaries (single writer per
  fact), one-way acyclic dependency direction (`Studio→API→Application→Foundation→{Persistence,
  adapters}`; `actuator→product` read-only; no Epic 8 layer imports a frozen engine and no frozen
  Epic imports Epic 8 — AST-verified), extension points, and the determinism/identity/config/
  provider/runtime/deployment/knowledge/audit/observability/security/operational rules. Freeze
  manifest: [`docs/releases/epic8-customer-platform-v1.0.md`](docs/releases/epic8-customer-platform-v1.0.md);
  cross-references the Epic 5/6/7 freeze manifests. Extension policy: additive extensions permitted;
  a breaking change requires a new ADR + major version + new baseline. Repository audit at freeze:
  **1774/1774 tests; ~218 Epic-8 capability tests; ruff clean; migrations 0001–0012 valid; frozen
  Epics 5/6/7 byte-for-byte unchanged.** **Governance status recorded honestly (manifest §22): the
  Epic 8 implementation is present in the working tree but NOT committed to version control (the
  unresolved M8.19 MEDIUM condition). This is an architecture freeze only — no commit, push, tag, or
  release is performed or claimed.** Verdict: 🔒 **EPIC 8 ARCHITECTURE FROZEN (Customer Platform
  v1.0)**. Rationale DL-0106.
- **Production Readiness, Hardening & GA Certification (M8.18)** — hardening + certification **only**
  (no architectural redesign, no new features, no new domains/aggregates/customer-APIs, no new ADR;
  frozen Epics 5/6/7 unchanged). **Production hardening** (all behind existing seams): real
  `HttpTransport` (stdlib `urllib`, connection pooling, per-request timeout, typed retry/non-retry
  errors) + a `CredentialProvider` secret seam (`EnvCredentialProvider`/`ChainedCredentialProvider`
  key-rotation) + `redact`/`redact_headers` — Echo/Env remain the deterministic offline default;
  configuration validation that **fails fast in production** (rejects the default JWT secret,
  wildcard CORS, and in-memory SQLite); startup dependency verification + graceful shutdown; rate
  limiting (429 + `Retry-After`) + payload limits (413) + a fuller security-header set (HSTS over TLS
  only, `Permissions-Policy`, `X-XSS-Protection`, `Cache-Control: no-store`, …); production JWT
  validation + a **key-rotation** seam (`verification_secrets` + `decode_any`, wired into auth); 5xx
  **error redaction** + structured, correlated logging. **Verification** — 27 new tests:
  `test_production_hardening` (13), `test_ga_certification` (10 — migration chain **0001…0012**
  contiguous/ordered/idempotent, OpenAPI completeness, SemVer + backward-compat, **dependency
  boundaries + one-way frozen-Epic freeze**, determinism, append-only audit), `test_scale` (4 —
  5 000-event audit history, 100-project org, 200-chunk knowledge base, 30-turn soak, 20
  deployments). **Documentation** — 13 operational documents (Production Architecture, Operations,
  Deployment, Troubleshooting, Scaling, Security, Backup & Recovery, Disaster Recovery, Runbook,
  Support Handbook, Production Checklist, GA Checklist) + the formal **GA Certification v1.0**
  (`docs/releases/ga-certification-v1.0.md`) verifying every ADR, Constitution rule, ownership
  boundary, dependency direction, and frozen Epic. **1774/1774 repository-wide; ruff clean; frozen
  Epics 5/6/7 byte-for-byte unchanged.** Verdict: 🟢 **GA v1.0 — CERTIFIED PRODUCTION-READY**.
  Rationale **DL-0106**.
- **Observability Platform (M8.17)** — a **passive read-model** over the durable Audit stream
  (M8.9): metrics / logs / traces / health / usage / cost / dashboards are *projections* computed on
  read from the events the platform already emits; **only alert rules are durable config**. **Golden
  rules:** Runtime owns execution, Deployment owns deployment, Knowledge owns retrieval, Tool
  Framework owns tools, **Observability owns observation** — no monitoring logic entered any of those
  layers (all reused exactly and unchanged); no new ADR, no redesign; frozen Epics 5/6/7 untouched;
  all 1733 prior tests preserved. Ships: **metrics** (`MetricsRegistry`/`MetricsCollector`/
  `MetricsService` + a 15-metric event-derivation catalog); **structured logs** (each audit record is
  one immutable `LogEntry` — the audit store *is* the log store; level/category derived); **distributed
  traces** (`Trace`/`Span` — correlated events grouped into spans, **no OpenTelemetry**); **alerting**
  (`AlertRule`/`AlertEvaluator`/`AlertManager` — comparators + windows, evaluated **on demand**, **no
  notifications, no poller**); **health** (`HealthMonitor` — success/failure ratios + consumed
  Deployment/Runtime health, never probes); **usage** + **estimated cost** (rate table, **not
  billing**) + **performance**; the eight dashboard read models (org/workspace/project/agent/
  deployment/knowledge/provider/system). **No polling, no direct Runtime/Deployment access, no
  duplicate audit events.** Foundation (additive): `observability_domain.py` + `observability.py`
  (all services over the injected `AuditReader`) + `AlertRuleRepository` + `FoundationEventType.
  observability.*` (alert-config facts only) + id prefix `alr`; the trace hash uses `hashlib` (no
  `product` import — boundary tests green). Persistence: `cp_alert_rule` (the **only** Observability
  table) + repo + **migration `0012`**; metrics/logs/traces/health add no tables. Application:
  `observability.py`. API: org-scoped `…/observability/…` router. Studio: Metrics/Trace/Log/Health/
  Usage/Cost/Alert/Provider/Knowledge/Deployment screens. 14 tests (metric/log/trace/health/usage/
  cost/perf/alert + API e2e **DoD** + audit-non-duplication + authorization + isolation + Studio).
  **1747/1747 repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0105**; spec
  `docs/architecture/epic8/observability-platform.md`.
- **Deployment Actuator (M8.16)** — the **Deployment Actuator** (ADR-0022): the sole owner of
  deployment **execution**, a new top-level `actuator/` package above the frozen Product Runtime,
  consuming its `DeploymentPlan` **read-only** (Product Runtime is not modified and never learns the
  Actuator exists). **Golden rules:** Deployment owns execution, Product Runtime owns planning,
  Runtime owns conversations, Studio owns interaction, Infrastructure owns infrastructure. No new
  ADR, no redesign; Product Runtime / Conversation Runtime / Tool Framework / Provider Binding /
  Knowledge Tool reused exactly and unchanged; frozen Epics 5/6/7 untouched; all 1717 prior tests
  preserved. Ships: the deterministic lifecycle **FSM** (`REQUESTED→VALIDATING→PENDING_APPROVAL→
  QUEUED→EXECUTING⇄VERIFYING→COMPLETED`, + `FAILED`/`ROLLING_BACK→ROLLED_BACK`/`CANCELLED`); the
  `DeploymentEngine` (**sole writer**) + 9 coordinators (queue/scheduler/approval/rollback/health/
  cancellation/retry/timeout/executor) + `DeploymentActuator` facade; provider-independent
  **Infrastructure Adapters** — **Local executes** deterministically offline, Docker/Kubernetes/
  GitOps/Terraform/AWS-ECS/Azure/GCP/Nomad **declared** with capability flags (raise on actuate);
  **approval** (separation of duties — requester ≠ approver, production auto-gated); first-class
  **rollback** (manual + health-failed auto-rollback); **health verification** (startup→readiness→
  liveness); durable execution history. `actuator/planning.py` is the **only** place Epic 7's
  `DeploymentPlan` is reused (read-only), so the **Foundation imports only `actuator`, never
  `product`** (frozen-Epic import boundary preserved). Foundation (additive): `deployment.py`
  `DeploymentActuatorService` (authorizes via existing `PROJECT_MANAGE`; mirrors `deployment.*` into
  the durable Audit Platform) + `FoundationEventType.DEPLOYMENT_*` + a defaulted execution repo.
  Persistence: `cp_deployment_execution` + repo + **migration `0011`** (aggregate as JSON).
  Application: `deployment.py` submit/approve/reject/cancel/retry/rollback + plan/status/history/
  timeline/logs/health/targets. API: thin `…/deployments/…` router. Studio: Deployment Dashboard/
  Environment Selector/History/Approval Queue/Rollback Manager/Health Viewer/Timeline/Execution
  Logs. 16 tests (FSM + adapters + engine lifecycle incl. health-failed auto-rollback + API e2e
  **DoD** + SoD + audit + isolation + Studio). **1733/1733 repository-wide; ruff clean; frozen Epics
  5/6/7 untouched.** Rationale **DL-0104**; spec `docs/architecture/epic8/deployment-actuator.md`.
- **Knowledge Tool / RAG Foundation (M8.15)** — Knowledge implemented **strictly as a Tool
  Framework capability**. **Knowledge is NOT part of Runtime / LLM-adapter / Memory** — a
  `knowledge`-category tool is invoked by the existing Tool Executor (M8.14) and reaches the
  Runtime **only as a Tool Result (Context Package)**. **Golden rules:** Knowledge owns retrieval,
  Runtime owns orchestration, LLM owns generation, Adapters own execution; **no retrieval logic in
  the Runtime**. Delivered **reuse-first** with an additive Foundation change (no new ADR, no
  redesign; existing contracts + all 1708 prior tests preserved; Runtime/Tool-Framework/Provider-
  Binding/Playground reused exactly and unchanged; frozen Epics 5/6/7 untouched). Ships: assets
  (collection→document→chunk + metadata/version/status/soft-delete/archive/restore); document
  pipeline (upload/replace/delete/archive/restore/version + checksum + duplicate + MIME + max-size,
  **no OCR/image**); **deterministic chunking** (fixed-size + overlap + checksum); **embedding
  providers** (OpenAI/Gemini/Voyage/Cohere/Local); **vector stores** (FAISS/Qdrant/PgVector/
  Pinecone/Chroma); the retrieval pipeline (query→embedding→search→ranking→filtering→selection→
  context package, **no LLM reranking**); the **Knowledge Tool** executor bound into the M8.14
  engine. Embedding/vector providers live in `api.adapters` behind provider-independent seams over
  the injectable transport — **deterministic offline** (hash embedder + cosine), real providers in
  production; **no provider SDK, no frozen-Epic import** (boundary tests green). Foundation
  (additive): `knowledge_domain.py` + `knowledge_chunking.py` + `knowledge_adapters.py` +
  `knowledge.py` + repos/DTOs/events (existing services + M8.11–M8.14 untouched). Persistence: 3
  tables + repos + **migration `0010`** (chunk vectors as JSON). Application: `knowledge.py`. API:
  thin `…/knowledge/…` router. Studio: Knowledge Dashboard/Collections/Documents/Upload/Chunk/
  Metadata/Vector Status/Retrieval Inspector/Search Playground. 9 tests (chunking + 5 embedders +
  5 stores + pipeline + retriever + API e2e DoD incl. **retrieval-through-Tool-execution** + audit
  + isolation + Studio). **1717/1717 repository-wide; ruff clean; frozen Epics 5/6/7 untouched.**
  Rationale **DL-0103**; spec `docs/architecture/epic8/knowledge-tool.md`.
- **Provider Adapter Binding (M8.14)** — binds the Conversation Runtime to **real execution
  providers** via the existing M8.12 adapter interfaces, in a new **`api.adapters` layer**.
  **Golden rules:** Runtime owns orchestration (unchanged — still only plans); Providers own
  execution; Adapters own translation; **no provider-specific logic in the Runtime**. Delivered
  **reuse-first** with an additive Foundation **seam** (no new ADR, no redesign; existing contracts
  + all 1698 prior tests preserved; frozen Epics 5/6/7 untouched). Ships production **LLM adapters**
  (Anthropic/OpenAI/Gemini/Groq/Azure — real request-shaping + response/token-usage parsing),
  **voice adapters** (Deepgram/ElevenLabs/Cartesia/OpenAI, metadata-only), **tool executors**
  (HTTP/Function/Webhook/MCP/Database/Custom respecting ordering/enable/override/deps/timeout/
  retry), a durable **memory adapter** (message store — **no vectors/RAG**), a
  `ProviderExecutionEngine` (execute + **streaming** with token/tool/voice events + cancellation +
  backpressure), and **resilience** (retry/timeout/circuit-breaker/rate-limit/graceful-degradation).
  **The network egress is an injectable `Transport` seam** — deterministic `EchoTransport` offline
  in tests (running the real translation code), HTTP in production; **no provider SDK, no
  frozen-Epic import** (boundary tests green). Foundation (additive seam): `execution_domain.py` +
  `execution.py` (`ExecutionEngine` ABC + offline `MockExecutionEngine` + `ConversationExecutionService`)
  + message repo + DTOs/events (`services.py` + M8.12 runtime + M8.13 playground untouched).
  Persistence: `cp_conversation_message` + repo + **migration `0009`**; composition threads an
  `engine_factory` (provider-agnostic). Application: `execution.py` `ExecuteTurn`(+stream)/messages/
  provider-diagnostics. API: additive `…/conversations/{id}/execute|stream|messages|provider`; the
  **customer Container** injects the real engine. Studio: Streaming View / Provider Inspector /
  Token Usage / Latency / Retry+Execution Timeline / Provider Diagnostics. 10 tests (5 LLM + 4 voice
  + 6 tool + resilience + engine/streaming + API e2e DoD + audit + isolation + determinism + Studio).
  **1708/1708 repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0102**;
  spec `docs/architecture/epic8/provider-adapter-binding.md`.
- **Conversation Playground (M8.13)** — a developer **playground** for inspecting/debugging/
  validating Conversation Runtime behaviour through **mock adapters only**. **A testing
  environment, NOT production deployment. No external providers, no real LLM API, no real tool
  execution, no voice synthesis, no network.** Delivered **reuse-first** with an **additive
  Foundation change** and **no new persistence** (no new ADR, no redesign; existing contracts +
  all 1687 prior tests preserved; frozen Epics 5/6/7 untouched). Deterministic **mock adapters**
  (`MockLLMAdapter` echoes prompt metadata + token/timing estimates; `MockToolExecutor` returns
  deterministic fake outputs; `MockVoiceAdapter` returns metadata only, `audio=None`;
  `MockMemoryAdapter` stateless stub) implement the M8.12 runtime adapter interfaces — identical
  inputs → byte-identical outputs. Conversation flow: **start** (reuses runtime create-session —
  **draft never runs**), **continue** (a turn runs the mocks over the resolved plan + appends a
  `ConversationTurn`), **reset** (clears turns, keeps the plan), **close**. **Reuses the existing
  `ConversationSession`** — turns live in `cp_conversation_session.state` JSON, so **no new table/
  migration**. Prompt/variable/tool-plan inspection **reuses the M8.12 runtime queries**; adds
  adapter-output/timeline/diagnostics views. The playground **never mutates** the published config
  or resolved plan; **replaying a conversation yields identical turns**. No new RBAC scope
  (workspace-scoped `PROJECT_MANAGE`); audit reuses M8.9. Foundation (additive): `runtime_mocks.py`
  + `playground_domain.py` + `playground.py` + DTOs/events (`services.py` + M8.12 runtime
  untouched). Application: `playground.py` commands/queries/use cases. API: thin
  `…/agents/{id}/playground/conversations` router. Studio: Conversation Playground / Chat Window /
  Prompt Inspector / Variable Inspector / Execution Timeline / Tool Invocation Viewer / Adapter
  Output Viewer / Diagnostics Panel / Session Browser. 11 tests (deterministic mock adapters +
  Foundation unit incl. draft-blocked/replay-determinism/no-mutation + API e2e incl. full
  Definition-of-Done, audit reuse, isolation, Studio served). **1698/1698 repository-wide; ruff
  clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0101**; spec
  `docs/architecture/epic8/conversation-playground.md`.
- **Conversation Runtime (M8.12)** — the **execution/orchestration plane** that runs a
  **Published Agent Version** to a deterministic **execution plan**. **No LLM, no tool execution,
  no voice, no real provider, no message history, no vectors, no deployment, no playground** —
  only orchestration. Delivered **reuse-first** with an **additive Foundation change** (no new
  ADR, no redesign; existing contracts + all 1676 prior tests preserved; frozen Epics 5/6/7
  untouched). **Golden rules (tested):** only a **published** version runs (a draft raises); the
  runtime **never mutates** config or history; it is **deterministic**; it is stateless except the
  explicit persisted `ConversationState`. Pipeline: load published version → **resolve variables**
  (precedence System < Project < Agent < Runtime, immutable) → **build prompt** (deterministic,
  secrets masked `***`) → **resolve tool registry** (M8.11 ordering/enable/override/deps → an
  executable plan, **not executed**) → **execution plan** + required **adapter interfaces**.
  **Adapter interfaces only** — `LLMAdapter`/`VoiceAdapter`/`ToolExecutor`/`MemoryAdapter` ABCs
  (no implementations, no SDKs) are the seam the frozen Epic 5/6 runtime binds later; the control
  plane **never imports the frozen runtime** (dependency-boundary test green). Session lifecycle:
  create/resume/close/expire + correlation ids + metadata. Execution result: resolved prompt/
  variables/tool plan/timing/diagnostics — **no model response**. No new RBAC scope (workspace-
  scoped `PROJECT_MANAGE`); audit reuses M8.9. Foundation (additive): `runtime_domain.py` +
  `runtime_adapters.py` + `runtime.py` + repo/DTOs/events/prefix (`services.py` untouched).
  Application: `runtime.py` commands/queries/use cases. Persistence: `cp_conversation_session`
  table (session + state JSON; **no message history/vectors**) + SQL repo + **migration `0008`**.
  API: thin `…/agents/{id}/sessions` router. Studio: Runtime Inspector / Execution Plan / Session /
  Variable Inspector / Tool Plan / Diagnostics (no chat). 11 tests (Foundation unit incl. draft-
  blocked/precedence/secret-mask/determinism/no-mutation + API/persistence e2e incl. full
  Definition-of-Done, audit reuse, **resume across restart**, isolation, Studio served). **1687/1687
  repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0100**; spec
  `docs/architecture/epic8/conversation-runtime.md`.
- **Tool Framework vertical slice (M8.11)** — a **provider-independent Tool Framework** that
  lets published Agents declare, configure and validate tools. **No tool execution, no LLM, no
  RAG, no deployment, no external calls** — declaration only; Conversation Runtime consumes it
  next. Delivered **reuse-first** with an **additive Foundation change** (no new ADR, no
  redesign; existing contracts + all 1666 prior tests preserved; frozen Epics 5/6/7 untouched).
  A **Tool** is a configuration object owned by one Project, reusable across its agents. Ships:
  **tool catalog** + registration + **typed-data configuration** (config/input/output schemas +
  settings + timeout + retry policy + depends_on — plain dicts/values, **never an SDK object or
  provider client**), **enable/disable**, **ordering**, **validation** (duplicate-name, category,
  typed schemas, **circular references** via a `depends_on` DFS, project/workspace/org isolation),
  **version** (monotonic, bumped on config change), and **agent integration** (attach/detach/
  enable/disable/**reorder**/configure, project-isolated, per-attachment enable + config
  override). **Tool execution is abstracted behind interfaces** — there is no execute path; the
  framework knows nothing about any LLM. History reuses the durable M8.9 Audit Platform. No new
  RBAC scope (workspace-scoped `PROJECT_MANAGE`/`PROJECT_READ`); CQRS/UoW/persistence seams
  unchanged. Foundation (additive): `tool_domain.py` + `tool_framework.py` + repos/DTOs/events/
  prefixes/diagnostics (`services.py` untouched). Application: `tool_framework.py` commands/
  queries/use cases into the existing buses. Persistence: `cp_tool` + `cp_agent_tool` tables +
  SQL repos + **migration `0007`**. API: thin `…/projects/{id}/tools` + `…/agents/{id}/tools`
  routers. Studio: Tool Catalog/Library/Details/Configuration + Agent Tool Assignment/Ordering.
  10 tests (Foundation unit + API/persistence e2e incl. full Definition-of-Done, circular-ref,
  attach/reorder, restart durability, authz failure, cross-project/cross-org isolation, Studio
  served). **1676/1676 repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale
  **DL-0099**; spec `docs/architecture/epic8/tool-framework.md`.
- **Agent Builder vertical slice (M8.10)** — the **first AI-native customer feature**: design,
  configure, validate, version and publish **Agents** (configuration objects owned by one
  Project). **No runtime, no conversations, no deployment, no LLM calls** — authoring only.
  Delivered **reuse-first** with an **additive Foundation change** (no new ADR, no redesign;
  existing contracts + all 1653 prior tests preserved; frozen Epics 5/6/7 untouched). Ships:
  agent **CRUD** (create/update/archive/restore/duplicate/**soft-delete**), **publish/unpublish/
  rollback** with **immutable versions**, full configuration (identity + **prompt builder** with
  live counter/preview + **provider-independent** model & voice + **typed variables incl.
  secrets**), **validation** (required/prompt-size/`{{variable}}`-refs/model+voice compatibility/
  duplicate-name/isolation), **version history + field-level comparison**, and **per-agent
  durable activity** over the M8.9 audit stream. Reuse decisions: **no new RBAC scope** (writes
  via workspace-scoped `PROJECT_MANAGE`, reads via `PROJECT_READ`); **versioning + activity reuse
  the durable Audit Platform** (one history system, not a second); CQRS/UoW/persistence seams
  unchanged. **Secrets** are retained in the domain for persistence but **redacted at every read
  boundary** (view + version snapshot). **Rollback never rewrites history** — it publishes a new
  version restored from the target. Foundation (additive): `agent_domain.py` + `agent_builder.py`
  + repos/DTOs/events/prefixes/diagnostics + one `resource_activity` projection method
  (`services.py` untouched). Application: `agent_builder.py` commands/queries/use cases into the
  existing buses. Persistence: `cp_agent` + append-only `cp_agent_version` tables + SQL repos +
  **migration `0006`**. API: thin `…/projects/{id}/agents` router. Studio: Agent list/editor/
  prompt-builder/variable-manager/version-history/rollback. 13 tests (Foundation unit + API/
  persistence e2e incl. full Definition-of-Done, versioning/rollback, audit reuse, restart
  durability, authz failure, cross-project/cross-org isolation, Studio served). **1666/1666
  repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0098**; spec
  `docs/architecture/epic8/agent-builder.md`.
- **Durable Audit & Activity Platform (M8.9)** — hardening: replaced the temporary in-memory
  activity stream with a durable, **append-only** Audit Store + a projection read-model. A
  **storage-only** change — business logic, ownership, and every existing API contract are
  unchanged (all 1641 prior tests still green; frozen Epics 5/6/7 untouched). Foundation
  (additive read seam): new `audit.py` (`AuditRecord`/`AuditQuery`/`AuditPage`/`AuditReader`)
  + `activity.py` (`ActivityProjectionService` — organization/workspace/project/recent/user
  projections with pagination + filtering + time-range, read-model only); `RecordingEventEmitter`
  additively implements the reader (default in-memory Foundation unchanged). Persistence
  (additive): new append-only `cp_audit_event` table (`seq` PK for ordering/pagination;
  indexed filter columns) + `SqlAuditStore` (write `emit` + read `query`/`stream`, **no
  update/delete**) committed atomically by the existing `SqlUnitOfWork` + migration `0005`;
  wired as the emitter **and** audit reader in `build_foundation` (replaces the in-memory
  emitter for the persistent app, no interface change). Application (additive extensions): the
  three existing activity queries gain an optional `ActivityFilter` and read the durable
  projection (preserving `{summary, feed}` + pagination meta); new `GetRecentActivity`/
  `GetUserActivity`. API (compatible-additive): the three `/activity` routes gain optional
  `limit`/`offset`/`actor`/`event_type`/`since`/`until` params + new `…/activity/recent` and
  `…/activity/users/{user_id}`. Studio: one reusable durable activity feed with event-type/
  user/time-range filters + infinite scroll. **Audit is append-only + immutable** (never
  updated/deleted). Component/architecture spec:
  [`docs/architecture/epic8/audit-activity-platform.md`](docs/architecture/epic8/audit-activity-platform.md).
  12 tests incl. history-survives-restart (file DB), large-history pagination (81 events),
  projection rebuild from the durable log, append-only guard, API params + recent/user, Studio
  controls. **1653/1653 repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale
  **DL-0097**.

## Unreleased — Epic 7 (Product Platform) · 🔒 FROZEN (M7.0F)

**Product Runtime v1.0** — Epic 7 is **architecturally frozen** as of M7.0F
(2026-07-27); the public architecture, ownership, `product → core` dependency
direction, and extension points are locked (freeze manifest:
[`docs/releases/epic7-product-runtime-v1.0.md`](docs/releases/epic7-product-runtime-v1.0.md)).
Recommended platform baseline at release: **v2.0.0**. This section rolls into a
tagged version section at release time (no tag/release performed in M7.0F).

### Added

- **ProductRuntime composition façade (M7.0E / certification finding C1)** — a minimal,
  immutable `product/runtime.py` façade that *composes* the Product Runtime artifacts a
  consumer holds: it accepts a `ProductPackage` and an optional `DeploymentPlan` and
  exposes the `CompiledProduct`, metadata, and diagnostics as read-only projections,
  guarding the package↔plan composition boundary. It **never executes, deploys, owns a
  runtime lifecycle/providers/workflows, or touches infrastructure** — realizing the
  M7.0A "Runtime Composition" façade without adding any execution surface. 7 tests
  (immutability, deterministic construction, composition, mismatch guard, no-execution
  surface, boundary). `product → core` only, unchanged.

### Certification & freeze preparation (M7.0D / M7.0E)

- **M7.0D** — independent architecture certification returned **CERTIFIED WITH
  CONDITIONS** (no architecture violations; three closure conditions).
- **M7.0E** — closed all three conditions: **C1** façade implemented (above); **C2**
  `FeatureFlag`/`TenantOverride` tuple/mapping representation documented as the
  permanent architecture (no unused `FeatureFlagSet`/`TenantOverrideSet` wrappers —
  would be dead code); **C3** the `RegistrySnapshot → BindingSnapshot` rename
  (ADR-0020) reconciled in the ADR Register and INDEX **without modifying the
  immutable ADR-0019**. Added `docs/releases/epic7-freeze-preparation.md`. Repository
  verified freeze-ready: Epic-7-only working tree, no temp/debug/TODO/placeholder,
  74/74 frozen dataclasses, zero side-effects, `product → core` only, 1556/1556 tests,
  ruff clean. No commit/push/tag/release performed.

- **Product Deployment Planning Layer (M7.0C)** — a new `platform/product/deployment/`
  subpackage turning an immutable `ProductPackage` into a validated, immutable
  **`DeploymentPlan`** (an immutable `DeploymentDescriptor` — deployment *intent* only —
  plus a machine-readable `DeploymentReport`). The layer owns deployment intent and
  nothing more: it **never performs a deployment, never calls infrastructure, and never
  starts a runtime session** (ADR-0018 applied — no new ADR introduced). Extends the
  identity chain by one link (…→Package ID→Deployment ID; content-derived, reproducible,
  timestamp-free). `DeploymentPlanner` builds and validates; `DeploymentValidator` checks
  package integrity, target compatibility, profile/target, environment + tenant overrides
  (rejecting execution-ownership keys), resource limits, feature-flag consistency,
  rollout-policy consistency, and rollback admissibility (same-major rule; verified against
  a read-only `ProductRegistry` when supplied). `DeploymentProfile` (Development…Hybrid),
  `DeploymentTarget` (Single/Multi-Tenant), `DeploymentPolicy`, `RollbackPolicy`,
  `ReleaseTarget`; `DeploymentDiagnostics` (categorized view over the shared finding set);
  byte-identical `DeploymentSerializer`. `EnvironmentProfile` and `DeploymentStrategy` are
  reused from `product.domain` (the latter extended additively with `IMMEDIATE` and
  `MANUAL_APPROVAL`). Extension seams only for scheduling/approval/orchestration/GitOps/
  Kubernetes/Terraform (none actuate). Deployment execution, infrastructure adapters,
  release pipeline, and enterprise features are explicit non-goals. `product → core` only
  (freeze preserved, AST-test-enforced). 27 tests; 1549/1549 repository-wide; ruff clean.

- **Product Package, Registry & Versioning (M7.0B-3)** — a new `platform/product/registry/`
  subpackage turning an immutable `CompiledProduct` into an immutable, content-addressed,
  distributable **`ProductPackage`** (the only deployable artifact of Product Runtime) via
  `ProductPackager`, plus an immutable read-only **`ProductRegistry`** over published
  packages. Deterministic identity chain (Manifest→Definition→Compilation→Package;
  content-derived, reproducible, timestamp-excluded); byte-identical `PackageSerializer`;
  `PackageValidator` + `IntegrityValidator` (recompute-and-compare the four content hashes,
  tamper-evident, verification only); `SignatureMetadata` + `SignatureProvider` seam (no
  cryptography — unsigned by default); `VersionManager` (transition classification +
  same-major rollback rule) + `CompatibilityMatrix` (static evaluation). Registry queries:
  latest / exact / compatible / rollback / history / certification / provenance + a unified
  `RegistryQuery`/`execute` surface; content-addressed `RegistrySnapshot`; `PackageStore`
  abstraction (in-memory default). Extension seams only for distributed/OCI/cache/mirror/
  signing (no implementation). Deployment, runtime execution, release pipeline, marketplace,
  and enterprise features are explicit non-goals. `product → core` only (freeze preserved,
  AST-test-enforced). 34 tests; 1522/1522 repository-wide; ruff clean.
- **ADR-0020** — resolves a naming collision: "Registry" nouns are reserved for the Product
  Registry of packages; the M7.0B-2 compiler binding-resolution catalog was renamed
  `RegistrySnapshot`→`BindingSnapshot` / `RegistryEntry`→`BindingSnapshotEntry` (behaviour
  unchanged; all callers + tests updated in the same change-set).

- **Product Compiler & Binding Resolution (M7.0B-2)** — a new `platform/product/compiler/`
  subpackage: a deterministic, side-effect-free 8-stage pipeline (validate → resolve
  bindings → resolve configuration → analyze dependencies → validate ownership →
  validate compatibility → produce `CompiledProduct` → produce `CompilationReport`)
  that turns a validated `ProductDefinition` into an immutable, content-addressed
  `CompiledProduct` plus a machine-readable `CompilationReport`. `BindingResolver`
  resolves manifest reference placeholders into immutable `Binding`s against an
  injected immutable `BindingSnapshot`
  ([ADR-0019](docs/adr/ADR-0019-binding-resolution-against-registry-snapshot.md)) —
  never a live Epic 6 registry, never executing/initializing/loading/communicating.
  `DependencyAnalyzer` (missing refs, cycles, invalid ordering, unreachable nodes,
  deterministic topological order); `ConfigurationResolver` (Default→Environment→
  Tenant→Deployment precedence with deep-merge, override tracing, and conflict
  reporting; runtime/request remain contracts); `CompatibilityValidator` (static
  version/schema/platform checks); `OwnershipValidator` (rejects any config asserting
  execution ownership). Byte-identical determinism for identical
  definition+snapshot+compiler version. Extension seams only for
  caching/incremental/parallel compilation (no optimization engine). Packaging,
  product registry, deployment, and runtime execution are explicit non-goals.
  `product → core` only (freeze preserved, AST-test-enforced). 34 tests;
  1488/1488 repository-wide; ruff clean.

- **Product Definition & Manifest Core (M7.0B-1)** — the first Epic 7 bundle, a new
  `platform/product/` package: the immutable authoring foundation of the Product
  Runtime, a declarative composition layer above the frozen Execution Platform
  ([ADR-0018](docs/adr/ADR-0018-product-runtime-declarative-composition-layer.md)).
  `ProductManifest` (declarative contract, data-not-code), `ProductDefinition`
  (validated + canonicalized + content-addressed, no runtime state), `ProductFactory`
  (load→parse→validate→canonicalize→construct, fail-fast), `ProductValidator`
  (schema/interface-version, required fields, SemVer, duplicate ids, reference
  well-formedness, secret integrity — no resolution), and `ProductDiagnostics`
  (deterministic finding set). Authoring-order-independent canonical hashing.
  References and secret references are inert placeholders — never resolved.
  Compilation, bindings, packaging, deployment, and registry are explicit non-goals.
  `product → core` only; no Epic 5/6 package imports it (freeze preserved,
  AST-test-enforced). 43 tests; 1454/1454 repository-wide; ruff clean.

## v1.0.0 — Execution Platform v1.0

**Status:** Production Certified · Epic 6 Frozen
**Date:** 2026-07-26
**Depends on:** Runtime Platform v1.0 (Epic 5)

The first official production baseline: the frozen **Execution Platform** — the
capabilities that turn the Epic 5 reasoning stack into an autonomous, tool-using,
multi-step agent platform.

### Major capabilities

- **Provider Runtime** — the sole execution boundary to external AI providers
  (registration, discovery, routing, failover, health, policy, tenancy).
- **Tool Runtime** — the execution layer for external capabilities an agent invokes,
  with governance, policy, observability, replay, and streaming.
- **Skill Runtime** — reusable, versioned, declarative capabilities (manifest, not code).
- **Hook Framework** — a deterministic cross-cutting lifecycle-interception library.
- **Workflow Runtime** — deterministic orchestration graphs over Skill/Tool/Hook.
- **Agent Runtime** — long-lived autonomous orchestration: goal decomposition and delegation.
- **Loop Engine** — a deterministic propose→critique→repair→validate→stop-check library.
- **Platform Integration** — the composition root wiring the certified capabilities end-to-end.
- **MCP Runtime** — the single owner of client-side Model Context Protocol communication.

### Architectural achievements

- One concern per capability; strict downward-only dependencies onto the contract floor.
- Acyclic dependency graph; capabilities are mutually independent and separately testable.
- Composition roots are the only integration points; nothing imports them back.
- Deterministic execution with offline-default adapters; capability-local, hash-chained replay.
- Capability-local observability with cross-capability correlation by `trace_id`.
- Tenant isolation across connections, credentials, and replay.

### Status

- Production Certified.
- **Epic 6 Frozen** — future work extends this baseline; it does not modify it.

See [`docs/releases/v1.0.0-release-notes.md`](docs/releases/v1.0.0-release-notes.md)
and the freeze manifest
[`docs/releases/epic6-execution-platform-v1.0.md`](docs/releases/epic6-execution-platform-v1.0.md).

## epic5-runtime-platform-v1.0 — Runtime Platform v1.0

**Status:** Production Certified · Epic 5 Frozen · **Date:** 2026-07-24

Memory, Knowledge, Context, Prompt, Reasoning, and Provider runtimes composed into
one deterministic Runtime Pipeline. See
[`docs/releases/epic5-runtime-platform-v1.0.md`](docs/releases/epic5-runtime-platform-v1.0.md).

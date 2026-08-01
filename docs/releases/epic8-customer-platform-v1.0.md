# Customer Platform v1.0 — Epic 8 Freeze Manifest (M8.20)

**Status:** 🔒 **Epic 8 FROZEN (architecture)** (M8.20) · **Date:** 2026-08-01 · **Depends on:**
Product Runtime v1.0 (Epic 7, frozen), Execution Platform v1.0 (Epic 6, frozen), Runtime Platform
v1.0 (Epic 5, frozen).

> This is the official architecture freeze of Epic 8. The Customer Platform's public architecture,
> ownership, dependency direction, and extension points are frozen as of this document. Future work
> **extends** this baseline; it does not modify it. This is a **governance document** — no commit,
> push, tag, or GitHub Release is performed here. **Governance status is recorded honestly in §22
> (Repository State): the Epic 8 implementation is present in the working tree but not yet committed
> to version control. This manifest freezes the architecture only; it does not assert a release.**

## 1. Executive Summary

The **Customer Platform** (`platform/api/*` + the top-level `platform/actuator/`) is the Epic 8
customer-facing surface — **VoxVoice Studio** and its Control Plane API — built as a deterministic,
layered, additive plane **atop** the frozen Runtime (Epic 5) / Execution (Epic 6) / Product Runtime
(Epic 7) stacks. It lets a customer authenticate, organize (org → workspace → project), build and
publish agents, attach tools, run conversations through real providers, manage knowledge, deploy
products, and observe the whole platform — entirely through the Studio UI + Control Plane API.

Delivered across milestones **M8.2–M8.18** and independently certified in **M8.19** (verdict:
*Certified with Conditions* — one MEDIUM release-governance condition, restated in §22). The
architecture is frozen here as **Customer Platform v1.0**. Evidence at freeze: **1774 repository-wide
tests pass** (~218 Epic-8 capability tests), `ruff check` clean on the Epic 8 surface, migration
chain **0001–0012** valid, and frozen Epics 5/6/7 **byte-for-byte unchanged**.

## 2. Frozen Scope

The following architecture is frozen. Everything is **additive** on the frozen Epics; no frozen Epic
was modified.

| Milestone | Frozen deliverable |
|---|---|
| M8.2 | Customer Platform **Foundation** (domain services, RBAC, seams) |
| M8.3 | **Application** layer (CQRS command/query buses, use cases, Unit of Work) |
| M8.4 | **Persistence** (SQLModel `cp_*` tables, repositories, composition root) |
| M8.5 | **Control Plane API** (thin FastAPI transport, middleware, error envelope) |
| M8.6 | **Organization Lifecycle** + invitations |
| M8.7 | **Workspace Lifecycle** + membership + navigation |
| M8.8 | **Project Lifecycle** + membership + environments |
| M8.9 | **Audit & Activity Platform** (durable, append-only read side) |
| M8.10 | **Agent Builder** (configuration objects; publish/version) |
| M8.11 | **Tool Framework** (catalog, attach/detach, ordering, config) |
| M8.12 | **Conversation Runtime** (deterministic execution plan; adapter interfaces) |
| M8.13 | **Conversation Playground** (mock adapters, deterministic) |
| M8.14 | **Provider Adapter Binding** (`api/adapters` — real LLM/voice/tool/memory over an injectable transport) |
| M8.15 | **Knowledge Tool** (RAG as a Tool Framework capability) |
| M8.16 | **Deployment Actuator** integration (`actuator/`, ADR-0022) |
| M8.17 | **Observability Platform** (metrics/logs/traces/health/usage/cost/dashboards) |
| M8.18 | **Production Hardening & GA readiness** |

## 3. Frozen Public Interfaces

Frozen as v1.0 (change only by MAJOR + ADR, per §24):

- **Foundation facade** (`api.foundation.Foundation`) — **28 domain services** (identity, membership,
  access, workspaces, projects, configuration, org/workspace/project lifecycle, audit reader,
  activity, agent builder, tools, agent-tools, conversation runtime, playground, conversation
  execution, knowledge, knowledge search, deployment, observability, …) + `FoundationFactory`.
- **Application facade** (`api.application.Application`) — the CQRS `CommandBus` + `QueryBus` and the
  registered command/query set; `ApplicationFactory`.
- **Persistence** — `build_persistent_application`, the `cp_*` schema (**22 tables**), the migration
  chain **0001–0012**.
- **Control Plane API** — **20 mounted routers** under `/v1/…` + `/health`, `/ready`, `/version`, and
  the Studio at `/studio`; the uniform error envelope (`{error:{type,message,request_id}}`).
- **Deployment Actuator** (`actuator`) — `DeploymentActuator`/`DeploymentEngine`, the lifecycle FSM,
  the `InfrastructureAdapter` contract + registry, `DeploymentExecutionRepository`.
- **Provider seams** (`api.adapters`) — `Transport` (`Echo`/`Failing`/`Http`), `CredentialProvider`,
  the LLM/voice/tool/embedding/vector registries, `ProviderExecutionEngine`.
- **Event envelope** — `FoundationEventType` (**109 typed facts**) on the append-only stream.

## 4. Ownership Boundaries (frozen — single writer per fact, §A2.5)

| Fact | Sole owner |
|---|---|
| Control-plane domain state | Foundation services |
| Conversation execution | Conversation Runtime / Provider Binding |
| Deployment execution state | **Deployment Actuator** (`actuator/`) only |
| Knowledge retrieval | Knowledge Tool (a Tool Framework capability) |
| Audit / event stream | the emitter (append-only; no update/delete) |
| Observation (metrics/logs/traces/health/usage/cost) | Observability (read-model; writes only alert-rule config) |
| User interaction / presentation | Studio (Consumer, ADR-0021 — owns no runtime state) |

No ownership overlap. Verified by the boundary + single-writer + observability-non-duplication tests.

## 5. Dependency Direction (frozen — acyclic, one-way, AR-15)

```
Studio → Control Plane API → Application → Foundation → { Persistence, api/adapters } (via composition roots)
actuator → product (read-only), core, tenant
```

- No Epic 8 layer imports a frozen runtime engine (AST-verified: `frozen-engine-imports = NONE` for
  foundation/application/persistence/customer/adapters/actuator).
- Foundation and Application never import persistence; Foundation never imports `product`.
- `actuator` never imports the control plane (`api.*`); it consumes `product` read-only.
- The freeze is one-way: **no frozen Epic imports Epic 8.**

## 6. Architecture Overview

A layered DDD control plane. Thin FastAPI controllers dispatch to the Application CQRS buses; use
cases coordinate Foundation domain services inside a Unit of Work; the Persistence composition root
wires SQL repositories + a durable audit store; `api/adapters` binds real providers behind an
injectable transport; `actuator/` owns deployment execution reading the frozen `DeploymentPlan`;
Observability projects the audit stream. Studio is a static Consumer served at `/studio`.

## 7. Component Inventory (frozen)

- **Foundation** — 37 modules (domain, services, RBAC, seams, factory, repositories, DTOs, events,
  observability, knowledge, deployment seam).
- **Application** — 24 modules (dispatcher, use cases, per-domain command/query modules, UoW).
- **Persistence** — models, mapping, repositories, composition, audit store, **migrations 0001–0012**.
- **Customer API** — app factory, middleware, config, container, auth, jwt, errors, 20 routers, Studio.
- **api/adapters** — transport, secrets, llm, voice, tools, memory, resilience, knowledge, engine,
  composition.
- **actuator/** — 9 modules (domain, lifecycle, engine, infrastructure, repository, planning, events,
  errors, `__init__`).

## 8. Extension Points (frozen seams — additive only)

Provider capabilities (LLM/voice/tool/embedding/vector registries), the `Transport` +
`CredentialProvider` seams, the `InfrastructureAdapter` registry (Local executes; production adapters
declared), the `KnowledgeRetriever`/`VectorStore`/`KnowledgeEmbedder` seams, the metric catalog, the
`AuditReader` seam, and injectable `Clock`/`IdGenerator`/`EventEmitter`. Adding a target/provider is
one adapter + one registry entry — no structural change (§A6/§A12).

## 9. Determinism Guarantees (frozen — §A2.4)

Control-plane behaviour is deterministic given fixed seams (`FixedClock` + `SequentialIdGenerator`
reproduce byte-identical output — `test_ga_certification::test_determinism_reproducible`). The FSM
transitions, chunking, hash embeddings, cosine retrieval, and metric projections are pure. Only the
provider/infra edge is non-deterministic and confined to the transport/adapter seams.

## 10. Identity Rules (frozen)

Prefixed, seam-generated ids (`usr`/`org`/`ws`/`proj`/`agt`/`tool`/`cvs`/`kc`/`kd`/`kch`/`alr`/`dpx`/
…). Deployment identity extends Epic 7's content-addressed chain read-only. Chunk/document checksums
and content-addressed package/deployment ids allow byte-level restore verification.

## 11. Configuration Rules (frozen)

Environment-driven `CustomerApiSettings` (`VOXVOICE_API_*`), validated at startup; production
**fails fast** on the default JWT secret, wildcard CORS, or in-memory SQLite. Secrets resolve through
the `CredentialProvider` seam (`VOXVOICE_SECRET_*`), never in code/logs/VCS; redacted at every
logging/serialisation boundary.

## 12. Provider Rules (frozen)

Providers are reached **only** through `api/adapters` behind the `Transport` seam (Echo/Failing
offline; Http in production). No provider SDK and no frozen-Epic import in the control plane. The
runtime owns orchestration; adapters own translation; **no provider-specific logic in the Runtime**.

## 13. Runtime Rules (frozen)

The Conversation Runtime consumes **only a published** agent version (drafts never execute), never
mutates an agent/tool/version/history, and produces a deterministic execution plan. Real execution is
bound in `api/adapters` (M8.14); retrieval reaches the runtime only as a Tool Result.

## 14. Deployment Rules (frozen — ADR-0022)

The **Deployment Actuator** is the **sole owner** of deployment execution; it consumes the frozen
`DeploymentPlan` read-only and never mutates Product Runtime. Deterministic lifecycle FSM
(`REQUESTED→VALIDATING→PENDING_APPROVAL→QUEUED→EXECUTING⇄VERIFYING→COMPLETED`, plus `FAILED`/
`ROLLING_BACK→ROLLED_BACK`/`CANCELLED`); separation-of-duties approval; first-class rollback; only
the Local adapter executes (production adapters declared behind the same contract).

## 15. Knowledge Rules (frozen)

Knowledge is a **Tool Framework capability** — not Runtime, not the LLM adapter, not Memory. A
`knowledge`-category tool is executed by the Tool Executor and reaches the Runtime only as a Context
Package. Deterministic chunking; provider-independent embedding/vector seams; **no OCR/image, no LLM
reranking**.

## 16. Audit Rules (frozen — §A5/§A11.2)

The event log **is** the audit log — append-only, immutable (the read seam exposes query/stream, no
update/delete — `test_audit_stream_is_append_only`). One typed event per fact; org-scoped.

## 17. Observability Rules (frozen)

Observability is a **passive read-model** over the audit stream — metrics/logs/traces/health/usage/
cost/dashboards are projections computed on read; it writes **only** alert-rule config. No polling, no
direct Runtime/Deployment access, **no duplicate audit events**
(`test_observability_does_not_duplicate_audit_events`).

## 18. Security Rules (frozen — §A11.2)

Every fact carries `org_id`; every query is org-scoped (cross-tenant is impossible). Workspace/project
scoping narrows further. Bearer JWT (HS256, expiry, key-rotation seam); RBAC via existing scopes;
deployment approvals enforce separation of duties. Security headers, payload limits, rate limiting, 5xx
redaction. Production config fail-fast.

## 19. Operational Guarantees (frozen)

Startup validates config + verifies dependencies (fail fast); graceful, idempotent shutdown; liveness
`/health`, readiness `/ready`, `/version`; migration chain 0001–0012 idempotent; deterministic offline
operation (Echo transport + Local adapter). Ops docs: Production Architecture, Operations, Deployment,
Troubleshooting, Scaling, Security, Backup & Recovery, Disaster Recovery, Runbook, Support Handbook,
Production Checklist, GA Checklist.

## 20. Testing Summary

**1774 repository-wide tests pass** at freeze (1 benign third-party `StarletteDeprecationWarning`).
~218 Epic-8 capability tests across foundation (17), application (13), persistence (9), customer API
(15), org/workspace/project lifecycle (9/11/11), audit (12), agent builder (13), tool framework (10),
conversation runtime (11), playground (11), provider binding (10), knowledge (9), deployment (16),
observability (14), production hardening (13), GA certification (10), scale/soak (4). `ruff check`
clean on the Epic 8 surface; boundary/determinism/append-only/single-writer/OpenAPI-completeness
tests green.

## 21. Certification Summary

Independent Architecture Review Board certification (**M8.19**): **CERTIFIED WITH CONDITIONS**. All
architectural, ownership, dependency, layering, CQRS, composition-root, seam-separation, determinism,
immutability, single-writer, append-only, and acyclic-import criteria met with repository evidence;
frozen Epics provably unmodified. One MEDIUM condition (release governance) — see §22. See
[GA Certification v1.0](ga-certification-v1.0.md) and the M8.19 review.

## 22. Repository State (governance — recorded honestly)

- **Frozen Epics 5/6/7:** tracked and **unmodified** (`git status` on `runtime/product/provider/
  pipeline/reasoning` = 0 changes). Freeze integrity: **strong**.
- **Epic 8 implementation:** **present in the working tree but NOT committed to version control.**
  `git ls-files actuator` = 0; `git ls-files api/foundation` = 0; the Epic 8 packages (`api/foundation`,
  `api/application`, `api/persistence`, `api/customer`, `api/adapters`, `actuator/`) and their tests +
  architecture/operations/GA docs appear as **untracked (`??`)**. Only documentation index files
  (INDEX, CHANGELOG, DECISION_LOG, ADR Register) are tracked-and-modified.
- **This is the M8.19 MEDIUM condition, unresolved at freeze time.** Therefore, per the milestone
  directive: **this manifest freezes the architecture only. It does NOT claim the repository has been
  released, that code has been committed, or that any tag exists.** Resolving the condition (commit
  the Epic 8 tree + docs, then tag) is a governance action requiring **no architectural change**.

## 23. Version Recommendation

- **Customer Platform v1.0** — the frozen Epic 8 architecture baseline (this document).
- **Platform baseline recommendation → v3.0.0** at tag time (after the §22 condition is met): Epic 8
  adds a new public surface (`api.*` + `actuator.*`) atop the Epic 7 baseline (v2.0.0) with **no
  breaking change** to Epic 5/6/7 interfaces. The MAJOR bump reflects a new top-level capability line
  (Customer Platform), consistent with the Epic 6→v1.0.0 / Epic 7→v2.0.0 baseline-line convention. The
  Control Plane API itself reports `1.0.0` (its own SemVer surface). **No tag is asserted here.**
- **SemVer policy:** MAJOR = breaking change to any frozen public interface (§24) or dependency
  direction; MINOR = additive services/routers/commands/queries/providers/adapters/metrics/migrations;
  PATCH = fixes.

## 24. Extension Policy (freeze policy)

After this freeze:

- Epic 8 **public interfaces are frozen.** A **breaking change** requires: a **new ADR** + a **major
  version** + a **new frozen baseline**.
- **Additive extensions remain permitted** — new services/routers/commands/queries/providers/vector
  stores/infrastructure adapters/metrics/migrations land additively behind the frozen seams (§A6/§A12),
  no ADR required.
- Compatibility-critical (change only by MAJOR + ADR): the ownership boundaries (§4), the dependency
  direction (§5), the append-only audit contract (§16), the single-writer rule (§4), and the frozen
  Epic 5/6/7 freeze.

## 25. Cross-References

- Epic 5 — [Runtime Platform v1.0](epic5-runtime-platform-v1.0.md) (frozen).
- Epic 6 — [Execution Platform v1.0](epic6-execution-platform-v1.0.md) (frozen).
- Epic 7 — [Product Runtime v1.0](epic7-product-runtime-v1.0.md) (frozen).
- Epic 8 — **Customer Platform v1.0 (this document)** — the customer-facing control plane atop Epic 7.
- Certification — [GA Certification v1.0](ga-certification-v1.0.md); operations — [docs/operations/](../operations/).
- ADRs — [ADR-0021](../adr/ADR-0021-studio-consumer-and-management-surface.md) (Studio = Consumer),
  [ADR-0022](../adr/ADR-0022-deployment-actuator-runtime-actuation.md) (Deployment Actuator).

## 26. Final Freeze Declaration

The Epic 8 Customer Platform **architecture** — its public interfaces, ownership boundaries, dependency
direction, and extension points — is **frozen as Customer Platform v1.0**. Future work extends this
baseline additively; breaking changes require a new ADR + major version + new baseline. **This is an
architecture freeze governance document; no commit, push, tag, or release is performed or claimed. The
Epic 8 implementation remains uncommitted in the working tree (§22).**

---

**EPIC 8 ARCHITECTURE IS OFFICIALLY FROZEN (Customer Platform v1.0).**

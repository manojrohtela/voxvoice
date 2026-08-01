# ADR-0022 — The Deployment Actuator is the sole owner of deployment execution; Product Runtime stays declarative

| | |
|---|---|
| **Status** | **Accepted 2026-07-27** (ratified at M8.0C). Recommended in the [Epic 8 Product Blueprint](../architecture/epic8/product-blueprint.md) §13/§24 and [ADR-0021](ADR-0021-studio-consumer-and-management-surface.md); this milestone ratifies it. |
| **Amends** | Introduces the Epic 8 **Deployment Actuator** atop the frozen Product Runtime v1.0 (Epic 7). Amends no Constitution Part-A/Part-B section and no frozen Epic; records the load-bearing ownership boundary for deployment *execution*. |
| **Deciders** | Technical Owner |
| **Related** | [Epic 7 deployment-planning design](../architecture/epic7/product-deployment-planning.md), [Epic 7 Freeze Manifest](../releases/epic7-product-runtime-v1.0.md), [Epic 8 Product Blueprint](../architecture/epic8/product-blueprint.md), [ADR-0018](ADR-0018-product-runtime-declarative-composition-layer.md) (Product Runtime = declarative composition layer), [ADR-0021](ADR-0021-studio-consumer-and-management-surface.md) (Studio owns no runtime state), [ADR-0009](ADR-0009-workflow-durable-repository-exception.md) (durable-repository precedent), Constitution §A2.4 (deterministic behaviour) / §A2.5 (one writer per fact) / §A4 (two-plane) / §A5 (event envelope) / §A6 (capability plugins) / §A9 (deployment stages) / §A10 (fault tolerance) / §A11.2 (security) / §A12 (additive extension), ARCHITECTURE_RULES AR-15 (acyclic) / AR-50 (one responsibility) |

## Status

**Accepted 2026-07-27**, ratified at milestone **M8.0C**. The Epic 8 Product Blueprint
(M8.0A) named the actuation gap as risk **R2** and recommended this ADR; ADR-0021 listed
it as a dependent decision. This ADR ratifies the boundary. It authorizes no code; it
fixes the architecture every future Deployment-Actuator implementation milestone (M8.x)
must honour. Product Runtime v1.0 (Epic 7) remains frozen and is not modified.

## Context

The Product Runtime produces a strictly declarative chain and, by deliberate design,
**stops there**:

```
ProductManifest → ProductDefinition → CompiledProduct → ProductPackage → DeploymentPlan
```

The `DeploymentPlan` (an immutable `DeploymentDescriptor` = deployment *intent*, plus a
`DeploymentReport`) is the terminal artifact. Epic 7's own design and Freeze Manifest are
explicit: it **never performs a deployment, never calls infrastructure, never starts a
runtime session** — its GitOps/Kubernetes/Terraform/scheduling/approval/orchestration
references are *extension seams that do not actuate*. That freeze is authoritative and
must not change.

Customers (Epic 8, VoxVoice Studio) expect the actions that a declarative plan cannot
perform: **Deploy, Rollback, Pause, Resume, Scale, Blue/Green, Canary, Environment
Promotion, Approval, Audit.** Each of these is *execution* — communicating with real
infrastructure, tracking long-lived deployment state, retrying, recovering. No component
owns that today.

The decision to settle before any Epic 8 deployment code lands: **where does execution
live, and how, without breaking Epic 7's declarative freeze or the ownership discipline
ADR-0018/0021 established?** Two constitutional constraints bound the answer:

- **§A2.5 — one writer per fact.** Deployment *execution state* (in-flight rollouts,
  approval status, rollback progress, live deployment health) is a fact. It must have
  exactly one owner. It is **not** Product Runtime's (which owns *intent*), and — per
  ADR-0021 — it is **not** Studio's (a Consumer owns no runtime state).
- **§A12 / §A6 — additive extension.** New infrastructure targets (Kubernetes, Docker,
  ECS, Azure, GCP, Nomad, GitOps) must be pluggable, exactly as provider capabilities are:
  one adapter + one registry entry, never a structural change.

## Decision

**Ratify a new component — the Deployment Actuator — as the single owner of deployment
execution, sitting above the frozen Product Runtime and consuming its `DeploymentPlan`
read-only.**

The Deployment Actuator **SHALL**:

- **consume `DeploymentPlan`s** (the frozen Epic 7 artifact) as immutable, read-only input;
- **execute** deployments (apply the plan's intent to real systems);
- **communicate with infrastructure** exclusively through **Infrastructure Adapters** (a
  capability-plugin seam, §A6 pattern);
- **own and report deployment status**, and **emit typed events** on the §A5 envelope;
- **support** rollback, retries, approvals, pause/resume, scale, blue/green, canary, and
  environment promotion;
- **remain completely outside Product Runtime** — a new top-level package
  `platform/actuator/`, a peer of `product/`/`integration/`/`bridge/` (new top-level
  directory ⇒ this ADR, per CODING_STANDARDS §2 / ADR-0018 precedent).

The **Product Runtime SHALL NEVER execute deployments.** It stays exactly as frozen:
declarative generation, validation, planning, identity, diagnostics. The Actuator reads
its output; Product Runtime never learns the Actuator exists.

**Deterministic behaviour, non-deterministic edge (§A2.4).** Deployment *content*
(what infrastructure does) is non-deterministic and confined to the adapter seam; the
Actuator's *behaviour* — lifecycle transitions, retry/backoff, approval gating, rollback
routing, timeouts — is a deterministic, table-driven state machine, exactly as the
conversation runtime is (§A4.4). "Own the loop, rent the edges" (§A2) applied to
deployment: VoxVoice owns the deployment loop; infrastructure is a rented edge behind a
contract.

## Ownership Model

No ownership overlap. Each fact has exactly one writer (§A2.5).

| Concern | Owner | Notes |
|---|---|---|
| ProductManifest / Definition / Compilation / Package | **Product Runtime** (Epic 7, frozen) | declarative authoring & compilation |
| `DeploymentPlan` **generation**, validation, planning, **identity**, diagnostics | **Product Runtime** (Epic 7, frozen) | deployment *intent* only; never actuates |
| Deployment **execution** & orchestration | **Deployment Actuator** (new, Epic 8) | applies a plan; drives adapters |
| **Runtime deployment state** (in-flight rollout, live version, health) | **Deployment Actuator** | durable, in the System of Record (§D6/§A7); sole writer |
| Deployment **lifecycle** (FSM), retries, **rollback**, **approvals** | **Deployment Actuator** | deterministic, table-driven (§A2.4) |
| **Infrastructure communication** | **Deployment Actuator** *via Infrastructure Adapters* | Actuator never speaks a vendor protocol directly |
| Kubernetes / Docker / Terraform / GitOps / Cloud provider mechanics | **Infrastructure** (external) | reached only through an adapter (§A6) |
| Deploy *request* + presentation/projection of status | **Studio** (Consumer, ADR-0021) | requests execution; owns no execution state |
| Auth / session admission / control routing | **Control Plane** (§A4) | routes a deploy request to the Actuator |
| Event log / `metric.*` (the truth Studio projects) | **Runtime** producers (§A5) | Actuator is one more event *producer* |

## Deployment Architecture

The Actuator's internal components (each a single responsibility, AR-50; specified per
§A11.3 when built):

| Component | Responsibility (single) |
|---|---|
| **Deployment Engine** | Sole writer of deployment execution state; drives the deployment FSM; applies a `DeploymentPlan` |
| **Execution Queue** | Bounded, durable work queue of deployment jobs (System of Record; never an unbounded in-memory queue — §A11.1) |
| **Job Scheduler** | Deterministic ordering/dispatch of jobs to workers; concurrency limits per env/target |
| **Approval Engine** | Gates execution on required approvals (RBAC + separation of duties, ADR-0021 / §A11.2); verdict-only, never actuates |
| **Rollback Engine** | Reverts to a prior version per Epic 7's `RollbackPolicy` (same-major rule); a first-class execution path, not an afterthought |
| **Health Verification** | Post-apply health/SLO checks that gate promotion/completion (consumes metrics, §A5) |
| **Deployment Events** | Emits typed `deployment.*` events on the §A5 envelope (one producer per fact) |
| **Deployment Metrics** | Emits `metric.deployment.*` (duration, success rate, rollout progress, MTTR) |
| **Deployment History** | Immutable, content-addressed record extending Epic 7's identity chain (…→Deployment ID→execution attempts) |
| **Retry Policy** | Deterministic backoff + budget + circuit-breaker hook (§A10), idempotent by construction |
| **Failure Recovery** | Graceful-degradation ladder (§A10): retry → partial-rollback → inform → clean failure with full event trail |
| **Idempotency** | Every apply keyed by `(deployment_id, attempt)`; re-applying a completed step is a no-op |
| **Concurrency Control** | One in-flight deployment per (product, environment); serialize or reject conflicting deploys |
| **Cancellation** | A propagated cancellation token (mirrors `PipelineCancellation`), always reaching a terminal state — never an orphaned deploy |
| **Timeouts** | Per-stage and whole-deployment timeouts with typed errors (§A10) |

**Deployment lifecycle (deterministic FSM).**
`REQUESTED → VALIDATING → PENDING_APPROVAL → QUEUED → EXECUTING (⇄ VERIFYING) → COMPLETED`,
with `ROLLING_BACK → ROLLED_BACK` and `FAILED`/`CANCELLED` reachable per table-driven
policy. All transitions are events (§A5); illegal transitions raise; identical
plan + adapter outcomes drive identical transitions (§A2.4).

## Interaction / Execution Flow

The full downward flow, extending ADR-0021's Studio→Platform model:

```
Studio  ──deploy request──►  Control Plane  ──authorized request──►  Deployment Actuator
                                                                          │ reads (immutable)
                                                                          ▼
                                                                   DeploymentPlan (Epic 7, frozen)
                                                                          │
                                                                          ▼
                                                              Infrastructure Adapter (§A6 seam)
                                                                          │
                                                                          ▼
                                                        Cloud / Kubernetes / Docker / GitOps / …
                                                                          │
                    ┌─────────────────────────────────────────────────────┘
                    ▼            ▼            ▼             ▼
                 Events  →   Metrics   →   Audit log  →  Deployment History
                    │            │            │             │
                    └────────────┴─────► projections ◄──────┘
                                             │
                                             ▼
                                          Studio (renders status — owns none of it, ADR-0021)
```

**No reverse ownership.** Product Runtime never calls the Actuator; the Actuator never
calls back into Studio (it emits events Studio subscribes to). Infrastructure is reached
only through an adapter — the Actuator contains no vendor-specific logic (§A6.6).

## Provider / Extension Model

Infrastructure targets are **plugins in an adapter registry**, selected per
deployment target by the plan's `DeploymentProfile`/`DeploymentTarget`, exactly as
provider capabilities work (§A6) and as `ProviderModelBridge`/`MCPToolBridge` already
demonstrate.

- **Contract:** one `InfrastructureAdapter` interface — `apply(plan) → status`,
  `rollback(target) → status`, `status(target)`, `health(target)` — with declared
  capability flags (supports-canary, supports-blue-green, supports-scale, …).
- **Adding a target** = one adapter module + one registry entry + passing the shared
  adapter contract-test suite. Nothing else changes (§A6.3 / §A12).
- **Planned adapters (examples, none privileged):** Kubernetes, Docker/Compose, GitOps
  (Argo/Flux-shaped), AWS ECS, Azure, GCP, Nomad. A **Local/Dev** adapter is the
  deterministic default (mirrors Epic 5's `EchoProvider`/`LocalTransport`) so the whole
  actuator path runs offline in tests.
- **Degraded modes are declared:** an adapter lacking canary/blue-green declares it; the
  Actuator adapts (e.g. falls back to rolling) and records the degradation — never a
  silent no-op (§A6.4 / §A2.6).

## Environment Model

Environments come from Epic 7's `EnvironmentProfile`/`DeploymentProfile`/`DeploymentTarget`
(reused, not redefined): **Development, Testing, Staging, Production, and Custom
environments.** **Promotion** between environments is a first-class Actuator operation —
deploy the *same immutable `ProductPackage`* to the next environment's target (identity
preserved across the promotion; content-addressed, reproducible). Promotion to Production
is gated by the Approval Engine and Health Verification. Per-environment concurrency and
policy (canary %, batch size, approval requirement) come from the plan's `DeploymentPolicy`.

## Security Model

Inherits §A11.2 and ADR-0021's boundary:

| Concern | Design |
|---|---|
| **Authentication** | The Actuator accepts only Control-Plane-authorized requests (org-scoped); no direct public surface |
| **Authorization** | Org-scoped on every action (§A11.2); RBAC (ADR-0021 §5: Developer deploys non-prod, Approver gates prod) |
| **Secrets** | Infrastructure credentials never in code/logs/VCS; injected via a `CredentialProvider` seam (the Tool/MCP precedent); redaction at the logging sink (§A11.1.6) |
| **Approvals** | Separation of duties — author ≠ approver; prod deploys and destructive actions require an explicit, audited approval before EXECUTING |
| **Audit** | Every request, approval, apply, rollback, and failure is an event; the event log **is** the audit log (§A11.2) |
| **RBAC** | Actions map to ADR-0021 roles; Approver/Workspace-Admin gate prod; Read-Only sees status only |
| **Least privilege** | Each adapter runs with the minimum infrastructure scope its target requires; no ambient cluster-admin |

## Failure Model

Full §A10 fault-tolerance ladder applied to deployment:

| Failure | Handling |
|---|---|
| **Transient / retryable** | Deterministic exponential backoff within a retry budget; circuit-breaker per target |
| **Rollback** | First-class path to the last-good version (Epic 7 `RollbackPolicy`, same-major rule); deterministic |
| **Partial failure** | Detected per-step; policy decides continue / halt / roll back; state never left ambiguous |
| **Timeout** | Per-stage + whole-deployment; typed error; routes to failure recovery |
| **Network failure** | Treated as retryable up to budget, then failover/degrade/inform |
| **Infrastructure failure** | Surfaced verbatim from the adapter; typed; audited; no silent swallow |
| **Provider (adapter) failure** | Isolated to that adapter; typed error; does not corrupt Actuator state |
| **Manual recovery** | An operator can inspect the deterministic history and drive a manual rollback/retry — every state is queryable |

**Idempotency + cancellation guarantee no orphans:** a crashed or cancelled deployment
recovers from durable state to a terminal or safely-resumable state; re-applying a
completed step is a no-op.

## Observability

- **Events:** typed `deployment.requested|validated|approved|queued|started|step.*|
  verifying|completed|rolling_back|rolled_back|failed|cancelled` on the §A5 envelope
  (one producer per fact; new event types are additive, §A5).
- **Metrics:** `metric.deployment.*` — duration, success/failure rate, rollout progress,
  MTTR, approval latency.
- **Logs / Tracing:** structured logs + trace correlation by `deployment_id` (reusing the
  existing observability seam; no new tracing engine).
- **Audit Trail:** the event log doubles as the immutable audit record (§A11.2).
- **Deployment Timeline:** a visualization-only projection (the Replay Platform pattern —
  read-only, deterministic, honest metrics) Studio renders; the Actuator owns the events,
  Studio owns none of the state.

## Dependency Direction

**Frozen, one-way:** `actuator → product (read-only), core, tenant` (+ its own adapter
registry). 

- Product Runtime **never** imports or depends on the Actuator (freeze preserved by
  construction — the import-boundary/AST test fails if `product` grows an `actuator`
  import).
- Studio → Control Plane → Actuator (ADR-0021 model extended); nothing calls *up* into
  Studio.
- The graph stays acyclic (AR-15), exactly as ADR-0018 fixed `product → …` and ADR-0021
  fixed `Studio → Platform`.

## Consequences

**Benefits.**
- Epic 7 stays frozen and declarative by construction; the Actuator reads its output and
  cannot alter it.
- One writer per fact holds (§A2.5): deployment execution state has exactly one owner.
- Infrastructure portability mirrors provider portability (I3): a new target is one
  adapter, not a rewrite.
- Deterministic behaviour (§A2.4) makes deployments reproducible, testable offline (Local
  adapter), and replayable; only the infra edge is non-deterministic.
- The customer verbs (deploy/rollback/canary/promote) get a real, governed home without a
  single frozen-Epic change.

**Trade-offs.**
- A new durable-state service is real operational surface (a control-plane-side service at
  §A9 Stage 2+, like the webhook dispatcher) — not free for a solo/small team (Blueprint
  R1/R2). Mitigated by shipping the Local adapter + a single target (Kubernetes *or*
  Docker) first, others demand-gated.
- Deployment execution state is **durable** (unlike the Epic 5 runtimes' temporary
  repositories). This is not the ADR-0009 "closed-runtime exception" — it is ordinary
  System-of-Record state (§D6/§A7), the same class as sessions and the outbox — but it
  does obligate the durable-state discipline (retention, recovery, one writer).

**Future extensibility.**
- New targets, new rollout strategies, telephony-style new *capability kinds* all land as
  additive adapters/strategies (§A12).
- GitOps-native flows (the Actuator opens a PR rather than applying directly) are just
  another adapter behaviour behind the same contract.

**Risks.**
- Chief risk is the Actuator reaching *into* Product Runtime or Studio caching execution
  state as truth. Mitigated by the one-way dependency test, ADR-0021's Forbidden Writes,
  and this ADR being mandatory on every deployment milestone.

## Alternatives Considered

1. **Extend Product Runtime to actuate (add execution to Epic 7).** **Rejected.** Breaks
   the Epic 7 freeze, violates ADR-0018 (Product Runtime owns description, never
   execution), and collapses the intent/execution separation the whole Product Platform
   rests on. Explicitly forbidden by the Freeze Manifest.
2. **Let Studio actuate directly (Studio calls infrastructure).** **Rejected.** Violates
   ADR-0021 — a Consumer owns no runtime state and issues requests, not infrastructure
   calls — and would scatter deployment execution across every client. Studio *requests*;
   the Actuator *executes*.
3. **A generic external CI/CD tool (pure GitOps/Argo/GitHub Actions) as the only path,
   with no VoxVoice component.** **Discussed, not adopted as the model.** VoxVoice would
   forfeit the loop it must own — typed deployment events, mid-deploy control, first-class
   rollback, deterministic history, and governed approvals tied to its own RBAC — exactly
   the "own the loop, rent the edges" principle (§A2) that D1 already applied to the voice
   engine. Such tools are welcome *as adapter targets* (GitOps adapter), never as the
   owner.
4. **Current proposal — a dedicated Deployment Actuator owning execution, reading the
   frozen plan, driving pluggable infrastructure adapters.** **Accepted.** The only option
   consistent with §A2.5 (one writer), the Epic 7 freeze + ADR-0018 (declarative Product
   Runtime), ADR-0021 (Studio owns nothing), and §A6/§A12 (pluggable, additive targets).

## Compliance Verification

- **Constitution:** consistent with §A2.4 (deterministic behaviour / non-deterministic
  edge), §A2.5 (one writer per fact — the spine), §A4 (Actuator is a control-plane-side
  service, not a third plane), §A5 (one event envelope; Actuator is a producer), §A6
  (pluggable infrastructure adapters with declared flags), §A9 (a Stage-2+ service,
  topology not new boundaries), §A10 (fault-tolerance ladder), §A11.2 (org-scoped
  security/audit), §A12 (additive targets). No Part-A/Part-B amendment.
- **ADR-0018:** consistent — Product Runtime remains declarative; the Actuator sits above
  it and depends downward; the `product → …` direction is never inverted.
- **ADR-0019 / ADR-0020:** no conflict — those govern the compiler's `BindingSnapshot` and
  reserved Registry nouns *inside* Product Runtime; the Actuator touches neither type nor
  term.
- **ADR-0021:** consistent and complementary — Studio issues a deploy *request*; the
  Actuator is the "future Deployment Actuator" ADR-0021's Ownership Matrix already names as
  the owner of deployment execution.
- **Product Runtime remains declarative:** verified — the Actuator only *reads* the
  immutable `DeploymentPlan`; Epic 7 gains no execution surface and is not modified.
- **Deployment Actuator is the only execution owner:** verified — the Ownership Model gives
  execution/orchestration/runtime-deployment-state to exactly one component.

## Documentation

Updated in the same change-set: the ADR Register ([adr/README.md](README.md)), the
documentation index ([INDEX.md](../INDEX.md)), and the [CHANGELOG](../../CHANGELOG.md);
cross-referenced with the [Epic 7 Freeze Manifest](../releases/epic7-product-runtime-v1.0.md),
the [Epic 8 Product Blueprint](../architecture/epic8/product-blueprint.md), and
[ADR-0021](ADR-0021-studio-consumer-and-management-surface.md). Rationale recorded as
**DL-0088**.

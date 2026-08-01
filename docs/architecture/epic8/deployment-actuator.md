# VoxVoice — Deployment Actuator (Epic 8, M8.16)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/deployment-actuator.md` |
| **Status** | 🟢 **Implemented (M8.16)** — the **Deployment Actuator** (ADR-0022): the sole owner of deployment *execution*, a new top-level `actuator/` package sitting above the frozen Product Runtime and consuming its `DeploymentPlan` **read-only**. |
| **Date** | 2026-08-01 |
| **Scope** | The deployment execution FSM + engine + coordinators (queue/scheduler/approval/rollback/health/cancellation/retry/timeout); provider-independent Infrastructure Adapters (Local executes; Docker/Kubernetes/GitOps/Terraform/ECS/Azure/GCP/Nomad declared); deployment lifecycle (`REQUESTED→VALIDATING→PENDING_APPROVAL→QUEUED→EXECUTING⇄VERIFYING→COMPLETED`, plus `FAILED`/`ROLLING_BACK→ROLLED_BACK`/`CANCELLED`); approval (separation of duties); rollback; health verification; durable execution history; API; Studio. **No Monitoring, no autoscaling, no distributed scheduling.** |
| **Authority** | Realizes **ADR-0022** on the frozen Product Runtime v1.0 (Epic 7). **No new ADR, no architectural redesign.** Product Runtime / Conversation Runtime / Tool Framework / Provider Binding / Knowledge Tool reused exactly and unchanged; frozen Epics 5/6/7 untouched. All prior 1717 tests still green. |
| **Related** | [ADR-0022](../../adr/ADR-0022-deployment-actuator-runtime-actuation.md) · [ADR-0018](../../adr/ADR-0018-product-runtime-declarative-composition-layer.md) · [ADR-0021](../../adr/ADR-0021-studio-consumer-and-management-surface.md) · [Epic 7 deployment planning](../epic7/product-deployment-planning.md) · [Knowledge Tool](knowledge-tool.md) · [Provider Adapter Binding](provider-adapter-binding.md) · Constitution §A2.4/§A2.5/§A6/§A10/§A11.2 · [INDEX](../../INDEX.md) |

## Problem & approach

The Product Runtime (Epic 7) produces a strictly declarative chain ending at an immutable
`DeploymentPlan` and, by design, **stops there** — it never actuates. Customers need the verbs a
plan cannot perform: **deploy, approve, execute, view progress, cancel, roll back, view health/logs.**
Each is *execution*: talking to infrastructure, tracking long-lived state, retrying, recovering.

Per **ADR-0022**, execution has exactly one owner (§A2.5): the **Deployment Actuator** — a new
top-level `actuator/` package (peer of `product/`), depending downward `actuator → product
(read-only), core, tenant`. It **reads** the frozen `DeploymentPlan` and never modifies Product
Runtime; Product Runtime never learns the Actuator exists (the import-boundary test fails if it
does). Its *behaviour* is a deterministic, table-driven FSM; only the infrastructure edge is
non-deterministic and confined to a pluggable adapter — "own the loop, rent the edges" (§A2).

## Deployment architecture

- **`DeploymentEngine`** — the **sole writer** of deployment execution state; drives the FSM; applies
  a plan through an adapter; emits typed `deployment.*` facts through an injected sink (so it never
  imports the control plane or the audit store).
- **Coordinators** (single responsibility each, AR-50): `DeploymentQueue` (bounded, ordered work
  queue — never unbounded, §A11.1), `DeploymentScheduler` (one in-flight deploy per
  product+environment — concurrency control), `ApprovalCoordinator` (separation-of-duties gate,
  verdict-only), `RollbackCoordinator` (resolves + drives revert), `HealthVerifier`
  (startup→readiness→liveness gate), `CancellationManager` (propagated token — never an orphan),
  `RetryManager` (deterministic backoff + budget), `TimeoutManager` (per-stage + whole deadlines),
  `DeploymentExecutor` (apply/verify step-runner).
- **`DeploymentActuator`** — the public facade the control plane calls (`deploy/approve/reject/
  cancel/retry/rollback` + reads). Contains no vendor logic; never mutates the plan.
- **`DeploymentExecution`** aggregate — immutable; every mutator appends a timeline entry
  (transitions/approvals/rollbacks/health/logs), so the full history *is* the audit trail.

## Execution lifecycle (deterministic FSM)

```
REQUESTED → VALIDATING → PENDING_APPROVAL → QUEUED → EXECUTING ⇄ VERIFYING → COMPLETED
                    │                                    │
                    └── FAILED / CANCELLED               └── ROLLING_BACK → ROLLED_BACK
```

`lifecycle.py` is a pure `LEGAL_TRANSITIONS` table + `assert_transition` (illegal transitions raise;
identical plan + adapter outcomes drive identical transitions, §A2.4). Terminal: `COMPLETED /
FAILED / ROLLED_BACK / CANCELLED`. A `FAILED` deploy can be retried (`→ QUEUED`); a `COMPLETED`
deploy can be rolled back (`→ ROLLING_BACK`). Every transition is a `deployment.*` event (§A5).

## Infrastructure adapter architecture

One provider-independent `InfrastructureAdapter` contract (`apply / stop / restart / status /
health / logs / rollback`) with declared capability flags (executes / rollback / scale / canary /
blue-green). Adding a target = one adapter + one registry entry (§A6.3 / §A12). The
`InfrastructureRegistry` selects the adapter per `DeploymentJob.target`.

- **`LocalDeploymentAdapter`** — the deterministic, in-process default (no containers, no cloud). It
  records applied versions in memory so status/health/rollback are reproducible; a `source_ref`
  prefixed `fail:` deterministically raises `InfrastructureError` (exercises the failure/retry/
  rollback paths offline). **The only adapter that executes.**
- **Declared production adapters** — Docker, Kubernetes, GitOps, Terraform, AWS ECS, Azure, GCP,
  Nomad: registered with capability flags, but each raises a typed `ProductionAdapterUnavailableError`
  on actuation (a declared degradation, never a silent no-op — §A2.6/§A6.4).

## Approval architecture

Production is approval-gated automatically (§Environment/Security). The `ApprovalCoordinator`
enforces **separation of duties** (ADR-0021 §5): the requester can never approve their own
deployment. Approvals are recorded on the aggregate; an `APPROVED` verdict advances `PENDING_APPROVAL
→ QUEUED`, a `REJECTED` verdict advances to `FAILED`. Auto-approve records a `system` approval (dev/
test convenience). Writes authorize via the existing workspace-scoped `PROJECT_MANAGE` — **no new
RBAC scope**; a workspace **admin** is the natural approver.

## Rollback architecture

First-class, not an afterthought. `RollbackCoordinator.resolve_target` derives the target version
from Epic 7's `RollbackPolicy`; `execute` drives the adapter's `rollback`, records a `RollbackRecord`
(target/reason/actor/ok), and advances `ROLLING_BACK → ROLLED_BACK`. Reasons: `HEALTH_FAILED` (a
failed health check auto-routes to rollback), `MANUAL` (an operator rolls back a completed deploy),
`EXECUTION_FAILED`.

## Health verification

Post-apply, the `HealthVerifier` probes **startup → readiness → liveness** through the adapter and
records a `HealthReport`. Healthy → `COMPLETED`; unhealthy → `HEALTH_FAILED` event → rollback. The
API exposes live health (`GET …/health`) and the per-transition health history.

## Execution history & durability

Deployment execution state is **durable** ordinary System-of-Record state (§D6/§A7 — one writer, not
the ADR-0009 closed-runtime exception). Persisted in `cp_deployment_execution` (**migration 0011**):
scalar columns for project/deployment/state queries + the full immutable aggregate (job +
transitions + approvals + rollbacks + health + logs + result) in a portable JSON `data` column (the
message-store precedent). Idempotent create; runs identically on SQLite and Postgres.

## UI screens (Studio)

Deployment Dashboard · Environment Selector · Deployment History · Approval Queue · Rollback Manager
· Health Viewer · Deployment Timeline · Execution Logs. Studio **requests** deployment and renders
status; it owns **no** execution state (ADR-0021).

## Layer changes (every Foundation change justified)

- **New top-level `actuator/` (ADR-0022):** `domain.py` (aggregate + FSM enums + views),
  `lifecycle.py` (pure transition table), `infrastructure.py` (adapter seam + Local + 8 declared
  targets + registry), `repository.py` (durable seam + in-memory), `engine.py` (engine + 9
  coordinators + facade), `planning.py` (the **only** place Epic 7's `DeploymentPlan` is reused —
  read-only), `events.py`, `errors.py`. Depends downward `actuator → product (read-only), core`.
- **Foundation (additive, required):** `deployment.py` — `DeploymentActuatorService` (the
  control-plane seam: authorizes, derives the plan via `actuator.planning`, enforces org/workspace/
  project isolation, mirrors `deployment.*` facts into the durable audit stream). **Why required:**
  the control plane needs a seam onto the Actuator and an audit bridge. **Why safe:** additive +
  defaulted (in-memory execution repo default); every prior service is untouched; **the Foundation
  imports only `actuator`, never `product`** (frozen-Epic import boundary preserved — boundary tests
  green). Additive `FoundationEventType.DEPLOYMENT_*`; the factory gains a defaulted execution repo.
- **Persistence (additive):** `cp_deployment_execution` + `SqlDeploymentExecutionRepository` + row↔
  aggregate mapping + **migration 0011**; composition threads the repo (provider-agnostic — it
  imports `actuator`, not `product`).
- **Application (additive):** `deployment.py` commands (submit/approve/reject/cancel/retry/rollback)
  + queries (plan/status/history/timeline/logs/health/targets) + use cases, registered on the buses.
- **API (additive):** the thin `…/deployments/…` router.
- **Studio (additive):** the eight deployment screens above.

## Definition of Done

A customer can: **publish an agent → create a deployment plan → submit a deployment → approve it →
execute the Local deployment → view progress → cancel → roll back → view health & logs** — using only
the Studio UI + Control Plane API. Proven by `test_definition_of_done_end_to_end` (publish → plan →
submit prod → approve by a workspace admin → completed → health/logs → rollback → cancel a fresh one
→ history/targets), plus lifecycle/approval/rollback/health/adapter/audit/isolation tests.

## Testing

`tests/test_deployment_actuator.py` — 16 tests: FSM legal/illegal transitions; the infrastructure
registry (Local executes, 8 production declared) + Local adapter capabilities; the engine
(submit→approve→execute→complete, auto-approve, no-approval, separation of duties, reject, cancel,
failure→retry, manual rollback, **health-failed auto-rollback**); the Control Plane API + Studio
**Definition-of-Done** flow; draft-agent rejection; SoD over the API; audit; cross-project + viewer
isolation; Studio screens served. **16/16 pass; 1733/1733 repository-wide; ruff clean; frozen Epics
5/6/7 untouched; every prior M8.2–M8.15 test green.**

## Architecture compliance

- **ADR-0022 honoured:** the Actuator is the sole owner of deployment execution; it consumes the
  `DeploymentPlan` read-only; Product Runtime is not modified and gains no execution surface;
  infrastructure is reached only through a pluggable adapter with declared capability flags; the
  behaviour is a deterministic FSM. No new ADR, no redesign.
- **Dependency direction (frozen, one-way):** `actuator → product (read-only), core, tenant`;
  `foundation → actuator`; the control plane never appears in `actuator`; Product Runtime never
  imports the Actuator. The graph stays acyclic (AR-15); the foundation import-boundary test
  confirms the Foundation imports no frozen Epic.
- **Constitution:** §A2.4 (deterministic FSM / non-deterministic edge), §A2.5 (one writer per fact),
  §A5 (one event envelope; the Actuator is a producer), §A6 (pluggable adapters), §A10 (retry/
  timeout/rollback ladder), §A11.2 (org/workspace/project isolation + separation of duties).

## Known limitations / later hardening

- **Only the Local adapter executes** — production adapters are declared with capability flags;
  wiring a real one (Docker/Kubernetes/GitOps/…) is one adapter behind the same contract.
- **Approve triggers execution** in this build (the Local path runs to completion on approval); a
  separate operator-driven execute step is a later refinement behind the same engine methods.
- **No Monitoring, no autoscaling, no distributed scheduling** — all explicitly out of scope; the
  engine is single-node and synchronous (deterministic + offline-testable).
- Execution state persists as a JSON aggregate (no per-timeline tables) — fine for this build;
  splitting into child tables is a later scale refinement behind the repository seam.

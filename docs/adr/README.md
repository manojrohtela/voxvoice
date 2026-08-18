# Architecture Decision Records (ADR)

ADRs are the second-highest authority in VoxVoice (below the Engineering
Constitution, above governance) — see [`.voxvoice/README.md`](../../.voxvoice/README.md)
authority hierarchy. Process: [`.voxvoice/DOCUMENTATION_RULES.md`](../../.voxvoice/DOCUMENTATION_RULES.md) §3.

## Kinds

- **Constitutional ADR** (`ADR-NNNN-slug.md`) — amends Constitution **Part A** (timeless
  architecture). Full template: Problem · Context · Alternatives Considered ·
  Selected Solution · Trade-offs · Risks · Future Impact. Owner approval required.
- **Profile ADR** (`P-ADR-NNNN-slug.md`) — amends Constitution **Part B** (Implementation
  Profile — today's vendors/frameworks). Lightweight: what row changes, why, migration note.
- **Epic 6 Agent Platform ADR** (`ADR-NNNN-slug.md`, same numbering sequence as
  Constitutional ADRs — no separate prefix) — records a load-bearing architectural
  decision within the Epic 6 Agent Platform (`docs/architecture/epic6/`) that does
  not itself amend Constitution Part A or B. Template: Status · Context · Decision ·
  Consequences · Alternatives Considered (per the M6.6.5 Documentation & ADR Closure
  milestone). Introduced starting at ADR-0008; the two existing kinds above predate
  Epic 6 and did not anticipate capability-internal architecture decisions of this
  kind, which is why this is recorded as a third kind rather than forced into either.

## Rules
- Numbers are sequential and never reused. Status: `Proposed → Accepted → Superseded by ADR-XXXX`.
- Accepted ADRs are **immutable**; to change course, write a superseding ADR (never edit).
- Every ADR is linked from [`docs/INDEX.md`](../INDEX.md) and cross-referenced from the docs it affects.

## Register

| ADR | Title | Amends | Status |
|---|---|---|---|
| [ADR-0001](ADR-0001-first-party-runtime.md) | First-party voice runtime; retire whole-engine adapter axis | Part A (D1) | Accepted 2026-07-16 |
| [ADR-0002](ADR-0002-substrate-behind-contracts.md) | Pipeline substrate confined behind owned contracts | Part A (D2) | Accepted 2026-07-16 |
| [ADR-0003](ADR-0003-two-plane-architecture.md) | Two-plane architecture (Control / Runtime) | Part A (D3) | Accepted 2026-07-16 |
| [ADR-0004](ADR-0004-event-driven-core.md) | Event-driven core with one typed envelope | Part A (D4) | Accepted 2026-07-16 |
| [ADR-0005](ADR-0005-streaming-first-turn-intelligence.md) | Streaming-first ingestion + Turn Intelligence layer | Part A (D5) | Accepted 2026-07-16 |
| [ADR-0006](ADR-0006-single-system-of-record.md) | Single System of Record; brokers only when needed | Part A (D6) | Accepted 2026-07-16 |
| [ADR-0007](ADR-0007-keep-control-plane-delete-adapters.md) | Keep control-plane assets; delete engine-adapter axis | Part A (D7) | Accepted 2026-07-16 |
| [P-ADR-0001](P-ADR-0001-implementation-profile-v1.md) | Adopt Implementation Profile v1 | Part B | Accepted 2026-07-16 |
| [ADR-0008](ADR-0008-skill-manifest-not-code.md) | Skills are a versioned manifest, not code | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0009](ADR-0009-workflow-durable-repository-exception.md) | Workflow execution state is the platform's one durable-repository exception | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0010](ADR-0010-hook-framework-cross-cutting-library.md) | The Hook Framework is a cross-cutting library, not a `core.ports` runtime | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| ADR-0011 | MCP/A2A are bridge-pattern adapters, never a `ToolPort`/`AgentPort` redesign | Epic 6 Agent Platform Architecture | Reserved — not yet ratified (MCP/A2A not yet designed) |
| [ADR-0012](ADR-0012-tool-contract-manifest-plus-adapter.md) | Tool contract is manifest + registered adapter, never manifest-supplied code | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0013](ADR-0013-dual-fsm-goal-scoped-for-agent.md) | Dual-FSM closed-runtime pattern; Agent's second FSM attaches to the Goal, never the identity | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0014](ADR-0014-structure-intelligence-correctness-split.md) | Structure, intelligence, and correctness are distinct ownership categories | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0015](ADR-0015-agent-workflow-port-seam.md) | Agent Runtime delegates via its own `AgentWorkflowPort` seam, not literal `WorkflowPort` | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0016](ADR-0016-loop-engine-no-registrable-identity.md) | Loop Engine has no registrable identity | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0017](ADR-0017-execution-context-duplication-lineage.md) | Execution-context shapes extend by structural duplication within one delegation lineage; Loop Engine is deliberately outside it | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0018](ADR-0018-product-runtime-declarative-composition-layer.md) | Product Runtime is a declarative composition layer above the frozen Execution Platform, in a new `platform/product/` package | Epic 7 Product Platform (new) | Accepted 2026-07-26 |
| [ADR-0019](ADR-0019-binding-resolution-against-registry-snapshot.md) | Binding resolution resolves against an injected immutable RegistrySnapshot, never live Execution Platform runtimes | Epic 7 Product Platform (refines ADR-0018) | Accepted 2026-07-26 — **terminology superseded by ADR-0020**: the type ADR-0019 calls `RegistrySnapshot`/`RegistryEntry` is implemented as `BindingSnapshot`/`BindingSnapshotEntry` (`product.compiler`); ADR-0019's substance is unchanged |
| [ADR-0020](ADR-0020-registry-nouns-reserved-for-product-registry.md) | "Registry" nouns are reserved for the Product Registry of packages; the compiler's binding catalog is a `BindingSnapshot` | Epic 7 Product Platform (refines ADR-0019 terminology) | Accepted 2026-07-27 |
| [ADR-0021](ADR-0021-studio-consumer-and-management-surface.md) | VoxVoice Studio is a Consumer + Management Surface; it owns no runtime state (`Studio → Platform`, one-way) | Epic 8 Customer Platform (new) | Accepted 2026-07-27 |
| [ADR-0022](ADR-0022-deployment-actuator-runtime-actuation.md) | The Deployment Actuator is the sole owner of deployment execution; Product Runtime stays declarative (`actuator → product`, read-only, one-way) | Epic 8 Customer Platform (new) | Accepted 2026-07-27 |
| ADR-0024 | Coach (AI Product Manager) autonomy runs through the same request/approval/audit path, never a privileged one | Epic 8 Customer Platform | **Reserved — not yet ratified** (recommended by ADR-0021; Coach autonomy not yet designed) |
| [ADR-0023](ADR-0023-billing-sole-owner-of-revenue.md) | The Billing Platform is the sole owner of revenue; it owns nothing else and meters the authoritative `metric.*` stream (`Billing → Platform`, read-only, one-way) | Epic 9 Billing & Subscription Platform (new) | Accepted 2026-08-01 |
| ADR-0025…ADR-0030 | Recommended Epic 9 architecture batch — entitlement mechanics · money/ledger discipline · payment & tax rented edges · critical-path prohibition · commerce/licensing reuse · billing-account hierarchy | Epic 9 Billing & Subscription Platform | **Reserved — recommended, not yet ratified** ([Blueprint](../architecture/epic9/billing-platform-blueprint.md) §26) |
| [ADR-0031](ADR-0031-billing-plane-repository-placement.md) | The Billing Platform lives permanently in `platform/billing/`, a self-contained top-level plane importing `core` only; surfaced additively through the Epic 8 layers; never wired into the runtime composition root | Epic 9 repository topology (structural placement) | Accepted 2026-08-01 |
| [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) | Event durability and delivery semantics: **at-least-once**, append-only, one event identity and one sequence authority, transactional outbox, bounded retry ladder, distinct DLQ and quarantine, explicit retention — all behind the **unchanged** `core.events.EventBus` contract with no producer change (DL-0017's deferred work) | Epic 10 Durable Event & Telemetry Spine (new) | Accepted 2026-08-18 |
| [ADR-0033](ADR-0033-metric-stream-seam.md) | The metric-stream seam is **pull-shaped**: a platform-owned buffer subscribes to the `EventBus` and Billing pulls through the frozen `MetricStreamPort`, never subscribing itself; `metric.*` is extended additively under AR-32; runtime `metric.*` is owned by `platform/observability/` while **`metric.billing.*` stays reserved to Billing** | Epic 10 (implements against ADR-0023 + ADR-0031; amends neither) | Accepted 2026-08-18 |
| [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md) | Durable storage seam for `core`-only edge packages: a scoped `platform/events/persistence/**` sub-package may import `sqlmodel`/`sqlalchemy` and owns its own `Database` (the ADR-0031 IR1a precedent generalized), enforced by per-layer boundary assertions; migrations continue the single global chain at **0034** with the **`evt_*`** prefix | Epic 10 structural/dependency-inversion seam | Accepted 2026-08-18 |

> **Terminology note (ADR-0019 → ADR-0020).** ADR-0019 was written using the name
> `RegistrySnapshot` (and `RegistryEntry`) for the compiler's injected, immutable
> binding-resolution catalog. ADR-0020 reserved the "Registry" nouns for the Product
> Registry of packages and renamed that catalog to **`BindingSnapshot`** /
> **`BindingSnapshotEntry`** (`product/compiler/snapshot.py`). ADR-0019 remains
> Accepted and immutable; only the *name* of the type it describes changed — read
> `RegistrySnapshot` in ADR-0019 as `BindingSnapshot`. The resolve-against-an-injected-
> immutable-snapshot decision itself is unchanged.

> **Epic 7 freeze (M7.0F).** ADR-0018, ADR-0019, and ADR-0020 are ratified and
> implemented under **Product Runtime v1.0** (🔒 frozen — see
> [`docs/releases/epic7-product-runtime-v1.0.md`](../releases/epic7-product-runtime-v1.0.md)).
> They are binding until superseded; changing an Epic 7 architectural invariant they
> encode requires a new superseding ADR.

> **Epic 8 architecture freeze (M8.20).** ADR-0021 and ADR-0022 are ratified and
> implemented under **Customer Platform v1.0** (🔒 architecture frozen — see
> [`docs/releases/epic8-customer-platform-v1.0.md`](../releases/epic8-customer-platform-v1.0.md)).
> They are binding until superseded; changing an Epic 8 architectural invariant they
> encode (Studio-owns-no-runtime-state; Deployment Actuator = sole owner of deployment
> execution) requires a new superseding ADR + major version + new baseline. This is a
> governance freeze of the architecture only — the Epic 8 implementation remains
> uncommitted in the working tree (recorded in the freeze manifest §22); no commit,
> tag, or release is claimed.

> **Epic 9 opening (M9.0B).** ADR-0023 ratifies the Billing Platform's ownership boundary
> **before** any Epic 9 implementation: **Billing owns revenue and nothing else** — it
> consumes `metric.*` read-only (it never measures), never blocks Runtime or Deployment,
> never performs a permission check, and publishes immutable entitlement snapshots the
> owning platform enforces locally. `Billing → Platform` is one-way; no frozen Epic may
> depend on Billing. **ADR-0024 stays reserved for the Coach-autonomy decision** recommended
> by ADR-0021; the remaining Epic 9 decisions are recommended as **ADR-0025–ADR-0030**
> ([Epic 9 Blueprint](../architecture/epic9/billing-platform-blueprint.md) §26) and are
> ratified separately. Epics 5/6/7/8 remain frozen and byte-for-byte unchanged.

> **Epic 9 repository topology (M9.1A).** ADR-0031 is the **structural placement** record
> CODING_STANDARDS §2 requires for a new top-level directory (the ADR-0018 / ADR-0022
> precedent). It fixes *where* the plane ADR-0023 ratified physically lives —
> `platform/billing/`, importing `platform/core/` + stdlib only, with exactly one capped
> read-only exception (`billing/refs.py` → `product`, mirroring `actuator/planning.py`) —
> plus its dependency direction, composition root, import rules, public surface, and
> extension points. It took **ADR-0031** rather than a reserved number: **ADR-0024** stays
> reserved for Coach and **ADR-0025–ADR-0030** stay reserved for the recommended Epic 9
> architecture batch. After it, Epic 9 repository placement is frozen: no future placement
> ADR is needed and no package movement is expected.

> **Epic 10 opening (M10.1).** ADR-0032, ADR-0033 and ADR-0034 are ratified **before** any Epic 10
> implementation (DOCUMENTATION_RULES §3.7). Together they fix the **Durable Event & Telemetry
> Spine** beneath the already-built runtime: ADR-0032 the delivery *semantics*, ADR-0034 the storage
> *structure*, ADR-0033 the `metric.*` producer side and namespace partition. They discharge the
> durability work **DL-0017** deferred at M4.1 — *"durable outbox + retry/DLQ + async/non-blocking
> delivery … implement the same `EventBus` contract — no producer change"* — and they take **only**
> that: a distributed broker, external transports and WS/SSE gateway expansion remain deferred with
> DL-0017; real providers (Epic 11) and the SDK / read API / mid-call control (Epic 12) are out of
> scope. Numbering continues at **0032** because **ADR-0011** (MCP/A2A), **ADR-0024** (Coach) and
> **ADR-0025–ADR-0030** (the recommended Epic 9 batch) all remain **Reserved — not yet ratified**.
> **ADR-0023 and ADR-0031 are referenced, not amended**: Epic 10 implements against the frozen
> Billing contract and modifies no file under `platform/billing/**`. Epics 5–9 remain frozen; the
> `v4.0.0` baseline, both Epic 9 digests, migration head `0033`, the 27 controls, advisories NS-5
> and ST-3, and all nine declared limitations are preserved as historical state.

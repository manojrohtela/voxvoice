# Changelog

All notable releases of the VoxVoice platform. Versioning follows SemVer at the
platform-baseline level (see `docs/releases/`).

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

# Product Deployment Planning Layer (Epic 7, M7.0C)

**Status:** 🟢 Implemented (M7.0C) · **Package:** `platform/product/deployment/` · **Authority:** below the Engineering Constitution, [ADR-0018](../../adr/ADR-0018-product-runtime-declarative-composition-layer.md), [ADR-0019](../../adr/ADR-0019-binding-resolution-against-registry-snapshot.md), [ADR-0020](../../adr/ADR-0020-registry-nouns-reserved-for-product-registry.md). **Builds on:** [Product Package, Registry & Versioning (M7.0B-3)](product-package-registry-versioning.md). Component spec per §A11.3.

> Epic 6 (Execution Platform v1.0) is **frozen**. This layer composes above it and
> changes nothing in it. Deployment **execution**, infrastructure adapters,
> Kubernetes, Terraform, GitOps, and enterprise features are **explicit non-goals**.

## 1. Responsibility (one, per AR-50)

Transform an immutable `ProductPackage` into a validated, immutable **`DeploymentPlan`**.
The layer owns **deployment intent only** — it produces immutable descriptors and
validation reports. It **never performs a deployment, never calls infrastructure, and
never starts a runtime session** (ADR-0018 applied — deployment actuation is
execution, which the Product Runtime never owns).

## 2. No new ADR (ownership boundary is ADR-0018 applied)

"Deployment planning owns intent, never actuation" is not a new invariant — it is
ADR-0018's "Product Runtime never owns execution" applied to deployment. No ownership
direction, dependency rule, or compiler invariant changes, so no new ADR is written
(the milestone requires one only if a new invariant is introduced). `product/deployment/`
is a subpackage of the already-authorized `product/` top-level package (ADR-0018), not
a new top-level directory.

Two reuse decisions (glossary — one concept, one name): `EnvironmentProfile` and
`DeploymentStrategy` are **reused** from `product.domain` (the manifest's
named-environment and rollout-strategy concepts); `DeploymentStrategy` gained
`IMMEDIATE` and `MANUAL_APPROVAL` **additively** for M7.0C rollout policies.

## 3. Identity chain (extended by one link)

```
Manifest ID → Definition ID → Compilation ID → Package ID → Deployment ID
  mf_…           pd_…            cp_…             pk_…          dp_…
```

`deployment_id` is derived from `package_id` + the canonical hash of the
`DeploymentDescriptor`, so it changes whenever deployment configuration changes and is
reproducible (the descriptor is pure config — no timestamps).

## 4. Components

| Component | File | Responsibility |
|---|---|---|
| **DeploymentDescriptor** | `descriptor.py` | Immutable declarative intent: package identity, deployment strategy, target environment (reused `EnvironmentProfile`), release target, resource profile, feature flags, environment + tenant overrides, rollout policy, rollback policy. No handles/clients/execution objects. |
| **DeploymentPlan** | `plan.py` | Immutable, content-addressed: package reference + descriptor + validation report + deployment identity. Nothing executable. |
| **DeploymentPlanner** | `factory.py` | The one builder: validates the descriptor against the package (optionally consulting a read-only registry for rollback), derives identity, assembles the plan + report. Never deploys. |
| **DeploymentProfile / DeploymentTarget / ReleaseTarget** | `descriptor.py` | Profile enum (Development…Hybrid), tenancy enum (Single/Multi-Tenant), concrete release destination. Declarative. |
| **DeploymentPolicy / RollbackPolicy** | `descriptor.py` | Rollout policy parameters (canary %, batch size, approval); rollback intent (previous / previous-compatible / explicit / manual). Describe intent only. |
| **DeploymentValidator** | `validation.py` | Static validation: integrity, target compatibility, profile/target, environment + tenant overrides, resource limits, feature-flag consistency, rollout-policy consistency, rollback admissibility. No runtime/infra validation. |
| **DeploymentDiagnostics** | `diagnostics.py` | Categorized view over the shared deterministic finding set (policy violations, compatibility issues, missing targets, invalid rollback targets, invalid environment overrides). |
| **DeploymentReport** + sub-reports | `report.py` | Machine-readable summary + compatibility + rollback resolution + policy report + diagnostics + deployment id. |
| **DeploymentIdentity** | `identity.py` | The five-link identity chain + `deployment_id_for`. |
| **DeploymentSerializer** | `serializer.py` | Deterministic, byte-identical descriptor/report/plan dicts + descriptor hash. |
| **extensions.py** | `extensions.py` | Interface-only seams: `DeploymentScheduler`, `ApprovalWorkflow`, `DeploymentOrchestrator`, `GitOpsIntegration`, `KubernetesIntegration`, `TerraformIntegration` (§7). None actuate. |

## 5. Validation coverage

Package integrity (recompute-and-compare via M7.0B-3's `IntegrityValidator`); target
compatibility (`CompatibilityMatrix.evaluate` against the descriptor's target platform
versions); missing release/environment target; environment & tenant overrides that
illegally assert execution ownership (reuses the compiler's `FORBIDDEN_KEYS`); negative
resource limits; feature flags not declared by the product; single-tenant with multiple
tenant overrides; rollout-policy consistency with the strategy (canary %, rolling batch,
manual-approval flag, dead-config); rollback target admissibility (same-major rule;
verifiable only when a registry is supplied).

## 6. Determinism

Identical package + descriptor ⇒ identical `deployment_id` and byte-identical
serialized plan (`test_plan_serialization_is_byte_identical`,
`test_deployment_identity_is_deterministic`). Rollback resolution and all orderings
reuse `compiler.versions`.

## 7. Performance extension points (design only)

`DeploymentScheduler`, `ApprovalWorkflow`, `DeploymentOrchestrator`, `GitOpsIntegration`,
`KubernetesIntegration`, `TerraformIntegration` — declared as protocols consuming an
immutable `DeploymentPlan`; **none actuate**. No scheduling/approval/orchestration/
GitOps/Kubernetes/Terraform is implemented (explicit non-goals).

## 8. Dependencies & tests

`product` (incl. `product.deployment`) imports only `core` + its own package + stdlib;
no sibling runtime; freeze preserved (AST-test-enforced). `product.deployment` depends
on `product.registry`/`product.compiler`/`product.domain` (all intra-package, acyclic;
registry/compiler never import deployment). Tests:
`platform/tests/test_product_deployment.py` — 27 tests (valid/invalid plans, identity
chain + determinism, byte-identical serialization, compatibility, rollout-policy,
rollback modes, override/flag/resource consistency, integrity feed, immutability,
no-execution-surface). Repository-wide: 1549 tests pass; ruff clean.

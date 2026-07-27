# Product Runtime v1.0 — Epic 7 Freeze Manifest (M7.0F)

**Status:** 🔒 **Epic 7 FROZEN** (M7.0F) · **Date:** 2026-07-27 · **Depends on:** Execution Platform v1.0 (Epic 6, frozen), Runtime Platform v1.0 (Epic 5, frozen).

> This is the official freeze of Epic 7. The Product Runtime's public architecture,
> ownership, dependency direction, and extension points are frozen as of this
> document. Future work **extends** this baseline; it does not modify it. This is a
> governance document — no commit, push, tag, or GitHub Release is performed here.

## 1. Product Runtime overview

The **Product Runtime** (`platform/product/`) is a declarative, deterministic
composition layer **above** the frozen Execution Platform (Epic 6). It lets an
organization *describe, compile, package, and plan the deployment of* a complete AI
product entirely as configuration. It **describes and binds; it never executes**
(ADR-0018). The end-to-end pipeline is:

```
ProductManifest → ProductDefinition → CompiledProduct → ProductPackage → DeploymentPlan
                                    ↘ composed read-only by ProductRuntime (façade) ↙
```

Delivered across bundles M7.0B-1 (Definition & Manifest Core), M7.0B-2 (Compiler &
Binding Resolution), M7.0B-3 (Package, Registry & Versioning), M7.0C (Deployment
Planning), and M7.0E (composition façade + certification closure). 46 modules; 145
capability tests; 1556 repository-wide tests green; ruff clean.

## 2. Frozen capabilities

| Area | Package | Frozen capabilities |
|---|---|---|
| **Definition** | `product/` | `ProductManifest`, `ProductMetadata`, reference placeholders (`AgentRef`/`WorkflowRef`/`SkillRef`/`ToolRef`/`ProviderRef`/`MCPServerRef`/`KnowledgeRef`), `SecretReference`, config layers (`EnvironmentProfile`/`TenantOverride`/`FeatureFlag`/`DeploymentMetadata`/`VersionMetadata`), `ProductDefinition`, `ProductFactory`, `ProductValidator`, `ProductDiagnostics`/`Diagnostic`/`ProductDiagnosticCode`/`Severity`, `ProductSerializer`, canonicalization + identities. |
| **Compilation** | `product/compiler/` | `ProductCompiler` (8-stage pipeline), `CompilationContext`, `CompiledProduct`, `CompilationResult`/`CompilationReport`, `BindingResolver`, `Binding`/`BindingKind` + kind constructors, `BindingSnapshot`/`BindingSnapshotEntry`, `DependencyGraph`/`DependencyAnalyzer`, `ConfigurationResolver`, `CompatibilityValidator`, `OwnershipValidator`. |
| **Packaging / Registry** | `product/registry/` | `ProductPackage`/`ProductPackageMetadata`/`PackageManifest`, `PackageIdentity`, `ProductPackager`, `PackageSerializer`, `PackageValidator`, `VersionManager`, `CompatibilityMatrix`, `ProductRegistry`, `RegistryEntry`/`RegistryIndex`/`RegistryQuery`/`RegistryReport`/`RegistrySnapshot`, `PackageStore`/`InMemoryPackageStore`, `ProvenanceRecord`, `IntegrityMetadata`/`IntegrityValidator`, `SignatureMetadata` abstraction. |
| **Composition** | `product/runtime.py` | `ProductRuntime` read-only composition façade. |
| **Deployment planning** | `product/deployment/` | `DeploymentDescriptor`, `DeploymentPlan`, `DeploymentPlanner`, `DeploymentProfile`/`DeploymentTarget`/`ReleaseTarget`/`DeploymentPolicy`/`RollbackPolicy`, `DeploymentValidator`, `DeploymentDiagnostics`, `DeploymentReport` (+ sub-reports), `DeploymentIdentity`, `DeploymentSerializer`. |

## 3. Frozen public interfaces

The public surface is the `__all__` export of `product/` and each subpackage
(`product.compiler`, `product.registry`, `product.deployment`). Frozen entry points:

- **Authoring:** `ProductFactory.from_manifest / from_mapping / parse / diagnose`.
- **Compilation:** `ProductCompiler.compile(definition, CompilationContext) → CompilationResult`.
- **Packaging:** `ProductPackager.package(definition, result, *, package_version, …) → ProductPackage`.
- **Registry (read-only + `register`):** `ProductRegistry.register / latest / exact / compatible / rollback_target / history / certification_history / provenance / get_package / index / snapshot / execute`.
- **Deployment planning:** `DeploymentPlanner.plan(package, descriptor, *, registry=None) → DeploymentPlan`.
- **Composition:** `ProductRuntime.of(package, deployment_plan=None) → ProductRuntime`.

These signatures follow Constitution A11.4 interface versioning; breaking changes
require the deprecation flow (§Extension Policy).

## 4. Ownership boundaries

**Product Runtime owns ONLY:** product description, compilation, packaging, and
deployment **planning**.

**It owns NONE of:** runtime execution, workflow execution, provider execution, MCP
execution, deployment **execution**/actuation, infrastructure, or orchestration —
all of which remain the frozen Epic 6 Execution Platform's or live outside Product
Runtime entirely. Mechanically enforced: `OwnershipValidator` rejects any manifest
config that asserts execution ownership; the registry exposes no deploy/execute verb;
the deployment layer's orchestration seam is a `Protocol` stub only.

## 5. Dependency graph (frozen direction)

```
Product  ──▶  Core        (product imports only core.errors + stdlib)
```

- `product` imports exactly one non-stdlib top-level package: **`core`**.
- **No** Epic 6/5 capability imports `product` (verified across all 25 packages).
- Intra-`product` edges are downward and acyclic: `runtime → {registry, deployment, compiler}`, `deployment → {registry, compiler, domain}`, `registry → {compiler, domain}`, `compiler → domain`.
- AST-enforced in every product test suite (both directions). The Epic 6 freeze is preserved by construction.

## 6. Extension points (frozen seams — interfaces only)

- **Compiler:** `CompilationCache`, `IncrementalCompiler`.
- **Registry:** `PackageStore` (+ `InMemoryPackageStore`), `DistributedRegistry`, `OCIRegistry`, `PackageCache`, `ArtifactMirror`, `SignatureProvider` (+ `SignatureMetadata` data contract).
- **Deployment:** `DeploymentScheduler`, `ApprovalWorkflow`, `DeploymentOrchestrator`, `GitOpsIntegration`, `KubernetesIntegration`, `TerraformIntegration`.

All are declared `Protocol`s with no implementation; every one consumes already-built
immutable artifacts and returns intent/records — **none actuate**.

## 7. Non-goals (frozen)

Runtime execution; workflow/provider/MCP execution; deployment execution/actuation;
infrastructure adapters; Kubernetes/Terraform/GitOps implementations; release
pipeline; product marketplace; enterprise features; cryptographic signing
(abstraction only); a live composition root that builds a `BindingSnapshot` from Epic
6 registries or hands a `DeploymentPlan` to an actuator (future, out-of-Product-Runtime).

## 8. Determinism guarantees (frozen)

Given identical inputs, every artifact is byte-identical and every identity is
reproducible:

- **Side-effect-free:** zero `open`/`socket`/`http*`/`time`/`random`/`asyncio`/`subprocess` in `product/`.
- **Canonical serialization:** one canonical-form + `canonical_hash` reused across all layers; authoring order never affects output; timestamps/creator are excluded from identity.
- **Compilation** is a pure function of `(ProductDefinition, BindingSnapshot, CompilationContext)`; **deployment planning** is a pure function of `(ProductPackage, DeploymentDescriptor, [Registry])`.

## 9. Identity chain (frozen)

```
Manifest ID (mf_) → Definition ID (pd_) → Compilation ID (cp_) → Package ID (pk_) → Deployment ID (dp_)
```

Auxiliary content ids: `cx_` (compilation report), `bd_` (binding), `rs_` (binding
snapshot), `rgs_` (registry snapshot). Each is a content hash; each changes when its
inputs change and is reproducible from them.

## 10. Testing summary

145 capability tests across 5 suites (`test_product_manifest` 43, `test_product_compiler`
34, `test_product_registry` 34, `test_product_deployment` 27, `test_product_runtime` 7);
**1556/1556 repository-wide**; ruff clean. Coverage spans architecture/ownership,
dependency boundaries (AST, both directions), determinism + byte-identical
serialization, immutability (`FrozenInstanceError`), integrity tamper-detection, and
24 explicit regression anchors.

## 11. Certification summary

Independent architecture certification (M7.0D): **CERTIFIED WITH CONDITIONS** — no
architecture violations; three closure conditions. M7.0E closed all three: **C1**
`ProductRuntime` façade implemented; **C2** `FeatureFlag`/`TenantOverride` tuple/mapping
documented as the permanent representation (no dead-code wrappers); **C3**
`RegistrySnapshot → BindingSnapshot` terminology reconciled in the register/INDEX
without modifying the immutable ADR-0019. See
[epic7-freeze-preparation.md](epic7-freeze-preparation.md).

## 12. Repository readiness

Working tree contains Epic 7 only; no temp/debug/orphan files (`__pycache__` git-ignored);
no TODO/FIXME/placeholder/`NotImplementedError`; 74/74 `@dataclass` frozen; docs
(INDEX, ADR Register, CHANGELOG, 4 architecture specs, freeze-prep, this manifest)
synchronized and cross-linked. Ratified ADRs: ADR-0018, ADR-0019, ADR-0020.

## 13. Version recommendation

- **Product Runtime v1.0** — the frozen Epic 7 baseline.
- **Platform baseline → v2.0.0.** Epic 7 adds a new public surface (`product.*`) atop
  v1.0.0 (Execution Platform) with **no breaking change** to Epic 5/6 interfaces. The
  MAJOR bump to `v2.0.0` reflects the platform gaining a new top-level capability line
  (Product Platform), recorded as a product-line decision at tag time; per strict
  SemVer of the existing surface alone this would be a MINOR bump — the MAJOR is the
  baseline-line convention (cf. Epic 6 → v1.0.0).
- **SemVer policy:** MAJOR = breaking change to any frozen public interface (per A11.4
  deprecation flow); MINOR = new products/manifests/bindings/registries/targets/
  strategies/serializers/validators/providers (additive); PATCH = fixes.
- **Compatibility policy:** frozen public interfaces evolve additively or via the A11.4
  deprecation window; the identity chain, canonical serialization, and `product → core`
  dependency direction are compatibility-critical and change only by MAJOR + ADR.

## 14. Extension policy

**Allowed (additive, no freeze impact):** new products, manifests, bindings, package
formats, registry implementations (`PackageStore`/`DistributedRegistry`/`OCIRegistry`),
deployment targets, deployment strategies, serializers, validators, compatibility
providers, integrity providers, signing providers (`SignatureProvider`).

**Forbidden (require a superseding ADR against the frozen architecture):** changing
ownership; breaking a public interface; reversing the dependency direction;
introducing execution; introducing infrastructure ownership; adding mutable runtime
state; adding runtime side-effects.

## 15. Architectural invariants (frozen)

1. **Ownership:** Product Runtime owns description/compilation/packaging/deployment-planning only.
2. **Dependency direction:** `product → core` only; nothing imports `product` back; acyclic.
3. **Immutability:** every artifact is a frozen value object (74/74).
4. **Determinism & reproducibility:** identical inputs → byte-identical outputs and identities.
5. **Canonical serialization:** one canonical form; authoring-order-independent; timestamp-free identities.
6. **Identity chain:** Manifest → Definition → Compilation → Package → Deployment.
7. **Side-effect-free compilation** and **side-effect-free deployment planning.**
8. **Registry is metadata-only, read-only** (single mutation: `register`); never deploys/executes.
9. **Execution separation** and **infrastructure separation:** neither is owned, imported, or actuated.

## 16. Cross-references

- Epic 5 — [Runtime Platform v1.0](epic5-runtime-platform-v1.0.md) (frozen).
- Epic 6 — [Execution Platform v1.0](epic6-execution-platform-v1.0.md) (frozen).
- Epic 7 — **Product Runtime v1.0 (this document)** — the declarative Product Platform atop Epic 6.

---

**EPIC 7 IS OFFICIALLY FROZEN.**

# Epic 7 — Product Runtime: Freeze Preparation (M7.0E)

**Status:** 🟡 Freeze-ready (NOT frozen) · **Date:** 2026-07-27 · **Scope:** Product Runtime (Product *Planning* Platform) · **Depends on:** Execution Platform v1.0 (Epic 6, frozen), Runtime Platform v1.0 (Epic 5, frozen).

> This document prepares Epic 7 for freeze. It does **not** freeze anything, create a
> tag, or cut a release. It records the independent certification (M7.0D), the closure
> of its conditions (M7.0E), repository readiness, and the recommended commit plan.

## 1. What Epic 7 delivers

A **declarative, deterministic Product Runtime** layered above the frozen Execution
Platform (ADR-0018). It *describes, compiles, packages, and plans deployment*; it
never executes, deploys, or owns infrastructure. Pipeline:

```
ProductManifest → ProductDefinition → CompiledProduct → ProductPackage → DeploymentPlan
     (M7.0B-1)        (M7.0B-1)          (M7.0B-2)         (M7.0B-3)         (M7.0C)
                                    composed read-only by ProductRuntime (M7.0E/C1)
```

| Bundle | Package | Summary |
|---|---|---|
| M7.0B-1 | `product/` | Definition & Manifest Core — manifest, definition, factory, validator, diagnostics, canonicalization, identities. |
| M7.0B-2 | `product/compiler/` | Compiler & Binding Resolution — 8-stage deterministic pipeline, `BindingSnapshot`, dependency graph, configuration/compatibility/ownership validators. |
| M7.0B-3 | `product/registry/` | Package, Registry & Versioning — `ProductPackage`, `ProductRegistry`, versioning, compatibility matrix, integrity, provenance. |
| M7.0C | `product/deployment/` | Deployment Planning — `DeploymentDescriptor`/`DeploymentPlan`/`DeploymentValidator`; intent only, never actuation. |
| M7.0E/C1 | `product/runtime.py` | `ProductRuntime` composition façade — read-only composition of package + plan; no execution surface. |

## 2. Certification (M7.0D) outcome

The independent architecture review board returned **CERTIFIED WITH CONDITIONS**:
architecture free of violations (ownership, `product → core`-only dependency graph,
determinism, immutability, registry read-only, deployment non-actuating, docs
consistent, tests green), conditioned on three closure items. All three are now
closed by M7.0E (§3). No architectural finding required code refactoring.

## 3. Certification conditions closed (M7.0E)

| ID | Finding | Resolution |
|---|---|---|
| **C1** | `ProductRuntime` composition façade (M7.0A Runtime Composition) absent | **Implemented** — `product/runtime.py`: a frozen, immutable façade that *accepts* a `ProductPackage`, *exposes* the `CompiledProduct`, an optional `DeploymentPlan`, metadata, and diagnostics; guards the package↔plan composition boundary. It **never** executes, deploys, owns a runtime lifecycle/providers/workflows, or touches infrastructure — asserted by `test_facade_exposes_no_execution_or_deploy_surface`. 7 tests added. |
| **C2** | `FeatureFlagSet` / `TenantOverrideSet` aggregates absent | **Documented as permanent architecture** (definition-manifest-core doc §6a): feature flags and tenant overrides are the tuple/mapping form already delivered (`ProductManifest.feature_flags`/`tenants`; descriptor `feature_flags`/`tenant_overrides`). Wrapper types would be unused dead code (DoD-prohibited); the "set" is the collection itself. Naming decision only — zero behavioural change. |
| **C3** | ADR-0019 uses the pre-rename term `RegistrySnapshot` | **Reconciled without modifying ADR-0019** (immutable): the ADR Register (`docs/adr/README.md`) and `docs/INDEX.md` now carry an explicit `RegistrySnapshot → BindingSnapshot` note pointing to ADR-0020; the compiler architecture doc already records the rename lineage. |

## 4. Repository readiness

Evidence gathered read-only during M7.0E:

- **Working tree = Epic 7 only.** New: `platform/product/**` (45 `.py`), `platform/tests/test_product_{manifest,compiler,registry,deployment,runtime}.py`, `docs/adr/ADR-0018/0019/0020`, `docs/architecture/epic7/**`, this doc. Modified: `CHANGELOG.md`, `docs/INDEX.md`, `docs/adr/README.md`. No unrelated work.
- **No temporary files / debug / orphans.** `__pycache__/*.pyc` are the only non-`.py` files and are git-ignored (root + `platform/.gitignore`).
- **No TODO/FIXME/XXX/HACK/WIP; no `print`/`pdb`/`breakpoint`; no `NotImplementedError`; no placeholder bodies** (extension points are `Protocol` stubs by design).
- **Immutability:** 74/74 `@dataclass` are `frozen=True`.
- **No side-effects / nondeterminism:** zero `open`/`socket`/`http*`/`time`/`random`/`asyncio`/`subprocess` usages anywhere in `product/`.
- **Dependency boundary:** `product` imports only `core` (+ stdlib + itself); no Epic 5/6 package imports `product`. AST-enforced in all 5 product suites.
- **Tests:** 145 product tests pass; **1556/1556 repository-wide**; **ruff clean**.
- **Docs synchronized:** 3 ADRs (Accepted), 4 Epic 7 architecture specs, this freeze-prep doc, INDEX + ADR Register + CHANGELOG all cross-linked and consistent.

## 5. Recommended Git commit plan (recommendation only — NOT executed)

Atomic commits, in dependency order; each builds and passes fast checks. `docs(adr)`
before the `feat` it governs (ADR precedes code — AI_AGENT_RULES #3).

1. `docs(adr): ADR-0018 Product Runtime declarative composition layer`
2. `feat(product): M7.0B-1 Product Definition & Manifest Core`
3. `docs(adr): ADR-0019 snapshot-based binding resolution`
4. `feat(product): M7.0B-2 Product Compiler & Binding Resolution`
5. `docs(adr): ADR-0020 Registry-noun reservation (RegistrySnapshot → BindingSnapshot)`
6. `feat(product): M7.0B-3 Product Package, Registry & Versioning`
7. `feat(product): M7.0C Product Deployment Planning Layer`
8. `feat(product): M7.0E ProductRuntime composition façade (C1)`
9. `docs(product): Epic 7 architecture, certification closure & freeze preparation`

Notes:
- Commits 2/4/6/7/8 each carry their own tests + component doc; the shared
  `product/diagnostics.py`, `product/domain.py`, and `product/__init__.py` evolve
  additively across bundles — if strictly one-touch-per-file commits are desired,
  fold their per-bundle deltas into the corresponding `feat` commit.
- Commit 9 carries `docs/INDEX.md`, `docs/adr/README.md`, `CHANGELOG.md`,
  `docs/architecture/epic7/**`, and this document.
- Branch: a `feat/epic7-product-runtime` work branch off `main` is recommended; the
  current branch is `docs/m6.0-epic6-agent-platform-design` (see §7).

## 6. Freeze / release readiness

- **Epic 7 Freeze:** ready. On freeze, author an `epic7-product-runtime-v1.0.md`
  manifest (mirroring the Epic 5/6 freeze manifests) enumerating each capability,
  its owner, frozen interfaces, and extension points.
- **Future Release v2.0.0:** Epic 7 is the additive Product Platform atop v1.0.0
  (Execution Platform). It introduces new public surface (`product.*`) with **no
  breaking change** to Epic 5/6 interfaces — consistent with a MINOR bump, but the
  platform-baseline release is a product-line decision recorded at freeze time.
- **Not done here (by mandate):** no commit, no push, no tag, no GitHub Release, no
  freeze manifest.

## 7. Preconditions for the person performing the freeze

- Confirm the target branch/PR flow per GIT_WORKFLOW (trunk-based; solo-direct or PR).
- Run the full suite from `platform/` on a clean checkout (`pytest`, `ruff check`).
- Author the freeze manifest + roll this `Unreleased` CHANGELOG section into a version
  section at tag time (GIT_WORKFLOW §5).

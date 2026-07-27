# Product Compiler & Binding Resolution (Epic 7, M7.0B-2)

**Status:** 🟢 Implemented (M7.0B-2) · **Package:** `platform/product/compiler/` · **Authority:** below the Engineering Constitution, [ADR-0018](../../adr/ADR-0018-product-runtime-declarative-composition-layer.md), and [ADR-0019](../../adr/ADR-0019-binding-resolution-against-registry-snapshot.md). **Builds on:** [Product Definition & Manifest Core (M7.0B-1)](product-definition-manifest-core.md). Component spec per Constitution §A11.3.

> Epic 6 (Execution Platform v1.0) is **frozen**. The compiler composes above it and
> changes nothing in it. Packaging, product registry, deployment, and runtime
> execution are **explicit non-goals** of this bundle.

## 1. Responsibility (one, per AR-50)

Transform a validated `ProductDefinition` into an immutable `CompiledProduct` and a
machine-readable `CompilationReport`, deterministically and without side effects. The
compiler **resolves and validates**; it never executes, initializes, loads, or
communicates with anything.

## 2. The pipeline (8 independently-testable stages)

```
ProductDefinition + CompilationContext(BindingSnapshot, platform versions, env/tenant)
   │
   1  validate definition        (product.ProductValidator)
   2  resolve bindings           (BindingResolver → immutable Bindings, vs snapshot)
   3  resolve configuration      (ConfigurationResolver → Default→Deployment)
   4  analyze dependencies       (DependencyAnalyzer → DependencyGraph)
   5  validate ownership         (OwnershipValidator)
   6  validate compatibility     (CompatibilityValidator, static)
   7  produce CompiledProduct    (only when zero error findings)
   8  produce CompilationReport  (always; deterministic)
   ↓
CompilationResult(compiled?, report)
```

Every stage is a **pure function** of its inputs (no shared mutable state, no I/O),
so each is unit-tested alone and a future orchestrator may run the independent ones
in parallel (§7). All stages run even on failure so the report is complete; a
`CompiledProduct` is produced only when there are no error-severity findings.

## 3. Components

| Component | File | Responsibility |
|---|---|---|
| **ProductCompiler** | `compiler.py` | Orchestrates the 8 stages; aggregates diagnostics; produces `CompilationResult`. Stateless, deterministic. |
| **CompiledProduct** | `compiled.py` | Immutable artifact: bindings + resolved configuration + dependency graph + compatibility/compilation metadata + diagnostics + deterministic `content_id`/`content_hash`. **No runtime/execution/network state.** |
| **CompilationContext** | `context.py` | Immutable, deterministic inputs: `BindingSnapshot`, `compiler_version`, target `environment`/`tenant_id`, static platform versions, expected schema version. |
| **CompilationResult / CompilationReport** | `compiler.py`, `report.py` | Result wrapper (`compiled?` + `report`); machine-readable report with summary + per-stage sub-reports + diagnostics + `compilation_id`. |
| **BindingResolver** | `resolver.py` | Resolves manifest reference placeholders → immutable `Binding`s against the snapshot. Missing → `MISSING_BINDING`; collision → `DUPLICATE_BINDING`. No execution. |
| **Binding / BindingKind** | `bindings.py` | One immutable, data-only binding shape with a `kind` discriminator (the certified `WorkflowNode` precedent). `AgentBinding`/`WorkflowBinding`/`SkillBinding`/`ToolBinding`/`ProviderBinding`/`MCPBinding`/`KnowledgeBinding` are kind-specialized constructors. Deterministic `binding_id`. |
| **BindingSnapshot** | `snapshot.py` | The injected, immutable, content-addressed binding-resolution catalog (ADR-0019; renamed from `RegistrySnapshot` per ADR-0020). `resolve_version` = exact for pinned, highest for unpinned. |
| **DependencyGraph / DependencyAnalyzer** | `graph.py` | Builds the configuration dependency graph (agent→workflow/skill edges); detects missing references, cycles, invalid ordering, unreachable nodes; deterministic Kahn topological order. |
| **ConfigurationResolver** | `configuration.py` | Deterministic precedence `Default→Environment→Tenant→Deployment`; deep-merge; override tracing; structural conflict reporting. `runtime`/`request` remain **contracts** — never applied at compile time. |
| **CompatibilityValidator** | `compatibility.py` | Static checks: interface/schema versions, product SemVer, `requires_execution`/`requires_runtime` vs context platform versions, constraint well-formedness. No live probing. |
| **OwnershipValidator** | `ownership.py` | Rejects any config asserting execution ownership (`fsm`, `execute`, `runtime_lifecycle`, `mcp_protocol`, callbacks, …). Enforces the ADR-0018 boundary mechanically. |
| **versions.py** | `versions.py` | SemVer ordering + `is_compatible` (same-major floor) + highest-version selection. |
| **serializer.py** | `serializer.py` | Machine-readable dict rendering + `canonical_hash` for the compilation identity. |
| **extensions.py** | `extensions.py` | Interface-only seams: `CompilationCache`, `IncrementalCompiler` (§7). No implementation. |

## 4. Determinism (a feature under test)

Given identical `ProductDefinition` + `BindingSnapshot` + `compiler_version` (+ the
rest of the context), the `CompiledProduct` and `CompilationReport` are
**byte-identical** — proven by `test_compilation_is_byte_identical_for_identical_inputs`
and `test_failed_compilation_report_is_deterministic`. Guarantees: bindings sorted by
identity; snapshot merges/sorts entries; Kahn's algorithm takes ready nodes in sorted
order; configuration flattens to sorted dotted paths; identities hash over the
canonical form. `snapshot_id` is recorded in the compilation identity so an artifact
is auditably tied to the catalog it was compiled against
(`test_different_snapshot_changes_compilation_identity`).

## 5. Dependencies (acyclic, downward-only)

`product` (incl. `product.compiler`) imports only `core` + its own package + stdlib.
Binding resolution is against the injected `BindingSnapshot`, **never** a live Epic
6 registry (ADR-0019) — so no sibling runtime is imported and the freeze is preserved
by construction. Enforced by `test_compiler_imports_no_sibling_runtime` and the
M7.0B-1 boundary tests (both directions).

## 6. Failure modes

All non-programmer failures are **reported, never raised**: the compiler returns a
`CompilationResult` with `compiled=None` and a complete, deterministic report when any
stage yields an error finding (missing binding, duplicate binding, missing reference,
cycle, configuration conflict, incompatibility, ownership violation, invalid
constraint, schema mismatch). Warnings (unreachable node, environment/tenant not
found) never block.

## 7. Performance extension points (design only)

- **Dependency caching** — `CompilationCache` protocol keyed by the deterministic
  compilation identity; a hit returns a prior `CompiledProduct` for identical inputs.
- **Incremental compilation** — `IncrementalCompiler` protocol: recompile only what
  changed from a prior artifact.
- **Parallel validation** — needs no interface: stages are already pure functions of
  their inputs, so a future orchestrator can fan the independent stages out safely.

No optimization engine is implemented in this bundle (explicit non-goal).

## 8. Tests

`platform/tests/test_product_compiler.py` — 34 tests across all 8 stages: success,
missing/duplicate bindings, dependency analysis (missing refs, cycles, unreachable,
topological order), configuration precedence/tracing/conflicts/contract-layers,
compatibility (interface/schema/platform/constraints), ownership violations,
immutability, no-execution-surface, machine-readable reports, and four determinism
regressions. Repository-wide: 1488 tests pass; ruff clean.

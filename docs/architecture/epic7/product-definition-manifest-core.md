# Product Definition & Manifest Core (Epic 7, M7.0B-1)

**Status:** 🟢 Implemented (M7.0B-1) · **Package:** `platform/product/` · **Authority:** below the Engineering Constitution and [ADR-0018](../../adr/ADR-0018-product-runtime-declarative-composition-layer.md); above sprint plans. **Governs:** the immutable authoring foundation of the Product Runtime (Epic 7). Component spec per Constitution §A11.3.

> Epic 6 (Execution Platform v1.0) is **frozen**. This bundle composes above it and
> changes nothing in it. It is **definition only** — nothing compiles, resolves,
> packages, deploys, or registers here.

## 1. Responsibility (one, per AR-50)

Own the **declarative authoring foundation** of a product: load a Product Manifest,
parse it, validate it structurally, canonicalize it deterministically, represent it
as an immutable `ProductDefinition`, and report findings through
`ProductDiagnostics`. Nothing else.

The Product Runtime *describes and binds*; the frozen Execution Platform *executes*.
This bundle delivers only the "describe" primitives — the manifest and its validated,
canonical, content-addressed definition.

## 2. Ownership boundary

| Owned by this bundle | Explicitly NOT owned (later bundles / Epic 6) |
|---|---|
| Product metadata + manifest shape | Compilation → runtime artifacts (M7.0B-2+) |
| Immutable domain models | Reference **resolution** / bindings |
| Manifest parsing (author dict → manifest) | Packaging, deployment actuation, registry |
| Structural + integrity validation | Dependency / compatibility resolution |
| Canonical ordering + deterministic hashing | Configuration precedence resolution |
| Content identity (`content_id`, `content_hash`) | Workflow/tool/provider/MCP execution (Epic 6) |
| Configuration diagnostics (finding set) | Reasoning, any FSM, runtime lifecycle (Epic 6) |

## 3. Components

| Component | File | Responsibility |
|---|---|---|
| **ProductManifest** | `domain.py` | The declarative, immutable, versioned contract — data, never code. Identity `(namespace, metadata.id, metadata.version)`; `interface_version` = manifest schema shape. Holds metadata + reference placeholders (agents/workflows/skills/tools/providers/mcp_servers/knowledge) + configuration layers (environments/tenants/feature_flags) + secrets + deployment/version metadata. |
| **ProductDefinition** | `domain.py` | The validated, canonicalized product: the immutable manifest + its deterministic `content_id`/`content_hash`. A distinct object from the manifest; holds **no runtime or execution state**. |
| **ProductMetadata & reference placeholders** | `domain.py` | Frozen, slotted value objects. References (`AgentRef`/`WorkflowRef`/`SkillRef`/`ToolRef`/`ProviderRef`/`MCPServerRef`/`KnowledgeRef`) and `SecretReference` are **inert placeholders** — declared data, never resolved here. A `SecretReference` is a pointer (`provider` + `path`), never a value. |
| **ProductFactory** | `factory.py` | The one construction entry point: `from_mapping` (load+parse) / `from_manifest` (validate+canonicalize+construct) / `parse` / `diagnose`. Fail-fast: raises `ProductValidationError` on any error-severity finding; warnings never block. |
| **ProductValidator** | `validation.py` | Stateless structural validation → `ProductDiagnostics`. Checks: schema/`interface_version`, required fields, metadata, SemVer format (product + pinned refs), duplicate ids (per `(namespace,id,version)`; environment/tenant/flag/secret keys), reference well-formedness, secret integrity (no inlined value). Never resolves dependencies or compatibility. |
| **ProductDiagnostics** | `diagnostics.py` | An immutable, deterministically-ordered finding set. Each `Diagnostic` carries `path`, `code` (`ProductDiagnosticCode`), `severity`, `message`, `remediation`. Ordering is `(path, code, severity)` — message wording never reorders a report. |
| **Canonicalization** | `canonical.py` | The single definition of order: recursively sort mapping keys and sort lists by element canonical JSON → authoring-order-independent `canonical_json`. |
| **Identity** | `ids.py` | `content_hash` (16-hex), `manifest_hash` (full digest over canonical JSON), `definition_id_for` (`pd_` + 16-hex). |
| **Serializer** | `serializer.py` | Deterministic, round-trippable manifest ⇄ dict; no I/O. |
| **Errors** | `errors.py` | `ProductError(ConfigError)` → `ProductValidationError` (carries the diagnostics). |

## 4. Determinism (a feature under test)

Identical manifest **content** yields byte-identical `canonical_json` and therefore
an identical `content_hash`/`content_id`, **regardless of authoring order** — proven
by `test_canonical_form_is_authoring_order_independent`. This is what makes a
definition diffable and (in later bundles) certifiable and exactly rollback-able.

## 5. Dependencies (acyclic, downward-only)

`product → core` only in this bundle (it imports just `core.errors`). No sibling
runtime is imported; no Epic 5/6 package imports `product`. Both directions are
AST-test-enforced (`test_product_dependency_rules`,
`test_no_epic6_or_epic5_package_imports_product`) — so the Epic 6 freeze is
preserved by construction. Later Epic 7 bundles may additionally depend *on* the
Execution/Runtime Platforms to resolve references — never the reverse (ADR-0018).

## 6. Failure modes

- **Invalid manifest** → `ProductValidator` returns error findings; `ProductFactory`
  raises `ProductValidationError` (a typed `ConfigError`) carrying them. Fail fast,
  never construct a degraded definition.
- **Unsupported `interface_version`** → error finding; construction refused.
- **Inlined secret value** → `SECRET_VALUE_PRESENT` error; construction refused.

## 6a. Feature flags & tenant overrides — representation (M7.0D/C2)

The M7.0A design named `FeatureFlagSet` and `TenantOverrideSet` aggregates. The
**permanent, ratified representation** is the tuple/mapping form already delivered,
not a wrapper type:

- Feature flags: `ProductManifest.feature_flags: tuple[FeatureFlag, ...]` at author
  time; resolved `feature_flags: Mapping[str, bool]` on the `DeploymentDescriptor`.
- Tenant overrides: `ProductManifest.tenants: tuple[TenantOverride, ...]` at author
  time; `tenant_overrides: Mapping[str, Mapping[str, Any]]` on the descriptor.

No `FeatureFlagSet`/`TenantOverrideSet` wrapper types are introduced: they would carry
no behaviour the tuple/mapping form lacks and would be unused aggregates (dead code,
prohibited by the Definition of Done). This is a naming decision only — zero
behavioural difference from M7.0A intent; the "set" concept is the collection itself.

## 7. Non-goals (deferred to later bundles)

Compilation, reference/binding resolution, configuration precedence resolution,
packaging, deployment descriptors/actuation, versioning compatibility checks, the
product registry, and any runtime/execution behaviour. These are M7.0B-2 / M7.0C
per the M7.0A design.

## 8. Tests

`platform/tests/test_product_manifest.py` — 43 tests: immutability, round-trip,
canonical ordering, deterministic hashing/identity, factory fail-fast, all
validation rules, deterministic diagnostics, reference placeholders staying inert,
and the two dependency-boundary AST tests. Regression anchors marked `# regression:`.

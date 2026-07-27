# Product Package, Registry & Versioning (Epic 7, M7.0B-3)

**Status:** 🟢 Implemented (M7.0B-3) · **Package:** `platform/product/registry/` · **Authority:** below the Engineering Constitution, [ADR-0018](../../adr/ADR-0018-product-runtime-declarative-composition-layer.md), [ADR-0019](../../adr/ADR-0019-binding-resolution-against-registry-snapshot.md), [ADR-0020](../../adr/ADR-0020-registry-nouns-reserved-for-product-registry.md). **Builds on:** [Product Compiler & Binding Resolution (M7.0B-2)](product-compiler-binding-resolution.md). Component spec per §A11.3.

> Epic 6 (Execution Platform v1.0) is **frozen**. This layer composes above it and
> changes nothing in it. Deployment, runtime execution, release pipeline,
> marketplace, enterprise features, and cryptographic signing are **explicit
> non-goals** of this bundle.

## 1. Responsibility (one, per AR-50)

Turn an immutable `CompiledProduct` into an immutable, content-addressed,
distributable **`ProductPackage`** — the *only* deployable artifact of Product
Runtime — and provide an immutable, read-only **`ProductRegistry`** over published
packages, with versioning, static compatibility, integrity verification, and
provenance. Packaging and the registry **execute nothing and deploy nothing**.

## 2. What a ProductPackage contains (immutable, inert)

`product_manifest` · `compiled_product` · `package_manifest` · `compilation_report`;
plus the dependency graph and compatibility metadata **as projections** off the
compiled product (never duplicated). **No runtime/deployment/network/execution
state.** The package is frozen and content-addressed (`content_id` = package id,
`content_hash` = package hash).

## 3. Identity chain (deterministic, reproducible)

```
Manifest ID  →  Definition ID  →  Compilation ID  →  Package ID
  mf_…            pd_…              cp_…               pk_…
```

Each is content-derived. `package_id` changes whenever package contents or the
package version change, and is **reproducible** from identical inputs. Author-supplied
`creator`/`created_at` are descriptive provenance and **do not** enter the identity —
so two packages of identical content built at different times share a package id
(`test_package_identity_is_reproducible_and_excludes_timestamp`).

## 4. Components

| Component | File | Responsibility |
|---|---|---|
| **ProductPackage / ProductPackageMetadata / PackageManifest** | `package.py` | The immutable artifact, its lightweight registry summary, and the package's own descriptor (identity + versions + integrity + provenance + signature + compatibility matrix). |
| **PackageIdentity** | `identity.py` | The deterministic identity chain + `manifest_id_for`/`package_id_for`. |
| **ProductPackager** | `factory.py` | Assembles a `ProductPackage` from a `ProductDefinition` + successful `CompilationResult`; derives identity/integrity/provenance/compatibility. Raises only when packaging a failed compilation. |
| **PackageSerializer** | `serializer.py` | Deterministic, byte-identical serialization; the content payload (product + package version, excluding identity/integrity/provenance) is what the package hash is computed over. |
| **PackageValidator / IntegrityValidator** | `validation.py` | Structural validation (identity-chain consistency, package version/schema, required metadata) + integrity **verification** (recompute hashes, compare). Verification only — no signing. |
| **ProductRegistry** | `registry.py` | Immutable, append-only, read-only catalog: latest / exact / compatible / rollback / history / certification / provenance lookups + a unified `RegistryQuery`/`execute` surface. Never deploys, never executes. |
| **RegistryEntry / RegistryIndex / RegistryQuery / RegistryReport** | `registry.py` | Immutable package metadata record; version-sorted index; declarative query; machine-readable result. |
| **RegistrySnapshot** | `registry.py` | Immutable, content-addressed (`rgs_…`) point-in-time view of the registry; validation/lookups operate against snapshots. |
| **PackageStore / InMemoryPackageStore** | `registry.py` | Storage abstraction (the registry indexes metadata; the store holds artifacts) + local default. |
| **VersionManager / CompatibilityMatrix** | `versioning.py` | Transition classification (upgrade/downgrade/same/invalid), same-major rollback rule, deterministic reports; static compatibility surface with `evaluate(...)`. |
| **ProvenanceRecord** | `provenance.py` | Immutable origin trail (creator, compiler version, binding-snapshot id, identity chain, timestamps, signature). |
| **IntegrityMetadata / SignatureMetadata** | `integrity.py` | The four content hashes (package/manifest/compilation/dependency); the pluggable, no-crypto signing abstraction (unsigned by default). |
| **extensions.py** | `extensions.py` | Interface-only seams: `DistributedRegistry`, `OCIRegistry`, `PackageCache`, `ArtifactMirror`, `SignatureProvider` (§7). |

## 5. Naming (ADR-0020)

The "Registry" nouns (`ProductRegistry`, `RegistryEntry`, `RegistrySnapshot`,
`RegistryQuery`, `RegistryIndex`, `RegistryReport`) are reserved for the Product
Registry of packages. The compiler's binding-resolution catalog was renamed
`RegistrySnapshot`→`BindingSnapshot` / `RegistryEntry`→`BindingSnapshotEntry` so no
term denotes two concepts.

## 6. Determinism & integrity

Identical packages serialize byte-for-byte identically and share a package id;
integrity verification recomputes the four hashes from package contents and detects
any tampering (`test_integrity_detects_tampered_integrity_metadata`). Registry
snapshots are content-addressed and stable. All version comparisons and orderings
reuse `compiler.versions` (deterministic).

## 7. Performance extension points (design only)

`DistributedRegistry`, `OCIRegistry` (a `PackageStore`), `PackageCache`,
`ArtifactMirror`, and `SignatureProvider` are declared as protocols only — the local
`InMemoryPackageStore` is the sole concrete store; no distributed/OCI/mirror/caching
or crypto is implemented (explicit non-goals).

## 8. Dependencies & tests

`product` (incl. `product.registry`) imports only `core` + its own package + stdlib;
no sibling runtime; the freeze is preserved (AST-test-enforced,
`test_registry_imports_no_sibling_runtime`). Tests:
`platform/tests/test_product_registry.py` — 34 tests (creation, immutability, identity
chain, reproducible/byte-identical serialization, integrity + tamper detection,
validation, version transitions + rollback rules, compatibility matrix, all registry
queries, snapshots). Repository-wide: 1522 tests pass; ruff clean.

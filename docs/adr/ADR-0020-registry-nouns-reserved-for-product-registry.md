# ADR-0020 — "Registry" nouns are reserved for the Product Registry of packages; the compiler's binding-resolution catalog is a BindingSnapshot

| | |
|---|---|
| **Status** | Accepted 2026-07-27 |
| **Amends** | Refines the terminology of [ADR-0019](ADR-0019-binding-resolution-against-registry-snapshot.md) (substance unchanged); enables Epic 7 M7.0B-3 |
| **Deciders** | Technical Owner |
| **Related** | [product-package-registry-versioning](../architecture/epic7/product-package-registry-versioning.md), [product-compiler-binding-resolution](../architecture/epic7/product-compiler-binding-resolution.md), CODING_STANDARDS §1 (glossary — one concept, one name), CODING_STANDARDS §10 (internal interfaces change within a change-set that updates all callers) |

## Status

Accepted for M7.0B-3 (Product Package, Registry & Versioning). It resolves a naming
collision between two genuinely different concepts before the registry code lands.

## Context

M7.0B-2 introduced, per ADR-0019, an injected immutable catalog the compiler resolves
bindings against — implemented as `product.compiler.RegistrySnapshot` /
`RegistryEntry` (elements = available `(kind, namespace, id, versions)` for binding
resolution).

M7.0B-3 introduces the **Product Registry** — an immutable catalog of published
`ProductPackage`s — whose components the milestone names `ProductRegistry`,
`RegistryEntry`, `RegistrySnapshot`, `RegistryQuery`, `RegistryIndex`,
`RegistryReport` (elements = published packages + their metadata).

These are two different concepts. CODING_STANDARDS §1 forbids one name denoting two
concepts ("one concept, one name, everywhere — including tests and docs"). Leaving
both as `RegistrySnapshot`/`RegistryEntry` — even in separate subpackages — would put
two unrelated types behind one term and make `product.RegistrySnapshot` ambiguous.

## Decision

1. **The noun "Registry" (and `RegistrySnapshot`, `RegistryEntry`, `RegistryQuery`,
   `RegistryIndex`, `RegistryReport`) is reserved, across Product Runtime, for the
   Product Registry of packages.** These names are owned by `product.registry`.

2. **The compiler's binding-resolution catalog is renamed** `RegistrySnapshot` →
   **`BindingSnapshot`** and `RegistryEntry` → **`BindingSnapshotEntry`** (the
   accurate name: it catalogues *bindings*, not a registry of products). This is a
   pure rename — the type's behaviour, ADR-0019's substance (resolve against an
   injected immutable, content-addressed snapshot; never a live Epic 6 registry), and
   the determinism contract are all unchanged. All callers and tests are updated in
   the same change-set (no half-migrated state — CODING_STANDARDS §10).

3. ADR-0019 remains Accepted and correct on substance; only the type name it cites is
   refined here. Where ADR-0019 says `RegistrySnapshot`, read `BindingSnapshot`.

## Consequences

- The Product Registry owns the `Registry*` vocabulary exactly as the milestone
  specifies; the compiler owns `BindingSnapshot*`. No term denotes two concepts.
- `product.compiler.snapshot` continues to house the (now `BindingSnapshot`) type; the
  module keeps its filename to minimise churn.
- The rename is internal to Epic 7 (not frozen); Epic 6 is untouched. The
  provenance field that records which catalog a compilation resolved against is named
  `binding_snapshot_id` accordingly.
- Future readers are spared the collision permanently; a contributor adding registry
  features will not accidentally reuse the compiler's type.

## Alternatives Considered

- **Keep both named `RegistrySnapshot`/`RegistryEntry` in separate subpackages.**
  Rejected — one term, two concepts; violates the glossary rule and makes the
  top-level re-export ambiguous.
- **Rename the Product Registry's types instead (e.g. `PackageCatalogSnapshot`).**
  Rejected — the milestone (and the M7.0A design) deliberately name the registry
  components `Registry*`; "Registry" most naturally denotes the catalog of published
  products, and the compiler's catalog is the one whose name was imprecise.
- **Fold both into a single shared snapshot type.** Rejected — their elements differ
  (available bindings vs published packages); unifying them would couple the compiler
  to the packaging layer for no benefit.

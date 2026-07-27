# ADR-0019 — Binding resolution resolves against an injected immutable RegistrySnapshot, never live Execution Platform runtimes

| | |
|---|---|
| **Status** | Accepted 2026-07-26 |
| **Amends** | Refines [ADR-0018](ADR-0018-product-runtime-declarative-composition-layer.md) (Product Runtime composition layer) for the compiler bundle (Epic 7, M7.0B-2) |
| **Deciders** | Technical Owner |
| **Related** | [product-compiler-binding-resolution](../architecture/epic7/product-compiler-binding-resolution.md), M7.0A Product Runtime Architecture (§Compilation Pipeline, §Dependency Rules), Execution Platform v1.0 freeze |

## Status

Accepted for M7.0B-2 (Product Compiler & Binding Resolution). It fixes the compiler
invariant every later Epic 7 bundle inherits. It does not amend Constitution Part A
or B; it refines ADR-0018's deliberately-open clause ("later Epic 7 bundles may
additionally depend *on* the Execution Platform … to resolve references").

## Context

ADR-0018 established that Product Runtime *describes and binds* while the frozen
Execution Platform *executes*, and left open **how** a manifest reference
(`agent`/`workflow`/`skill`/`tool`/`provider`/`mcp`/`knowledge`) becomes a resolved
binding. Two paths exist:

1. **Resolve against live Epic 6 registries** — the compiler imports `tool`,
   `skill`, `workflow`, `agent`, `provider`, `mcp` runtimes and asks each registry
   whether a `(namespace, id, version)` exists.
2. **Resolve against an injected, immutable registry *snapshot*** — a plain,
   deterministic catalog of available `(kind, namespace, id, versions)` entries,
   supplied to the compiler as data.

The M7.0A design's own determinism clause names *"identical ProductDefinition +
identical Registry Snapshot + identical Compiler Version ⇒ byte-identical output"* —
i.e. it already presumes a snapshot is the resolution input, not a live runtime.

Path 1 would: (a) make `product` import six frozen sibling runtimes, enlarging the
dependency surface and risking the freeze/AR-15 acyclicity; (b) make compilation
non-deterministic and non-reproducible (registry contents change over time, lookups
can touch process state); and (c) make the compiler un-unit-testable without
standing up the whole Execution Platform.

## Decision

Binding resolution resolves **only** against an injected, immutable
`RegistrySnapshot` value object. The `ProductCompiler` and every resolution
component depend on **`core` plus the `product` package itself — nothing else**
(no Epic 6, no Epic 5). The snapshot is a deterministic catalog (content-addressed
by `snapshot_id`); a *future* composition-root bundle may build one *from* the live
Epic 6 registries, but that construction happens outside `product` and is never
imported by the compiler.

Resolution, dependency analysis, configuration resolution, compatibility, and
ownership validation are all **pure functions of (ProductDefinition, RegistrySnapshot,
CompilationContext)** — side-effect free, no I/O, no network, no execution.

## Consequences

- **Determinism & reproducibility** are structural: identical inputs give a
  byte-identical `CompiledProduct` and `CompilationReport` (regression-tested).
- **The freeze is preserved and cheaply proven** — the existing AST dependency test
  (`product` imports no sibling runtime) continues to pass unchanged, now also
  covering the compiler subpackage.
- **Independently testable** — the whole compiler is exercised with hand-built
  snapshots and zero Epic 6 wiring.
- A future contributor must not "just import the tool registry" for convenience —
  the snapshot seam exists specifically to prevent that; live-registry integration
  belongs in a later composition-root bundle that builds a snapshot, never in the
  compiler.
- The snapshot is a *point-in-time* input: a compiled product is only as current as
  the snapshot it was compiled against. Recording `snapshot_id` in the compilation
  identity makes that explicit and auditable.

## Alternatives Considered

- **Resolve against live Epic 6 registries (Path 1).** Rejected — imports six frozen
  runtimes into `product`, makes compilation non-deterministic and hard to test, and
  puts the freeze at risk for no benefit the snapshot cannot provide.
- **No resolution input at all — treat every reference as satisfiable.** Rejected —
  then "missing reference" / "binding compatibility" can never be detected, defeating
  the compiler's purpose.
- **Let the compiler reach registries but cache results for determinism.** Rejected —
  a cache over a mutable source is still non-reproducible across snapshots and still
  couples `product` to Epic 6; the snapshot *is* the honest, immutable input.

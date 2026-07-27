# ADR-0018 — Product Runtime is a declarative composition layer above the frozen Execution Platform, in a new `platform/product/` package

| | |
|---|---|
| **Status** | Accepted 2026-07-26 |
| **Amends** | Introduces Epic 7 (Product Platform) atop the frozen Execution Platform v1.0 (Epic 6) |
| **Deciders** | Technical Owner |
| **Related** | [product-definition-manifest-core](../architecture/epic7/product-definition-manifest-core.md), Execution Platform v1.0 freeze ([releases/epic6-execution-platform-v1.0.md](../releases/epic6-execution-platform-v1.0.md)), CODING_STANDARDS §2 (new top-level dir → ADR), ARCHITECTURE_RULES AR-15/AR-50/AR-52, ADR-0008 (manifest-not-code) |

## Status

Accepted for the start of Epic 7 (Product Platform). This ADR authorizes the
**first** Epic 7 code (M7.0B-1, Product Definition & Manifest Core) and fixes the
boundary every later Epic 7 bundle (compiler, bindings, packaging, deployment,
registry) must honour. It does not amend Constitution Part A or Part B; it records
a load-bearing structural decision, recorded as an Epic-6-style capability ADR
(the third ADR kind, per [adr/README.md](README.md)).

## Context

Epic 6 delivered a production-grade, **frozen** Execution Platform: workflow/agent
FSMs, tool/provider/MCP execution, reasoning, loop, and the two composition roots.
Freezing means no Epic 6 ownership, dependency direction, or contract may change
(see the Execution Platform v1.0 freeze manifest).

Epic 7's mission is a **Product Platform**: let an organization define, configure,
validate, compile, package, version, and deploy a complete AI Voice Agent purely by
**declaration** — no runtime code change. The approved M7.0A design established the
principle that description and execution are separate concerns: the Product Runtime
*describes and binds*; the Execution Platform *executes*.

Two structural questions must be settled before any Epic 7 code lands:

1. **Where does this code live?** CODING_STANDARDS §2 requires an ADR for any new
   top-level directory; the folder map is Constitution A14 and is not locally
   negotiable.
2. **Which way do the dependencies point?** A composition layer that the frozen
   Execution Platform could import would violate the freeze and risk a cycle
   (AR-15).

## Decision

1. **New top-level package `platform/product/`.** Epic 7 code lives in its own
   package, a peer of the Epic 6 capability packages (`tool/`, `skill/`,
   `workflow/`, `agent/`, `loop/`, `mcp/`, `integration/`). It follows the same
   closed-runtime module shape already established (domain / ids / serializer /
   validation / diagnostics / factory / config / `__init__` public surface).

2. **Strictly downward, one-way dependency.** `product → core[, tenant]` only for
   M7.0B-1 (Definition & Manifest Core needs nothing else). Later Epic 7 bundles
   may additionally depend *on* the Execution Platform and Runtime Platform to
   resolve references — **never the reverse**. No Epic 6 or Epic 5 package may ever
   import `product`. The graph stays acyclic (AR-15), proven by the existing
   AST/import-boundary test pattern extended to `product`.

3. **Product Runtime owns description, never execution.** It owns product metadata,
   configuration, manifests, validation, canonicalization, deterministic hashing,
   compilation, packaging, deployment *descriptors*, versioning, and configuration
   diagnostics. It must never own workflow/tool/provider/MCP execution, reasoning,
   any FSM, runtime lifecycle, or platform integration — those remain Epic 6's,
   untouched.

4. **Declarative, immutable, deterministic.** The Product Manifest is the
   versioned, diffable source of truth — data, never executable code (the
   ADR-0008 "manifest-not-code" discipline applied one layer up). Identical
   manifest content yields a byte-identical canonical form and hash regardless of
   authoring order.

5. **References are declared, never resolved, in this bundle.** M7.0B-1 models
   agent/workflow/skill/tool/provider/MCP/knowledge references and secret
   references as inert placeholders. Resolution into Epic 6 registries (bindings)
   is a later bundle and is explicitly out of scope here.

## Consequences

- Epic 7 is independently testable and certifiable without touching Epic 6; the
  freeze is preserved by construction (the import-boundary test fails if any
  frozen package grows a `product` import).
- A future contributor must not "reach down" from `product` into Epic 6 execution
  internals to take a shortcut — resolution always goes through the reference/
  binding seam a later bundle will add, mirroring how `bridge`/`integration`
  compose without redesigning what they compose.
- The new package obligates the same governance every capability carries:
  component spec, tests-in-change-set, INDEX linkage, changelog.
- `pyproject.toml`'s install `packages.find` list is intentionally left unchanged
  (it already omits every Epic 6 capability package); `product` is import-resolved
  the same way its Epic 6 peers are for tests and in-repo use.

## Alternatives Considered

- **Extend an existing Epic 6 package (e.g. `integration/`) instead of a new
  package.** Rejected — Product definition is a distinct responsibility (AR-50:
  one responsibility per module); folding it into a composition root would conflate
  "wire the runtime" with "describe the product" and blur the freeze boundary.
- **Put Product Runtime below or beside Epic 6 in the dependency graph so Epic 6
  could consume product config directly.** Rejected — that inverts the intended
  direction, forces an Epic 6 change (violating the freeze), and risks a cycle.
  Execution must stay ignorant of the Product layer.
- **Allow the manifest to carry executable hooks/config expressions for
  flexibility.** Rejected — violates ADR-0008's manifest-not-code principle and the
  M5.8-certified zero-dynamic-execution invariant; declared data + a later
  deterministic compiler is the sanctioned path.

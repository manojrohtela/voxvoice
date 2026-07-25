# ADR-0008 — Skills are a versioned manifest, not code

| | |
|---|---|
| **Status** | Accepted 2026-07-25 |
| **Amends** | Epic 6 Agent Platform Architecture (ratifies a decision proposed, not yet accepted, at Phase 0) |
| **Deciders** | Technical Owner |
| **Related** | [epic6-agent-platform-architecture](../architecture/epic6/epic6-agent-platform-architecture.md) §18, [skill-runtime-design](../architecture/epic6/skill-runtime-design.md) §17, DL-0042, DL-0049, DL-0052 |

## Status

Proposed at Phase 0 (M6.0, DL-0042, "ADR-0008 (proposed)") as a placeholder number
reserved for this exact decision. Formally ratified here now that the complete
Skill Runtime design (M6.2A) and its production certification (M6.2B-3, DL-0052,
PASS) both confirm the decision held through implementation with zero drift.

## Context

Skill Runtime needed a contract shape for what a "Skill" is. The two live
alternatives were: (a) a Skill is a manifest describing *what* a capability
requires/provides, resolved by an injected `CapabilityResolver`, or (b) a Skill
carries or references executable code directly. Option (b) would reopen the
exact "code as intelligence" risk the M5.8 Epic 5 closure certification
identified and closed for Tool Runtime (manifest + registered adapter, never
manifest-supplied code) — a Skill is one layer of composition above a Tool, so
the same risk applies with even less scrutiny if skills are treated as a
marketplace-distributable unit.

## Decision

A `SkillManifest` is a versioned, declarative, hybrid YAML/dataclass contract.
It declares capability requirements, dependencies, and metadata — it never
carries or references arbitrary executable code. Skill Runtime discovers and
registers manifests only; resolving a declared capability requirement to an
actual Tool/Workflow is the injected `CapabilityResolver`'s job, never Skill
Runtime's own.

## Consequences

- A `SkillManifest` can be authored, reviewed, and distributed (including by
  third parties, in a future marketplace) without ever executing untrusted code
  at registration time — the same zero-dynamic-execution invariant M5.8
  certified for the whole platform now extends one layer up.
- Capability requirements are declared, never resolved, by Skill Runtime itself
  (a distinct, related decision recorded in the design's own ADR item 2) —
  Skill Runtime cannot silently "just check the Tool Registry directly."
- No `SkillPort` exists at this design stage (no M5.0 port anticipated this
  shape); Skill Runtime is discovered structurally through its own registry/
  catalog surface. This is an accepted, deferred gap, not a defect — see the
  M6.6.5 Deferred Decisions record for its current status.
- A future marketplace-scale skill ecosystem inherits this invariant for free;
  reversing it later would require a new, explicitly superseding ADR, not a
  quiet code change.

## Alternatives Considered

- **Skills carry executable code directly (a plugin-style shape).** Rejected —
  reopens the "code as intelligence" risk M5.8 closed for Tool Runtime, one
  layer up, with less scrutiny (skills are meant to be marketplace-distributable).
- **Skills declare capabilities but also resolve them internally against a
  hardcoded Tool Registry reference.** Rejected — collapses the "requires
  without owning" boundary and would require Skill Runtime to import Tool
  Runtime directly, violating the platform's acyclic dependency discipline.
- **A hybrid: manifest-only for public/marketplace skills, code-carrying for
  "internal" skills.** Rejected — two enforcement paths for the same invariant
  is strictly worse than one; the manifest-only rule is simpler to audit and
  has no legitimate use case the `CapabilityResolver` seam doesn't already cover.

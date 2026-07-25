# ADR-0012 — Tool contract is a versioned manifest plus a registered adapter, never manifest-supplied code

| | |
|---|---|
| **Status** | Accepted 2026-07-25 |
| **Amends** | Epic 6 Agent Platform Architecture |
| **Deciders** | Technical Owner |
| **Related** | [tool-runtime-design](../architecture/epic6/tool-runtime-design.md) §7, §16 (ADR-Tool-01..04), DL-0043, DL-0044, DL-0048, [ADR-0008](ADR-0008-skill-manifest-not-code.md) |

## Status

Accepted, consolidating the Tool Runtime design's own four recommended ADR
items (ADR-Tool-01 through 04) into one record — all four are facets of the
same central "what is a Tool" decision. Ratified from the complete Tool
Runtime design (M6.1A) and its production certification (M6.1B-5, DL-0048,
PASS).

## Context

Tool Runtime is the execution layer for external capabilities (calendar,
email, Slack, CRM, database, search, filesystem, browser, HTTP, internal
services) an Agent invokes. The M5.8 Epic 5 closure certification established
a zero-dynamic-execution invariant across the whole platform — no runtime
executes caller-supplied or manifest-supplied code. Tool Runtime, being the
one Epic 6 capability whose entire purpose is *executing* something, needed
this invariant preserved explicitly rather than assumed.

## Decision

A Tool's contract is a hybrid: a JSON-Schema-backed `ToolManifest` (the
versioned, discoverable contract) paired with a separately **registered**
`ToolAdapter` (real code, but never manifest-supplied — an adapter is
registered by the platform/operator, never authored inline in a manifest
field). Three closely-related facets of this same decision:
- **Identity** is `(name, version)`, not `name` alone — mirroring every other
  closed runtime's versioning model.
- **Approval gating** introduces a `PENDING` policy verdict, one addition
  beyond the allow/deny binary Provider Runtime's policy engine uses — real-
  world side effects justify a third state Provider Runtime's own routing
  decisions never needed.
- **No secrets ever cross the boundary** — Tool Runtime never receives or
  proxies credentials; an adapter resolves its own, and secret-shaped fields
  are excluded from discoverable descriptors at the schema level, not by
  convention alone.

## Consequences

- Tool Runtime can register third-party/community adapters without ever
  executing untrusted code sourced from a manifest — the manifest is pure
  data, always.
- The `PENDING` verdict has no consumer yet (the approval *resolution*
  mechanism — a human-in-the-loop UI/API — is explicitly deferred to
  Developer Platform or a dedicated later milestone, flagged not silently
  assumed, per the design's own §18 risk).
- Secret-field exclusion is a schema-authoring discipline backed by a
  registration-time linter check (heuristic, not a proof) — a residual,
  documented risk, not a guarantee.
- This same manifest-not-code shape was independently re-derived one layer up
  for Skill Runtime (ADR-0008) — the two decisions reinforce each other and
  should be read together.

## Alternatives Considered

- **A Tool is executable code registered directly, no separate manifest.**
  Rejected — loses discoverability/schema-validation and reopens the
  dynamic-execution risk M5.8 closed platform-wide.
- **A Tool's manifest may embed a small amount of inline logic (e.g. a
  templated request body) for simple HTTP-style tools.** Rejected — any
  "small amount" of inline logic is still manifest-supplied code; the
  zero-dynamic-execution invariant does not admit a size-based exception.
- **Skip the `PENDING` verdict; approval-gated tools simply get denied until
  manually re-enabled.** Rejected — collapses "needs approval" and "is
  denied" into one state, losing information a future approval UI would need
  to distinguish a pending request from a rejected one.
- **Proxy secrets through Tool Runtime with encryption at rest.** Rejected —
  even encrypted, a proxied secret widens the blast radius of a Tool Runtime
  compromise to every credential it has ever proxied; adapter-local
  resolution keeps each adapter's credential scope minimal and independent.

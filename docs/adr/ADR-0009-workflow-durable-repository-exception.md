# ADR-0009 — Workflow execution state is the platform's one durable-repository exception

| | |
|---|---|
| **Status** | Accepted 2026-07-25 |
| **Amends** | Epic 6 Agent Platform Architecture (ratifies a decision proposed, not yet accepted, at Phase 0) |
| **Deciders** | Technical Owner |
| **Related** | [epic6-agent-platform-architecture](../architecture/epic6/epic6-agent-platform-architecture.md) §18, [workflow-runtime-design](../architecture/epic6/workflow-runtime-design.md) §7, §12 item 4, DL-0042, DL-0056, DL-0059 |

## Status

Proposed at Phase 0 (M6.0, DL-0042, "ADR-0009 (proposed)"), establishing the
precedent and its criteria before a second runtime could claim the same
exception. Formally ratified here now that the complete Workflow Runtime design
(M6.4A) — backed by `WorkflowCheckpoint`/`WorkflowSnapshot` — and its production
certification (M6.4C, DL-0059, PASS) both confirm the decision held through
implementation.

## Context

Every closed Epic 6 runtime's own repository (Tool, Skill, Hooks, and later
Agent, Loop) follows a **temporary** convention: registration/execution records
carry a `*_ttl_s` expiry, since their state is fully reconstructible from other
already-durable sources (a manifest, a decision-hash chain, a delegated
workflow's own snapshot). A Workflow execution is different: it can pause for
an arbitrarily long external signal (`WorkflowExecutionState.RECOVERING`,
awaiting `WorkflowPort.signal`), and there is no other durable source its
in-flight node-by-node progress can be reconstructed from if that record
expires mid-pause.

## Decision

`WorkflowExecutionRepository` is the platform's one deliberate, named durable-
repository exception. Its execution/checkpoint/snapshot records carry no
`*_ttl_s` field. Every other closed runtime's repository stays temporary by
convention.

## Consequences

- A paused Workflow execution can be recovered correctly no matter how long
  the pause lasts, without inventing a second recovery mechanism.
- This durability is a *criterion-gated exception*, not a precedent that "the
  next runtime can claim the same durability by analogy." A future runtime
  proposing its own durable repository must show the same "can pause
  indefinitely with no other durable reconstruction source" property Workflow
  has — Agent Runtime's own goal-execution repository was explicitly evaluated
  against this criterion (M6.5A §10.3) and found to *not* qualify (a goal's
  state is reconstructible from its decision-hash chain plus the delegated
  workflow's own durable snapshot), so it correctly stayed temporary.
- Operational cost: `WorkflowExecutionRepository` records require an explicit
  cleanup/archival strategy of their own (never an automatic TTL sweep) —
  tracked as accepted, ongoing operational debt, not a design gap.

## Alternatives Considered

- **Make every runtime's repository durable by default.** Rejected — most
  execution state genuinely is reconstructible from other sources; blanket
  durability multiplies storage/cleanup burden with no correctness benefit for
  Tool/Skill/Hooks-shaped state.
- **Keep Workflow's repository temporary too, and recover paused executions by
  re-running from the last checkpoint on a fixed retention window.** Rejected
  — an external signal (human approval, a webhook, a long-running third-party
  process) can legitimately arrive after any fixed window; silently discarding
  a still-valid paused execution would be a correctness regression, not a
  storage optimization.
- **Introduce a separate "long-pause" side-store instead of making the whole
  repository durable.** Rejected as unnecessary complexity — the existing
  `WorkflowCheckpoint`/`WorkflowSnapshot` shapes already capture exactly the
  state that needs to survive, so a second storage tier would duplicate
  machinery for no additional guarantee.

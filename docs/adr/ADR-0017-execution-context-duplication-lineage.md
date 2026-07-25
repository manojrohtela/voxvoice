# ADR-0017 — Execution-context shapes extend by structural duplication within one delegation lineage; Loop Engine is deliberately outside it

| | |
|---|---|
| **Status** | Accepted 2026-07-25 |
| **Amends** | Epic 6 Agent Platform Architecture |
| **Deciders** | Technical Owner |
| **Related** | [workflow-runtime-design](../architecture/epic6/workflow-runtime-design.md) §12 item 3, [agent-runtime-design](../architecture/epic6/agent-runtime-design.md) §11 item 5, [loop-engine-design](../architecture/epic6/loop-engine-design.md) §4.7, §9 item 3, DL-0056, DL-0060, DL-0065 |

## Status

Accepted. The "extend by structural duplication" half was first applied when
`WorkflowExecutionContext` extended `SkillExecutionContext` (M6.4A), applied a
second time when `AgentExecutionContext` extended `WorkflowExecutionContext`
(M6.5A), and confirmed by M6.5C certification as declared-and-tested (though
not yet consumed by any bundle — see Deferred Decisions). The "deliberately
independent" half was applied for the first time by `LoopExecutionContext`
(M6.6A), per that milestone's own explicit instruction, and confirmed by
M6.6C certification (with the same "declared, not yet consumed" caveat).

## Context

Each execution-context shape in the platform needs to carry the fields of
the capability one level above it in the delegation chain (a Workflow step
may itself be a Skill invocation; an Agent's plan step may itself be a
Workflow delegation) — but the platform's dependency rules forbid any
runtime from importing a sibling runtime's implementation package directly.
Loop Engine's own context needed a different answer: it is not a callee one
level below any single capability — it is a general-purpose primitive any
future caller (Agent, Workflow, or something outside Epic 6 entirely) may
adopt.

## Decision

Within the Skill→Workflow→Agent delegation lineage — a strict "caller above
delegates execution to the callee below" chain — each execution-context
shape extends the one below it **by structural field duplication, never by
inheritance or a cross-package import**: `WorkflowExecutionContext`
duplicates `SkillExecutionContext`'s fields; `AgentExecutionContext`
duplicates `WorkflowExecutionContext`'s (which itself duplicates
`SkillExecutionContext`'s). **`LoopExecutionContext` is a wholly independent
shape** — it does not extend or duplicate `AgentExecutionContext`, because
Loop Engine sits outside that lineage entirely; it carries only its own
inline `session_id`/`trace_id`/`org_id` fields (the same precedent every
context shape in the platform independently applies on its own terms) plus
loop-specific `iteration`/`variables`.

## Consequences

- No execution-context shape in the platform ever imports a sibling
  runtime's implementation package — every "extend" relationship is
  achieved by duplicating field shapes locally, preserving the acyclic
  dependency graph every closed runtime's own dependency test enforces.
- A future contributor must not retrofit `LoopExecutionContext` into the
  Skill→Workflow→Agent lineage by analogy (e.g. having it extend
  `AgentExecutionContext` "for consistency") — Loop Engine's independence is
  the *correct* application of the same underlying principle (context
  shapes extend only within a genuine delegation lineage), not an exception
  to it.
- If a future capability is added one level below Agent in the same
  delegation lineage (e.g. a hypothetical Sub-Agent capability), its own
  execution context should extend `AgentExecutionContext` by the same
  structural-duplication convention — this record is the template to cite.
- Both `AgentExecutionContext` and `LoopExecutionContext` are, as of their
  own certifications, declared and structurally tested but not yet actually
  threaded through any execution code that consumes their extended/
  independent fields — tracked in the M6.6.5 Deferred Decisions record, not
  resolved by this ADR.

## Alternatives Considered

- **Have each context shape import and inherit from the one below it
  directly (real Python inheritance across package boundaries).** Rejected
  — would require `agent/` to import `workflow/`'s implementation package
  directly, violating the platform's acyclic, no-sibling-import dependency
  rule every closed runtime's own test enforces.
- **Have every context shape import a single shared base from `core`
  instead of duplicating fields pairwise.** Rejected — was considered and
  rejected earlier in the platform's history (Workflow's own design): a
  shared `core` base would need to anticipate every future capability's
  fields in advance, coupling `core` to Epic 6-specific concerns it should
  stay agnostic to.
- **Have `LoopExecutionContext` extend `AgentExecutionContext` by structural
  duplication anyway, for consistency with the rest of the platform.**
  Rejected (the M6.6A milestone's own explicit instruction) — Loop Engine is
  a general-purpose primitive usable by callers other than Agent Runtime;
  binding its context shape to Agent's own accumulated fields (`goal_id`,
  `plan_id`, `delegated_execution_id`, ...) would wrongly assume every future
  caller is Agent Runtime.

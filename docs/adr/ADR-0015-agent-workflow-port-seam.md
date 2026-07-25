# ADR-0015 — Agent Runtime delegates workflow execution through its own `AgentWorkflowPort` seam, never `core.ports.workflow.WorkflowPort` directly

| | |
|---|---|
| **Status** | Accepted 2026-07-25 |
| **Amends** | Epic 6 Agent Platform Architecture |
| **Deciders** | Technical Owner |
| **Related** | [agent-runtime-design](../architecture/epic6/agent-runtime-design.md) §11 item 3, §13, DL-0060, DL-0064 |

## Status

Accepted from the complete Agent Runtime design (M6.5A) and confirmed
unchanged through implementation (M6.5B-1/2/3) and certification (M6.5C,
DL-0064) — the seam was never bypassed by a direct `WorkflowExecutionRuntime`
import anywhere in `agent/`, confirmed by AST scan across all 47 files
spanning 3 bundles.

## Context

Agent Runtime needs to delegate plan steps to Workflow Runtime. Two
candidate mechanisms existed: calling `WorkflowExecutionRuntime` through the
frozen M5.0 port it is supposed to implement (`core.ports.workflow.
WorkflowPort`), or declaring Agent's own thin delegation seam. The complication:
whether `WorkflowExecutionRuntime` literally implements
`core.ports.workflow.WorkflowPort` has never actually been verified — the same
open, deferred conformance gap Tool Runtime's own M6.1B-5 certification
recorded for `core.ports.tool.ToolPort`.

## Decision

Agent Runtime declares and uses its own `AgentWorkflowPort` seam
(`resolve`/`submit`/`status`/`signal`) for all workflow delegation. It never
assumes or calls through literal `core.ports.workflow.WorkflowPort`
conformance, because that conformance has never been checked.

## Consequences

- Workflow delegation is provably decoupled: an AST scan confirms
  `agent.planning.delegation`/`agent.execution.engine` never construct or
  execute a `WorkflowGraph` directly — all interaction is resolve/submit/
  status/signal through the seam.
- `AgentWorkflowPort`'s own concrete adapter implementation (wherever it is
  eventually wired, at Agent Platform Integration) wraps an already-unverified
  `WorkflowPort` conformance gap. If a future milestone verifies or fixes
  `WorkflowExecutionRuntime` to literally implement `core.ports.workflow.
  WorkflowPort`, the `AgentWorkflowPort` adapter should be revisited to
  delegate to the real port rather than duplicating logic — tracked as a soft
  dependency between two open items (see the M6.6.5 Deferred Decisions
  record), not actioned by this ADR.
- This mirrors, and extends, the same honest "conformance is a target, not a
  guarantee" posture Tool Runtime's own M6.1B-5 debt already established —
  Agent Runtime's own certification (M6.5C) separately found that the
  M6.5A design's opening paragraph never revisited whether Agent Runtime
  itself literally implements `core.ports.agent.AgentPort`, a related but
  distinct gap tracked separately (see Deferred Decisions: "AgentPort
  conformance").

## Alternatives Considered

- **Call `WorkflowExecutionRuntime` through literal `core.ports.workflow.
  WorkflowPort` directly, assuming conformance.** Rejected — conformance was
  never verified; silently assuming it would be exactly the "assumed complete
  when it is not" risk the Workflow Runtime design's own §11 explicitly warns
  against.
- **Verify/fix `WorkflowPort` conformance first, as a prerequisite to Agent
  Runtime's own design.** Rejected as out of scope and unnecessarily
  blocking — Agent Runtime's own seam lets delegation proceed correctly today
  regardless of when (or whether) that separate verification work happens.
- **Import `workflow.execution.engine.WorkflowExecutionEngine` directly and
  skip the port abstraction entirely, since it's the only implementation that
  exists.** Rejected — this is the same "just import the sibling runtime
  directly, it's simpler" shortcut the platform's dependency rules
  consistently foreclose (Agent Runtime declares zero import of `workflow/`
  anywhere, confirmed by AST scan); it would also make Agent Runtime brittle
  to any future internal refactor of Workflow Runtime's own implementation.

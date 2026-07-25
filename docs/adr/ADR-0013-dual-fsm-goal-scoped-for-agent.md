# ADR-0013 — The dual-FSM shape is the platform's closed-runtime pattern; Agent Runtime attaches the second FSM to the Goal, never the identity

| | |
|---|---|
| **Status** | Accepted 2026-07-25 |
| **Amends** | Epic 6 Agent Platform Architecture |
| **Deciders** | Technical Owner |
| **Related** | [tool-runtime-design](../architecture/epic6/tool-runtime-design.md) §-dual FSM, [workflow-runtime-design](../architecture/epic6/workflow-runtime-design.md) §12 item 1, [agent-runtime-design](../architecture/epic6/agent-runtime-design.md) §11 item 1, DL-0044, DL-0056, DL-0059, DL-0060, DL-0064 |

## Status

Accepted — a cross-cutting pattern first applied by Tool Runtime (M6.1B-1),
restored explicitly by name for Workflow Runtime (M6.4A, since Skill/Hooks in
between used only a single descriptor FSM), and restored a second time with a
deliberate placement variant for Agent Runtime (M6.5A). Certified structurally
sound for Agent Runtime specifically by M6.5C (DL-0064): zero shared enum
values between `AgentLifecycleState`/`AgentGoalState`, confirmed by direct
enum inspection.

## Context

Some registrable entities in the platform have state beyond "is it
registered/active" — they genuinely *execute over time* and need a second,
independent lifecycle tracking that execution, distinct from the descriptor's
own registration lifecycle. Skill Runtime and the Hook Framework do not need
this (a Skill/Hook registration has no separate "is currently executing"
concept of its own); Tool, Workflow, and Agent do. For Agent specifically, a
further question arose: does the second FSM belong to the Agent's own
identity record, or to something else the Agent processes?

## Decision

Every closed runtime whose registered entity genuinely executes over time
gets **two independent `core.fsm` lifecycles**: a descriptor lifecycle
(registration state: DRAFT→ACTIVE→DEPRECATED→RETIRED, or equivalent) and an
execution lifecycle (tracking one run's progress to a terminal state).
**For Agent Runtime, the second FSM (`AgentGoalState`) attaches to `AgentGoal`,
never to the Agent identity (`AgentIdentity`/`AgentDescriptor`)** — an Agent's
identity is durable-but-idle between many goals across many conversations;
the Goal is the bounded unit that genuinely executes over time, exactly the
same "genuinely executes over time" criterion Workflow's own execution FSM
satisfies, but applied to a different record than the registrable one.

## Consequences

- Skill Runtime and Hook Framework correctly stay single-FSM (registration
  only) — a future contributor must not "complete the pattern" by adding an
  execution FSM to either without first identifying what actually executes.
- Agent Runtime supports multiple concurrent/sequential goals per Agent
  identity without any FSM conflict — the identity's own descriptor FSM never
  encodes single-goal-at-a-time behavior.
- A future contributor must not collapse Agent's dual-FSM into one (by
  analogy with Skill/Hooks) or misplace the second FSM on the Agent identity
  record (which would incorrectly imply single-goal-at-a-time) — both
  mistakes are explicitly named and foreclosed by this record.
- Tool Runtime's own dual-FSM (descriptor + execution, RECEIVED→VALIDATING→
  EXECUTING→terminal) was the first application and remains the baseline
  shape Workflow's and Agent's own execution FSMs were checked against for
  consistency, not literal reuse (each execution FSM's own states are
  runtime-specific).

## Alternatives Considered

- **One FSM per runtime, encoding both registration and execution state
  together.** Rejected — conflates two genuinely independent concerns (is
  this entity registered vs. is this particular run/goal in progress),
  and would force awkward states like "ACTIVE-but-currently-executing."
- **Attach Agent's second FSM to `AgentIdentity` (mirroring Workflow's
  execution FSM attaching to the one Workflow execution record).**
  Rejected — an Agent identity is durable and persists across many goals; a
  Workflow's single execution record has no equivalent "many goals" concept,
  so the direct analogy does not hold. Attaching there would incorrectly
  suggest one goal at a time per Agent.
- **No second FSM for Agent Runtime at all; goal progress tracked as a plain
  status string.** Rejected — loses the deterministic, `core.fsm`-enforced
  transition guarantees every other execution lifecycle in the platform
  relies on (illegal transitions raising `RuntimeConflict`, not silently
  accepted).

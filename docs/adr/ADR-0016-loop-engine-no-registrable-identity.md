# ADR-0016 — Loop Engine has no registrable identity — no Registry, Manifest, Descriptor, Lifecycle FSM, Repository, or Runtime facade

| | |
|---|---|
| **Status** | Accepted 2026-07-25 |
| **Amends** | Epic 6 Agent Platform Architecture |
| **Deciders** | Technical Owner |
| **Related** | [loop-engine-design](../architecture/epic6/loop-engine-design.md) §1, §9 item 1, DL-0065, DL-0066, DL-0067, M6.6C certification (93/100, PASS) |

## Status

Accepted from the complete Loop Engine design (M6.6A) and confirmed unchanged
through both implementation bundles (M6.6B-1/2) and production certification
(M6.6C, PASS, 93/100 — the highest Epic 6 certification score to date,
attributed directly to this decision's minimal dependency surface).

## Context

Every other Epic 6 capability certified so far (Tool, Skill, Hooks, Workflow,
Agent) is a registrable, discoverable, versioned closed runtime: a caller
looks it up by `(namespace, id, version)` and reuses it across many
invocations. Loop Engine is structurally different: a loop is a single,
bounded refinement pass with all its inputs (a `LoopDefinition`, an injected
`ReasoningPort` and `LoopValidator`) supplied directly at the call site, the
same way a caller invokes `core.fsm.StateMachine` or `core.policies.
RetryPolicy.delay_for` directly — never as a discoverable, reusable
capability a second caller would look up by name.

## Decision

Loop Engine ships **no Registry, Manifest, Descriptor, Lifecycle FSM,
Repository, or Runtime facade**. `LoopDefinition` is constructed by the
caller at the call site and passed directly into `run_loop()` (a plain
function, not a class hierarchy) exactly once per invocation. There is no
`LoopFactory`-as-composition-root either — `LoopFactory` in this package
builds only two caller-supplied value objects (`LoopDefinition`,
`LoopExecutionContext`), never a long-lived runtime object.

## Consequences

- A future contributor extending Loop Engine must not "complete the pattern"
  by adding a `LoopRegistry` by analogy with Tool/Skill/Hooks/Workflow/Agent —
  this decision exists specifically to guard against that reflex.
- Every future adopter (Agent Runtime's own future plan-revision step,
  Workflow Runtime's own future retry-with-improvement node, a future MCP
  tool-call refinement, or any caller outside Epic 6 entirely) wires
  `ReasoningPort` + `LoopValidator` + `LoopDefinition` together independently
  — there is no shared composition root. This is a deliberate simplicity
  trade-off, tracked as an open item if three or more callers end up
  duplicating near-identical wiring (see the M6.6.5 Deferred Decisions
  record: "Loop composition-root strategy").
- Loop Engine ships zero persistence of any kind — if a future adopter needs
  to resume a loop across a process restart, that adopter's own repository
  (e.g. a future `AgentGoalSnapshot` extension) carries the iteration
  history; Loop Engine itself never will.
- Loop Engine's dependency graph is the simplest of any Epic 6 capability:
  `core` plus exactly one already-frozen port (`core.ports.reasoning.
  ReasoningPort`) — directly responsible for its certification scoring the
  highest of any Epic 6 capability to date.

## Alternatives Considered

- **Give Loop Engine a registry so callers can register and reuse named loop
  configurations.** Rejected — a `LoopDefinition` is fully specified by its
  caller at the call site with no reuse-across-callers use case identified;
  a registry would add discoverability machinery for something nobody looks
  up by name.
- **Model Loop Engine as a lightweight runtime with a minimal lifecycle FSM
  (e.g. RUNNING→COMPLETED/FAILED) for observability parity with other Epic 6
  capabilities.** Rejected — a loop's own `LoopResult`/`LoopIteration`
  history already carries everything an observer needs; a parallel FSM would
  track information the result object already encodes, adding ceremony
  without new capability.
- **Give `LoopFactory` composition-root responsibilities (assembling a
  `ReasoningPort`+`LoopValidator`+pipeline bundle), mirroring `AgentFactory`/
  `WorkflowFactory`.** Rejected — there is no long-lived object for such a
  factory to assemble; `run_loop()` is called once per invocation, and
  inventing a composed object around it would be composition-root ceremony
  for a stateless function call.

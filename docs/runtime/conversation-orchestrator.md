# Runtime Component Spec — Conversation Orchestrator

| | |
|---|---|
| **Component** | `runtime.orchestrator.ConversationOrchestrator` · **Status:** Implemented (M2B.2) |
| **Drives** | `runtime.conversation.ConversationStateMachine` (M2B.1) over `core.states.SessionState` |
| **Format** | Constitution §A11.3 component specification |
| **Milestone** | M2B.2 — Conversation Orchestrator (2026-07-16) |

## Responsibility (single)
Be the **sole coordinator of conversation execution** and the **sole writer** of
conversation state. It owns one `ConversationStateMachine`, coordinates the
conversation lifecycle phases, routes failures through the approved state machine,
invokes provider capabilities only through their `core.providers` interfaces, and
emits lifecycle notifications. It performs **no** speech processing, AI inference,
provider implementation, turn logic, or business logic.

## Ownership model (Definition of Done)
```
SessionWorker            → worker execution lifecycle (WorkerState)  [M2A]
ConversationOrchestrator → conversation lifecycle (SessionState, SOLE WRITER)  [M2B.2]
ConversationStateMachine → deterministic transitions + history  [M2B.1]
Providers                → capability interfaces (DI; no execution yet)
Turn Intelligence (M3)   → turn execution (separate)
```
These boundaries are strict and enforced by construction: the state machine is a
**private** member of the orchestrator (no public writer); the worker holds the
orchestrator and calls `begin/run/end/cancel` but never transitions conversation
state itself.

## SessionWorker integration
The worker hosts exactly one orchestrator, created and begun during the worker's
`_initialize` (so worker startup encompasses conversation startup and failures
propagate cleanly). The worker's run loop delegates entirely to
`orchestrator.run()`; `stop()`/`cancel()` call `orchestrator.end()`/`cancel()`.
`worker.conversation_state` exposes the conversation state **read-only**.

## Conversation flow (coordination only)
```
begin(): CREATED ─► CONNECTED ─(open providers, M2C)─► IN_CONVERSATION
run()  : await end signal   (the turn loop STT→LLM→TTS is M2C+; idle here)
end()  : IN_CONVERSATION ─► ENDING ─(close providers, M2C)─► ENDED
```
`run()` tolerates an already-ended conversation (returns promptly) so a session
stopped immediately after start is race-free.

## Inputs / Outputs
- **Inputs:** `RuntimeContext` (session, injected provider interfaces, config);
  optional transition observer. Methods: `begin()`, `run()`, `end(reason,source)`,
  `cancel(reason)`, `fail(reason)`.
- **Outputs:** read-only `state`, `is_terminal`, `history`, `dependencies`;
  transition notifications via the state-machine observer.

## Provider integration
Dependency injection only. The orchestrator holds the provider **interfaces**
(`core.providers`) and will open/close them in `_open_providers`/`_close_providers`
at **M2C**. At M2B.2 these are no-ops — **no provider is executed** (verified by a
test asserting injected mocks receive zero calls). No vendor logic, no
provider-specific conditions.

## Failure handling (all through the state machine — no hidden transitions)
| Failure | Routing |
|---|---|
| Startup (`begin`) failure | conversation → `FAILED` + `OrchestrationStartupError` (CREATED/CONNECTED → FAILED is legal) |
| Mid-conversation failure (`fail`) | → `ENDING` → `ENDED` (the machine forbids `IN_CONVERSATION → FAILED`, §A4.4) |
| Cancellation | `end()` path, reason-tagged `cancelled` |
| Worker cleanup with a dangling conversation | defensively terminalized via `fail()` |

## Observability
Lifecycle notifications are the conversation state transitions, delivered via the
state machine's synchronous observer + logs. This is **not** the Event Bus (M4); a
bridge can forward `TransitionRecord`s as `core.events` events later.

## Dependencies & boundaries
Imports `core.states`, `runtime.conversation`, `runtime.contracts`,
`runtime.errors`, and provider **interfaces** via DI. Never imports `api`; never a
provider implementation. Consumed by `SessionWorker`.

## Not in scope (later milestones)
Provider/transport wiring + the turn loop (M2C) · Turn Intelligence (M3) · Event
Bus emission (M4) · memory/snapshots (M5).

# AI Runtime Foundation (Epic 5, M5.0)

| | |
|---|---|
| **Scope** | Shared infrastructure every Epic-5 runtime module reuses · **Status:** Implemented (M5.0) |
| **Milestone** | M5.0 — AI Runtime Foundations (2026-07-18) · infrastructure only, additive |
| **Basis** | The approved Epic-5 Architecture Summit |

## Purpose
Eliminate duplicated infrastructure **before** implementing any AI module. After
M5.0, Memory/Prompt/Knowledge/Context/Tool/Reasoning are implementable with *almost
no infrastructure code* — each writes only its domain logic on top of these shared
abstractions. **No AI functionality is implemented here.**

## What landed (all additive — runtime/events/gateway/replay untouched)

### 1. `core/fsm/` — deterministic state-machine framework
Generic `StateMachine[S, Tr]` over a validated `StateMachineDefinition`: single
`(state, trigger) → target` edges, guards + actions, injected clock, immutable
`history`, `on_transition` observer, and `replay(triggers)`. Duplicate edges,
undefined transitions, rejected guards, and firing from a terminal state all raise
typed errors (`DuplicateTransition`/`InvalidTransition`/`GuardRejected`/
`TerminalStateError`, rooted in `core.errors`). Async-ready: an async machine
subclasses and overrides `_run_action`; the table/validation/history/events are
reused. *Replaces the hand-written FSM logic in the five Turn Intelligence engines;
migrating them is a later milestone (not M5.0).*

### 2. `core/module/` — RuntimeModule + BusConsumer
- **`RuntimeModule`**: a deterministic **FSM-driven lifecycle** (CREATED →
  INITIALIZING → READY → RUNNING → STOPPING → STOPPED, FAILED) with `initialize/
  start/stop/shutdown` + overridable `on_*` hooks; `health()`; `ModuleMetrics`;
  `ModuleConfig`; dependency injection (`add_dependency`/`require`); a structured
  logger; error **isolation** (`isolate`); idempotent graceful **shutdown**. A
  failing hook routes to FAILED and raises `ModuleError`.
- **`BusConsumer(RuntimeModule)`**: the one reusable Event-Bus integration —
  `attach/detach`, `subscribe`, `publish(type, data, *, session_id, source, …)`,
  isolated `_dispatch` → `handle_event`. Because it publishes **standard
  `core.events.Event` envelopes to the shared bus, Replay records and the Gateway
  streams its events automatically** (AR-30) — no change to either. Cross-session by
  design (per-event `session_id`, unlike the session-bound `EventPublisher`).

### 3. Content event vocabulary (`core.events`, defined only)
Additive `EventType` members (input/transcript, memory, prompt, knowledge, context,
reasoning/model, tool, workflow/agent, cost) + `EventSource` members (interaction,
memory, prompt, knowledge, context, reasoning, tool, mcp, workflow, agent, model,
metering). **No producer emits these yet** — they are the shared vocabulary the
Epic-5 modules publish through. This unblocks Memory/Knowledge/Context, which need
conversational content on the bus.

### 4. `core/ports/` — dependency-inversion interfaces
`MemoryPort`, `PromptPort`, `KnowledgePort`, `ContextPort`, `ReasoningPort`,
`ToolPort`, `WorkflowPort`, `AgentPort` + neutral DTOs. Each future runtime
*implements* its port; consumers depend on the port in `core`, **never on another
runtime package** — so no runtime imports another and the graph stays acyclic.
Interfaces only; shapes provisional until each module lands.

### 5. `core/policies.py` — generic policy value objects
`RankingPolicy`, `SelectionPolicy`, `RetryPolicy`, `TimeoutPolicy`, `BudgetPolicy`,
`CompressionPolicy`, `ValidationPolicy` — frozen, validated at construction, with
tiny generic helpers (`RetryPolicy.delay_for`, `BudgetPolicy.fits`, …). **No business
rules** — the module supplies the domain.

### 6. Storage capability contracts (`core/providers/stores.py`, interfaces only)
`ObjectStore`, `DocumentStore`, `MetadataStore`, `VectorStore` (+ `VectorMatch`),
`Embedder`. Provider-neutral persistence seams (S3/Postgres/pgvector/… implement them
in `providers/`, resolved via the registry). **No implementation** — nothing couples
to a database.

### 7. `telemetry/` — observability foundation (stdlib only)
`Telemetry` (counters/gauges/histograms/timers; structurally satisfies
`core.module.Meter`), `Tracer`/`Span` (with replay `correlation_id`),
`StructuredLogger`, and `MetricsExporter`/`SpanExporter` **extension points** for
OpenTelemetry/Prometheus. **No exporters.** A floor package: imports nothing above it,
so `core` uses it via the `Meter` protocol without importing it.

### 8. `tenant/` — multi-tenancy foundation (interfaces only)
`TenantContext`/`OrganizationContext` value objects + `Authorizer` (RBAC),
`QuotaEnforcer`, `AuditSink`, `TenantPolicy` **interfaces**. **No implementation, no
authentication.** The Context Runtime will be the single isolation choke point.

## Dependency graph (acyclic, verified by test)
```
              ┌──────────────────────── core ────────────────────────┐
              │ events · errors · ids · state                         │
              │ fsm/ · module/ · ports/ · policies.py                 │
              │ providers/ (capability + store contracts)             │
              └───────────────────────────────────────────────────────┘
   floor utils (no upward imports):  telemetry/     tenant/
                                        ▲               ▲
   modules use core.module.Meter (structural) ─────────┘ (no core→telemetry edge)
   providers/  events/  runtime/  gateway/  replay/   → depend on core (unchanged)
   (future) memory/ prompt/ knowledge/ context/ … → core + core.ports + bus only
```
`core` imports nothing above it; `telemetry`/`tenant` import nothing above them (not
even `core`) — dependency inversion keeps everything acyclic (a test enforces the
rules).

## Ownership
The foundation owns **mechanics only** — FSM, module lifecycle, bus integration,
policies, telemetry, contracts. No domain logic, no AI, no persistence, no bus
changes. Each future module owns its concern and reuses these.

## Determinism & isolation (carried invariants)
Injected clocks everywhere; FSM replay is pure; `Telemetry` is deterministic under a
fake clock; BusConsumer dispatch is isolated (a handler failure is counted, never
raised into the bus/Runtime). 288 tests pass; the 271 prior are unchanged.

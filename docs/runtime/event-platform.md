# Runtime Component Spec — Internal Event Bus (Event Platform)

| | |
|---|---|
| **Component** | `events` (`InProcessEventBus`, `EventDispatcher`, `EventRegistry`, `EventPublisher`, `EventSubscriber`) + `core.events` (`Event`/`EventEnvelope`, `EventType`, `EventSource`, `EventMetadata`, `EventBus`) + `runtime.event_bridge` · **Status:** Implemented (M4.1) |
| **Format** | Constitution §A5/§D4 · ADR-0004/0006 · component spec §A11.3 |
| **Milestone** | M4.1 — Internal Event Bus Foundation (2026-07-17) · **opens Epic 4** |

## Responsibility (single)
Own **event delivery** — the single internal communication mechanism for runtime
facts. Producers publish typed `Event` envelopes; the bus fans them out to matching
subscribers deterministically and in order. It owns **no runtime state** and knows
nothing about who consumes an event. M4.1 is **in-process only** — no external
transports (WebSocket/SSE/REST streaming/SDK), no durable persistence, no
distributed broker, no analytics (those are M4.2+ and later epics).

## Event model (`core.events`)
`Event` (aliased `EventEnvelope`) — one frozen, slotted envelope for every fact:
| Field | Meaning |
|---|---|
| `id` | globally unique (uuid4-hex) |
| `type` | `EventType` (dotted, versioned vocabulary) |
| `ts` | runtime clock, seconds |
| `seq` | strict monotonic dispatch order (assigned by the bus) |
| `session_id` | the owning session |
| `source` | `EventSource` — the producing subsystem (never the consumer) |
| `org_id` | tenant |
| `conversation_id` | when known (the session is the conversation, DL-0007) |
| `correlation_id` | cross-event correlation |
| `turn` | conversation turn index, when known |
| `data` | versioned payload (dict) |
| `metadata` | free-form, non-payload (dict) |

Schema evolution: new `EventType`/`EventSource` members and new envelope fields are
**additive** (AR-32); payload schemas are versioned in `data`. `EventMetadata` is
the publisher's binding context (session/source/org/conversation/correlation),
stamped onto every event it emits so producers only supply `(type, payload)`.

### Event types (M4.1 vocabulary)
`runtime.{started,stopped,error}` · `session.{started,ended}` ·
`turn.{started,completed,cancelled}` · `speech.{started,stopped}` ·
`endpoint.{detected,cancelled}` · `interrupt.{detected,confirmed,cancelled}` ·
`assistant.{started_speaking,stopped_speaking}` · `smart_turn.decision` ·
`provider.{connected,disconnected}`. **One producer per type** (AR-2).

## Architecture (`events/`)
- **`EventRegistry`** — insertion-ordered subscriber table (ordered delivery);
  rejects registering the same handler twice (`DuplicateSubscription`); `remove`
  is idempotent; filters by `EventType`.
- **`EventDispatcher`** — fan-out with **per-subscriber isolation**: a raising
  subscriber is caught + logged (optional `on_error` hook) and never stops the
  others or corrupts the bus.
- **`InProcessEventBus`** (implements `core.events.EventBus`) — composes the
  registry + dispatcher + a FIFO queue + a monotonic `seq` counter + a reentrancy
  guard. `publish`/`subscribe`/`unsubscribe`/`shutdown` + `publisher()`/`subscriber()`.
- **`EventPublisher`** — a source-bound publish handle; stamps identity/ordering/
  context so a producer never builds a full envelope or knows its consumers.
- **`EventSubscriber`** — a self-unsubscribing subscription handle.

## Publish / subscribe + delivery guarantees
Deterministic, synchronous, **run-to-completion** dispatch:
- `publish` stamps an authoritative monotonic `seq`, appends to the FIFO queue, and
  — if a dispatch is not already running — drains the queue to completion, then
  returns the stamped envelope.
- **Ordered**: delivery follows `seq`, which follows publish order; subscribers are
  invoked in registration order.
- **Exactly-once (in-process)**: each event is delivered once to each matching
  subscriber.
- **Subscriber isolation**: no subscriber can block, break, or observe another.
- **Recursive-publish protection**: a subscriber that publishes re-entrantly is
  **queued** (never recursively dispatched) — the ongoing drain picks it up in
  order. Deep reentrant chains run iteratively (no stack growth; tested to 500).
- **Deterministic replay**: identical publish sequences yield identical delivery
  sequences (pure, ordered, no scheduling).

## Runtime integration (engines stay decoupled)
Engines keep their existing sync `on_event` observer hook — the extension point
each engine's `events.py` was built with ("a bridge can later forward these into
`core.events`"). The **`runtime.event_bridge.RuntimeEventBridge`** (composition
layer) maps each native engine event → an `Event` envelope on the injected bus:
Turn Engine → `TurnStarted/Completed/Cancelled` + `AssistantStarted/StoppedSpeaking`;
Voice Activity → `SpeechStarted/Stopped`; Endpoint → `EndpointDetected/Cancelled`;
Interrupt → `InterruptDetected/Confirmed/Cancelled`; Smart Turn →
`SmartTurnDecision`. The Orchestrator publishes lifecycle (`RuntimeStarted`,
`SessionStarted`, provider connect/disconnect, `SessionEnded`, `RuntimeStopped`,
`RuntimeError`) and shuts the bus down on session end. **No engine imports the bus
or knows its consumers** (AR-30); ownership is unchanged.

## Ownership (unchanged)
```
EventBus → event delivery (owns no runtime state)
TurnEngine / VoiceActivity / Endpoint / Interrupt / SmartTurn → their own decisions
RuntimePipeline → execution · ProviderRegistry → provider resolution
```
The bridge lives in `runtime/` (it reads engine decision enums); the bus lives in
`events/` and is injected. **`events/` never imports `runtime`/`api`** (AR — the M1
cycle fix) — a clean DAG: `core ← events ← runtime`.

## Failure handling (never corrupts the bus)
- **subscriber failure** → isolated + logged (dispatcher); the bus continues.
- **duplicate registration** → `DuplicateSubscription` (runtime-safe rejection).
- **invalid event** (non-`EventType`) → `InvalidEvent` at publish.
- **use-after-shutdown** → `publish` drops (logged, no raise); `subscribe` raises
  `EventBusShutdown`.
- **recursive publish** → queued, never recursive.
- **shutdown during dispatch** → the drain loop stops between events; the queue is
  cleared; subscribers are dropped. All bus errors root in `core.errors.PlatformError`.

## Performance posture (architectural readiness)
Minimal allocations (one `replace` per publish, dict-ordered registry, `deque`
FIFO); lock-free single-threaded run-to-completion (deterministic; a thread-safe or
async-decoupled variant is additive behind the same contract). Designed for
10k+ events/min in-process; **transport independence** is guaranteed by the
`core.events.EventBus` contract (a distributed broker implements it later, ADR-0006).
Not optimized prematurely.

## Design note (DL-0017)
`core.events` was unreleased M1 scaffolding (no consumers) — refined at M4.1 to the
execution-ready shape (additive envelope fields + a deterministic **synchronous**
`EventBus` contract), a refinement not a versioned break (DL-0009 precedent).
Synchronous run-to-completion is the deterministic in-process choice; AR-31's
"producers never block on consumers" (async decoupling / durability) is a
distributed-delivery concern satisfied by the M4.2 **outbox** — and, even now, a
subscriber that publishes re-entrantly is never blocked (its event is queued).

## Extension points for M4.2+
Durable **outbox** + retry/DLQ (persist before dispatch) · WS/SSE event gateway
(subscribe → serialize → stream) · mid-call control API · distributed broker
(implement `EventBus`) · metrics/analytics subscribers · the SDK event surface. All
consume the same envelope + contract — no producer or engine changes.

## Revision note — Epic 10 (M10.1, 2026-08-18)

The **durable outbox + retry/DLQ** named above is ratified by
[ADR-0032](../adr/ADR-0032-event-durability-and-delivery-semantics.md) (delivery *semantics*:
at-least-once, append-only, one event identity and one sequence authority, transactional outbox,
bounded retry ladder, distinct DLQ and quarantine, explicit retention) and
[ADR-0034](../adr/ADR-0034-durable-storage-seam-core-only-packages.md) (storage *structure*: a
scoped `events/persistence/**` sub-package, its own `Database`, migrations from `0034`, `evt_*`
tables). The `metric.*` producer side and namespace partition are
[ADR-0033](../adr/ADR-0033-metric-stream-seam.md).

**Nothing on this page changes.** DL-0017's constraint is carried forward verbatim — the durable
implementation sits **behind the same `core.events.EventBus` contract with no producer change** —
so the envelope, the delivery model, the ordering and reentrancy guarantees, and every statement
above remain accurate. Of the extension points listed, Epic 10 takes **only** the durable outbox +
retry/DLQ; the **WS/SSE event gateway, mid-call control API, distributed broker and SDK event
surface stay deferred** (Epic 11/Epic 12 and DL-0017).

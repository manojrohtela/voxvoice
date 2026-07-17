# Component Spec — Live Event Gateway

| | |
|---|---|
| **Component** | `gateway` (`LiveEventGateway`, `ConnectionManager`, `SubscriptionManager`, `EventChannel`, `EventSerializer`, `EventStream`, `ClientSession`, `GatewayMetrics`, `WebSocketTransport`/`SSETransport`) · **Status:** Implemented (M4.2) |
| **Format** | Constitution §A5 · ADR-0004/0006 · component spec §A11.3 |
| **Milestone** | M4.2 — Live Event Gateway (2026-07-17) |

## Responsibility (single)
Own **external event delivery** — stream Internal Event Bus events to external
clients over pluggable transports. It does **not** own Runtime state and does **not**
create events. It subscribes to the bus through the public `core.events.EventBus`
contract, so the **Runtime never knows a gateway, client, transport, dashboard, or
frontend exists** and continues publishing exactly as M4.1. M4.2 is delivery only —
no analytics, recording, replay, SDK, or durable/broker queues (all non-goals).

## Architecture (`gateway/`)
```
Internal Event Bus ──subscribe(_on_event)──► LiveEventGateway
                                               │  serialize once (EventSerializer)
                                               ▼
                        SubscriptionManager.matching(clients, event)   (deduped)
                                               │  offer (non-blocking)
                        ┌──────────────────────┼───────────────────────┐
                        ▼                       ▼                       ▼
                  ClientSession           ClientSession           ClientSession
                   EventStream             EventStream             EventStream   (bounded, backpressure)
                        │                       │                       │  async drain
                  WebSocketTransport       SSETransport            WebSocketTransport
                        ▼                       ▼                       ▼
                     client                  client                  client
```
- **`LiveEventGateway`** — composes the managers + serializer + metrics; `attach(bus)`
  / `detach_all`, `connect`/`disconnect`, `subscribe`/`unsubscribe`, `shutdown`; the
  bus subscriber `_on_event`.
- **`ConnectionManager`** — client lifecycle (connect/disconnect, auth hook,
  heartbeat/idle sweep, metrics).
- **`SubscriptionManager`** — per-client channels (subscribe/unsubscribe, authz hook,
  duplicate detection) + `matching` (deduped).
- **`EventChannel` / `Scope`** — a subscription unit: scope (session/conversation/
  agent/org/all) + optional `EventType` filter.
- **`EventSerializer`** — versioned JSON encoder (transport-independent).
- **`EventStream`** — one client's bounded, ordered, non-blocking delivery buffer.
- **`ClientSession`** — identity + lifecycle + stream + subscriptions.
- **`GatewayMetrics`** — connection/delivery counters.
- **`WebSocketTransport` / `SSETransport`** — wire-framing adapters.

## Connection management
`ConnectionManager.connect` runs an **authentication hook** (extension point:
`(client_id, context) -> auth | None`; `None` rejects) and issues a `ClientSession`
with a bounded stream. Lifecycle states: `CONNECTING → CONNECTED → CLOSING → CLOSED`.
**Heartbeat / idle** are pure predicates over an injected clock (`session.is_idle`,
`ConnectionManager.sweep_idle`) so cadence is transport-driven and deterministic;
transports expose `heartbeat(conn)` (WS ping message / SSE `: ping` comment). Ping/
pong liveness updates `last_seen` via `touch`. Reconnect: SSE emits monotonic `id:`
lines for `Last-Event-ID`; the client re-subscribes on reconnect. **Authentication is
not implemented — only the hook** (M4.2 non-goal).

## Subscription model
A client holds many **channels**; an event is delivered **once** if any channel
matches (dedup). Scopes: `SESSION`, `CONVERSATION`, `ORG`, `AGENT` (from
`metadata.agent_id` when the producer supplies it — extensible), and `ALL`
(wildcard), each with an optional `EventType` set. `subscribe` runs an
**authorization hook** (extension point) and rejects exact duplicates
(`DuplicateSubscription`). New scopes/filters are additive — no producer/bus/Runtime
change.

## Transport design
Both transports consume **exactly the same** serialized `EventEnvelope` (from the
`EventSerializer`) and differ only in wire framing: WebSocket sends the JSON text
as-is; SSE frames `id: <n>\ndata: <json>\n\n`. Each adapter drains one client's
`EventStream` to a transport-agnostic `TransportConnection` (async `send`/`close`) —
a real ASGI socket or an in-memory test double plugs in identically. Future transports
(gRPC, HTTP streaming, MQTT) add an adapter here with **no change** to the gateway,
bus, or Runtime. A per-connection send failure ends that drain loop and closes the
connection (reported via `on_close`); it never reaches the Runtime.

## Event delivery + guarantees
- **Ordered**: FIFO per client, monotonic bus `seq` preserved.
- **Non-blocking for the Runtime**: `_on_event` serializes once and `offer`s to each
  client's bounded buffer — it never awaits, never does I/O.
- **Backpressure / slow-consumer protection**: bounded `EventStream` with a policy —
  `DROP_OLDEST` (default; keeps freshest, counts drops), `DROP_NEWEST`, or
  `DISCONNECT` (drops the slow client, `slow_disconnects++`).
- **Connection isolation**: one client's overflow/failure never affects another or
  the Runtime.
- **Multiple clients**, **no duplication** (dedup per client).
- **Graceful shutdown**: `shutdown` detaches from every bus and closes all clients.

## Runtime integration
`LiveEventGateway.attach(bus)` subscribes to an Internal Event Bus (it may attach to
many — e.g. one per session). The composition root (or a future session service)
attaches the gateway to each session's `orchestrator.event_bus`. **The Runtime is
never modified and never initiates this** — attachment uses the bus's public API, so
the Runtime never knows WebSocket/SSE/clients/dashboard/frontend exist. (Verified:
`runtime/`, `core/`, `events/` are byte-for-byte unchanged from M4.1.)

## Ownership (explicit, unchanged)
```
Runtime → execution · Internal Event Bus → internal delivery
LiveEventGateway → external streaming · ConnectionManager → client lifecycle
SubscriptionManager → client subscriptions · EventSerializer → encoding
```

## Failure handling (no client failure reaches the Runtime)
- **serialization failure** → metric + skip (never raises into the bus).
- **subscriber/offer failure** → isolated; the client is disconnected, others +
  Runtime unaffected.
- **slow consumer** → backpressure policy (drop or disconnect).
- **connection lost / network error** → the transport closes that connection; drop
  the client.
- **invalid / duplicate subscription** → `InvalidSubscription` / `DuplicateSubscription`.
- **use-after-shutdown** → `GatewayShutdown`.
The bus already isolates subscribers; `_on_event` double-guards so a gateway bug can
never corrupt the bus or block the producer. All gateway errors root in
`core.errors.PlatformError`.

## Boundaries & isolation
`gateway/` imports `core` (`core.events` envelope + `EventBus` contract) and the
stdlib — **never** `runtime`, `api`, or `events/` (verified by AST scan). A future
`api` layer mounts the transports onto ASGI routes; the gateway core stays transport-
and network-independent. Clean DAG: `core ← gateway` (and `core ← events ← runtime`,
disjoint from the gateway).

## Security (extension points only — not implemented, M4.2 non-goals)
Authentication (connect hook), authorization (subscribe hook), and — as documented
extension seams — RBAC, rate limiting, audit logging, tenant isolation. Tenant
isolation is naturally expressible today via `ORG` scope + an authz hook.

## Performance posture (architectural readiness)
Serialize-once per event, O(clients) non-blocking offers, bounded per-client buffers
(slow clients can't grow memory unboundedly), no Runtime-thread blocking. Designed
for ~10k concurrent connections / ~100k events/min in-process; a scope/type
**subscription index** (replacing the linear match) and async/worker fan-out are
additive optimizations (M4.3+). Not optimized prematurely.

## Extension points for M4.3+
Durable delivery (persist before offer) · a subscription index · binary/compressed
serialization (`EventSerializer.encode`/`compress`) · real ASGI WS/SSE routes in
`api/` · gRPC/HTTP-streaming/MQTT transports · the security hooks' implementations ·
a metrics exporter · the client SDK. All additive — the envelope, bus, and Runtime
are untouched.

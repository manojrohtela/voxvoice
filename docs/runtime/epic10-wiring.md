# Runtime → Epic 10 Spine, Telemetry and Metering

**Status:** Active v1.0 (2026-09-18) · **Owner:** Technical Owner · **Milestone:** M11.12
**Authority:** [ADR-0032](../adr/ADR-0032-durable-event-foundation.md) (durable events), [ADR-0033](../adr/ADR-0033-runtime-observability.md) MS8, [ADR-0035](../adr/ADR-0035-durable-write-semantics.md) DW2/DW4, [ADR-0038](../adr/ADR-0038-runtime-latency-taps-and-the-metric-turn-producer.md), [ADR-0039](../adr/ADR-0039-metric-stream-and-metering.md) MB1–MB10, [ADR-0023](../adr/ADR-0023-metering-and-billing-boundary.md) M2, [ADR-0052](../adr/ADR-0052-certification-is-historical-not-a-permanent-tree-lock.md); AR-2, AR-31, AR-32, AR-42
**Decisions:** DL-0201 (the plane), DL-0202 (vocabulary and ownership), DL-0203 (metering boundary), DL-0204 (limitations and risks)

Epic 10 built a durable event log, a `metric.turn` producer and a metering buffer — and then
stopped, because nothing in the runtime ever supplied them. M11.12 supplies them. **It adds no
capability, no EventType, no dependency and no mechanism**; every part below was built and frozen
in Epic 10 and had simply never been given a producer.

## The connection

```
ConversationOrchestrator (one InProcessEventBus per session)
        │
        ├── durable=        SqlDurableEventSpine      → durable event log   (events/)
        ├── metrics.subscribe(bus)  MetricTurnBuffer  → Billing pulls later (api/metering/)
        └── telemetry(publisher)    TurnLatencyTap    → metric.turn         (observability/)
```

`ObservabilityPlane` (in `runtime/observability_plane.py`) is the whole seam: a frozen value
carrying references, consulted by the Orchestrator when it builds the session's bus.

**Layer discipline.** `runtime/` may not import `events`, `billing` or `observability`, so the
plane names none of them — the attachments are structural Protocols and the **composition root**
supplies the concrete objects, exactly as `LatencyObserver` already does for `observability/`.

```python
controller = bootstrap.build_session_controller(
    observability=bootstrap.build_observability_plane(
        durable=SqlDurableEventSpine(database),   # optional
        metrics=MetricTurnBuffer(),               # optional
        telemetry=True,                           # default
    ),
)
```

**Default: `UNWIRED`.** Nothing connected — precisely the behaviour of every milestone through
M11.11. Each attachment is independently optional; `MetricStreamPort`'s own rule governs the
absent case: *"An unwired port means no ingestion, not invented usage."*

## Event vocabulary — no new EventType

Every runtime fact already had a member of the frozen vocabulary. The mapping is asserted by test,
not merely described:

| Runtime fact | EventType | Producer |
|---|---|---|
| runtime start/stop | `runtime.started` / `runtime.stopped` | SessionWorker / Orchestrator |
| session lifecycle | `session.started` / `session.ended` | Orchestrator |
| transport reality | `transport.connected` / `media_ready` / `disconnected` / `failed` | transport, via the bridge (AR-6) |
| turn lifecycle | `turn.started` / `turn.completed` / `turn.cancelled` | Turn Engine |
| speech | `speech.started` / `speech.stopped` | Voice Activity |
| endpoint | `endpoint.detected` / `endpoint.cancelled` | Endpoint Intelligence |
| interrupt / barge-in | `interrupt.detected` / `confirmed` / `cancelled` | Interrupt Intelligence |
| assistant audio | `assistant.started_speaking` / `stopped_speaking` | Runtime Pipeline |
| provider connect/fail | `provider.connected` / `provider.disconnected` | Provider Registry / Runtime |
| per-turn latency | `metric.turn` | **`observability/` only** (AR-2, MS8) |

Barge-in uses the existing `interrupt.*` vocabulary — no new event was invented for it.

## Ownership, correlation and ordering

**One producer per semantic event** (AR-2). The `RuntimeEventBridge` maps each engine's native
observer hook to exactly one `EventType` from one `EventSource`; no fact is emitted from two
layers. `metric.turn` has a single producer, `observability/`, and the runtime never constructs a
metric payload.

**Correlation** reuses the existing envelope fields — `session_id`, `conversation_id`, `turn` —
stamped by the bus-bound `EventPublisher`. No parallel ID scheme. Two concurrent sessions sharing
one durable log remain fully separable by correlation alone (asserted).

**Ordering** is the bus's `Event.seq`, a strict monotonic dispatch counter — **not** wall-clock
time. The durable log preserves it verbatim (ADR-0032 ED3).

## Idempotency

Duplicates are prevented **at the owning lifecycle boundary**, not by dropping at the persistence
layer: `session.ended` is emitted once by the one path that ends a session, so repeated `stop()`
produces exactly one record. Epic 10's own `append` is additionally idempotent on `Event.id`, and
Billing's ingestion is idempotent on `(event_id, meter_code)` — three independent layers, none of
them a substitute for not producing the duplicate.

## Telemetry and `metric.turn`

`TurnLatencyTap` is built per session from a publisher bound to `EventSource.OBSERVABILITY`, and
handed to the pipeline as its `LatencyObserver` — a parameter that existed since M10.9 and that
**nothing had ever passed**. The chain, the key names and the payload are M11.8's, unchanged.

`tts_first_audio` and `first_audio_out` mark real events (M11.10/M11.11); a silent response stamps
neither, and the buffer counts that as `missing_measurement` rather than inventing a magnitude.

## Metering — a pull boundary, not a push

Billing's only inbound path is the frozen `MetricStreamPort`, and it is **pull-shaped**: *"Billing
never subscribes to a runtime bus, never opens a connection, and never holds a runtime resource…
There is no emit, publish, or write method — Billing is structurally incapable of producing an
operational metric."*

So the runtime **does not call Billing at all**. It publishes `metric.turn`; the platform-owned
`MetricTurnBuffer` (built at M10.10, outside `billing/`) subscribes itself for that type only,
decodes to Billing's `MetricSignal`, and holds it until Billing pulls.

**No pricing, no rates, no money.** M11.12 records measurements. Rating, invoicing and the ledger
are Epic 9's and are untouched.

### Source of truth for each quantity

| Quantity | Authoritative producer |
|---|---|
| per-turn latency (`voice_to_voice_ms`) | `observability/turn_latency.py` |
| audio duration | `AudioFrame`'s own declared format — one clock, no frame counting |
| dropped inbound audio | the existing `audio_dropped` degradation (M11.7) |
| session / turn identity | the runtime lifecycle that owns the boundary |

One turn produces exactly one metering signal; two turns produce two with distinct idempotency
keys; a second `drain()` returns nothing. **Provider token/character usage is not metered** — see
the risks below.

## Failure semantics

| Failure | Behaviour |
|---|---|
| durable write fails | the bus catches it, declines to dispatch, and calls `on_durable_error` **exactly once**; it never raises into the producer (AR-31, ADR-0035 DW4). The conversation continues — asserted. |
| metering sink raises | isolated by the bus's subscriber dispatch; the conversation continues — asserted. |
| telemetry raises | isolated by the pipeline's tap guard (ADR-0038 TP9); the conversation continues — asserted. |
| spine unavailable | Epic 10's existing outbox/ladder semantics; M11.12 adds no retry strategy and no second store. |

**Telemetry is never on the audio hot path.** No durable write happens per audio frame — the
durable stream is lifecycle-shaped, asserted directly (a one-turn session produces far fewer
events than it does frames).

## Boundedness

The only new buffer is Epic 10's own `MetricTurnBuffer`, bounded at its ratified capacity with
explicit drop accounting (MB3). The runtime adds no queue, no accumulator and no growing list; the
per-session subscription handle is released at session close, so a process-wide buffer cannot
accumulate handlers on dead sessions' buses (asserted, and a mutant that skips the release dies).

## Privacy and data minimization

Epic 10 stores **metadata, not content**, and M11.12 preserves that exactly. No raw audio, no
transcript, no LLM response and no TTS text is persisted — asserted with a conversation whose
transcript and reply contain marker phrases that must not appear in any event.

No credential, API key, authorization header or vendor SDK object reaches an event, telemetry or
metering payload. Provider **names** do appear (`{"capability": "stt", "provider": "deepgram"}`) —
that is the pre-existing, deliberate design of `provider.connected`: an opaque capability selector
is neutral data, not a vendor type (AR-21). Every payload value is asserted to be a builtin.

## Inspecting a session's trail

```python
spine.read_session(session_id)          # every durable envelope, in seq order
[e for e in … if e.type is EventType.METRIC_TURN]   # the per-turn latency chain
buffer.drain(org_id)                    # what Billing would pull next
buffer.counters                         # dropped / malformed / missing_measurement / unattributed
```

## What M11.12 did not do

No Epic 10 redesign, no certification or freeze-manifest change, no rebaselining, no new
EventType, no new dependency, no second bus/telemetry/metering/billing system, no pricing, no
performance certification, no resilience work, and no M11.13 evaluation work.

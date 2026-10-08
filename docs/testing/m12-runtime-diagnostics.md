# Playground Runtime Diagnostics

| | |
|---|---|
| **Status** | Active v1.0 (M12.14) |
| **Date** | 2026-10-07 |
| **Code** | `platform/api/console/diagnostics/service.py` · `routers/diagnostics.py` · `console/src/features/playground/{diagnostics.ts,DiagnosticsPanel.tsx,LiveConversation.tsx}` |
| **Tests** | `platform/tests/test_console_diagnostics.py` (30) · `console/src/features/playground/Diagnostics.test.tsx` (27) · `e2e/browser.mjs` |
| **Related** | [ADR-0038](../adr/ADR-0038-runtime-latency-taps-and-the-metric-turn-producer.md) (TP10) · [ADR-0055](../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) · [LLD §5](../architecture/epic12/m12-lld.md) · [Security model](../security/m12-security-model.md) |

## 1. What this is

The Playground now shows what the runtime is doing: the conversation as it is recorded, the
runtime's current state, and a chronological log of every event the session produced.

**It adds no event, no store, no bus and no state machine.** Epic 10's durable log already holds
every event the runtime publishes, in `seq` order, and M11's transcript sink already holds every
finalized line. This milestone added a **projection** of those two and one endpoint to stream it.

```
runtime ─► Event Bus ─► Epic 10 durable log ──┐
                                              ├─► diagnostics projection ─► SSE ─► Playground
runtime ─► TranscriptSink ─► transcript rows ─┘
```

The Playground is a consumer of runtime truth. It is never the source.

## 2. Why there is no "live versus durable" distinction

Because there is one read. A browser watching a live call asks for everything after the `seq` it
has seen; a browser opening a finished call asks from 0. Both run the same query against the same
durable rows. A dropped connection therefore loses nothing — reconnecting resumes from the last
`seq` — and what is on screen during the call is what is on screen after it, by construction
rather than by reconciliation.

## 3. Event coverage — complete, partial, and deliberately absent

### Shown (every one is a real event the runtime publishes)

| Category | Events |
|---|---|
| `SESSION` | `session.started`, `session.ended` |
| `RUNTIME` | `runtime.started`, `runtime.stopped`, `runtime.error` |
| `TRANSPORT` | `transport.connected`, `transport.media_ready`, `transport.disconnected`, `transport.failed` |
| `VAD` | `speech.started`, `speech.stopped` |
| `ENDPOINT` | `endpoint.detected`, `endpoint.cancelled`, `smart_turn.decision` |
| `TURN` | `turn.started`, `turn.completed`, `turn.cancelled` |
| `SPEECH` | `assistant.started_speaking`, `assistant.stopped_speaking` |
| `INTERRUPT` | `interrupt.detected`, `interrupt.confirmed`, `interrupt.cancelled` |
| `PROVIDER` | `provider.connected`, `provider.disconnected` (including declared degradation) |
| `LATENCY` | `metric.turn` — per-stage durations and voice-to-voice |

### Partial, and why

**Stage boundaries are not events.** There is no `stt.started` or `llm.completed`, and M12.14 did
not add one. ADR-0038 **TP10** rules that stage boundaries are captured on the hot path and
**published once, after the turn is terminal**, because `InProcessEventBus.publish()` is
synchronous run-to-completion: publishing at six boundaries would run every subscriber inside the
voice-to-voice budget, six times per turn. Emitting them would also mean modifying Epic 11 runtime
code whose latency certification depends on that ruling.

So stage visibility arrives as the runtime produces it: **per-stage durations on `metric.turn`, as
each turn completes** — `endpoint`, `stt`, `llm`, `tts`, `egress`, plus voice-to-voice. A stage the
runtime could not measure is **absent**, never rendered as 0 (E11-10).

This is the one place where "complete diagnostics" means *complete within the existing event
architecture*, not per-boundary tracing. Changing it is an ADR decision, not an implementation one.

### Deliberately server-only

| Not shown | Why |
|---|---|
| Interim (non-final) transcript | It reaches `on_transcript` with `is_final=False` and the Control-Plane sink drops it; nothing persists it. Surfacing it would need a second transcript path that could diverge from the persisted conversation. The UI says so rather than leaving a gap. |
| Provider names | `capability` ("stt", "tts") carries the diagnostic value. The console tells the browser nothing about which vendor is configured — `/v1/console/meta` does not either. |
| Raw payloads, stack traces, SQL, environment | Never projected; see §5. |
| Platform events outside a session's vocabulary | A type with no place in a conversation is not rendered as "unknown". |

## 4. Runtime state

Derived from the event stream by a pure function — **not** a second state machine. It holds
nothing and decides nothing; the latest event that implies a state wins.

`CONNECTING` · `CONNECTED` · `LISTENING` · `USER_SPEAKING` · `PROCESSING` ·
`ASSISTANT_SPEAKING` · `ENDING` · `ENDED` · `ERROR`

The names are the runtime's own: `TurnPhase.LISTENING` and `TurnPhase.ASSISTANT_SPEAKING` are the
Turn Engine's vocabulary, and the rest are transport and session facts.

## 5. Redaction

`data` is **never passed through**. Each event type names the fields it may contribute
(`SAFE_FIELDS`); every surviving value is re-checked by `_safe_value`, which accepts only scalars,
truncates at 200 characters, and drops anything secret-shaped. A payload that gains a field later
is excluded until someone adds it here deliberately — the allowlist fails closed.

Tested: allowlist enforcement, secret-shaped values (`sk-…`, `vf_…`, `Bearer …`, long opaque
tokens), nested payloads, over-long values, and provider-name suppression.

## 6. Security boundary

| | |
|---|---|
| **Authentication** | The console's own `HttpOnly` cookie. SSE is a plain `GET`, so no new credential enters the browser. |
| **Authorization** | `developer` and above — the Playground's rule, not Conversations'. A `viewer` may read a conversation's transcript and timeline but is refused the live runtime stream (`403`). |
| **Tenant** | The session is resolved with the same org-scoped lookup Conversations uses: another organization's session is `404`, as it is there. |
| **CSRF** | Not applicable to a `GET`; the console header guard exempts safe methods, which is why `EventSource` works at all. |
| **Signalling token** | Not involved. It is single-purpose and belongs to the media path (S1). |

## 7. Failure behaviour

**A runtime failure** is recorded by the runtime as it always was, appears in the diagnostics with
`severity: error` and its safe reason, and raises a plain-language banner above the panel — *"The
runtime could not continue this call."* The implementation detail stays in the panel; the banner
never carries it. Session cleanup is unchanged.

**A diagnostics failure is not a call failure.** Diagnostics are an auxiliary observation
(DL-0013): if the stream drops, the panel says it is reconnecting and resumes from its last `seq`;
if `EventSource` cannot be constructed at all, the panel says diagnostics are unavailable and the
**call continues unaffected**. Both are tested.

## 8. Performance

| | |
|---|---|
| Delivery | **Server push over one persistent SSE connection** per open Playground: the browser makes a single `GET` and holds it. The browser never polls. The **server** re-reads the session's own rows every 400 ms — the durable log has no change feed — and pushes a frame only when there is something new, with a keepalive comment every 15 s. One request per session, not one per event. |
| Rendering | The panel keeps at most **300 rows** in the DOM and says what it is showing. Tested with 100, 500, 1,000 and 5,000 events: the DOM stays at 300. |
| Keepalive / bound | A comment frame every 15 s; one connection is capped at 30 min, after which the browser resumes from its `seq`. |
| Clearing | "Clear view" hides rows **in this view only**, and says so. It never deletes durable history. |

## 9. No ADR was added

This milestone makes no architectural decision that an ADR must record: it adds no event type, no
store, no bus, no state machine and no runtime change. It *applies* an existing one — ADR-0038
TP10 — and the one judgement it makes (SSE over WebSocket or the data channel) is a consequence of
constraints already ruled on: the stream is one-directional and read-only, and the signalling
token is reserved for the media path. The reasoning is recorded in `routers/diagnostics.py` and
in DL-0252.

## 10. Known limitations

| Limitation | Status |
|---|---|
| No per-boundary stage events | **By ruling** (ADR-0038 TP10). Per-turn durations instead. |
| No interim transcript | **By design** — nothing records it. |
| Polling interval, not push | The durable log has no change feed; 400 ms is well inside human perception for a voice call. |
| One process | The stream reads the database, so it works across workers — but the rendezvous registry is still in-process (R-E12-5, unchanged). |
| Not real-provider validated | The runtime evidence is the deterministic stack, as everywhere in M12. |
| No download/export of diagnostics | Not built: the data is already on screen and copyable, and an export is a product decision. |

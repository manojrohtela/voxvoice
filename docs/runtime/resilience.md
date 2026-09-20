# Runtime Resilience and the §A10 Degradation Ladder

**Status:** Active v1.0 (2026-09-19) · **Owner:** Technical Owner · **Milestone:** M11.14
**Authority:** Constitution v2 **§A10** (fault tolerance), **§A6.2**, **§A6.4**, **§A4.4** (the conversation state machine), **§A11.1.7**; [ADR-0046](../adr/ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md) **FT-D1–FT-D9**, [ADR-0045](../adr/ADR-0045-streaming-adapter-pattern-and-vad-execution.md) **SD4**, [ADR-0047](../adr/ADR-0047-audio-driven-endpointing-and-metric-turn.md) **ME8**, [ADR-0048](../adr/ADR-0048-llm-tts-clause-pipelining-and-playback.md) **PB6**, [ADR-0044](../adr/ADR-0044-audio-representation-resampling-and-media-buffers.md) **AD6/AD7**, [ADR-0035](../adr/ADR-0035-durable-write-interposition-on-the-event-bus.md) **DW4**, [ADR-0038](../adr/ADR-0038-runtime-latency-taps-and-the-metric-turn-producer.md) **TP9**; **AR-2, AR-6, AR-23, AR-24, AR-31, AR-41**; DL-0013
**Companion:** [Provider Fault-Tolerance Contract](../providers/fault-tolerance-contract.md) — what a plugin owes; this document is what the *session* does.
**Decisions:** DL-0208 (the ladder and the three defects), DL-0209 (failure scope), DL-0210 (limits)

> **This is not a claim that VoxVoice is resilient.** It is a record of what happens under
> injected failure, measured hermetically on one machine. No live vendor, no real network peer and
> no load were involved, and the milestones that would change that have not run.

## The ladder, quoted rather than invented

Constitution **§A10** defines it, and ADR-0046 **FT-D6** makes it the required behaviour:

> **failover → declared degraded mode → inform-the-caller policy → clean session failure with a
> full event trail.** Exhaustion is a clean failure, never a hang.

M11.14 implements and proves these four rungs. It does **not** rename, reorder or reinterpret
them.

| Rung | Entry condition | Mechanism | Allowed | Prohibited | Terminal? |
|---|---|---|---|---|---|
| **1 — failover** | a candidate failed and another is configured | `FailoverController`, walked by `ProviderGuard.call_with_failover` | move forward to the next untried candidate, once each | retry a failed candidate, sleep, back off | no |
| **2 — declared degraded mode** | a capability is reduced but the conversation can continue | `DegradationRecorder` (§A6.4, AR-24) | continue with less; **record** it | adapt invisibly; pretend the capability worked | no |
| **3 — inform the caller** | the turn cannot be answered | turn retirement + `provider.degraded` on the event seam | end the turn, let the caller speak again | fabricate an answer, a transcript or audio | no |
| **4 — clean session failure** | the session cannot continue | `ConversationOrchestrator.fail()` → ENDING → ENDED, providers released | terminate deterministically with a full event trail | hang, leak, or terminate silently | **yes** |

Exhaustion of rung 1 raises `ProvidersExhausted` — a typed terminus reached in finite time, never
a hang.

### What the runtime deploys today

Rung 1 is **mechanism-complete and unexercised in production composition**: the composition root
wires exactly one provider per capability, so no session has a chain to walk. The mechanism is
proved at the guard; the deployment that would use it does not exist yet. Rungs 2–4 run in every
session.

## Failure scope

The single most important design rule here, and the one the milestone's largest defect violated:

> **Do not invent process-wide recovery for a turn-local failure, and never let a session-local
> failure reach another session.**

| Scope | What it means | Examples |
|---|---|---|
| **frame** | one 20 ms frame is affected | inbound buffer overflow (DROP_OLDEST), one failed VAD inference |
| **utterance** | the current utterance is discarded | VAD outage during speech — **no** boundary is produced |
| **response / turn** | this answer does not happen; the caller may speak again | LLM failure, TTS failure, transport send failure, barge-in |
| **session** | the call ends deterministically | STT stream failure, media path broken, transport connect refused |
| **process** | never used by a media-path failure | — |

A session-scoped failure is a **clean** end: the state machine routes it (§A4.4 forbids
IN_CONVERSATION → FAILED, so a mid-call fault goes ENDING → ENDED with the cause in the reason),
providers are released, and the event trail carries what happened.

## The failure matrix

Populated from measured behaviour, not from intent. Every row has executable evidence in
[`tests/test_resilience.py`](../../platform/tests/test_resilience.py).

| Failure | Detection | Classification | Scope | Retry / failover | Degradation | Recovery | Terminal |
|---|---|---|---|---|---|---|---|
| Transport connect refused | `start()` raises | `ProviderUnavailable` | session | none | — (fails before a recorder exists) | none — session never starts | **yes** (startup) |
| Transport inbound broken | pump observes; `inbound()` raises | `ProviderStreamTruncated` | session | none | `transport:unavailable` | none | **yes** |
| Transport send fails | guarded `call` | `ProviderUnavailable` | response | none | `transport:unavailable` | next turn | no |
| Transport clean hangup | stream ends | not a failure | session | — | none | — | **yes** (completed) |
| STT open fails | guarded `stream` | `ProviderUnavailable` | session | none | recorded | none | **yes** |
| STT stream breaks | guarded `stream` | `ProviderStreamTruncated` | session | none | recorded | none | **yes** |
| STT cancelled | `CancelledError` | **not** a failure | — | never | none | — | — |
| VAD start fails | `start()` raises | auxiliary | session (continues) | none | `voice_activity:auxiliary_disabled` | runs untapped | no |
| VAD inference fails | tap catches | auxiliary | frame | none | `auxiliary_disabled` **per frame** (**R-48**) | next frame | no |
| VAD unavailable, sustained | — | auxiliary | utterance | none | as above | — | no |
| Endpoint has no verdict | no counter advances (ME8) | not a decision | utterance | — | via VAD | next utterance | no |
| LLM open fails | guarded `stream` | typed per vendor | response | none | `llm:unavailable` | next turn | no |
| LLM fails after first token | guarded `stream` | typed | response | **none** (**R-56**) | `llm:unavailable` | next turn | no |
| LLM timeout | guard deadline | `ProviderTimeout` | response | none | `llm:timeout` | next turn | no |
| TTS open fails | guarded `stream` | typed | response | none | `tts:unavailable` | next turn | no |
| TTS fails after first audio | guarded `stream` | typed | response | **none** (**R-61**) | `tts:unavailable` | next turn | no |
| TTS declares no streaming input | capability flag | not a failure | response | — | `tts:not_streaming` | per-clause synthesis | no |
| Playback consumer fails | sender task | `PlaybackState.FAILED` | response | none | via transport guard | next turn | no |
| Playback back-pressure | buffer full | not a failure | frame | — | none | producer waits | no |
| Inbound buffer overflow | queue full | not a failure | frame | — | `audio_dropped` | — | no |
| Barge-in | interrupt engine | not a failure | response | — | none | new turn | no |
| Provider chain exhausted | `call_with_failover` | `ProvidersExhausted` | depends on capability | chain walked once each | `exhausted` | per capability | depends |
| Telemetry tap raises | `_tap` catches (TP9) | swallowed by design | none | none | none | next tap | no |
| Durable sink raises | bus catches (AR-31, DW4) | `on_durable_error` | none | **NOT DEFINED** — no outbox drain in the media path | none | next event | no |
| Metering sink raises | dispatcher isolates | swallowed | none | none | none | next event | no |
| Session stopped repeatedly | terminal-state guard | not a failure | — | — | none | — | idempotent |

### Cells that are deliberately NOT DEFINED

- **Durable-sink recovery.** ADR-0036's outbox retry ladder and ADR-0037's recovery driver exist
  in Epic 10 and are **not** driven from a live session. A write that fails during a call is
  reported through `on_durable_error` and not re-attempted within that session. Defining this is
  Epic 10's business, not M11.14's, and inventing a second retry path here would be the duplicate
  mechanism §7 forbids.
- **Chain exhaustion scope.** It depends on which capability exhausted, and no deployed profile
  configures a chain, so there is no measured behaviour to record. Stating one would be fiction.

## Three defects this milestone found and fixed

Each was found by injecting a failure into a running session and asking what happened next.
None was visible from the unit tests that already covered the same components.

### 1. A failed turn made the session permanently deaf

**The worst of the three.** A response that failed skipped the endpoint reset *and* left the Turn
Engine parked in `ASSISTANT_THINKING` for a turn that would never complete. The next utterance
asked the engine to enter `ASSISTANT_THINKING` again; the engine correctly refused the illegal
transition; and every later turn in the call failed at its first transition — no transcript, no
audio, and no second degradation to explain it. **One vendor hiccup cost the whole call, silently.**

`RuntimePipeline._respond_once` now retires the turn deterministically — `TURN_CANCELLED`, the
terminal metric released, the endpoint reset — and the caller simply speaks again. Nothing is
retried and nothing is fabricated.

### 2. A broken media path was indistinguishable from a caller hanging up

The transport emitted `transport.failed` on the event seam — correctly, since M11.5 — and then
handed the runtime the **same end-of-stream sentinel a hangup produces**. `RuntimePipeline.run()`
reads stream exhaustion as *the caller hung up*, so the conversation ended with reason
`completed` and recorded no degradation. The reality event was right and everything downstream of
it was wrong.

ADR-0045 **SD4** and **AR-6** are unambiguous: a stream that *ends* is a hangup, a stream that
*raises* is a disconnect. `WebRTCTransport.inbound()` now raises `ProviderStreamTruncated` when
the path broke — and only then, never on a deliberate `stop()`, because a fix that turned every
goodbye into an outage would be worse than the defect.

Two M11.5 tests changed, both **strengthened**: their docstrings already claimed this property,
and they asserted it only of the event.

### 3. A failed session never released its providers

`fail()` terminalized the conversation without calling `_close_providers()`, and `end()` returned
early when already terminal — so a session that failed mid-call left its transport connected, its
pipeline unstopped, its playback engine running and its metric subscription registered on a bus
about to be discarded. **A clean session failure is §A10's last rung; a leaking one is not on the
ladder.**

Providers are now released from both terminal paths, and `_close_providers` is idempotent so the
session that fails and is then stopped still tears down exactly once.

## Provider resilience: one mechanism, no second one

`ProviderGuard` remains the **sole** provider protection mechanism (ADR-0046 FT-D1/FT-D3), reusing
`provider.CircuitBreaker` and `provider.FailoverController` rather than reimplementing them.
M11.14 adds no breaker, no retry ladder, no provider-specific circuit breaker and no second
degradation registry — asserted structurally over the AST.

**Retry is bounded at one**, because there is no retry in the media path at all: failover is
*selection*, not repetition. That is asserted by counting calls at the vendor, inside a deadline,
so a regression fails rather than hangs.

Authentication failures and unsupported capabilities are **not** retried: a credential rejected
once will be rejected again, and retrying spends quota to learn nothing while delaying the only
honest answer.

`CancelledError` is never translated, never counted against a breaker and never recorded as a
degradation. The caller withdrawing is not the vendor misbehaving, and conflating them would make
every ordinary hangup look like a vendor incident — and the degradation record useless for the
incidents that are real.

## Degradation behaviour, and why R-48 stays open

`DegradationRecorder` retains a **bounded** history (64 entries) with **unbounded counts**, so a
long outage is still visible after the oldest entries roll off. Memory is safe.

What is *not* bounded is the **event rate**. A sustained VAD outage records one
`auxiliary_disabled` per inbound frame — at the 20 ms media cadence, **50 per second, per
session**, published onto the event seam for as long as the outage lasts. That is bounded
recording and unbounded telling, and they are different questions.

**R-48 therefore stays open.** The measurement is in
`test_R48_sustained_vad_failure_records_one_degradation_per_frame`, which asserts the behaviour
that exists rather than the behaviour that would be nicer. A risk closed by a test measuring
something else is a risk that is still open.

## Telemetry during failure

Observability is never on the critical path, and each layer proves it independently:

- a telemetry tap that raises is caught by `_tap` (**TP9**) and the conversation continues;
- a durable sink that raises is caught by the bus (**AR-31**, **DW4**) and the operator is told
  through `on_durable_error`;
- a metering subscriber that raises is isolated by the dispatcher;
- a degradation sink that raises never loses the record it was told about.

A provider failure is still recorded when telemetry is the thing that is broken — the cascade that
would otherwise hide every incident behind the failure of the system meant to report it.

## No fabrication, no silent fallback

`metric.turn` semantics are **unchanged** by M11.14. A failed turn emits `cancelled` — the
existing vocabulary, which has no `failed` and did not gain one. No boundary the audio never
reached is stamped: in particular `first_audio_out` is stamped **after** the transport accepts the
frame, so a refusing transport reports no voice-to-voice latency rather than a flattering one.

A failing real provider never becomes a local one. Resilience must not become a hidden test escape
hatch, and the real profiles still refuse to start without their credentials.

## Evidence

91 tests in [`tests/test_resilience.py`](../../platform/tests/test_resilience.py), plus the
mechanism-level suite in `tests/test_fault_tolerance.py` (M11.4) that this builds on.

```bash
cd platform
.venv/bin/python -m pytest tests/test_resilience.py -q          # the §A10 ladder
.venv/bin/python -m pytest tests/test_fault_tolerance.py -q     # the guard mechanism
.venv/bin/python -m pytest -q                                   # the full suite
```

Repeated-lifecycle evidence runs **50 cycles** each of provider failure, cancellation and
barge-in, comparing live asyncio task counts before and after. This is **not** load testing: it
reveals accumulation across repeated lifecycles and says nothing about throughput or concurrency.

**Every test here is hermetic.** Live-provider resilience is **NOT EXECUTED** — no credential was
available for Deepgram, Groq or Cartesia, and §34 makes a live smoke optional. Nothing in this
document is evidence about a vendor, a network, or production behaviour.

## Known limitations

| Question | Status |
|---|---|
| Live vendor failure behaviour | **NOT EXECUTED** — R-54, R-58, R-63 stand |
| Real WebRTC peer disconnect | **NOT EXECUTED** — no peer |
| Mid-stream LLM failover | not implemented — **R-56** stands, deliberately |
| Mid-utterance TTS failover | not implemented — **R-61** stands, deliberately |
| Sustained-degradation event rate | unbounded — **R-48** stands, now measured |
| Failover chain in a deployed profile | none configured; mechanism proved at the guard |
| Durable-write recovery inside a session | **NOT DEFINED** — Epic 10's outbox is not driven from a call |
| Concurrency, throughput, multi-session isolation under load | out of scope |
| Provider consumption metering under failure | **R-64** stands — not implemented here (§33) |

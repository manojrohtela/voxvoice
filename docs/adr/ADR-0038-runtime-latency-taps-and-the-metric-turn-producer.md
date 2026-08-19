# ADR-0038 — Runtime latency taps and the `metric.turn` producer: satisfying AR-42 within the Epic 10 boundary

| | |
|---|---|
| **Status** | **Accepted 2026-08-19** (ratified at the M10.9 owner decision gate, **before** implementation — DOCUMENTATION_RULES §3.7). |
| **Completes** | [ADR-0033](ADR-0033-metric-stream-seam.md) **MS8**, which assigns runtime `metric.*` production to `platform/observability/` but decides neither where the taps sit nor what `metric.turn` contains. **It supersedes nothing.** ADR-0032, ADR-0033, ADR-0034, ADR-0035, ADR-0036 and ADR-0037 remain immutable and intact; **ED1–ED12**, **DW1–DW11**, **RL1–RL17**, **RC1–RC16**, **MS1–MS10**, **AR-2**, **AR-31**, **AR-32** and **AR-42** all continue to bind. |
| **Deciders** | Technical Owner |
| **Milestone** | Ratified at **M10.9**. Authorizes the M10.9 implementation and nothing beyond it. |
| **Related** | [ADR-0033](ADR-0033-metric-stream-seam.md) (MS1 vocabulary, MS8 producer, MS9 `metric.billing.*`), [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) (ED3 sequence authority, ED10 producers never block), [ADR-0037](ADR-0037-outbox-recovery-driver-and-restart-re-dispatch.md) (RC2 caller-driven, no worker), [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md) (DS11 injected clock), [DL-0013](../../.voxvoice/DECISION_LOG.md) (auxiliary observation degrades gracefully), [Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md), Constitution v2 §A15 / I1 / §A8, **AR-2**, **AR-42** |

## Status

**Accepted 2026-08-19.** This ADR authorizes **M10.9** only. Epics 5–9 remain frozen;
`platform/billing/**` is byte-for-byte unchanged; migrations `0001`–`0036` are untouched; the frozen
Epic 8 observability capability (`api/**/observability*`, `cp_alert_rule`, `v0012`) is untouched;
`v4.0.0` is not reopened.

## Context

**AR-42** is a merge gate: *"Every stage boundary in the conversation loop carries a latency tap
feeding `metric.turn`; a new stage without a tap doesn't merge."* Constitution v2 makes the same
demand (`§ Streaming principle`): *"Every stage boundary carries a monotonic timestamp feeding
`metric.turn`."* Neither has ever been implemented: `platform/observability/` is a two-file skeleton
whose own README says the taps land *"M3 (with the conversation loop)"* — they did not.

M10.8a supplied the vocabulary (`METRIC_TURN = "metric.turn"`, the sole ratified `metric.*` member).
M10.9 must supply the producer. Four questions had to be answered first, and the M10.9 design gate
found that the repository answered none of them cleanly:

1. **The conflict (C-1).** The Epic 10 plan §1.12 lists *"Conversation Runtime changes"* as out of
   scope for the whole epic, while AR-42 requires taps inside the conversation loop. The design gate
   established that three of the six required boundaries — the first LLM token, the first synthesized
   audio frame and the first outbound frame — occur **inside `async for` loops in
   `RuntimePipeline._respond`** and emit nothing. No bus-derived design can observe them. AR-42 is
   therefore unsatisfiable without *some* seam into the loop.
2. **The clock.** `runtime/turn/context.py` already captures six phase instants, but through the Turn
   Engine's clock, which defaults to `time.time` and is documented as *"monotonic-agnostic wall
   clock"*. v2 requires **monotonic**. Subtracting a monotonic instant from a wall-clock one is a
   correctness bug, so the existing timing cannot be reused for latency arithmetic.
3. **The payload.** No ratified document defines one. The only enumeration lives in Constitution
   **v1**, which is marked *"SUPERSEDED… v1 was never ratified"* and cannot be cited as authority.
4. **The ordering.** v2's budgets describe *"endpoint decision ≤ 250 ms after true end-of-speech ·
   STT final ≤ 150 ms **after endpoint**"* — endpoint before STT final. The implemented pipeline runs
   the **opposite** order (see **TP3a**), so the intuitive derivation would emit negative durations.

## Decision

### TP1 — §1.12 excludes Epic 11 rework, not an additive observation seam

**Owner ruling (D-2).** The Epic 10 plan §1.12 prohibition on *"Conversation Runtime changes"* is
interpreted as excluding **substantive Conversation Runtime / provider / vendor-integration rework**,
which Epic 11 owns. It does **not** prohibit the minimal **additive, optional, notify-only** latency
observation seam that ratified **AR-42** requires.

The interpretation rests on repository evidence, not convenience:

* the phrase appears **once**, at plan §1.12, and is **defined nowhere**;
* the M10.0 Blueprint's own §8 non-goals — the authoritative Epic 10 *"does not"* list — **omit runtime
  changes entirely**;
* the frozen boundary **F1–F17** freezes `platform/replay/**` (F16) and existing `EventType` members
  (F17) but **does not freeze `platform/runtime/**`**;
* Blueprint §12 assigns *"per-turn latency taps at every conversation-loop stage boundary"* **to Epic
  10**;
* §1.12's list groups the phrase with *"real providers, vendor SDKs, streaming/semantic VAD,
  production latency validation"*, all attributed to **(Epic 11)** — every neighbouring item is about
  integrating real vendors.

The plan is amended minimally to record this boundary. **No other milestone's scope changes**, and no
accepted ADR is amended.

### TP2 — The seam: an optional injected observer on `RuntimePipeline`

**Owner ruling (D-1).** `RuntimePipeline` gains one **optional, injected, notify-only** observer,
following the pattern it already carries six times — `turns`, `voice_activity`, `endpoint`,
`interrupt`, `smart_turn` and `on_transcript`, each documented *"notify-only… never affects
speech/audio"* and *"When absent, the pipeline behaves exactly as before."*

Binding constraints:

* the contract is a **`Protocol` declared in `runtime/contracts.py`**, beside `TranscriptSink` and
  `LifecycleObserver` — so **`runtime/` never imports `observability/`** and `observability/` never
  imports `runtime/`. The two meet only at a composition root, structurally;
* `RuntimePipeline` **never constructs an `EventPublisher`** and never touches the bus;
* when the observer is absent the pipeline behaves **exactly** as before — asserted;
* observation never blocks, never awaits, and never alters conversation semantics or provider
  contracts.

### TP3 — The six latency boundaries (AR-42, enumerated)

**Owner ruling (D-3).** *"Every stage boundary"* means exactly these six, and AR-42 is enforceable
against this written list:

| # | Boundary | Captured at |
|---|---|---|
| 1 | `speech_end` | `VoiceActivityCoordinator.observe()` returns `SILENCE_CONFIRMED` — **true end of utterance**, not an STT artefact |
| 2 | `stt_final` | an STT `Final` result arrives in the transcribe loop |
| 3 | `endpoint_decided` | the Endpoint Intelligence decision is **final** (`FINAL_ENDPOINT_DECISIONS`) |
| 4 | `llm_first_token` | the **first** `TokenDelta` observed in `llm.stream(...)` |
| 5 | `tts_first_audio` | the **first** frame yielded by `tts.synthesize(...)` |
| 6 | `first_audio_out` | the **first** frame handed to `transport.send(...)` |

**Lifecycle markers, not latency stages:** `playback_start` (recorded where the existing
`ASSISTANT_SPEAKING` transition occurs), and the turn's terminal outcome (`TURN_COMPLETED` /
`TURN_CANCELLED`). They mark the turn; they are not stage boundaries and no duration is derived
across them.

The superseded Constitution v1 enumeration is **not** imported. It conflated `playback-start` with the
first outbound frame, which this repository proves are distinct instants — `on_assistant_speaking()`
fires *before* `tts.synthesize` is even entered.

### TP3a — The pipeline is segment-driven: `stt_final` precedes `endpoint_decided`

**Recorded explicitly, because it inverts the v2 budget ordering.**

Constitution v2 describes the **target** streaming architecture, in which endpointing runs on VAD
audio and the STT final lands *inside* the endpoint delay — hence *"STT final ≤ 150 ms after
endpoint"*. **The implemented pipeline is segment-driven:** every endpoint decision is derived *from*
an STT `Final`. In `RuntimePipeline.run()` a `Final` result is received and only then does
`_on_segment` call `endpoint.on_segment()`; all nine endpoint call sites were checked and no
silence-timer path can produce a decision before a `Final`.

Therefore, **in the current runtime the real order is**:

```
speech_end → stt_final → endpoint_decided → llm_first_token → tts_first_audio → first_audio_out
```

**Owner ruling (option b).** The derived stage keys follow the **real order**, so no negative duration
is ever written. The **raw boundary names are preserved exactly** — they name instants, and instants
do not change meaning when the architecture does. The v2 ordering is recorded as **target semantics**
for Epic 11 and is explicitly **not** a reason to alter the current runtime.

This is why **TP5 keeps absolute instants in the payload alongside derived durations**: M10.11 can
re-derive any interval it wants from `at`, even if a derived key in `ms` is remapped when Epic 11
makes endpointing audio-driven.

### TP4 — `EventSource.OBSERVABILITY`

**Owner ruling (D-4).** One additive `EventSource` member. `EventSource` is **enum-typed** on both
`EventMetadata.source` and `Event.source`, and its own docstring says *"every event names the
component that produced it"*. ADR-0033 **MS8** names `platform/observability/` as the sole producer of
runtime `metric.*`, so reusing `PIPELINE` or `RUNTIME` would name a component that is not the
producer and would make `MetricSignal.producer` wrong downstream at M10.10. `METERING` is
Billing-adjacent and is not used. **No existing member changes** (AR-32).

### TP5 — The `metric.turn` payload

**Owner ruling (D-5).** Exactly these fields, and nothing else:

```
status              str                "completed" | "cancelled" | "interrupted"
at                  dict[str, float]   absolute monotonic instants, seconds
ms                  dict[str, int]     derived per-stage durations, integer milliseconds
voice_to_voice_ms   int | None         speech_end → first_audio_out
```

`at` keys are drawn only from `{speech_end, stt_final, endpoint_decided, llm_first_token,
tts_first_audio, first_audio_out, playback_start}`. `ms` keys only from `{stt, endpoint, llm, tts,
egress}`.

**A boundary that was not reached is an absent key — never `0`.** A fabricated zero is
indistinguishable from a real zero-millisecond stage, and the repository already refuses that
(*"never fabricate a number"*).

**Attribution is not duplicated.** `session_id`, `org_id`, `conversation_id` and `turn` are stamped on
the **envelope** by `EventPublisher` from its bound `EventMetadata`; repeating them in `data` would
create a second, divergable copy of identity the envelope already owns (ED3).

**Deliberately absent:** `meter_code` (named nowhere in ADR-0033), any billing field, any
`metric.billing.*` (MS9), tracing fields, logging fields, provider-specific fields.

`voice_to_voice_ms` is the **primary scalar** corresponding to the frozen `MetricSignal.raw_units:
int`, which M10.10 will decode. It is the one number invariant **I1** (voice-to-voice ≤ 1.0 s) is
about.

**Derivation (TP3a order), integer milliseconds, computed only when both endpoints exist:**

| Key | Interval |
|---|---|
| `stt` | `speech_end → stt_final` |
| `endpoint` | `stt_final → endpoint_decided` |
| `llm` | `endpoint_decided → llm_first_token` |
| `tts` | `llm_first_token → tts_first_audio` |
| `egress` | `tts_first_audio → first_audio_out` |
| `voice_to_voice_ms` | `speech_end → first_audio_out` |

The five stages form a **contiguous chain** over the six boundaries, so when all six are present the
stages sum to `voice_to_voice_ms` (up to integer rounding). A negative interval is **never emitted**;
it is dropped and logged, because a negative latency is evidence of a defect, not a measurement.

### TP6 — One emission per terminal turn

**Owner ruling (D-6).** Exactly **one** `metric.turn` per terminal turn outcome that has **at least one
recorded boundary**. A turn with zero recorded boundaries emits nothing — there is no metric to
report. `status` carries the terminal reason: `completed`, `cancelled` or `interrupted`.

Interrupted and cancelled turns **do** emit, with whatever boundaries were reached. A barge-in that
happened 300 ms into synthesis is exactly the latency an operator needs to see, and suppressing it
would discard the metric's best use.

### TP7 — One monotonic clock, owned by the tap

**Owner ruling (D-7a).** The observability tap owns **one injected monotonic clock**
(`Callable[[], float]`, defaulting to `time.monotonic`), and **every** boundary instant in a given
`metric.turn` comes from it. All duration arithmetic subtracts instants from that one clock.

`TurnTiming` is **not** reused for latency: it is wall-clock by construction and documented as such.
**`TurnEngine`'s clock semantics are not changed, `TurnEvent.ts` keeps its meaning, and
`runtime/turn/**` is not modified** — the tap carrying its own clock is precisely what makes that
unnecessary.

Tests inject a frozen monotonic clock, assert durations **by value**, and contain no sleeps.

### TP8 — Bounded per-turn lifecycle

**Owner ruling (D-7).** Per-turn state is keyed `(session_id, turn_index)`, created at the **first**
recorded boundary, and **released immediately after** the terminal emission. It is bounded by
**in-flight turns**, never by session lifetime. There is no global accumulator, no session-lifetime
metric map, no metric history, and no cross-session collision — two sessions recording the same
`turn_index` occupy different keys.

A terminal call for a turn with no recorded state is a **no-op**, not an error: at-least-once
observation of a lifecycle event is ordinary, and a stray `complete` must not resurrect a released
turn.

### TP9 — Failure isolation

**Owner ruling (D-8).** Metric observation is **best-effort**. Every observation call is wrapped:
failures are **caught and logged explicitly**, never propagated into the conversation loop, never
allowed to mutate the turn FSM, never allowed to interrupt STT/LLM/TTS/transport processing, and never
allowed to affect audio.

This is the repository's unanimous precedent — `_tap_voice_activity` (*"VAD must not break the
pipeline"*), `_emit` (*"a sink must never break the pipeline"*), `EventDispatcher.dispatch` (*"per-
subscriber isolation. Never raises"*) — and **DL-0013**'s rule: explicit, logged degradation, *"not
silent recovery"*, while **the state machine stays strict**. A failure is never swallowed silently.

### TP10 — Capture on the hot path, publish once off it

**Owner ruling (performance).** Boundary capture is a clock read and a dict write. **Publication
happens once, after the turn is already terminal.**

The reason is a repository fact, not a preference: `InProcessEventBus.publish()` is **synchronous
run-to-completion** — it stamps `seq`, optionally performs a **durable write inline** (ADR-0035 DW3),
then drains its queue calling **every matching subscriber on the caller's stack**. Publishing at each
of six boundaries would therefore run unbounded third-party work six times per turn on the
conversation path, and once M10.10's buffer subscribes — or a durable sink is attached — that includes
a synchronous database write inside a **≤ 1.0 s voice-to-voice budget**. That is Blueprint risk
**R-9** (*"latency taps violate I1"*) realised.

Emitting after the terminal outcome means added latency **cannot** reach the caller, which is how MS8's
*"cheap, non-blocking, must never alter conversation behaviour"* is satisfied literally rather than
hopefully.

**No thread, scheduler, sleep, worker, queue hand-off, background task, buffer, persistence or
retention** is introduced — consistent with ADR-0037 RC2 and the repository's caller-driven convention.
The metric buffer is **M10.10**.

### TP11 — Boundary with M10.10, M10.11 and Epic 11

M10.9 **produces** and nothing more. **M10.10** owns `MetricStreamPort`, its implementation including
`replay()`, and the platform-owned buffer that subscribes and decodes into `MetricSignal`. **M10.11**
owns shadow validation. **Epic 11** owns real providers, audio-driven endpointing (which will make
TP3a's ordering match v2's budgets) and production latency validation. `platform/observability/`
becomes a real producer but stays small: **not** a tracing framework, **not** a logging framework, not
a generic telemetry framework, not an aggregation engine, not a cost meter, not a persistence layer.

## Verification criteria

| | Criterion |
|---|---|
| **TV1** | The six boundary names are exactly `speech_end`, `stt_final`, `endpoint_decided`, `llm_first_token`, `tts_first_audio`, `first_audio_out`. |
| **TV2** | `playback_start` and the terminal outcome are lifecycle markers; no duration is derived across them. |
| **TV3** | All instants come from one **injected monotonic** clock; tests use a frozen clock and assert by value. |
| **TV4** | Derived `ms` follow the TP3a chain; **no negative duration is ever emitted**. |
| **TV5** | `voice_to_voice_ms` is `speech_end → first_audio_out`; absent when either endpoint is missing. |
| **TV6** | An unreached boundary is an **absent key**, never `0`. |
| **TV7** | A completed turn emits **exactly one** `metric.turn`. |
| **TV8** | A cancelled turn emits exactly one when boundaries exist; likewise an interrupted turn. |
| **TV9** | A turn with zero recorded boundaries emits **nothing**. |
| **TV10** | `llm_first_token`, `tts_first_audio` and `first_audio_out` are captured **inside** the streaming loops, on the first item only. |
| **TV11** | **No publish occurs at any intermediate boundary** — exactly one per turn. |
| **TV12** | A raising observer is logged and the conversation completes unaffected. |
| **TV13** | Two sessions with equal `turn_index` never collide. |
| **TV14** | Per-turn state is released after terminal emission; no growth across turns. |
| **TV15** | `EventSource.OBSERVABILITY` is used; `EventType.METRIC_TURN` remains the only `metric.*` member. |
| **TV16** | Envelope attribution is not duplicated in `data`. |
| **TV17** | `observability/` imports `core` + stdlib only; `runtime/` imports no `observability`. |
| **TV18** | With no observer injected, `RuntimePipeline` behaves exactly as before. |
| **TV19** | `runtime/turn/**`, `TurnEngine` clock semantics and `TurnTiming` are unchanged. |
| **TV20** | No migration, schema, buffer, retention, scheduler, thread or new `EventType`. |

## Non-goals

Tracing · cost meter · structured-logging configuration · `MetricStreamPort`, its implementation and
`replay()` (**M10.10**) · the metric buffer (**M10.10**) · shadow validation (**M10.11**) · real
providers, audio-driven endpointing and production latency validation (**Epic 11**) · retention
(**M10.5**) · any new `EventType`, per-stage event types or provider-specific metric events ·
`metric.billing.*` (**MS9**) · aggregation, percentiles or dashboards · persistence, migration or
schema change · scheduler, thread, worker or background task · any change to `platform/billing/**`,
`platform/replay/**`, `platform/providers/**`, provider contracts, `EventBus`/`EventPublisher`/
`EventMetadata` semantics, `MetricSignal`, the frozen Epic 8 observability capability,
`ConversationStateMachine`, `TurnEngine` clock semantics, `TurnTiming`, migrations `0001`–`0036`,
ADR-0023/0031/0032/0033/0034/0035/0036/0037, or `v4.0.0`.

## Dependency Direction

Unchanged and acyclic: `core ← events ← runtime`, and `core ← observability`. The observer contract is
a `Protocol` in `runtime/contracts.py`; `observability/` satisfies it **structurally** and imports
`core` + stdlib only. **`runtime/` gains no import of `observability/`, and `observability/` gains no
import of `runtime/`.** They meet at a composition root, which is the same inversion `TranscriptSink`
and `RuntimeEventBridge` already use.

## Consequences

**Gained.** AR-42 satisfiable for the first time since it was written · `metric.turn` emitted from real
per-turn taps, which the frozen Billing contract has always expected · the three boundaries no
bus-derived design could ever see · a written boundary enumeration that makes AR-42's merge gate
enforceable · an explicit record that the pipeline is segment-driven, so the next reader does not
rediscover it as a bug.

**Accepted costs.** `platform/runtime/**` is modified for the first time in Epic 10 — minimally,
additively, and only under TP1 · the derived stage keys follow the current segment-driven order, so
`ms["endpoint"]` and `ms["stt"]` will need remapping when Epic 11 makes endpointing audio-driven (the
raw instants in `at` will not) · `speech_end` is unavailable when no Voice Activity coordinator is
wired, so `voice_to_voice_ms` is absent on those turns · nothing consumes `metric.turn` yet — the
buffer is M10.10.

**Explicitly preserved.** ADR-0032/0033/0034/0035/0036/0037 immutable · MS1–MS10, ED1–ED12, DW1–DW11,
RL1–RL17, RC1–RC16 intact · AR-2, AR-31, AR-32, AR-42, DL-0013, DL-0017 preserved · `core.events.EventBus`,
`EventPublisher` and `EventMetadata` semantics untouched · `TurnEngine` clock and `TurnTiming` untouched ·
`platform/billing/**` byte-for-byte unchanged · migrations `0001`–`0036` untouched · Epic 8 observability
capability untouched · `v4.0.0` not re-tagged.

## Alternatives Considered

**Bus-derived observation (no runtime change).** Rejected — the design gate proved
`llm_first_token`, `tts_first_audio` and `first_audio_out` occur inside `async for` loops in
`_respond` that emit nothing. It is the one model that cannot satisfy AR-42, and satisfying AR-42 was
the milestone.

**Extending `RuntimeEventBridge` with per-stage events.** Rejected — it needs new `EventType` members
M10.8a deliberately did not add, publishes on the hot path (TP10), and still modifies runtime, so it
pays the §1.12 cost without avoiding anything.

**A direct `observability` import in `RuntimePipeline`.** Rejected — no existing seam couples runtime
to a consumer plane; `TranscriptSink` and `RuntimeEventBridge` both invert instead.

**Reusing `TurnTiming` for latency.** Rejected — wall-clock by construction and documented as
*"monotonic-agnostic"*; mixing it with monotonic instants would silently corrupt every derived
duration.

**Making `TurnEngine`'s clock monotonic.** Rejected — it changes the meaning of existing turn
timestamps and `TurnEvent.ts` for a milestone that has no mandate to touch turn semantics.

**Inheriting Constitution v1's payload.** Rejected — v1 is marked superseded and *"never ratified"*,
and its enumeration conflates `playback-start` with the first outbound frame, which this repository
proves are different instants.

**Keeping the v2 budget ordering for derived keys.** Rejected (owner ruling, option b) — it emits a
negative `ms["stt"]` on every turn in the current segment-driven pipeline.

**Publishing at each boundary.** Rejected — `publish()` is synchronous run-to-completion and would run
every subscriber, and potentially a durable write, six times per turn inside the I1 budget.

**Zero-filling unreached boundaries.** Rejected — indistinguishable from a real zero and forbidden by
TP5.

**Suppressing metrics for interrupted turns.** Rejected — barge-in latency is the metric's most
valuable case.

## What this ADR does not change

It creates no schema, no migration and no persistence, and it changes no conversation behaviour. It
authorizes the M10.9 implementation and nothing beyond M10.9. Every constraint in the
[Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md) §1 continues to bind, as
amended by **TP1** for §1.12 alone.

# ADR-0047 — Audio-driven endpointing and the additive `metric.turn` stage remap: completing ADR-0038 TP3a

| | |
|---|---|
| **Status** | **Accepted 2026-08-24** (ratified at M11.1). Recommended by the Epic 11 **M11.0 Blueprint** and settled at the **M11.0a** decision gate (ruling **D-9**); this milestone ratifies it. |
| **Kind** | Epic 11 architecture ADR. **Amends no Constitution Part A or Part B section.** |
| **Completes** | [ADR-0038](ADR-0038-runtime-latency-taps-and-the-metric-turn-producer.md) **TP3a**, which recorded that the pipeline is segment-driven, that the inverse ordering is *"target semantics for Epic 11"*, and that `ms["endpoint"]` and `ms["stt"]` *"will need remapping when Epic 11 makes endpointing audio-driven."* **It supersedes nothing.** ADR-0038 remains immutable and intact; **TP1–TP11** all continue to bind, as do **AR-2**, **AR-31**, **AR-32** and **AR-42**. |
| **Deciders** | Technical Owner |
| **Related** | Constitution v2 **§A8** (per-stage budgets and their ordering), **I1**; ARCHITECTURE_RULES **AR-32** (additive or version bump, never silent mutation), **AR-42** (every stage boundary carries a latency tap), **AR-2**; [ADR-0033](ADR-0033-metric-stream-seam.md) (MS8 producer ownership), [ADR-0039](ADR-0039-metric-stream-buffer-and-api-billing-edge.md) (MB6 the buffer is not durable; MB9 the fixed unit), [ADR-0050](ADR-0050-epic10-spine-telemetry-metering-production-wiring.md) (the wiring that makes this observable) |

## Status

**Accepted 2026-08-24**, ratified at milestone **M11.1**. **This ADR authorizes no code.** It fixes
the endpointing and telemetry semantics M11.8 must implement.

## Context

**Today's ordering is inverted relative to the Constitution, and ADR-0038 said so honestly.**

`RuntimePipeline` is **segment-driven**: an endpoint decision is derived *from* an STT `Final`
(`_on_segment` → `EndpointCoordinator.on_segment()`), so the taps fire `stt_final` **before**
`endpoint_decided`. ADR-0038 TP3a recorded this and stated that the derived stage keys follow the
**real** order specifically so that *no negative duration is ever emitted*.

Constitution §A8 states the target ordering through its budgets: *"endpoint decision ≤ 250 ms
**after true end-of-speech** · STT final ≤ 150 ms **after endpoint**."* True end-of-speech is an
**audio** fact, supplied by a real VAD — not an STT artefact.

**The consumer side is smaller than it appears, and this was verified rather than assumed.**
`billing/foundation/metering_factories.py::MetricSignal` carries:

```
event_id · event_type · source · observed_at · raw_units · raw_unit
scope · received_at · producer · sequence · payload_digest
```

There is **no stage payload**. Its docstring is explicit: *"Billing keeps no copy of an
operational payload it does not own, which is what stops this seam becoming a second home for
runtime telemetry."*

`api/metering/metric_buffer.py` confirms the coupling precisely:

- `MEASUREMENT_KEY = "voice_to_voice_ms"` — *"The payload key carrying the one scalar that may
  become `raw_units`"*.
- `RAW_UNIT = "ms"` — fixed, *"`voice_to_voice_ms` is milliseconds and nothing else is ever the
  magnitude (MB9)"*.
- `payload_digest = canonical_payload_digest(data)` — a digest over the whole payload.

**Therefore Billing never reads `ms{}` or `at{}`, and changing stage keys cannot break the frozen
`MetricSignal`.**

One further verified behaviour is load-bearing. `MetricTurnBuffer.record()` **never raises**; it
counts rejections in a fixed precedence — `malformed` → `missing_measurement` → `unattributed`.
`voice_to_voice_ms` is `None` unless **both** `speech_end` and `first_audio_out` were recorded,
and `speech_end` originates **only** in the voice-activity tap. Since
`build_local_registry(include_voice_activity=False)` is the shipped default, a VAD-off profile
today would produce `metric.turn` events that the buffer drops as `missing_measurement`.

## Decision

### ME1 — Real VAD supplies true end-of-speech, and endpointing becomes audio-driven

`speech_end` is derived from the voice-activity signal (`VoiceActivityState.SILENCE_CONFIRMED`),
and the endpoint decision is driven from it rather than from an STT `Final`. The resulting
boundary order matches §A8: `speech_end → endpoint_decided → stt_final → llm_first_token →
tts_first_audio → first_audio_out`.

### ME2 — The change to `metric.turn` is **additive**. No version bump

New stage keys may be added to `ms{}`; existing key names are **preserved**. AR-32 is satisfied by
the additive limb, so no payload version bump is taken.

### ME3 — No new event type

`metric.turn` remains the single runtime telemetry event, and `platform/observability/` remains
its **sole producer** (AR-2, ADR-0033 MS8). No `metric.*` member is added to
`core.events.EventType`.

### ME4 — `voice_to_voice_ms` is frozen: name, unit and meaning

It is the metering contract. It **must not** be renamed, removed, or change unit from
milliseconds. Its *semantics improve* under ME1 — measured from **true** end-of-speech rather
than from an STT-derived proxy — which is the accuracy gain, not a contract change.

### ME5 — `at{}` remains raw instants and stays order-agnostic

ADR-0038 kept absolute instants as the evidence precisely so a derived key could be remapped
later without losing information. That property is used here and preserved for the future.

### ME6 — No negative duration is ever emitted

ADR-0038's rule stands: a derived interval that would be negative is dropped with a warning, never
emitted. Under ME1 the derived stages follow the real order, so the condition should not arise —
but the guard is not removed on that expectation.

### ME7 — `payload_digest` changing is correct behaviour, not a break

The digest covers **content**, not schema. A payload that legitimately changed yields a different
digest; nothing downstream treats the digest as a schema identifier.

### ME8 — A missing measurement must stay distinguishable from a wrong one

**No timing may ever be fabricated.** An unreached boundary remains an **absent key, never `0`**
(ADR-0038 TP5). When `voice_to_voice_ms` cannot be derived, it is absent, and
`MetricTurnBuffer` counts **`missing_measurement`** — a visible, countable rejection. A
fabricated zero would be indistinguishable from a real zero-millisecond stage and is prohibited.

### ME9 — VAD-off is a `missing_measurement` condition, and it is a wiring dependency

Because `speech_end` exists only when a voice-activity provider is registered, a VAD-off profile
yields no `voice_to_voice_ms` and therefore **no metering signal at all**. This is the correct
behaviour under ME8 — silence rather than invention — but it means **M11.12 (metering wiring)
hard-depends on M11.7 with VAD on by default** in the real-provider profile. The local profile is
unaffected and keeps its current default.

### ME10 — Boundary with ADR-0038

ADR-0038 owns the tap mechanism, the injected monotonic clock, the one-emission-per-terminal-turn
rule and **TP10** (capture is cheap; publication happens post-terminal, never inside the turn
budget). None of that is reopened. This ADR changes only *which real-world fact* produces
`speech_end` and *what may be added* to the derived keys.

## Consequences

- M11.8 implements ME1 and the additive remap; M11.7 must precede it.
- `MetricSignal`, `MetricStreamPort` and every Billing surface remain **untouched**;
  `events → billing` remains forbidden.
- `voice_to_voice_ms` becomes a genuine voice-to-voice measurement for the first time, which is
  what makes the I1 gate at G9 meaningful.
- The `missing_measurement` counter becomes an operational signal that VAD is not participating.

## Alternatives considered

- **A `metric.turn` payload version bump.** Rejected: AR-32 permits additive evolution, the
  additive limb suffices, and a version bump would imply a consumer contract change where the
  verified consumer reads none of the affected keys.
- **A new event type (e.g. `metric.turn.v2`).** Rejected: it would create a second producer path
  for the same fact and put AR-2 at risk for no benefit.
- **Renaming `ms["endpoint"]` / `ms["stt"]` to reflect the new order.** Rejected: renaming is
  precisely the *silent mutation* AR-32 forbids; the keys keep their names and gain accuracy.
- **Emitting `0` when a boundary is unreached, so `voice_to_voice_ms` is always present.**
  Rejected outright by ME8 — it would convert a visible `missing_measurement` into an invisible
  wrong number, and would corrupt every percentile derived from it.
- **Making metering tolerate a missing measurement by substituting a proxy.** Rejected for the
  same reason; the honest outcome of VAD-off is no signal (ME9).

## Evidence required later

1. A test proving the boundary order under a real VAD matches §A8.
2. A test proving `voice_to_voice_ms` survives the remap with unit `ms` and unchanged name.
3. A test proving an unreached boundary yields an **absent key**, never `0`.
4. A test proving a VAD-off profile produces `missing_measurement` rather than a wrong number.
5. A test proving no negative duration is emitted.
6. Proof that no Billing file changed and `events → billing` remains absent.

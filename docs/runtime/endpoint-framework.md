# Runtime Component Spec — Endpoint Intelligence Framework

| | |
|---|---|
| **Component** | `runtime.endpoint` (`EndpointEngine`, `EndpointCoordinator`, `EndpointContext`, `EndpointDecision`, `EndpointPolicy`, **`AudioEndpointDetector`**, **`AudioEndpointPolicy`**) · **Status:** Implemented (M3.3), **extended M11.8** |
| **Format** | Constitution §A11.3 component specification |
| **Milestone** | M3.3 — Endpoint Intelligence Framework (2026-07-17); **M11.8 — audio-driven endpointing + `metric.turn` remap (2026-09-16)** |
| **Authority (M11.8)** | [ADR-0047](../adr/ADR-0047-audio-driven-endpointing-and-metric-turn.md) ME1–ME10, [ADR-0038](../adr/ADR-0038-runtime-latency-taps-and-the-metric-turn-producer.md) TP3a/TP5/TP6/TP7, §A8, AR-25, AR-32, AR-42 |
| **Decisions (M11.8)** | DL-0184 (design), DL-0185 (timing policy + activation), DL-0186 (turn-attribution defect), DL-0187 (risks) |

## Responsibility (single)
Own the **endpoint model** — the deterministic, provider-neutral decision of
*when a caller's turn is truly complete*. It consumes Voice Activity signals and
completed transcript segments and produces a turn-completion **decision**; it
**replaces the pipeline's STT-final heuristic** (previously "respond on every
Final"). M3.3 is deterministic, policy-driven endpointing only: **no** semantic /
LLM endpointing, interruptions / barge-in, double-talk, echo cancellation, or
noise suppression (all M3.4+). It does **not** own turns (the Turn Engine does),
conversation state (the Orchestrator does), voice-activity state (the
VoiceActivityEngine does), or do any AI / speech processing.

## Inputs (neutral signals, never provider objects)
Per the Constitution (§A6) the engine consumes **neutral signals**, not STT/VAD
provider objects or vendor specifics:
- `note_speech` — speech is in progress (an STT partial, or a VAD `SPEAKING`).
- `note_segment` — a completed transcript segment (an STT `Final`). The primary
  completion trigger: transcript text is available at this boundary.
- `note_silence` — confirmed end-of-speech (a VAD `SILENCE_CONFIRMED`).
- the injected **`EndpointPolicy`** (runtime configuration).

The engine never imports an STT or VAD provider; the pipeline / VAF translate
provider events into these signals.

## Ownership (explicit — no component owns another's state)
```
SessionWorker            → worker lifecycle (WorkerState)
ConversationOrchestrator → conversation lifecycle (SessionState); ALWAYS creates the
                           endpoint framework, exposes it read-only
EndpointEngine           → endpoint-decision state (EndpointDecision, sole writer)
TurnEngine               → turn lifecycle (the endpoint framework notifies it that a
                           turn should finish; never writes it)
VoiceActivityEngine      → voice-activity state (feeds the endpoint framework)
RuntimePipeline          → execution; feeds transcript segments to the coordinator
```

## Sub-components
- **`EndpointDecision`** — the decisions + `ENDPOINT_TRANSITIONS` table +
  `FINAL_ENDPOINT_DECISIONS`.
- **`EndpointPolicy`** — the configurable rules (nothing hardcoded).
- **`EndpointContext` / `EndpointTiming`** — per-session decision + latest
  decision-entry timestamps; `turn_index` counts finalized turns.
- **`EndpointEngine`** — session-scoped; the sole writer of decision state; applies
  the policy; `note_speech` / `note_segment` / `note_silence` / `flush` /
  `reset_turn`.
- **`EndpointCoordinator`** — the pipeline's façade; drives the engine and notifies
  the Turn Engine (`on_user_final`) on a final decision.
- **`EndpointEvent` / `EndpointObserver`** — the internal notification extension
  point (not the Event Bus, M4).

## Decision model (deterministic, cyclic — no terminals)
```
                        ┌───────────────── speech resumed ──────────────┐
                        ▼                                                │
 CONTINUE_LISTENING ─► POTENTIAL_ENDPOINT ─► CANCEL_ENDPOINT ────────────┘
   │        │              │  (silence hold satisfied)
   │        │              ▼
   │        └──────────► ENDPOINT_CONFIRMED ──► CONTINUE_LISTENING  (next turn)
   │  (segment, no hold)
   └──────────────────► TIMEOUT_ENDPOINT ─────► CONTINUE_LISTENING  (next turn)
       (max_turn_frames exceeded)
```
Table: `runtime.endpoint.decision.ENDPOINT_TRANSITIONS`. The model is **cyclic**
(many turns per conversation) so there are no terminal decisions. The three
momentary decisions — `CANCEL_ENDPOINT` (same turn resumes) and the two *final*
decisions `ENDPOINT_CONFIRMED` / `TIMEOUT_ENDPOINT` (the Turn Engine is notified to
finish the turn) — normalize back to `CONTINUE_LISTENING` at the start of the next
observation, so a returned decision reports exactly what happened on that call
(mirroring the Voice Activity engine). Every transition is validated; illegal or
duplicate moves raise (no silent recovery).

## Policy (configurable — nothing hardcoded)
`EndpointPolicy` (frame counts = provider observations; deterministic):
| Knob | Meaning | Default |
|---|---|---|
| `min_speech_frames` | speech observations required before an endpoint may confirm | 1 |
| `silence_hold_frames` | silence to hold after a potential endpoint before confirming | 0 |
| `min_silence_frames` | minimum silence observations to confirm via silence | 0 |
| `confidence_threshold` | minimum confidence to accept an endpoint signal | 0.0 |
| `max_turn_frames` | force `TIMEOUT_ENDPOINT` after this many frames (0 disables) | 0 |

The **defaults reproduce the pre-M3.3 behaviour**: a completed segment confirms
immediately (no hold, no timeout), so the local profile behaves exactly as before
while the *decision* now flows through this framework.

## Runtime integration (replaces the STT-final heuristic)
The Orchestrator **always** creates the endpoint framework (unlike the optional
VAF) and wires it into both the pipeline and (when present) the VAF, sharing the
one Turn Engine. In the pipeline's turn loop:
- an STT **`Partial`** → `on_user_speech` (keeps the turn open);
- an STT **`Final`** → `on_segment`; the pipeline responds **only** when the
  returned decision is *final* (`ENDPOINT_CONFIRMED` / `TIMEOUT_ENDPOINT`). On a
  final decision the coordinator has already notified the Turn Engine
  (`on_user_final`); the pipeline then runs the LLM→TTS response and `reset`s the
  engine for the next turn.
- **stream end (hangup)** with a segment still pending → `flush` force-confirms so
  the last utterance is never dropped.
When a VAD provider is registered, the VAF also feeds the engine: `SPEAKING` →
`on_user_speech` (enriches the speech run), `SILENCE_CONFIRMED` → `on_user_silence`
(counts toward a pending endpoint's silence hold). The VAF **never** decides
completion — only enriches. With the default policy these VAD signals change
nothing observable, so behaviour is unchanged (verified end-to-end).

## Failure handling (no silent recovery)
- **invalid endpoint transition** → `InvalidEndpointTransition` (strict; the model
  only proposes legal edges — a raise means a coding error, surfaced not hidden).
- **duplicate endpoint decision** → `DuplicateEndpointDecision` (no self-loops);
  a same-signal observation that causes no net change returns `None`
  (deterministically suppressed, not an error).
- **provider shutdown** → the endpoint framework has **no provider** (pure runtime
  logic); provider teardown remains the STT/VAF concern. The pipeline's `stop()`
  calls `endpoint.cancel()` to terminalize any pending decision through the
  validated table.
- **runtime cancellation** → `cancel()` / `reset_turn()` return the engine to
  `CONTINUE_LISTENING` from any decision (a pending hold is cancelled first) — via
  the transition table, never a silent overwrite.
Both endpoint errors root in `core.errors.RuntimeConflict` (a lifecycle
violation), like the turn / conversation / voice-activity state machines.

## Timing captured (in `EndpointContext.timing`)
Latest decision-entry timestamps → named accessors: `potential_at`, `confirmed_at`,
`cancelled_at`, `timeout_at`. Captured only — no analytics processing (M3.4+).

## Dependencies & boundaries
`runtime.endpoint` imports `core.errors` (via `runtime.errors`) and stdlib only.
Never imports `api`, the pipeline substrate, or a provider implementation.
Consumed by the pipeline and the VAF coordinator (via `EndpointCoordinator`) and
the orchestrator (creates + exposes the engine).

## Design note (DL-0014)
The completion **trigger** stays at the STT-final segment boundary (where the
transcript is available) while the **decision** is made by the policy-driven
engine — VAD signals *enrich* the decision but never independently trigger a
response. This keeps the local profile (discrete text frames, no acoustic silence
between utterances) working unchanged, while an acoustic-silence-first profile is
enabled purely by policy (`silence_hold_frames` > 0) with a real streaming VAD.
Fully independent acoustic-silence-triggered completion in the live pipeline (no
STT segment needed) is M3.4+.

## M11.8 — audio-driven endpointing (ADR-0047 ME1)

> **VAD is speech detection; endpointing decides when a speech turn ends.**

Until M11.8 the turn-completion trigger was an STT `Final`, which ADR-0038 TP3a recorded as
inverted relative to §A8. M11.8 adds the audio half. **No existing behaviour changed by default:**
audio endpointing is opt-in per session and every profile that does not select it runs the M3.3
segment-driven model exactly as before.

### The chain

```
AudioFrame ─► VoiceActivityProvider ─► VoiceActivityCoordinator.last_sample (per-frame verdict)
                                              │
                                              ▼
                         AudioEndpointDetector.observe(speech, duration_ms)
                                              │  speech_start / speech_end
                                              ▼
                     EndpointEngine.note_speech_end ─► EndpointCoordinator ─► Turn Engine
```

The detector is **provider-neutral by construction**: its entire input surface is a boolean and a
duration in milliseconds. No Silero, model, probability or vendor type exists anywhere in
`runtime/endpoint/` (AST-asserted).

### The detector state machine

| State | Meaning | Exits |
|---|---|---|
| `IDLE` | no speech evidence (silence before first speech) | `SPEECH_PENDING` |
| `SPEECH_PENDING` | speech seen, below `min_speech_ms` | `SPEAKING`, `IDLE` (it stopped) |
| `SPEAKING` | utterance in progress | `TRAILING_SILENCE`, `IDLE` (max-utterance guard) |
| `TRAILING_SILENCE` | silence after speech, hold unsatisfied | `SPEAKING` (resumed), `IDLE` (ended) |

One cycle per utterance; `speech_start` and `speech_end` are each emitted **exactly once** per
cycle. Illegal transitions raise rather than being absorbed.

### Timing policy (implementation defaults, not tuned values)

| Knob | Default | Rationale |
|---|---|---|
| `min_speech_ms` | 120 ms | ~6 × 20 ms transport frames / 4 × 32 ms Silero windows — a click or one false-positive window cannot open a turn |
| `trailing_silence_ms` | 240 ms | sits inside §A8's *"endpoint decision ≤ 250 ms after true end-of-speech"*, the only budget the repository states |
| `max_utterance_ms` | 30 000 ms | a guard, not a policy: a turn that never ends is closed; `0` disables |

All thresholds are **audio duration**, accumulated from what each frame carries — never wall clock,
never `sleep`, never scheduler timing. A frame's duration is computed from its own format
(`PCM16`: bytes ÷ 2 ÷ channels ÷ rate); an `OPAQUE` frame declares none, so the composition root's
fallback (the VAD provider's declared `frame_ms`) stands in.

**Known tension (R-49):** conversational systems commonly hold 0.5–0.8 s to tolerate mid-sentence
pauses. 240 ms favours the documented §A8 budget over that; changing it needs measurement this
milestone does not have. **No accuracy, latency, noise or Hinglish claim is made.**

### Speech-start and speech-end semantics

* **speech_start** — `min_speech_ms` of contiguous speech. Opens the turn through the existing
  `TurnCoordinator.on_user_speech()` (`LISTENING → USER_SPEAKING`); subsequent speech frames add
  nothing. Silence below the threshold discards the accrual entirely.
* **speech_end** — `trailing_silence_ms` of *contiguous* silence. **One silent frame never ends a
  turn**; speech resuming inside the hold continues the same utterance and restarts the hold.
  It confirms the endpoint decision via `note_speech_end`, which notifies the Turn Engine through
  the one existing pathway (`USER_FINISHED`).
* **Backdating.** A threshold is crossed after the fact, so each boundary reports how much audio
  ago it really happened and the tap subtracts it. Without this every `voice_to_voice_ms` would be
  understated by the whole silence hold.

### Pre-roll: none, and why

Nothing gates audio on the endpoint decision — the VAD tap is pass-through, so **STT receives every
frame from the start of the utterance regardless of when endpointing recognises it**. There is no
lost onset to recover, so no pre-roll buffer exists and none is needed. (K: no queue, no buffered
audio, O(1) state.)

### Turn Engine integration

`IDLE → LISTENING → USER_SPEAKING → USER_FINISHED` is unchanged and remains the Turn Engine's
alone; M11.8 adds no phase and no second state machine. Endpointing **never invokes an LLM or
TTS**: it closes the *user* half of the turn, and the response stays where it always was — driven
by a transcript through `_on_segment` / `_start_segment`.

Two orderings arise, and both are deterministic:

| Order | Behaviour |
|---|---|
| audio end, then Final | the transcript is answered immediately (the turn was already closed) |
| Final, then audio end | the text stays pending; it is answered at the next transcript or at stream end (**R-50**) |

An utterance that produces **no** transcript closes the user turn and is then retired as
`TURN_CANCELLED` when the next utterance starts, so a turn is never left wedged in `USER_FINISHED`.

### Interruption (unchanged)

M11.8 adds no interruption code. With `allow_interruptions=False` there is no interrupt framework
wired at all and ordinary speech cannot interrupt anything. With `allow_interruptions=True` the
existing M3.4 path consumes the same per-frame VAD verdicts it always did — now real ones. A
pre-empted turn clears the "a transcript is owed" flag; the detector keeps the caller's in-progress
utterance.

### Degraded voice activity

A provider failure is **not silence** (ADR-0047 ME8). `note_unavailable()` advances no counter and
produces no boundary, so an outage can neither end a turn nor invent one; the frame still passes
through to STT and the existing M11.4 auxiliary degradation is recorded. When the provider
recovers, the hold resumes exactly where it stopped. A hangup during an outage still closes the
turn — distinguishably, because a flush-based end backdates only the silence actually counted.

### `metric.turn` remap (ME1–ME8)

* The chain is now §A8's: `speech_end → endpoint_decided → stt_final → llm_first_token →
  tts_first_audio → first_audio_out`.
* **Every stage key name is preserved** (`endpoint`, `stt`, `llm`, `tts`, `egress`); only which
  instants they span changed. No version bump (ME2), no new event type (ME3).
* `voice_to_voice_ms` keeps its name, its `ms` unit and its meaning — measured from **true**
  end-of-speech for the first time (ME4).
* Additive: the `speech_start` boundary and a `user_speech` stage (`speech_start → speech_end`),
  deliberately **outside** the chain so the five chain stages still sum to `voice_to_voice_ms`.
* A segment-driven profile reaches `stt_final` before `endpoint_decided`, so its `stt` interval
  would be negative and is **dropped, never emitted** (ME6) — exactly what TP3a anticipated.
* An unreached boundary stays an absent key, never `0` (ME8). `metric.turn` remains in-memory;
  Epic 10 spine wiring is M11.12's (ADR-0050).

### Configuration (AR-25)

`StartSessionRequest.audio_endpointing` → `AgentSpec.audio_endpointing`, default `False`. Selecting
it **without a voice-activity provider is refused at session start** (`ConfigError`), the same
stance `allow_interrupt` takes — never a silent downgrade to segment-driven turns.

### Known limitations

* Thresholds are unmeasured defaults (R-49); §A8 compliance was preferred over pause tolerance.
* Final-before-end-of-speech defers the response to the next transcript or stream end (R-50).
* Turn-level consumers (Smart Turn, interrupt thresholds) still use their M3.x frame-count
  policies; only endpointing became duration-based (R-51).

## Extension points for M3.4+
Semantic / smart-turn EOT → an alternative engine or a policy that consumes an
LLM/classifier signal, plugged in behind the same coordinator surface · adaptive
delay → policy that scales `silence_hold_frames` by prosody / dialogue act ·
interrupt engine → react to `on_user_speech` during assistant playback ·
metrics → consume `EndpointEvent`s / timing (turn-completion latency). All plug in
without changing this subsystem's shape.

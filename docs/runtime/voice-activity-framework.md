# Runtime Component Spec — Voice Activity Framework (VAF)

| | |
|---|---|
| **Component** | `runtime.voice_activity` (`VoiceActivityEngine`, `VoiceActivityCoordinator`, `VoiceActivityContext`, `VoiceActivityState`) + capability `core.providers.voice_activity.VoiceActivityProvider` · **Status:** Implemented (M3.2) |
| **Format** | Constitution §A11.3 component specification |
| **Milestone** | M3.2 — Voice Activity Framework (2026-07-17) |

## Responsibility (single)
Own the **voice-activity model** — the deterministic, provider-neutral view of
whether the caller is speaking. M3.2 is the **framework only**: it derives a
clean activity state (with hysteresis) from a provider's raw per-frame verdicts
and supplies speech-activity signals to the Turn Engine. It does **not** decide
turns (the Turn Engine does), do semantic/adaptive endpointing, interruptions,
double-talk detection, echo cancellation, or noise suppression (all M3.3+). No
conversation-state ownership, no AI, no business logic.

## Provider model (new capability)
A new capability kind, **`VoiceActivityDetection`**
(`CapabilityKind.VOICE_ACTIVITY`), resolved through the Provider Registry like
every other capability. The contract `core.providers.voice_activity.VoiceActivityProvider`
answers one question per frame — `detect(frame) -> VoiceActivitySample(speech, confidence)`
— plus `start`/`stop` per session. Future engines (Silero, WebRTC VAD, Deepgram,
OpenAI Realtime, custom) implement this one contract and register via the
registry — **no Runtime changes** (Constitution §A6, AR-21). No vendor names or
branching anywhere in the runtime.

## Ownership (explicit — no component owns another's state)
```
SessionWorker            → worker lifecycle (WorkerState)
ConversationOrchestrator → conversation lifecycle (SessionState); creates the VAF
                           when a VAD provider is registered, exposes it read-only
VoiceActivityEngine      → voice-activity state (VoiceActivityState, sole writer)
TurnEngine               → turn lifecycle (the VAF notifies it; never writes it)
RuntimePipeline          → execution; taps inbound audio into the coordinator
ProviderRegistry         → provider resolution
```

## Sub-components
- **`VoiceActivityState`** — the activity states + `VA_TRANSITIONS` table.
- **`VoiceActivityContext` / `VoiceActivityTiming`** — per-session state + latest
  state-entry timestamps.
- **`VoiceActivityEngine`** — session-scoped; the sole writer of activity state;
  applies consecutive-frame hysteresis (`VoiceActivityConfig`); `observe(sample)`.
- **`VoiceActivityCoordinator`** — the pipeline's façade; runs the provider, feeds
  the engine, and notifies the Turn Engine on confirmed speech onset.
- **`VoiceActivityEvent` / `VoiceActivityObserver`** — the internal notification
  extension point (not the Event Bus, M4).
- **`LocalVoiceActivity`** (in `providers/`) — the deterministic dev/test detector.

## State model (deterministic, cyclic — no terminals)
```
 SILENCE ─► SPEECH_STARTING ─► SPEAKING ─► SPEECH_ENDING ─► SILENCE_CONFIRMED ─► SILENCE
              │  (false start)                │ (speech resumed)                     ▲
              └───────► SILENCE               └───────► SPEAKING                      │
   SILENCE_CONFIRMED is momentary — normalized back to SILENCE before the next cycle ┘
```
Table: `runtime.voice_activity.state.VA_TRANSITIONS`. The model is **cyclic** (a
conversation has many utterances) so there are no terminal states. Hysteresis:
`speech_start_frames` consecutive speech frames confirm `SPEAKING` (via
`SPEECH_STARTING`); `silence_end_frames` consecutive silence frames confirm the
end (via `SPEECH_ENDING`). Both default to 1 (single-frame confirm) and are
clamped to ≥ 1. Designed for extensibility — a richer engine may add finer states
additively (Constitution §A12) without changing existing edges.

> `VoiceActivityState` describes **acoustic activity** and is distinct from both
> `TurnPhase` (per-turn lifecycle, M3.1) and the Constitution's `core.states.TurnState`
> (fine speech-processing states). The VAF *feeds* turn onset; endpoint detection
> (which will consume `SILENCE_CONFIRMED`) is M3.3+.

## Runtime integration (behaviour unchanged; graceful fallback)
The Orchestrator resolves a VAD provider **only if one is registered**
(`registry.has(VOICE_ACTIVITY)`); otherwise no VAF is created and the pipeline
runs exactly as M3.1 (graceful fallback). When present, the `RuntimePipeline`
**taps the inbound audio stream**: each frame is passed to the coordinator
(`observe`) *and then* handed to STT byte-for-byte unchanged — STT/LLM/TTS
behaviour is identical either way. On confirmed speech onset the coordinator calls
the Turn Engine's idempotent `on_user_speech`, so VAD-driven and STT-driven turn
onset compose safely. The shipped local profile leaves VAD **off** by default
(`build_local_registry(include_voice_activity=True)` turns it on).

## Failure handling (DL-0013)
Voice activity is **auxiliary** — its failure must never break a call:
- **provider startup failure** → `VoiceActivityProviderError`; the pipeline logs
  and continues **without** VAD (degrades to the M3.1 path).
- **provider detect failure** → `VoiceActivityProviderError`; the pipeline logs
  and skips that frame (STT still receives it).
- **provider shutdown failure** → logged, swallowed (session is already ending).
- **invalid activity transition** → `InvalidVoiceActivityTransition` (strict; the
  model only proposes legal edges — a raise means a coding error, surfaced not
  hidden). No silent recovery in the state machine.
- **duplicate notifications** → a same-verdict frame simply causes no transition
  (`observe` returns `None`) — suppressed deterministically, not an error.
- **runtime cancellation** → `stop()` tears down the provider; the pipeline's
  `stop()` calls it best-effort.

## Timing captured (in `VoiceActivityContext.timing`)
Latest state-entry timestamps → named accessors: `speech_started_at`,
`speaking_at`, `speech_ending_at`, `silence_confirmed_at`. Captured only — no
analytics processing (M3.3+).

## Dependencies & boundaries
`runtime.voice_activity` imports `core` (audio, errors, the provider contract) and
stdlib only. Never imports `api`, the pipeline substrate, or a provider
implementation. Consumed by the pipeline (via the coordinator) and the
orchestrator (creates + exposes the engine).

## Extension points for M3.3+
Endpoint detection → consume `SILENCE_CONFIRMED` to drive `on_user_final` with
adaptive delay · interrupt engine → react to `SPEAKING` during assistant playback
to pre-empt the active turn · metrics → consume `VoiceActivityEvent`s / timing ·
real providers → register a streaming VAD engine (thresholds → 1). All plug in
without changing this subsystem's shape.

# Runtime Component Spec — Interrupt Intelligence Framework

| | |
|---|---|
| **Component** | `runtime.interrupt` (`InterruptEngine`, `InterruptCoordinator`, `InterruptContext`, `InterruptDecision`, `InterruptPolicy`) · **Status:** Implemented (M3.4) |
| **Format** | Constitution §A11.3 component specification |
| **Milestone** | M3.4 — Interrupt Intelligence Framework (2026-07-17) |

## Responsibility (single)
Own the **interruption model** — the deterministic, provider-neutral decision of
whether the caller is *barging in* (speaking while the assistant is speaking) —
and coordinate a **safe pre-emption** of assistant playback. M3.4 is barge-in
only: **no** semantic interruption prediction, speaker identification, wake-words,
echo cancellation, or noise suppression (all later milestones). It does **not**
own turns (the Turn Engine does), conversation state (the Orchestrator does),
voice-activity state (the VoiceActivityEngine does), endpoint decisions (the
Endpoint framework does), or do any AI / speech processing.

## Inputs (neutral signals, never provider objects)
Per the Constitution (§A6) the engine consumes **neutral runtime signals**:
- `note_assistant_started` / `note_assistant_finished` — assistant playback state
  (from the pipeline, around the response task).
- `note_user_speech` — a Voice Activity `SPEAKING` / per-frame speech verdict.
- `note_user_silence` — a Voice Activity silence verdict.
- the injected **`InterruptPolicy`** (runtime configuration).

The engine never imports a VAD/STT/TTS provider. The VAF supplies the per-frame
speech verdict; the pipeline supplies playback state.

## Ownership (explicit — no component owns another's state)
```
SessionWorker            → worker lifecycle (WorkerState)
ConversationOrchestrator → conversation lifecycle (SessionState); wires the interrupt
                           framework when opted-in + VAD present, exposes it read-only
InterruptEngine          → interrupt-decision state (InterruptDecision, sole writer)
TurnEngine               → turn lifecycle (the interrupt framework cancels the
                           interrupted turn; never writes turn state otherwise)
VoiceActivityEngine      → voice-activity state (feeds the interrupt framework)
EndpointEngine           → endpoint decisions (independent)
RuntimePipeline          → execution; pre-empts playback + drives the action phase
```

## Sub-components
- **`InterruptDecision`** — the decisions + `INTERRUPT_TRANSITIONS` table +
  `CONFIRMED_INTERRUPT`.
- **`InterruptPolicy`** — the configurable rules (nothing hardcoded).
- **`InterruptContext` / `InterruptTiming`** — per-session decision + latest
  decision-entry timestamps; `interrupt_count` counts confirmed barge-ins.
- **`InterruptEngine`** — session-scoped; the sole writer of decision state; applies
  the policy; detection signals + the action-phase advancers + `reset`.
- **`InterruptCoordinator`** — the pipeline/VAF façade; owns the `stop_requested`
  signal and cancels the interrupted turn on the Turn Engine.
- **`InterruptEvent` / `InterruptObserver`** — the internal notification extension
  point (not the Event Bus, M4).

## Lifecycle model (deterministic, cyclic — no terminals)
```
 IDLE ─(assistant starts)─► ASSISTANT_SPEAKING ─(user speech, policy)─► POTENTIAL_INTERRUPT
   ▲                              │                                          │
   │                     (assistant finishes)                    ┌───────────┴───────────┐
   │                              ▼                     (hold met)│                       │(silence / assistant done)
   │                            IDLE                              ▼                       ▼
   │                                                     INTERRUPT_CONFIRMED       INTERRUPT_CANCELLED
   │                                                              │                       │
   │                                              (pipeline pre-empts playback)           ▼
   │                                                              ▼              ASSISTANT_SPEAKING / IDLE
   │                                                     ASSISTANT_STOPPING
   │                                                              ▼
   │                                                     USER_TURN_STARTING ─(cancel interrupted turn)
   │                                                              ▼
   └──────────────────────────────────────────────────  INTERRUPT_COMPLETED
```
Table: `runtime.interrupt.decision.INTERRUPT_TRANSITIONS`. Detection runs
signal-driven up to `INTERRUPT_CONFIRMED`; the pipeline then drives the **action
phase** (`begin_stop` → `start_user_turn` → `complete`). The two momentary
outcomes — `INTERRUPT_COMPLETED` (a real interrupt ran) and `INTERRUPT_CANCELLED`
(a false alarm) — normalize back to a resting decision (`ASSISTANT_SPEAKING` if the
assistant is still playing, else `IDLE`) at the start of the next signal, mirroring
the Voice Activity / Endpoint engines. Every transition is validated; illegal or
duplicate moves raise (`InvalidInterruptTransition` / `DuplicateInterruptDecision`,
rooted in `core.errors.RuntimeConflict`).

## Policy (configurable — nothing hardcoded)
`InterruptPolicy` (frame counts = provider observations; deterministic):
| Knob | Meaning | Default |
|---|---|---|
| `min_interrupt_speech_frames` | sustained speech obs (after grace) to raise a potential | 1 |
| `confidence_threshold` | minimum confidence to accept an interrupt speech signal | 0.0 |
| `interrupt_hold_frames` | extra speech obs to hold a potential before confirming | 0 |
| `assistant_grace_frames` | leading speech obs after assistant start that never interrupt | 0 |
| `duplicate_suppression` | collapse repeat confirms within one assistant utterance | True |

## Runtime integration (opt-in; graceful fallback)
The Orchestrator wires the framework **only when the agent opts in**
(`AgentSpec.allow_interrupt`) **and** a VAD provider is registered — barge-in needs
a speech signal. Otherwise the pipeline runs the **M3.3 sequential path**
(uninterruptible playback), byte-for-byte unchanged (graceful fallback).

When active, `RuntimePipeline.run()` dispatches to an **interruptible loop** that
runs assistant playback as a concurrent task while it keeps reading inbound audio.
The VAF feeds the interrupt engine the per-frame speech verdict; on a confirmed
interrupt the coordinator sets `stop_requested`. The loop then, deterministically:
1. **stops assistant playback** (cancels the playback task; the send loop also
   cooperatively checks `stop_requested` between frames),
2. **cancels remaining assistant output** (the task is cancelled mid-stream),
3. **preserves consistency** — drives `begin_stop` → `start_user_turn` (which
   cancels the interrupted turn on the Turn Engine) → `complete`, and resets the
   endpoint's pending decision,
4. **begins a new user turn** — via the normal speech path (`on_user_speech`), so
   the Turn Engine stays the sole owner of turn state,
5. **continues normal execution** for the barge-in utterance.

## Failure handling (no silent recovery)
- **duplicate interrupts** → suppressed deterministically (a repeat speech signal
  after confirmation returns `None`; no illegal transition attempted).
- **invalid interruption transition** → `InvalidInterruptTransition` (strict).
- **interruption after assistant completed** → `note_assistant_finished` returns
  the engine to `IDLE`; later speech cannot interrupt a finished utterance.
- **interruption during shutdown / cancellation** → `cancel()`/`reset()` hard-reset
  to `IDLE` (recorded, deterministic); the pipeline's `stop()` cancels any in-flight
  playback task and the coordinator.
- **assistant stop failures** → surfaced as `AssistantStopError` (a lifecycle
  conflict); never swallowed.

## Timing captured (in `InterruptContext.timing`)
Latest decision-entry timestamps → named accessors: `assistant_speaking_at`,
`potential_at`, `confirmed_at`, `completed_at`, `cancelled_at`. Captured only — no
analytics processing (M3.5+).

## Performance posture (architectural readiness)
The confirm→stop path is synchronous within the run loop: `stop_requested` is set
the instant `INTERRUPT_CONFIRMED` is reached and the playback task is cancelled
immediately after — no polling delay in the decision path. Interruption ordering is
deterministic (single-writer engine + a single run loop). Cancelling the playback
task before starting the next response guarantees **no duplicated assistant
responses** and **no lost user turn**. Wall-clock latency (< 150 ms target) is a
provider/deployment property validated with a real streaming VAD — not optimized
here (architectural readiness only).

## Dependencies & boundaries
`runtime.interrupt` imports `core.errors` (via `runtime.errors`) and stdlib
(`asyncio` in the coordinator) only. Never imports `api`, the pipeline substrate,
or a provider implementation. Consumed by the pipeline and the VAF coordinator (via
`InterruptCoordinator`) and the orchestrator (creates + exposes the engine).

## Extension points for M3.5+
Semantic interruption prediction → an alternative engine / a policy that consumes an
LLM/classifier signal, behind the same coordinator surface · adaptive grace / hold →
policy scaled by dialogue act · interrupt metrics → consume `InterruptEvent`s /
timing (barge-in latency) · speaker-aware barge-in → gate on speaker identity
(needs speaker ID, a later milestone). All plug in without changing this
subsystem's shape.

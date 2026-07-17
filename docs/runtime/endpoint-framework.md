# Runtime Component Spec — Endpoint Intelligence Framework

| | |
|---|---|
| **Component** | `runtime.endpoint` (`EndpointEngine`, `EndpointCoordinator`, `EndpointContext`, `EndpointDecision`, `EndpointPolicy`) · **Status:** Implemented (M3.3) |
| **Format** | Constitution §A11.3 component specification |
| **Milestone** | M3.3 — Endpoint Intelligence Framework (2026-07-17) |

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

## Extension points for M3.4+
Semantic / smart-turn EOT → an alternative engine or a policy that consumes an
LLM/classifier signal, plugged in behind the same coordinator surface · adaptive
delay → policy that scales `silence_hold_frames` by prosody / dialogue act ·
interrupt engine → react to `on_user_speech` during assistant playback ·
metrics → consume `EndpointEvent`s / timing (turn-completion latency). All plug in
without changing this subsystem's shape.

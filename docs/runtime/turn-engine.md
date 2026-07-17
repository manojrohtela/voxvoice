# Runtime Component Spec — Turn Engine

| | |
|---|---|
| **Component** | `runtime.turn` (`TurnEngine`, `TurnCoordinator`, `TurnLifecycle`, `TurnContext`, `TurnPhase`) · **Status:** Implemented (M3.1) |
| **Format** | Constitution §A11.3 component specification |
| **Milestone** | M3.1 — Audio Turn Engine Foundation (2026-07-17) |

## Responsibility (single)
Own the **turn lifecycle** — the deterministic per-turn model of one
user-utterance → assistant-response cycle. M3.1 is the **framework only**: it
tracks turns and their timing when notified; it does **not** make conversations
smarter. No VAD, endpoint/semantic detection, interruptions, double-talk, or
adaptive delay (those plug in at M3.2+). No speech processing, no providers, no
conversation-state ownership.

## Ownership (explicit — no component owns another's state)
```
SessionWorker            → worker lifecycle (WorkerState)
ConversationOrchestrator → conversation lifecycle (SessionState) — creates the
                           TurnEngine, exposes it read-only, never touches turn state
TurnEngine               → turn lifecycle (TurnPhase, sole writer)
RuntimePipeline          → execution; notifies the TurnCoordinator (notify-only)
ProviderRegistry         → provider resolution
```

## Sub-components
- **`TurnPhase`** — the per-turn lifecycle phases + `TURN_TRANSITIONS` table.
- **`TurnContext` / `TurnTiming`** — one turn's data + phase-entry timestamps.
- **`TurnLifecycle`** — one turn's deterministic state machine (sole writer of a
  turn's phase); validates transitions, records timing, emits `TurnEvent`s.
- **`TurnEngine`** — session-scoped: holds the current turn + history; the sole
  writer of turn state; `begin_turn` / `advance` / `cancel_current`.
- **`TurnCoordinator`** — the Runtime Pipeline's integration façade; translates
  coarse pipeline signals into engine transitions.
- **`TurnEvent` / `TurnObserver`** — the internal notification extension point
  (not the Event Bus, M4).

## Lifecycle model
```
 IDLE ─► LISTENING ─► USER_SPEAKING ─► USER_FINISHED ─► ASSISTANT_THINKING
                                                              │      │
                                                              ▼      ▼
                                              ASSISTANT_SPEAKING ─► TURN_COMPLETED (terminal)
   any non-terminal phase ───────────────────────────────────────► TURN_CANCELLED (terminal)
```
Table: `runtime.turn.phase.TURN_TRANSITIONS`. Illegal moves raise; terminal
phases are immutable; duplicate (self) transitions are rejected. A new turn is a
fresh `TurnContext`/`TurnLifecycle`; completed/cancelled turns move to history.

> `TurnPhase` (coarse per-turn lifecycle) is distinct from the Constitution's
> finer `core.states.TurnState` (which adds ENDPOINTING/TOOL_WAIT). Those
> speech-processing states plug into this lifecycle as VAD / endpoint detection /
> tools land (M3.2+). Reconciliation is a future ADR (DL-0012).

## Timing captured (in `TurnContext.timing`)
Phase-entry timestamps → named accessors: `turn_started_at` (LISTENING),
`first_audio_at` (USER_SPEAKING), `user_finished_at`, `assistant_started_at`
(ASSISTANT_THINKING), `assistant_speaking_at`, `completed_at`. Captured only — no
analytics processing.

## Runtime integration (behaviour unchanged)
The `RuntimePipeline` takes an optional `TurnCoordinator` and calls it as pure
notifications interleaved with the existing flow: `on_user_speech` (partial),
`on_user_final` (final), `on_assistant_thinking`, `on_assistant_speaking`,
`on_turn_completed`; `cancel` on shutdown. When the coordinator is absent the
pipeline behaves **exactly as before** — turn tracking never affects speech,
transcripts, or audio. The Orchestrator creates the `TurnEngine` in
`_open_providers` and injects a coordinator; existing conversation behaviour is
identical (verified: transcripts + played-back audio unchanged).

## Failure handling
Invalid/duplicate/terminal transitions raise typed errors
(`InvalidTurnTransition`, `DuplicateTurnTransition`, `TerminalTurnError`, all
under `core.errors.RuntimeConflict`); `advance` with no active turn raises
`NoActiveTurnError`. Cancellation and shutdown terminalize the active turn
(`TURN_CANCELLED`). Deterministic — no silent recovery. An observer that raises
is swallowed and logged.

## Dependencies & boundaries
Imports `core.errors` and stdlib only. Never imports `api`, `providers`, or a
provider implementation. Consumed by the pipeline (via the coordinator) and the
orchestrator (creates + exposes the engine).

## Extension points for M3.2+
VAD → drives `on_user_speech`/turn-start with real speech detection (**done M3.2**,
via the Voice Activity Framework) · endpoint detection → decides `on_user_final`
(**done M3.3**: the [Endpoint Intelligence Framework](endpoint-framework.md) now
owns turn completion and calls `on_user_final` when a turn should finish — the Turn
Engine remains the sole writer of turn state) · interrupt engine → cancels/pre-empts
the active turn (**done M3.4**: the [Interrupt Intelligence Framework](interrupt-framework.md)
cancels the interrupted assistant turn via `cancel()` on a confirmed barge-in; the
new user turn begins via the normal `on_user_speech` path) · metrics → consume
`TurnEvent`s / `TurnTiming`. All plug in without changing this subsystem's shape.

> **M3.3 update:** the pipeline no longer calls `on_user_final` directly on an STT
> Final. The Endpoint Intelligence Framework decides when the turn is complete and
> issues that notification; the Turn Engine's contract is unchanged.
>
> **M3.4 update:** when barge-in is enabled the Interrupt Intelligence Framework
> `cancel()`s the pre-empted assistant turn (→ `TURN_CANCELLED`); the Turn Engine
> API is unchanged and remains the sole writer of turn state.
>
> **M3.5 update (Epic 3 complete):** the [Smart Turn Intelligence Engine](smart-turn-framework.md)
> arbitrates the endpoint/interrupt decisions into the final conversational-timing
> decision the pipeline consumes. It only *reads* the sub-frameworks' outputs (Turn
> phase included) — it writes none of their state. The Turn Engine remains the sole
> owner of turn lifecycle; its contract is unchanged.

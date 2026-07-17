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
VAD → drives `on_user_speech`/turn-start with real speech detection · endpoint
detection → decides `on_user_final` (adaptive delay) · interrupt engine →
cancels/pre-empts the active turn · metrics → consume `TurnEvent`s / `TurnTiming`.
All plug in without changing this subsystem's shape.

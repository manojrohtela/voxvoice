# Component Spec — Conversation Replay & Observability Platform

| | |
|---|---|
| **Component** | `replay` (`ConversationRecorder`, `ReplaySession`, `ReplayEngine`, `ReplayController`, `TimelineBuilder`, `EventIndex`, `TimelineCursor`, `SessionInspector`, `EventCorrelator`, `MetricsCollector`, `ReplaySerializer`) · **Status:** Implemented (M4.3) |
| **Format** | Constitution §A5 · ADR-0004/0006 · component spec §A11.3 |
| **Milestone** | M4.3 — Conversation Replay & Observability (2026-07-17) |

## Responsibility (single)
Record every Internal Event Bus event and turn it into **deterministic,
visualization-only** replay + observability: a colour-coded timeline, a
filterable/searchable event index, event correlation, derived metrics, and a
point-in-time runtime-state inspector, driven by a playback controller. It owns
**recording + read-only views only** — it never creates events and **never modifies
Runtime behaviour**. No Runtime logic runs during replay (replay is visualization
only). M4.3 is in-process; durable storage/persistence is a non-goal.

## Backend architecture
```
Runtime ─► Internal Event Bus ─► Live Event Gateway (M4.2)
                    │  subscribe (read-only, isolated)
                    ▼
            ConversationRecorder ──► ReplaySession  (ordered event log per conversation)
                                          │
                                          ▼  ReplayEngine (pure, read-only views)
        ┌───────────────┬───────────────┬──────────────┬───────────────┬──────────────┐
   TimelineBuilder   EventIndex   EventCorrelator  MetricsCollector  SessionInspector
     (+Cursor)      (+EventFilter)                                          │
        │                                                                   │
        └──────────────► ReplayController (play/pause/step/jump/speed) ◄────┘
                                          │
                                    ReplaySerializer ──► JSON ──► API / UI
```
- **`ConversationRecorder`** — `attach(bus)` subscribes read-only; records each event
  into a per-session `ReplaySession`; fully isolated (a recording failure is counted,
  never raised into the bus).
- **`ReplaySession`** — the ordered event log for one conversation (the single source
  of truth); `closed` on `session.ended`.
- **`ReplayEngine`** — assembles the read-only views for one session.
- **`TimelineBuilder` / `Timeline` / `TimelineEntry`** — colour-coded, laned entries
  with paired durations (speech / turn / assistant-speaking). **`TimelineCursor`** is
  the deterministic playback position.
- **`EventIndex` / `EventFilter`** — filter + search.
- **`EventCorrelator`** — correlation chains by `correlation_id` and by turn.
- **`MetricsCollector` / `ConversationMetrics`** — derived metrics.
- **`SessionInspector` / `RuntimeStateSnapshot`** — point-in-time runtime state.
- **`ReplayController`** — transport controls over the cursor.
- **`ReplaySerializer`** — stable JSON for the API/UI.

## Recording flow
`recorder.attach(session.event_bus)` → `bus.subscribe(_record)`. Every published
`Event` is appended (in bus `seq` order) to `ReplaySession[event.session_id]`;
`session.ended` closes it. Recording only ever **reads** envelopes — no Runtime
object is retained and no Runtime call is made, so the Runtime is unaffected and
unaware (verified: `runtime/`/`core/`/`events/`/`gateway/` are byte-for-byte
unchanged from M4.2). Isolation is double-guarded (the bus isolates subscribers; the
recorder also catches internally).

## Event correlation
`EventCorrelator.by_turn` groups events sharing a conversation `turn`; `by_correlation`
groups by `correlation_id`; `related(event)` returns everything correlated to one
event (same turn or correlation id), in recorded order. Deterministic — order is the
bus `seq` preserved from recording.

## Replay flow (deterministic; no Runtime logic)
`ReplayController` drives a `TimelineCursor`: `play/pause/resume/stop`,
`step_forward/backward`, `jump_to_timestamp`, `jump_to_event`, `set_speed`. Real-time
playback is driven by the caller feeding elapsed wall-time into `advance(elapsed_s)`
— the controller scales it by `speed` into *virtual recorded time* and moves the
cursor — so replay is fully deterministic and testable (no background timers, no
hidden clock). Every view is a pure function of the recorded event list; replaying a
session mutates nothing and executes no Runtime code.

## Filtering & metrics
Filter by **session · conversation · turn · agent · org · provider · event-type ·
time-range** (`EventFilter`); free-text **search** across type/source/payload.
Metrics derived deterministically: turn count / completed / cancelled, avg & total
turn duration, avg response time (turn-start → assistant-speaking), speech time &
segments, assistant-speaking time, interrupt count, endpoint count, smart-turn
decisions, provider usage, errors. **Honesty:** metrics that depend on subsystems not
yet emitting events — **thinking time, token usage, tool calls, memory access,
warnings** — are reported as `0` and listed in `ConversationMetrics.pending_metrics`
(source events land with M5 prompt/tool/memory runtime). The UI renders these as
`—·pending`, never a fabricated value.

## Ownership (strict, downward)
```
Runtime → execution · Event Bus → internal delivery · Gateway → external streaming
Replay Platform → recording + deterministic replay/observability (read-only)
```
`replay/` imports `core` (`Event`/`EventBus`) + `events` (the bus type for tests) +
stdlib — **never** `runtime`/`api`/`gateway` (verified by AST scan). Clean DAG:
`core ← replay` (with `events` for the concrete bus in composition/tests).

## Failure handling
Recording failure → counted (`failure_count`), never raised into the bus. Out-of-
range cursor → `CursorOutOfRange`. Unknown session → `UnknownSession`. Malformed
filter → `InvalidFilter`. All root in `core.errors.PlatformError`. No client/replay
failure can reach the Runtime.

## API design (serialized views)
`ReplaySerializer` emits stable JSON for: `session_summary`, `timeline` (entries with
`seq/ts/type/source/category/lane/color/label/turn/duration_ms`), `event` (full
envelope), `metrics` (`ConversationMetrics.to_dict`), `snapshot`
(`RuntimeStateSnapshot`). A future `api/` layer exposes these as read endpoints
(`GET /replay/{session}/timeline|metrics|events|state?at=`) — additive, no Runtime
change (M4.4).

## UI (design)
A dark-theme observability UI (published as an interactive artifact) — six screens
(Conversation Explorer, Conversation Detail, **Replay Timeline** (hero), Event
Inspector, Runtime Inspector, Metrics Dashboard) + a component library. Every
component maps to a backend object (Timeline→`TimelineBuilder`, Event
Inspector→`ReplaySerializer.event`, Runtime Inspector→`SessionInspector.state_at`,
Metric Card→`ConversationMetrics`, Filter Chip→`EventFilter`, Command
Palette→`EventIndex.search`, Playback Controls→`ReplayController`). Pending metrics
are shown as `—·pending`, mirroring `pending_metrics` — no fake UI.

## Extension points for M4.4+
Durable session store + retention · read API endpoints in `api/` (serve the
serialized views) · live tail (subscribe the recorder output to the gateway) · the
pending metrics once M5 emits thinking/token/tool/memory events · export (HAR-like) ·
a metrics time-series backend. All additive — the envelope, bus, gateway, and Runtime
are untouched.

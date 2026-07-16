# Runtime Component Spec — SessionWorker

| | |
|---|---|
| **Component** | `runtime.worker.SessionWorker` · **Status:** Implemented (M2A) · **Owner:** Technical Owner |
| **Format** | Constitution §A11.3 component specification (mandatory for every runtime component) |
| **Milestone** | M2A — Session Worker Foundation (2026-07-16) |

## Responsibilities (single)
Own the **execution lifecycle of exactly one voice session**: startup, run,
graceful shutdown, cancellation, guaranteed cleanup, and health/metadata
exposure. Nothing else — no conversation orchestration, turn detection, memory,
provider implementations, audio, playback, tools, or AI. Those are later
milestones and must not leak into this class (business logic is prohibited here).

## Inputs
- `RuntimeContext` — the dependency-injection seam: `SessionContext` (the one
  session's identity + consumer metadata), `RuntimeDependencies` (capability
  provider *interfaces* + metrics, all optional at M2A), and
  `WorkerConfiguration` (worker id, startup/shutdown timeouts).
- `on_lifecycle` (optional) — a synchronous `LifecycleObserver` notified on every
  state change.

## Outputs
- Lifecycle signals (`LifecycleSignal`) to the observer + structured logs.
- `health` — a dict: worker/session id, state, `healthy`, uptime.
- `metadata` — an immutable `RuntimeMetadata` snapshot.

## Dependencies
`core.errors` (typed base), `runtime.contracts`, `runtime.state`. Standard
library (`asyncio`) for concurrency. **Never** imports `api`; **never**
instantiates a provider (AR-20/21) — providers are received via
`RuntimeDependencies` and merely stored for future components.

## Lifecycle (state machine)
```
CREATED → INITIALIZING → READY → RUNNING → STOPPING → STOPPED
   └────────────────── (STOPPING from CREATED: stop-before-start) ┘
   any non-terminal ── on error ──► FAILED
```
Defined in `runtime/state.py` (`WORKER_TRANSITIONS`). STOPPED and FAILED are
terminal; lifecycle calls on a terminal worker are no-ops. Illegal transitions
raise `InvalidWorkerTransition`. **Deterministic:** the same call sequence always
yields the same transitions.

> This is the **worker's execution lifecycle** and is intentionally distinct from
> the **conversation lifecycle** (`core.states.SessionState`, owned by the
> Conversation Orchestrator at M2B+). One concept, one name.

### Methods
- `await start()` — CREATED→INITIALIZING→READY→RUNNING; spawns a cancellable run
  task. The run task idles (`await stop_event`) at M2A — the conversation loop is
  wired in from M2B.
- `await stop()` — graceful: signal + drain the run task (bounded by
  `shutdown_timeout_s`, cancels if exceeded), guaranteed cleanup, →STOPPED.
- `await cancel()` — forceful: cancel the run task immediately, guaranteed
  cleanup, →STOPPED. Both stop/cancel are idempotent once terminal.

## Emitted events
Lifecycle signals only: `worker_created` … `worker_ready`, `worker_running`,
`worker_stopping`, `worker_stopped`, `worker_failed` (as `LifecycleSignal`s +
logs). The full typed Event Bus (`core.events`) is **M4**; a bridge can adapt
these signals into it then.

## Failure modes → recovery
| Failure | Result |
|---|---|
| Initialization/startup error | →FAILED, cleanup runs, `WorkerStartupError` raised |
| Shutdown error | →FAILED, cleanup runs, `WorkerShutdownError` raised |
| Illegal transition | `InvalidWorkerTransition` raised |
| Observer raises | swallowed + logged (observability is best-effort; never breaks the worker) |

Cleanup (`_cleanup`) is **idempotent** and runs on every stop/cancel/failure
path. No silent failures — every failure is logged, signalled, and typed. Snapshot-
based session recovery (Constitution §A7) arrives with the Memory Engine (M5).

## Concurrency & determinism
One session per worker. No shared mutable state; no runtime globals — all state is
instance state. `start`/`stop`/`cancel` are async; the run task is held on the
instance (no fire-and-forget) and always awaited/cancelled on teardown. No
blocking calls. (Sequential lifecycle calls assumed; concurrent start/stop on one
instance is out of scope for M2A.)

## Update (M2B.2 — hosts the Conversation Orchestrator)
As of M2B.2 the worker **hosts one `ConversationOrchestrator`** (created + begun in
`_initialize`, driven via `_run` → `orchestrator.run()`, ended in `stop`/`cancel`)
and exposes `conversation_state` read-only. The worker still owns only the worker
lifecycle (`WorkerState`); it never writes conversation state — the orchestrator is
the sole writer. See [`conversation-orchestrator.md`](conversation-orchestrator.md).

## Not in scope (later milestones)
Provider/transport wiring + the turn loop (M2C) · Turn Intelligence (M3) · Event
Bus emission (M4) · memory/snapshots (M5).

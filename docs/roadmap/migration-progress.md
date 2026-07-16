# VoxVoice — Migration Progress (M0–M6)

| | |
|---|---|
| **Document** | `docs/roadmap/migration-progress.md` · **Status:** Living · **Owner:** Technical Owner |
| **Tracks** | Execution of the Constitution migration roadmap (Part B) from prototype → first-party runtime. |
| **Updated** | 2026-07-16 |

| Milestone | Scope | Status |
|---|---|---|
| **M0 — Foundation freeze** | git init, security/.gitignore, ADR extraction, Dograh knowledge archived, baseline snapshot, governance | ✅ Done (tag `foundation-v1`) |
| **M1 — Repo reshape** | Create `core/`; runtime/providers/events/observability skeletons; delete engine-adapter axis; session-start → 503; break `api⇄webhooks` cycle | ✅ Done (branch `chore/m1-repo-reshape`) |
| **M2A — Session Worker foundation** | `SessionWorker` lifecycle owner (start/stop/cancel/cleanup, deterministic FSM, DI seam, health) — no conversation/provider logic | ✅ Done (branch `feat/m2a-session-worker`) |
| **M2B.1 — Conversation State Machine** | Deterministic conversation-lifecycle FSM over `core.states.SessionState` (validation, guards, immutable history, typed errors, notifications) — no orchestration | ✅ Done (branch `feat/m2b1-conversation-state-machine`) |
| **M2B.2 — Conversation Orchestrator** | Sole-writer orchestrator drives the state machine (begin/run/end/cancel + failure routing); hosted by SessionWorker; providers via interfaces only — no provider execution/turn/AI | ✅ Done (branch `feat/m2b2-conversation-orchestrator`) |
| **M2C.1 — Provider Registry** | Capability-based provider reference management (register/unregister/resolve/list, validation, hot-swap, thread-safe) — manages references only, no execution | ✅ Done (branch `feat/m2c1-provider-registry`) |
| **M2C.2 — Provider wiring + turn loop** | Implement first STT/TTS/LLM/transport plugins; wire the orchestrator's `_open_providers`/`_close_providers` to resolve them via the registry; port the voicebot turn loop; transcripts live via events; restore session start | ⏳ Next (awaiting approval) |
| **M3 — Streaming & Turn Intelligence** | Streaming STT plugin; endpoint scorer + interrupt engine; latency taps + `metric.turn` (exit: measured p50 ≤ 1.2 s) | ▫ Pending |
| **M4 — Event surface & SDK** | Event Bus impl + outbox + retry/DLQ; WS event gateway; mid-call control API; client SDK | ▫ Pending |
| **M5 — Memory & prompt runtime** | Context builder, rolling summary, entity memory, metadata templating, snapshot/reconnect | ▫ Pending |
| **M6 — Moat & hardening** | Target-language provider plugins + evals; smart-turn EOT; state-machine timeouts; load test; tool runtime | ▫ Pending |

## M1 outcome (2026-07-16)

**Structure now matches Constitution §A14.** New packages: `core/` (contracts,
event types, state defs, errors, ids, provider interfaces), `runtime/` (skeleton:
worker + orchestrator/turn/ai/memory/speech/pipeline), `providers/` (empty
capability homes), `events/` (decoupled webhook dispatcher; bus/outbox at M4),
`observability/` (skeleton).

**Removed** (ADR-0001/0007): `adapters/` (Dograh/LiveKit/factory), the
`/v1/engine/dograh/events` router, the transcript poller (`ingest.py`),
`Agent.engine_workflow_id`, the Dograh webclient harness, engine/Dograh settings.

**Behaviour change (Constitution-mandated, DL-0005):** `POST /v1/sessions`
returns **503** until the Runtime Plane lands at M2. All other endpoints
(agents CRUD, auth, session read/end, transcript) unchanged. Org-scoping preserved
and still tested.

**Quality gates:** 8 tests pass · imports resolve · ruff clean (all packages) ·
zero import-layer violations · `api⇄webhooks` cycle eliminated · doc links resolve.

**Open debt into M2:** `Session.engine` field unused until the runtime sets it;
`voicebot/bot.py` 2 ruff findings (resolved when promoted to a plugin at M2);
orphaned Dograh deploy scripts under `platform/scripts/` (superseded, kept for
Oracle/TURN knowledge).

## M2A outcome (2026-07-16)

First Runtime Plane behaviour: `runtime.worker.SessionWorker` owns exactly one
session's **execution lifecycle** (spec: [`docs/runtime/session-worker.md`](../runtime/session-worker.md)).
Added `runtime/state.py` (WorkerState + transitions — distinct from the
conversation `SessionState` in core), `runtime/contracts.py` (SessionContext,
RuntimeContext, RuntimeMetadata, RuntimeDependencies, WorkerConfiguration,
LifecycleSignal/Observer), `runtime/errors.py` (worker error hierarchy under
`core.errors.PlatformError`), and 12 lifecycle tests.

Deterministic FSM (CREATED→INITIALIZING→READY→RUNNING→STOPPING→STOPPED, FAILED on
error); graceful async start/stop/cancel; guaranteed idempotent cleanup on every
path; dependency injection (providers stored, never instantiated); lifecycle
observability via injected observer + logs. **No** conversation/turn/memory/
provider/AI logic; no runtime globals.

**Quality gates:** 20 tests pass (12 new + 8 existing) · ruff clean · zero
import-layer violations (`runtime` imports no `api`; substrate confined). See
DL-0006 for the "one session per worker" interpretation.

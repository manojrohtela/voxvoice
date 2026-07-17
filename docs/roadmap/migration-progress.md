# VoxVoice — Migration Progress (M0–M6)

| | |
|---|---|
| **Document** | `docs/roadmap/migration-progress.md` · **Status:** Living · **Owner:** Technical Owner |
| **Tracks** | Execution of the Constitution migration roadmap (Part B) from prototype → first-party runtime. |
| **Updated** | 2026-07-17 |

| Milestone | Scope | Status |
|---|---|---|
| **M0 — Foundation freeze** | git init, security/.gitignore, ADR extraction, Dograh knowledge archived, baseline snapshot, governance | ✅ Done (tag `foundation-v1`) |
| **M1 — Repo reshape** | Create `core/`; runtime/providers/events/observability skeletons; delete engine-adapter axis; session-start → 503; break `api⇄webhooks` cycle | ✅ Done (branch `chore/m1-repo-reshape`) |
| **M2A — Session Worker foundation** | `SessionWorker` lifecycle owner (start/stop/cancel/cleanup, deterministic FSM, DI seam, health) — no conversation/provider logic | ✅ Done (branch `feat/m2a-session-worker`) |
| **M2B.1 — Conversation State Machine** | Deterministic conversation-lifecycle FSM over `core.states.SessionState` (validation, guards, immutable history, typed errors, notifications) — no orchestration | ✅ Done (branch `feat/m2b1-conversation-state-machine`) |
| **M2B.2 — Conversation Orchestrator** | Sole-writer orchestrator drives the state machine (begin/run/end/cancel + failure routing); hosted by SessionWorker; providers via interfaces only — no provider execution/turn/AI | ✅ Done (branch `feat/m2b2-conversation-orchestrator`) |
| **M2C.1 — Provider Registry** | Capability-based provider reference management (register/unregister/resolve/list, validation, hot-swap, thread-safe) — manages references only, no execution | ✅ Done (branch `feat/m2c1-provider-registry`) |
| **M2C.2 — Runtime Pipeline & first conversation** | RuntimePipeline (Transport→STT→LLM→TTS→Transport via contracts); local provider profile; orchestrator resolves providers via the registry + coordinates their lifecycle; transcripts via injected sink; **`POST /v1/sessions` restored** through the Runtime Plane. First complete end-to-end conversation. | ✅ Done (branch `feat/m2c2-runtime-pipeline`) |

**Epic 2 complete.** The Runtime Plane executes a real multi-turn conversation end-to-end (local provider profile) without bypassing any architectural boundary; the prototype voicebot path is no longer primary.

**Epic 2 Production-Readiness Gate (2026-07-17):** passed. Critical audit found no
ownership violations, no import cycles, zero layer-boundary violations, and a clean
linear branch stack (`main` fast-forwardable to `feat/m2c2-runtime-pipeline`, 6
commits, no merges/conflicts). Safe improvements applied: stale post-M2C.2
docstrings refreshed, a duplicated webhook-target call removed, +2 tests
(concurrent-session isolation, api runtime-failure → 502). 101 tests pass, ruff
clean. Certified ready for a **v0.1 Runtime Foundation** tag on merge. Open items
before Epic 3: rotate exposed keys (DL-0002), merge the stack.

**Epic 2.1 — Architecture Consolidation (2026-07-17):** removed `RuntimeDependencies`,
the vestigial second provider-provision seam — the `ProviderRegistry` is now the
single mechanism (DL-0011). Nothing read its values, so runtime behaviour is
unchanged; 2 obsolete DI tests removed. 99 tests pass, ruff clean, no cycles,
boundaries intact. No other true duplication found (worker vs conversation state
machines are complementary, different domains).
| **M3 — Streaming & Turn Intelligence** | Streaming STT plugin; endpoint scorer + interrupt engine; latency taps + `metric.turn` (exit: measured p50 ≤ 1.2 s) | ▫ Pending |
| **M4 — Event surface & SDK** | Event Bus impl + outbox + retry/DLQ; WS event gateway; mid-call control API; client SDK | ▫ Pending |
| **M5 — Memory & prompt runtime** | Context builder, rolling summary, entity memory, metadata templating, snapshot/reconnect | ▫ Pending |
| **M6 — Moat & hardening** | Target-language provider plugins + evals; smart-turn EOT; state-machine timeouts; load test; tool runtime | ▫ Pending |

### Epic 3 — Voice Intelligence
| Milestone | Scope | Status |
|---|---|---|
| **M3.1 — Turn Engine foundation** | Per-turn lifecycle subsystem (TurnEngine/TurnCoordinator/TurnLifecycle/TurnContext/TurnPhase); deterministic phases + timing; notify-only pipeline integration (behaviour unchanged); no VAD/endpointing/interruptions | ✅ Done (branch `feat/m3.1-turn-engine`) |
| **M3.2 — Voice Activity Framework** | Provider-neutral VAD: `VoiceActivityDetection` capability (registry-resolved), `VoiceActivityEngine` deterministic activity model + hysteresis, `VoiceActivityCoordinator` taps inbound audio + notifies the Turn Engine; deterministic `LocalVoiceActivity` provider; graceful fallback when unconfigured (behaviour unchanged); no semantic/adaptive endpointing, interruptions, double-talk, noise suppression | ✅ Done (branch `feat/m3.2-voice-activity`) |
| **M3.3 — Endpoint Intelligence Framework** | Provider-neutral turn-completion decisions: `EndpointEngine` deterministic decision model + configurable `EndpointPolicy` (min speech, silence hold, min silence, confidence, timeout), `EndpointCoordinator` consumes neutral speech/segment/silence signals and notifies the Turn Engine (`on_user_final`); **replaces the pipeline's STT-final heuristic** (always wired); VAD signals enrich decisions but never trigger completion; default policy reproduces pre-M3.3 behaviour (no regression). Trigger-vs-decision rationale DL-0014 | ✅ Done (branch `feat/m3.3-endpoint-intelligence`) |
| **M3.4+ — interrupt engine / smart-turn EOT** | Interruptions / barge-in; semantic (LLM) endpointing + adaptive delay; independent acoustic-silence-triggered completion; real streaming VAD provider plugins | ▫ Pending |

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

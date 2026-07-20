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
| **M4 — Event surface & SDK** | Event Bus impl (**M4.1 ✅**) + outbox + retry/DLQ; WS event gateway; mid-call control API; client SDK | ◐ In progress |
| **M5 — Memory & prompt runtime** | Context builder, rolling summary, entity memory, metadata templating, snapshot/reconnect | ▫ Pending |
| **M6 — Moat & hardening** | Target-language provider plugins + evals; smart-turn EOT; state-machine timeouts; load test; tool runtime | ▫ Pending |

### Epic 3 — Voice Intelligence
| Milestone | Scope | Status |
|---|---|---|
| **M3.1 — Turn Engine foundation** | Per-turn lifecycle subsystem (TurnEngine/TurnCoordinator/TurnLifecycle/TurnContext/TurnPhase); deterministic phases + timing; notify-only pipeline integration (behaviour unchanged); no VAD/endpointing/interruptions | ✅ Done (branch `feat/m3.1-turn-engine`) |
| **M3.2 — Voice Activity Framework** | Provider-neutral VAD: `VoiceActivityDetection` capability (registry-resolved), `VoiceActivityEngine` deterministic activity model + hysteresis, `VoiceActivityCoordinator` taps inbound audio + notifies the Turn Engine; deterministic `LocalVoiceActivity` provider; graceful fallback when unconfigured (behaviour unchanged); no semantic/adaptive endpointing, interruptions, double-talk, noise suppression | ✅ Done (branch `feat/m3.2-voice-activity`) |
| **M3.3 — Endpoint Intelligence Framework** | Provider-neutral turn-completion decisions: `EndpointEngine` deterministic decision model + configurable `EndpointPolicy` (min speech, silence hold, min silence, confidence, timeout), `EndpointCoordinator` consumes neutral speech/segment/silence signals and notifies the Turn Engine (`on_user_final`); **replaces the pipeline's STT-final heuristic** (always wired); VAD signals enrich decisions but never trigger completion; default policy reproduces pre-M3.3 behaviour (no regression). Trigger-vs-decision rationale DL-0014 | ✅ Done (branch `feat/m3.3-endpoint-intelligence`) |
| **M3.4 — Interrupt Intelligence Framework** | Provider-neutral barge-in: `InterruptEngine` deterministic decision model + configurable `InterruptPolicy` (min interrupt speech, confidence, hold, assistant grace, duplicate suppression), `InterruptCoordinator` (owns the `stop_requested` signal + cancels the interrupted turn); pipeline runs an **interruptible concurrent playback path** that safely pre-empts the assistant and starts a new user turn. Opt-in (`AgentSpec.allow_interrupt` + VAD); graceful fallback to the M3.3 sequential path. Opt-in/per-frame-verdict rationale DL-0015 | ✅ Done (branch `feat/m3.4-interrupt-intelligence`) |
| **M3.5 — Smart Turn Intelligence Engine** | Deterministic **arbiter** combining Turn/Voice-Activity/Endpoint/Interrupt outputs + adaptive `SmartTurnPolicy` (adaptive silence, adaptive grace, dynamic interruption threshold, confidence weighting, timing smoothing) + an optional **registry-resolved semantic evaluator** (`core.providers.semantic_turn`, `CapabilityKind.SEMANTIC_TURN`) into the final conversational-timing decision the pipeline consumes; sub-frameworks stay independent (no ownership overlap). Opt-in (`AgentSpec.smart_turn`); identity default → unchanged behaviour; runtime never calls an LLM. Arbiter/semantic-capability rationale DL-0016 | ✅ Done (branch `feat/m3.5-smart-turn`) |

**Epic 3 — Voice Intelligence complete.** The five Turn Intelligence subsystems
(Turn Engine → Voice Activity → Endpoint → Interrupt → Smart Turn) are implemented,
deterministic, provider-neutral, and independently owned; Smart Turn arbitrates them
into human-like conversational timing with a semantic extension point that future
models plug into with no runtime change. Default profiles preserve behaviour
end-to-end (223 tests). Next: Epic 4 (Event surface & SDK).

### Epic 4 — Event surface & SDK
| Milestone | Scope | Status |
|---|---|---|
| **M4.1 — Internal Event Bus Foundation** | The single internal communication mechanism: `core.events` envelope (refined additively — source/correlation/conversation/metadata) + deterministic `EventBus` contract; `events.InProcessEventBus` (dispatcher/registry/publisher/subscriber) — ordered, exactly-once in-process, subscriber-isolated, reentrancy-safe, type-filtered; `runtime.event_bridge` publishes each engine's events with zero coupling (engines never know consumers); orchestrator publishes runtime/session/provider lifecycle. In-process only (no external transports/durability). Behaviour unchanged (239 tests). Refinement/sync-delivery rationale DL-0017 | ✅ Done (branch `feat/m4.1-event-bus`) |
| **M4.2 — Live Event Gateway** | External event streaming: `gateway.LiveEventGateway` attaches to the bus (public contract) and streams to clients over **WebSocket + SSE** (same versioned envelope, different framing); ConnectionManager (lifecycle + auth hook + heartbeat/idle), SubscriptionManager (session/conversation/agent/org/all scopes + type filter + authz hook), EventStream (bounded, ordered, non-blocking, backpressure/slow-consumer policy), EventSerializer (versioned JSON + compression/binary hooks), GatewayMetrics. Purely additive `gateway/` package (imports `core` only) — **Runtime byte-for-byte unchanged, never knows clients**. Auth/authz/RBAC/rate-limit/audit are hooks only. In-process only. Additive-edge rationale DL-0018 | ✅ Done (branch `feat/m4.2-event-gateway`) |
| **M4.3 — Conversation Replay & Observability** | `replay.ConversationRecorder` records the Event Bus read-only into per-session `ReplaySession`s; `ReplayEngine` builds deterministic, visualization-only views — `TimelineBuilder` (colour-coded, laned, paired durations) + `TimelineCursor`, `EventIndex`+`EventFilter` (session/conversation/turn/agent/org/provider/type/time + search), `EventCorrelator`, `MetricsCollector` (honest `pending_metrics` for not-yet-emitted signals), `SessionInspector` (point-in-time runtime state), `ReplayController` (play/pause/step/jump/speed), `ReplaySerializer`. Purely additive (imports `core`+`events`) — **Runtime byte-for-byte unchanged**; no Runtime logic runs during replay. Dark-theme observability UI shipped as an interactive artifact (6 screens + component library, each mapped to a backend object). Read-only/deterministic/honest-metrics rationale DL-0019 | ✅ Done (branch `feat/m4.3-replay-observability`) |
| **M4.4+ — durable store / read API / SDK / control** | Durable session store + retention; read API endpoints in `api/` (serve serialized replay views); live tail; mid-call control API; client SDK; the pending metrics once M5 emits thinking/token/tool/memory events; durable outbox + WS/SSE routes; gRPC/MQTT; distributed broker | ▫ Pending |

### Epic 5 — AI Runtime (post Architecture Summit)
| Milestone | Scope | Status |
|---|---|---|
| **Architecture Summit** | Independent architecture review + design of the full AI Runtime (Memory/Prompt/Knowledge/Context/Reasoning/Tool/MCP/Workflow/Agent) as bus-attached, provider-neutral, deterministic modules around a hexagonal core; ports-and-adapters; consolidate the 5 turn engines; generalize the pipeline; add tenancy/storage seams | ✅ Done (design; approved) |
| **M5.0 — AI Runtime Foundations** | Shared infrastructure only (no AI): `core/fsm` (deterministic FSM), `core/module` (RuntimeModule + BusConsumer, replay/gateway-compatible), content-event vocabulary (defined only), `core/ports` (8 dependency-inversion interfaces), `core/policies` (generic), `core/providers/stores` (Object/Document/Metadata/Vector/Embedder contracts), `telemetry/` (metrics/tracing/logging + OTel/Prom extension points), `tenant/` (context + RBAC/quota/audit/policy interfaces). Fully additive — runtime/events/gateway/replay untouched. Rationale DL-0020 | ✅ Done (branch `feat/m5.0-ai-runtime-foundations`) |
| **M5.1A — Memory Runtime Design** | Full Memory Runtime architecture (types, components, repository/storage/event/retrieval/policy/tenancy/observability, extension points, risks, M5.1B plan) reusing the M5.0 abstractions; design only | ✅ Done (design; approved) |
| **M5.1B-1 — Memory Runtime Core** | New additive `memory/` package: domain model, `core.fsm` record lifecycle, repository layer over the store contracts, deterministic local adapters + embedder, serializer, config, factory, and `MemoryRuntime` (sync `MemoryPort` over async core; store/retrieve/forget with `memory.*` events → Replay/Gateway free). Retrieval/ranking/policies/expiration/cache/ingestion are non-goals. Fully additive (runtime/events/gateway/replay/core untouched; 314 tests). Rationale DL-0021 | ✅ Done (branch `feat/m5.1b-1-memory-runtime-core`) |
| **M5.1B-2 — Memory Runtime Intelligence** | Completes the Memory Runtime: query planner + retrieval engine (keyword/semantic/hybrid, conversation/session/user/org/agent scopes) + deterministic ranking; policy engine (validation/privacy/merge/conflict/expiration/retention/capacity); hot-object cache behind the repository; content-event ingestion (Conversation/Semantic/Episodic + access feedback); tenant isolation via M5.0 hooks; observability; Reranker/Summarizer/Compressor/Redactor extension interfaces. Fully additive within `memory/` (foundation untouched; 345 tests). Rationale DL-0022 | ✅ Done (branch `feat/m5.1b-2-memory-intelligence`) |
| **M5.1C — Memory Runtime Validation & Closure** | Full production-grade audit of the Memory Runtime (architecture/behaviour/determinism/observability/performance/debt/docs); scores + official closure. No code change | ✅ Done (audit; CLOSED, deployment-gated on adapters) |
| **M5.2A — Knowledge Runtime Design** | Full Knowledge Runtime architecture (types, document/chunk pipelines, retrieval/ranking/policies, event flow, tenancy, observability, extension points, risks, self-review + closure certification) reusing the M5.0 + Memory patterns; design only | ✅ Done (design; approved) |
| **M5.2B-1 — Knowledge Runtime Core** | New additive `knowledge/` package: domain (Document/Version/Chunk) with content-hash ids, two `core.fsm` lifecycles, repository layer over Object/Document/Vector/Metadata contracts, async-first document pipeline (import→parse→chunk→embed→index→store→version) with Parser/ChunkStrategy ports + local defaults, VersionManager, KnowledgeIndex (write path), serializer, config, factory, and index/get/remove + retrieve placeholder. Fully additive (`knowledge → core` only; 367 tests). Rationale DL-0023 | ✅ Done (branch `feat/m5.2b-1-knowledge-runtime-core`) |
| **M5.2B-2 — Knowledge Runtime Intelligence** | Completes + **closes** the Knowledge Runtime (additive within `knowledge/`): query planner (KEYWORD/SEMANTIC/HYBRID/METADATA/VERSION, pure decision layer), retrieval engine (tenancy→planner→repos→filter→policy→ranking→selection), deterministic ranking (8 neutral signals, id tie-break), policy engine (11 verdict policies), repository-owned LRU cache, tenancy isolation (org/private/shared/department), self-loop-safe content-event ingestion, retrieval profiles (Fast/Balanced/High-Precision/Compliance/Research), provenance-complete `KnowledgeResult`, and extension interfaces (Reranker/Summarizer/Redactor/Compressor/KnowledgeSource/GraphStore/GraphRetriever/MultiVectorRetriever). `knowledge → core, tenant` only; 397 tests. Rationale DL-0024/DL-0025 | ✅ Done (branch `feat/m5.2b-2-knowledge-intelligence`) |
| **M5.3A — Context Runtime Design** | Full Context Runtime architecture (component/ownership matrix, assembly pipeline, provenance-complete `ContextPackage`, budget/ranking/compression/validation/policy architecture, event flow, tenancy, observability, extension points, risks, self-review + closure certification) — implements the frozen `core.ports.ContextPort`, consumes inputs via ports + immutable data (dependency inversion; `context → core, tenant` only), temporary-only repository; design only. Rationale DL-0026 | ✅ Done (design; approved) |
| **M5.3B-1 — Context Runtime Core** | New additive `context/` package: immutable domain (`ContextItem`+provenance/budget/compression metadata, `ContextPackage`, `ContextWindow`, `ContextSession`) with content-hash ids, `core.fsm` package lifecycle (REQUESTED→ASSEMBLED→VALIDATED→DELIVERED/REJECTED/EXPIRED), **temporary-only** `ContextRepository`, `ContextSessionManager` (rolling window + history), `ContextSource` adapters over injected Memory/Knowledge/Prompt ports (+ StaticContextSource), deterministic assembler/builder (merge→order→naive-truncation budget), default WordTokenizer, serializer/config/factory/observability, and assemble/get/remove/history/expire/statistics. Consumes inputs via **dependency inversion** (`context → core` only; no sibling-runtime import); 423 tests. Rationale DL-0027 | ✅ Done (branch `feat/m5.3b-1-context-runtime-core`) |
| **M5.3B-2 — Context Runtime Intelligence** | Completes + **closes** the Context Runtime (additive within `context/`): ranking engine (12 signals + full ranking lineage on every item), deduplication (exact + semantic hook, merged provenance), compression (rollup/trim + injected summarizer/compressor, provenance+lineage+citation preserved), budget manager (global/per-source/reserved/emergency/overflow/adaptive), ordering engine, validation engine (verdicts only), policy engine (9 policies, no I/O), tenancy (isolation before assembly), observability stats, and extension interfaces (Tokenizer/ModelProfile/Summarizer/Compressor/Redactor/SemanticDeduplicator/Reranker/ContextConsumer/StreamingContextSink). Opt-in via `ContextFactory.create(intelligence=True)`; the M5.3B-1 core path is unchanged. `context → core, tenant` only; 454 tests. Rationale DL-0028/DL-0029 | ✅ Done (branch `feat/m5.3b-2-context-intelligence`) |
| **M5.4A — Prompt Runtime Design** | Full Prompt Runtime architecture (component/ownership matrix, construction pipeline, self-sufficient `PromptPackage`, template/rendering/budget/validation/policy architecture, prompt lineage, event flow, tenancy, observability, extension points, risks, self-review + closure certification) — implements the frozen `core.ports.PromptPort`, consumes the frozen `core.ports.context.AssembledContext` + injected ProviderProfile/ModelProfile/Tokenizer ports (dependency inversion; `prompt → core, tenant` only), temporary-only repository; design only. Rationale DL-0030 | ✅ Done (design; approved) |
| **M5.4B-1 — Prompt Runtime Core** | New additive `prompt/` package: immutable domain (`PromptSection`+lineage, `PromptPackage`, `PromptMessage`, `PromptRequest`/`GenerationParameters`) with content-hash ids, `core.fsm` lifecycle (REQUESTED→BUILT→VALIDATED→RENDERED→DELIVERED/REJECTED/EXPIRED), `TemplateRegistry`/`TemplateResolver` (built-in+org+user+override precedence, content-hash versioning), `SectionGenerator` (from the frozen `AssembledContext` only), formatter + provider-neutral renderer, builder (naive-truncation budget), **temporary-only** `PromptRepository`, default tokenizer/provider/model profiles behind ports, serializer/config/factory/observability, and build/render/register/get/remove/history/expire/statistics. Consumes inputs via **dependency inversion** (`prompt → core` only; no sibling-runtime import); 484 tests. Rationale DL-0031 | ✅ Done (branch `feat/m5.4b-1-prompt-runtime-core`) |
| **M5.4B-2 — Prompt Runtime Intelligence** | Completes + **closes** the Prompt Runtime (additive within `prompt/`): budget manager (global/reserved/completion/per-section/adaptive/overflow/truncation), provider profiles (Anthropic/OpenAI/Gemini/Local/Generic — role mapping only, no SDK/HTTP/LLM), model profiles (+ named registry), tokenizer abstraction (Word+Estimated), validation engine (verdicts), policy engine (7 policies, no I/O), tenancy (isolation before template resolution + provider allow-list), prompt lineage (provider/model per section) + provider-independent prompt fingerprint, observability, and extension interfaces (Optimizer/Rewriter/Compressor/Enhancer/Analyzer/MultiModalRenderer/PromptConsumer). Opt-in via `PromptFactory.create(intelligence=True)`; the M5.4B-1 core path is unchanged. `prompt → core, tenant` only; 508 tests. Rationale DL-0032/DL-0033 | ✅ Done (branch `feat/m5.4b-2-prompt-intelligence`) |
| **M5.5+ — Reasoning → Tool → MCP → Workflow → Agent** | Real provider clients + model-exact tokenizers (Anthropic/OpenAI/Gemini SDKs, tiktoken/HF) behind the ports; then the Reasoning, Tool, MCP, Workflow, Agent runtimes — each domain-only on the M5.0 substrate, consuming the `PromptPackage` via `PromptConsumer`; plus Turn-engine consolidation onto `core/fsm` | ▫ Pending |

| **beyond Epic 3 — real semantic models / streaming VAD** | Register real EOT/intent (or LLM-backed) `SEMANTIC_TURN` + streaming `VOICE_ACTIVITY` providers; prosody-scaled adaptive policies; wall-clock delay scheduling + sub-150ms latency validation | ▫ Pending |

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

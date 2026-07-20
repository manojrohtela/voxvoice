# VoxVoice — Documentation Index

> Central index of all engineering documentation. Every audit, architecture
> review, design decision (ADR), and recommendation lives in this tree — never
> only in chat. See [README.md](README.md) for the documentation conventions.

_Last updated: 2026-07-17_

## Specification

| Document | Status | Description |
|---|---|---|
| [SPEC.md](SPEC.md) | Superseded in part | The original executable build spec. Authoritative for product goal, consumer model, org/auth contract, and Hinglish acceptance phrases; superseded by the Constitution on engine-adapter architecture, Dograh-primary, and repo layout (§22; banner at top of SPEC). `../CLAUDE.md` was a byte-identical copy — replaced at M0 with a governance pointer (no info lost; content lives here). |

## Audits

| Document | Date | Status | Description |
|---|---|---|---|
| [audits/phase-1-repository-audit.md](audits/phase-1-repository-audit.md) | 2026-07-16 | Final v1.0 | Complete repository audit: architecture, code quality, dead/duplicate code, performance, security, scalability, scores. Top findings: no version control (H1), live secrets on disk (H2), voice loop never closed end-to-end (H3). |
| [audits/phase-1.5-voice-runtime-audit.md](audits/phase-1.5-voice-runtime-audit.md) | 2026-07-16 | Final v1.0 | Voice-runtime-only audit vs. Vapi-class platforms. Central finding: VoxVoice owns no runtime — control plane over a black-box Dograh (no live events, no mid-call control, post-call transcript polling) + a disconnected Pipecat prototype with batch (non-streaming) STT and silence-timer turn detection. Runtime Maturity 22/100. Defines the strategic fork (deepen Dograh vs. first-party Pipecat engine — ADR required), top-20 improvements, target architecture, and complexity estimates. |

_Audit reports are immutable once final — new findings go in a new versioned document (e.g. `phase-1-repository-audit-v2.md` or the next phase's audit)._

## Governance (binding on every implementation)

| Document | Date | Status | Description |
|---|---|---|---|
| [../.voxvoice/README.md](../.voxvoice/README.md) | 2026-07-16 | Active v1.0 | **Engineering Governance System** — 13 documents defining how all human/AI engineering work happens within the ratified Constitution: GOVERNANCE (supreme policy + authority hierarchy), AI_AGENT_RULES, DEVELOPMENT_WORKFLOW (13 mandatory phases), CODING_STANDARDS, ARCHITECTURE_RULES (AR-1…54, CI-enforceable), DOCUMENTATION_RULES (incl. ADR workflow), TESTING_POLICY, PERFORMANCE_POLICY, SECURITY_POLICY, REVIEW_CHECKLIST (14 dimensions), DEFINITION_OF_DONE, GIT_WORKFLOW, DECISION_LOG (DL-0001…0004 open items incl. M0 key rotation + git init). Index page carries the governance↔Constitution cross-reference map. |

## Architecture

| Document | Date | Status | Description |
|---|---|---|---|
| [architecture/engineering-constitution-v2.md](architecture/engineering-constitution-v2.md) | 2026-07-16 | **RATIFIED v2.0** | **The binding Engineering Constitution (once ratified).** Part A: technology-neutral architecture — capability-based provider contracts, two-plane design, event-driven core, turn intelligence, layered memory, state machines, staged scalability, permanent engineering rules (component specs, interface versioning, no silent config). Part B: versioned Implementation Profile binding capabilities to current selections (Python/Pipecat/Postgres/Deepgram/Cartesia/Groq/Sarvam…) — replaceable via lightweight profile ADRs without amending Part A. Appendix R: full v1→v2 revision record, impact reports, readiness score 88/100. |
| [architecture/ai-runtime-foundation.md](architecture/ai-runtime-foundation.md) | 2026-07-18 | Implemented (M5.0) | The shared AI Runtime foundation (Epic 5): `core/fsm` (deterministic FSM), `core/module` (RuntimeModule + BusConsumer), the content-event vocabulary (defined only), `core/ports` (dependency-inversion interfaces), `core/policies`, `core/providers/stores` (storage contracts), `telemetry/`, `tenant/`. Infrastructure only — fully additive (runtime/events/gateway/replay untouched); acyclic (test-enforced). Every future AI module is domain-only on this substrate. Rationale in DL-0020. |
| [architecture/memory-runtime.md](architecture/memory-runtime.md) | 2026-07-19 | Complete (M5.1B-2) | The Memory Runtime (Epic 5): a `BusConsumer`/`RuntimeModule` implementing `core.ports.MemoryPort` that *only* manages memory. **M5.1B-1** core (domain, `core.fsm` lifecycle, repositories over the store contracts, local adapters, serializer, config, factory, store/retrieve/forget). **M5.1B-2** intelligence — planner→retrieval→ranking→selection (keyword/semantic/hybrid, all scopes), deterministic `PolicyEngine` (validation/privacy/merge/conflict/expiration/retention/capacity), hot-object cache behind the repository, content-event ingestion (Conversation/Semantic/Episodic), tenant isolation (M5.0 hooks), observability, and Reranker/Summarizer/Compressor/Redactor extension interfaces. Provider-neutral, deterministic, replay/gateway-compatible, tenant-isolated; fully additive; acyclic (test-enforced). Rationale in DL-0021/DL-0022. |
| [architecture/knowledge-runtime.md](architecture/knowledge-runtime.md) | 2026-07-20 | ✅ Complete — architecturally closed (M5.2B-2) | The Knowledge Runtime (Epic 5): a `BusConsumer` implementing `core.ports.KnowledgePort` that owns the knowledge lifecycle + retrieval only. **Core (M5.2B-1):** domain (Document/Version/Chunk) with content-hash ids, two `core.fsm` lifecycles, a repository layer over the Object/Document/Vector/Metadata store contracts, an async-first document pipeline (import→parse→chunk→embed→index→store→version) with Parser/ChunkStrategy ports + local defaults, VersionManager, KnowledgeIndex, serializer, config, factory, index/get/remove. **Intelligence (M5.2B-2):** pure query planner (KEYWORD/SEMANTIC/HYBRID/METADATA/VERSION), retrieval engine (tenancy→planner→repos→filter→policy→ranking→selection, repositories-only, isolation-first), deterministic ranking (8 neutral signals, id tie-break), policy engine (11 verdict policies), repository-owned LRU cache, tenancy (org/private/shared/department), self-loop-safe content-event ingestion, retrieval profiles, provenance-complete `KnowledgeResult`, and extension interfaces (Reranker/Summarizer/Redactor/Compressor/KnowledgeSource/GraphStore/GraphRetriever/MultiVectorRetriever — GraphRAG/MultiVector/Federated land as new strategies with no engine redesign). Provider-neutral, deterministic, replay/gateway-compatible; additive; acyclic (`knowledge → core, tenant`, test-enforced); 397 tests. Rationale in DL-0023/DL-0024/DL-0025. |
| [architecture/context-runtime.md](architecture/context-runtime.md) | 2026-07-20 | ✅ Complete — architecturally closed (M5.3B-2) | The Context Runtime (Epic 5): a `BusConsumer` implementing the frozen `core.ports.ContextPort` that owns **context assembly only** — never Memory/Knowledge/Prompts/Reasoning/Tools/LLMs. Consumes every input through ports or immutable data (injected `MemoryPort`/`KnowledgePort`/`PromptPort` wrapped as `ContextSource`s + a `ContextSource` seam for future tool/workflow/agent/federated inputs), producing one immutable, **provenance-complete `ContextPackage`** so no downstream runtime re-queries Memory/Knowledge. **§1–18 = approved M5.3A design.** **Core (M5.3B-1):** immutable domain (ContextItem+provenance/budget/compression metadata, ContextPackage/Window/Session) with content-hash ids, `core.fsm` lifecycle (REQUESTED→ASSEMBLED→VALIDATED→DELIVERED/REJECTED/EXPIRED), **temporary-only** ContextRepository (sole store-toucher; long-term stays Memory's), session/rolling-window management, source adapters over the injected ports, deterministic assembler/builder (merge→normalize→fixed-order→naive-truncation budget), default WordTokenizer, serializer/config/factory/observability, and assemble/get/remove/history/expire/statistics; `context.*` events ride existing CONTEXT_ASSEMBLED/CONTEXT_TRUNCATED (+phase, bus unmodified). Provider-neutral, deterministic, replay/gateway-compatible; acyclic (`context → core` only via dependency inversion, test-enforced); 423 tests. **Intelligence (M5.3B-2):** ranking (12 signals + full ranking lineage on every item), deduplication (exact + semantic hook, merged provenance), compression (rollup/trim + injected summarizer/compressor, provenance+lineage+citation preserved), budget manager (global/per-source/reserved/emergency/overflow/adaptive), ordering, validation (verdicts only), policy engine (9 policies, no I/O), tenancy (isolation before assembly), and extension interfaces (Tokenizer/ModelProfile/Summarizer/Compressor/Redactor/SemanticDeduplicator/Reranker/ContextConsumer/StreamingContextSink — Prompt/Reasoning/Tool/Workflow/Agent land as ContextSource/ContextConsumer with no engine change). Opt-in via `ContextFactory.create(intelligence=True)`; the core path is unchanged. Acyclic (`context → core, tenant` via dependency inversion, test-enforced); 454 tests. Rationale in DL-0026 (design) / DL-0027 (core) / DL-0028 (tenant dep) / DL-0029 (intelligence + closure). |
| [architecture/engineering-constitution.md](architecture/engineering-constitution.md) | 2026-07-16 | Superseded by v2 | v1 draft blueprint, retained unmodified as version history. Origin of decisions D1–D7 and the M0–M6 roadmap; v2 generalized it into Part A/Part B form. |

## Architecture Decision Records (ADR)

Register and process: [adr/README.md](adr/README.md). Extracted from Constitution decisions D1–D7 + Part B at milestone M0 (2026-07-16).

| ADR | Title | Status |
|---|---|---|
| [ADR-0001](adr/ADR-0001-first-party-runtime.md) | First-party voice runtime; retire whole-engine adapter axis | Accepted |
| [ADR-0002](adr/ADR-0002-substrate-behind-contracts.md) | Pipeline substrate confined behind owned contracts | Accepted |
| [ADR-0003](adr/ADR-0003-two-plane-architecture.md) | Two-plane architecture (Control / Runtime) | Accepted |
| [ADR-0004](adr/ADR-0004-event-driven-core.md) | Event-driven core with one typed envelope | Accepted |
| [ADR-0005](adr/ADR-0005-streaming-first-turn-intelligence.md) | Streaming-first ingestion + Turn Intelligence | Accepted |
| [ADR-0006](adr/ADR-0006-single-system-of-record.md) | Single System of Record; brokers only when needed | Accepted |
| [ADR-0007](adr/ADR-0007-keep-control-plane-delete-adapters.md) | Keep control-plane assets; delete engine-adapter axis | Accepted |
| [P-ADR-0001](adr/P-ADR-0001-implementation-profile-v1.md) | Adopt Implementation Profile v1 (Part B bindings) | Accepted |

## Research (archived knowledge)

| Document | Description |
|---|---|
| [research/dograh-api-notes.md](research/dograh-api-notes.md) | Live-verified Dograh v1.35.0 API knowledge, archived at M0 before the adapter code is deleted at M1 (ADR-0007 / AR-54). |

## Deployment

_Currently lives beside the code (to be migrated or cross-linked here):_

| Document | Description |
|---|---|
| [../platform/README.md](../platform/README.md) | Run instructions, verified Dograh API mapping, engine-swap, transcript flow. |
| [../platform/DEPLOY.md](../platform/DEPLOY.md) | Deploy runbook: Dograh remote mode on a public-IP host; why local macOS voice can't work. |
| [../platform/ORACLE_DEPLOY.md](../platform/ORACLE_DEPLOY.md) | Step-by-step Oracle Cloud VM deploy (firewall, DNS, TLS, remote profile). |
| [../platform/voicebot/README.md](../platform/voicebot/README.md) | Local Pipecat voice-agent MVP (Groq STT/LLM + Cartesia TTS). |

## Runtime (component specs — Constitution §A11.3)

| Document | Date | Status | Description |
|---|---|---|---|
| [runtime/session-worker.md](runtime/session-worker.md) | 2026-07-16 | Implemented (M2A) | `SessionWorker` — owns one session's execution lifecycle (start/stop/cancel/cleanup, deterministic FSM, DI seam, health). No conversation/provider/AI logic (later milestones). |
| [runtime/conversation-state-machine.md](runtime/conversation-state-machine.md) | 2026-07-16 | Implemented (M2B.1) | `ConversationStateMachine` — deterministic conversation-lifecycle FSM over `core.states.SessionState` (validation, guards, immutable history, typed errors, notifications). Single source of truth for conversation state; consumed by the Orchestrator at M2B.2. Reconciliation of the milestone's example states in DL-0007. |
| [runtime/conversation-orchestrator.md](runtime/conversation-orchestrator.md) | 2026-07-16 | Implemented (M2B.2) | `ConversationOrchestrator` — sole coordinator/writer of conversation execution; drives the state machine (begin/run/end/cancel + failure routing); hosted by `SessionWorker`; provider capabilities via interfaces only (no execution yet). Establishes the runtime ownership boundaries. |
| [runtime/provider-registry.md](runtime/provider-registry.md) | 2026-07-16 | Implemented (M2C.1) | `ProviderRegistry` — capability-based provider reference management (register/unregister/resolve/list, validation, hot-swap, thread-safe, deterministic). Manages references only; never constructs/executes a provider. Placement rationale (runtime, not core/providers) in DL-0008. |
| [runtime/turn-engine.md](runtime/turn-engine.md) | 2026-07-17 | Implemented (M3.1) | Turn Engine (`TurnEngine`/`TurnCoordinator`/`TurnLifecycle`/`TurnContext`/`TurnPhase`) — the per-turn lifecycle foundation: deterministic phases + timing, notify-only pipeline integration (conversation behaviour unchanged). No VAD/endpointing/interruptions (M3.2+). `TurnPhase`-vs-`TurnState` reconciliation in DL-0012. |
| [runtime/voice-activity-framework.md](runtime/voice-activity-framework.md) | 2026-07-17 | Implemented (M3.2) | Voice Activity Framework (`VoiceActivityEngine`/`VoiceActivityCoordinator`/`VoiceActivityState` + the `VoiceActivityProvider` capability) — provider-neutral speech detection: a deterministic activity state model with hysteresis, resolved via the registry (`CapabilityKind.VOICE_ACTIVITY`), tapping inbound audio to feed the Turn Engine. Behaviour unchanged; graceful fallback when no VAD provider is configured. Auxiliary-failure policy in DL-0013. |
| [runtime/endpoint-framework.md](runtime/endpoint-framework.md) | 2026-07-17 | Implemented (M3.3) | Endpoint Intelligence Framework (`EndpointEngine`/`EndpointCoordinator`/`EndpointDecision`/`EndpointPolicy`) — deterministic, provider-neutral turn-completion decisions from neutral speech/segment/silence signals, governed by a configurable policy. **Replaces the pipeline's STT-final heuristic** (always wired); notifies the Turn Engine when a turn should finish (never owns turn state). Default policy reproduces pre-M3.3 behaviour (no regression). Trigger-vs-decision rationale in DL-0014. |
| [runtime/interrupt-framework.md](runtime/interrupt-framework.md) | 2026-07-17 | Implemented (M3.4) | Interrupt Intelligence Framework (`InterruptEngine`/`InterruptCoordinator`/`InterruptDecision`/`InterruptPolicy`) — deterministic, provider-neutral barge-in: detects caller speech during assistant playback (from the VAF's per-frame verdict), confirms via a configurable policy, and safely pre-empts playback (cancel playback → cancel the interrupted turn → begin a new user turn; Turn Engine stays the owner). Opt-in (`allow_interrupt` + VAD); graceful fallback to the M3.3 sequential path. Opt-in/concurrency + per-frame-verdict rationale in DL-0015. |
| [runtime/replay-platform.md](runtime/replay-platform.md) | 2026-07-17 | Implemented (M4.3) | Conversation Replay & Observability (`replay.ConversationRecorder`/`ReplayEngine`/`ReplayController`/`TimelineBuilder`/`EventIndex`/`SessionInspector`/`EventCorrelator`/`MetricsCollector`/`ReplaySerializer`) — records the Event Bus read-only and turns it into deterministic, visualization-only replay: colour-coded timeline, filter/search, correlation, derived metrics, point-in-time runtime-state inspector, and a play/pause/step/jump/speed controller. Purely additive (imports `core`+`events`; Runtime **byte-for-byte unchanged**). No Runtime logic runs during replay; pending metrics surfaced honestly (no fake UI). Dark-theme UI shipped as an interactive artifact. Read-only/deterministic/honest-metrics rationale in DL-0019. |
| [runtime/event-gateway.md](runtime/event-gateway.md) | 2026-07-17 | Implemented (M4.2) | Live Event Gateway (`gateway.LiveEventGateway` + ConnectionManager/SubscriptionManager/EventChannel/EventSerializer/EventStream/ClientSession/GatewayMetrics + WebSocket/SSE transports) — streams Internal Event Bus events to external clients. Purely additive edge package (imports `core` only; Runtime **byte-for-byte unchanged**, never knows clients). WS+SSE consume the same versioned envelope; scope/type filtering, backpressure + slow-consumer protection, connection isolation, multi-client, graceful shutdown; auth/authz are hooks only. In-process only (no durable queue/broker). Additive-edge/non-blocking rationale in DL-0018. |
| [runtime/event-platform.md](runtime/event-platform.md) | 2026-07-17 | Implemented (M4.1) | Internal Event Bus (`events.InProcessEventBus` + dispatcher/registry/publisher/subscriber, behind `core.events.EventBus`) — **opens Epic 4**. The single internal communication mechanism: typed `Event` envelopes, deterministic ordered in-process run-to-completion delivery, subscriber isolation, type filtering, reentrancy-safe. Runtime engines publish via `runtime.event_bridge` with zero coupling (they never know consumers). In-process only (no external transports/durability — M4.2+). Refinement/sync-delivery rationale in DL-0017. |
| [runtime/smart-turn-framework.md](runtime/smart-turn-framework.md) | 2026-07-17 | Implemented (M3.5) | Smart Turn Intelligence Engine (`SmartTurnEngine`/`SmartTurnCoordinator`/`SmartTurnDecision`/`SmartTurnPolicy`) — **completes Epic 3**. A deterministic arbiter combining the Turn/Voice-Activity/Endpoint/Interrupt outputs + adaptive policy + an optional **registry-resolved semantic evaluator** (`core.providers.semantic_turn.SemanticTurnEvaluator`, `CapabilityKind.SEMANTIC_TURN`) into the final conversational-timing decision the pipeline consumes. Sub-frameworks stay independent (Smart Turn only arbitrates). Opt-in (`smart_turn`); identity default policy → unchanged. Runtime never calls an LLM. Arbiter/semantic-capability/recorded-delay rationale in DL-0016. |
| [runtime/runtime-pipeline.md](runtime/runtime-pipeline.md) | 2026-07-17 | Implemented (M2C.2) | `RuntimePipeline` + `RuntimeSessionService` — the streaming turn loop (Transport→STT→LLM→TTS→Transport via contracts) and the api↔runtime seam. First complete end-to-end conversation; `POST /v1/sessions` restored. Includes the runtime sequence diagram. |
| [providers/local-profile.md](providers/local-profile.md) | 2026-07-17 | Implemented (M2C.2) | The local development provider profile (LocalTransport/LocalSTT/EchoLLM/LocalTTS) + the M2C.2 contract refinement. First-class, in-process, deterministic; real vendors are drop-in replacements. |

## Roadmap

| Document | Date | Status | Description |
|---|---|---|---|
| [roadmap/migration-progress.md](roadmap/migration-progress.md) | 2026-07-16 | Living | M0–M6 migration tracker. M0 ✅, M1 ✅ (repo reshape; engine-adapter axis removed; session-start → 503; structure now matches Constitution §A14). M2 next. |
| [roadmap/baseline-M0.md](roadmap/baseline-M0.md) | 2026-07-16 | Baseline (immutable) | M0 Foundation Freeze snapshot — structure, dependencies, architecture/governance/documentation posture, statistics, known debt, deferred items, open risks. Git tag `foundation-v1`. The state every future milestone starts from. |

## Planned sections (created when first document lands)

Open: `architecture/` · `audits/` · `adr/` · `research/`.
Pending first document: `runtime/` (component specs, M2+) · `apis/` (event catalogue, M4) ·
`providers/` (capability matrices) · `performance/` (benchmark baselines) · `security/`
(threat model) · `testing/` · `deployment/` (migrate platform runbooks at M1) · `roadmap/`.

Empty directories are not pre-created; a section is opened by its first document
and added to this index in the same change.

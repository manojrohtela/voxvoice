# runtime/ — the Runtime Plane

Executes live conversations (Constitution §A4, §A5; ADR-0001, ADR-0003). One
Session Worker hosts N sessions, each an isolated task-group "actor". **Skeleton
only at M1** — every module below is a boundary with a documented responsibility
and no behaviour yet. Each component gets its full specification (Constitution
§A11.3) in `docs/runtime/` as it is implemented.

| Module | Responsibility (single) | Built at |
|---|---|---|
| `worker.py` | **SessionWorker: owns one session's execution lifecycle** (start/stop/cancel/cleanup, health, DI) — **implemented M2A** (spec: [`docs/runtime/session-worker.md`](../../docs/runtime/session-worker.md)). Conversation execution wires in at M2B. | M2A ✅ |
| `state.py` · `contracts.py` · `errors.py` | Worker state machine, runtime contracts (SessionContext/RuntimeContext/AgentSpec/…), typed worker + orchestration + registry errors | M2A ✅ |
| `registry.py` | **ProviderRegistry: capability-based provider reference management** (register/unregister/resolve/list, validation, hot-swap, thread-safe, deterministic) — **implemented M2C.1** (spec: [`docs/runtime/provider-registry.md`](../../docs/runtime/provider-registry.md)). Manages references only; never constructs/executes a provider. | M2C.1 ✅ |
| `conversation.py` | **ConversationStateMachine: deterministic conversation-lifecycle FSM** over `core.states.SessionState` (validation, guards, immutable history, typed errors, notifications) — **implemented M2B.1** (spec: [`docs/runtime/conversation-state-machine.md`](../../docs/runtime/conversation-state-machine.md)) | M2B.1 ✅ |
| `orchestrator/` | **ConversationOrchestrator: sole writer of conversation state**; drives the state machine; coordinates begin/run/end/cancel + failure routing; providers via interfaces only — **implemented M2B.2** (spec: [`docs/runtime/conversation-orchestrator.md`](../../docs/runtime/conversation-orchestrator.md)). Hosted by `SessionWorker`. | M2B.2 ✅ |
| `turn/` | **Turn Engine: per-turn lifecycle subsystem** (TurnEngine/TurnCoordinator/TurnLifecycle/TurnContext/TurnPhase) — **foundation M3.1** (spec: [`docs/runtime/turn-engine.md`](../../docs/runtime/turn-engine.md)); tracks turns + timing (notify-only); VAD/endpointing/interruptions are M3.2+ | M3.1 ✅ |
| `ai/` | Context assembly, prompt runtime, response validation/formatting, tool scheduling | M5 |
| `memory/` | Layered token-budgeted context; snapshots; recovery | M5 |
| `speech/` | Sentence chunking, playback scheduling, filler/ack policy, interruption flush | M3 |
| `pipeline/` | **RuntimePipeline: the streaming turn loop** (Transport→STT→LLM→TTS→Transport via contracts) — **implemented M2C.2** (spec: [`docs/runtime/runtime-pipeline.md`](../../docs/runtime/runtime-pipeline.md)). Also the only package permitted to import the pipeline substrate (AR-13) when real vendor plugins arrive. | M2C.2 ✅ |
| `service.py` | **RuntimeSessionService: the api↔runtime seam** (implements `core.session_control.SessionController`); owns live sessions, creates one SessionWorker each | M2C.2 ✅ |
| `registry.py` (see above) | provider resolution consumed by the orchestrator | M2C.1 ✅ |

Layer rules (CI-checked): `runtime` imports `core` and provider interfaces; it
does **not** import `api`. Only `runtime/pipeline` imports the substrate.

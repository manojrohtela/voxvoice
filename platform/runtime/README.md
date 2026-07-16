# runtime/ — the Runtime Plane

Executes live conversations (Constitution §A4, §A5; ADR-0001, ADR-0003). One
Session Worker hosts N sessions, each an isolated task-group "actor". **Skeleton
only at M1** — every module below is a boundary with a documented responsibility
and no behaviour yet. Each component gets its full specification (Constitution
§A11.3) in `docs/runtime/` as it is implemented.

| Module | Responsibility (single) | Built at |
|---|---|---|
| `worker.py` | **SessionWorker: owns one session's execution lifecycle** (start/stop/cancel/cleanup, health, DI) — **implemented M2A** (spec: [`docs/runtime/session-worker.md`](../../docs/runtime/session-worker.md)). Conversation execution wires in at M2B. | M2A ✅ |
| `state.py` · `contracts.py` · `errors.py` | Worker state machine, runtime contracts (SessionContext/RuntimeContext/RuntimeDependencies/…), typed worker + orchestration + registry errors | M2A ✅ |
| `registry.py` | **ProviderRegistry: capability-based provider reference management** (register/unregister/resolve/list, validation, hot-swap, thread-safe, deterministic) — **implemented M2C.1** (spec: [`docs/runtime/provider-registry.md`](../../docs/runtime/provider-registry.md)). Manages references only; never constructs/executes a provider. | M2C.1 ✅ |
| `conversation.py` | **ConversationStateMachine: deterministic conversation-lifecycle FSM** over `core.states.SessionState` (validation, guards, immutable history, typed errors, notifications) — **implemented M2B.1** (spec: [`docs/runtime/conversation-state-machine.md`](../../docs/runtime/conversation-state-machine.md)) | M2B.1 ✅ |
| `orchestrator/` | **ConversationOrchestrator: sole writer of conversation state**; drives the state machine; coordinates begin/run/end/cancel + failure routing; providers via interfaces only — **implemented M2B.2** (spec: [`docs/runtime/conversation-orchestrator.md`](../../docs/runtime/conversation-orchestrator.md)). Hosted by `SessionWorker`. | M2B.2 ✅ |
| `turn/` | Turn Intelligence: activity detection, endpoint scoring, interruption policy, timing | M3 |
| `ai/` | Context assembly, prompt runtime, response validation/formatting, tool scheduling | M5 |
| `memory/` | Layered token-budgeted context; snapshots; recovery | M5 |
| `speech/` | Sentence chunking, playback scheduling, filler/ack policy, interruption flush | M3 |
| `pipeline/` | **The only package that imports the pipeline substrate** (ADR-0002, AR-13); transports + service glue | M2 |

Layer rules (CI-checked): `runtime` imports `core` and provider interfaces; it
does **not** import `api`. Only `runtime/pipeline` imports the substrate.

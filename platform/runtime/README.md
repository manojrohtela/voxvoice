# runtime/ — the Runtime Plane

Executes live conversations (Constitution §A4, §A5; ADR-0001, ADR-0003). One
Session Worker hosts N sessions, each an isolated task-group "actor". **Skeleton
only at M1** — every module below is a boundary with a documented responsibility
and no behaviour yet. Each component gets its full specification (Constitution
§A11.3) in `docs/runtime/` as it is implemented.

| Module | Responsibility (single) | Built at |
|---|---|---|
| `worker.py` | **SessionWorker: owns one session's execution lifecycle** (start/stop/cancel/cleanup, health, DI) — **implemented M2A** (spec: [`docs/runtime/session-worker.md`](../../docs/runtime/session-worker.md)). Conversation execution wires in at M2B. | M2A ✅ |
| `state.py` · `contracts.py` · `errors.py` | Worker state machine, runtime contracts (SessionContext/RuntimeContext/RuntimeDependencies/…), typed worker errors | M2A ✅ |
| `orchestrator/` | Sole writer of conversation state; state machine; lifecycle policy; emits `session.*` | M2 |
| `turn/` | Turn Intelligence: activity detection, endpoint scoring, interruption policy, timing | M3 |
| `ai/` | Context assembly, prompt runtime, response validation/formatting, tool scheduling | M5 |
| `memory/` | Layered token-budgeted context; snapshots; recovery | M5 |
| `speech/` | Sentence chunking, playback scheduling, filler/ack policy, interruption flush | M3 |
| `pipeline/` | **The only package that imports the pipeline substrate** (ADR-0002, AR-13); transports + service glue | M2 |

Layer rules (CI-checked): `runtime` imports `core` and provider interfaces; it
does **not** import `api`. Only `runtime/pipeline` imports the substrate.

# Runtime Component Spec — Runtime Pipeline

| | |
|---|---|
| **Component** | `runtime.pipeline.pipeline.RuntimePipeline` (+ `runtime.service.RuntimeSessionService`) · **Status:** Implemented (M2C.2) |
| **Format** | Constitution §A11.3 component specification |
| **Milestone** | M2C.2 — Runtime Pipeline & First Conversation (2026-07-17) |

## Responsibility (single)
Execute one conversation's streaming turn loop by connecting the capability
stages — Transport → STT → (LLM → TTS) → Transport — **only through their
`core.providers` contracts**. No vendor names, no provider-specific branching,
no conversation-state ownership (that is the Orchestrator's). Scope M2C.2: the
minimum streaming flow for a real multi-turn conversation — NO Turn Intelligence
/ endpointing (M3), NO Memory / Prompt Runtime (M5), NO Event Bus (M4).

## Ownership (unchanged boundaries, now exercised end-to-end)
```
SessionWorker            → worker execution lifecycle (WorkerState)
ConversationOrchestrator → conversation lifecycle (SessionState, sole writer);
                           resolves providers from the registry; coordinates
                           provider lifecycle (open on begin, close on end)
RuntimePipeline          → executes the streaming turns (this component)
ProviderRegistry         → resolves capability providers (never constructs)
Providers                → capability implementations (local profile at M2C.2)
RuntimeSessionService    → owns the set of live sessions; the api↔runtime seam
```

## Runtime sequence (one session, co-located Stage-1 dev)
```
Consumer ──POST /v1/sessions──► Control Plane (api)
   api: auth → persist Session → SessionController.start(StartSessionRequest)
        │  (core.session_control interface; impl injected at the composition root)
        ▼
   RuntimeSessionService.start
        │  build RuntimeContext {session, registry, AgentSpec, transcript sink}
        ▼
   SessionWorker.start ──► Orchestrator.begin
        │  CREATED→CONNECTED
        │  _open_providers: registry.resolve(TRANSPORT/STT/LLM/TTS by capability)
        │                   → RuntimePipeline(...) ; pipeline.start() (transport.start)
        │  CONNECTED→IN_CONVERSATION
        ▼  Orchestrator.run ──► pipeline.run  (background run task)

   ── conversation turn loop (streaming) ─────────────────────────────────────
   caller audio ─► Transport.inbound ─► STT.transcribe ─► Partial… ─► Final
        Final ─► context[system+user] ─► LLM.stream (tokens) ─► TTS.synthesize
              ─► Transport.send (audio to caller)
        each item ─► transcript sink (Control Plane persists finals)
   ───────────────────────────────────────────────────────────────────────────

   caller hangup (Transport.inbound ends) ─► pipeline.run returns
        ─► Orchestrator.run ─► end(): IN_CONVERSATION→ENDING→(pipeline.stop)→ENDED
   OR  Consumer POST /sessions/{id}/end ─► SessionController.end ─► worker.stop
        ─► Orchestrator.end (same terminal path)
```

## Inputs / Outputs
- **RuntimePipeline** ctor: transport/stt/llm/tts (injected instances), session_id,
  system_prompt, language, sample_rate, tts_voice, on_transcript sink.
  Methods: `start()`, `run()`, `stop()`.
- **Emissions:** transcript items `(session_id, role, text, is_final)` via the sink
  (the Event-Bus integration point, M4); audio frames to the transport;
  structured lifecycle logs (pipeline/provider start, conversation start/end,
  pipeline stop — the metric integration points, M3).

## Provider integration
Providers are obtained **exclusively through the Provider Registry** (the
Orchestrator resolves by capability, optionally by opaque name). No direct
construction in runtime code; no vendor branching. The M2C.2 profile
(`bootstrap.build_local_registry`) registers the local in-process set; a real
vendor set is a drop-in registry change (providers remain fully replaceable).

## Streaming architecture
Genuinely streaming and provider-independent: STT yields Partials during speech +
a Final per utterance; the LLM streams tokens; TTS streams audio frames; the
transport plays them as they arrive. A Final marks a completed user turn (real
adaptive endpointing is Turn Intelligence, M3). Extensible: new stages/providers
plug in behind the same contracts.

## Failure handling (through the approved state machines)
| Failure | Routing |
|---|---|
| Provider resolution / startup (`begin`) | conversation → FAILED + `OrchestrationStartupError`; worker → FAILED + `WorkerStartupError` |
| Pipeline stage error mid-conversation | `Orchestrator.run` catches → `fail()` → ENDING→ENDED (the machine forbids IN_CONVERSATION→FAILED) |
| Caller hangup (transport inbound ends) | natural end → ENDING→ENDED |
| Cancellation / `POST /end` | `worker.cancel`/`stop` → `Orchestrator.end` terminal path |
No silent recovery; every failure is a typed, logged, state-machine transition.

## Transcripts
Partial + Final transcripts flow to the injected sink as produced. The Control
Plane persists Finals (`GET /v1/sessions/{id}/transcript`). This callback is the
clean integration point the Event Bus (M4) will subsume.

## Control-Plane integration
`POST /v1/sessions` is operational: persists the Session, then calls
`SessionController.start` (the `core.session_control` interface; the concrete
`RuntimeSessionService` is injected by the composition root, DL-0010). The api
never imports `runtime`. `POST /v1/sessions/{id}/end` ends the runtime session.

## Not in scope (later milestones)
Turn Intelligence / semantic endpointing (M3) · Event Bus (M4) · Memory / Prompt
Runtime (M5) · real vendor plugins + telephony transport (once keys rotated,
DL-0002) · full observability platform + async transcript persistence.

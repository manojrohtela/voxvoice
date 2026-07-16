# VoxVoice — Engineering Constitution
### Master Re-Architecture Blueprint (Phase 2 Foundation)

| | |
|---|---|
| **Document** | `docs/architecture/engineering-constitution.md` |
| **Status** | **SUPERSEDED by [engineering-constitution-v2.md](engineering-constitution-v2.md) (2026-07-16).** Retained unmodified below as version history. v1 was never ratified; ratification applies to v2. The v2 revision relocated all technology/vendor choices into a versioned Implementation Profile (Part B) and generalized the constitution (Part A) to be technology-neutral — see v2 Appendix R for the full change record. |
| **Date** | 2026-07-16 |
| **Author** | Claude Code (acting Chief Architect / Technical Owner) |
| **Authority** | Once ratified: every future implementation follows this architecture unless a documented ADR explicitly changes it. |
| **Inputs** | [Phase 1 repository audit](../audits/phase-1-repository-audit.md) · [Phase 1.5 voice runtime audit](../audits/phase-1.5-voice-runtime-audit.md) · SPEC.md (superseded in part — see §22) |
| **Constraints acknowledged** | No production customers, no backward-compatibility requirements, no migration constraints, solo developer, prototype status of all existing code. |

---

# 1. Product Vision

**VoxVoice is a provider-agnostic, production-grade Voice AI Runtime Platform. The runtime is the product.**

Applications — interview agents (VoxHire), sales agents, support agents, healthcare/banking/education agents, future multimodal agents — are *consumers* built on top of the runtime. They configure agents, start sessions, receive events, and call control APIs. They never touch audio, providers, or conversation mechanics.

What a consumer buys from VoxVoice, in one sentence: **"Give me a configured agent and a session, and I get a natural, low-latency, interruptible, context-aware spoken conversation — with live events, mid-call control, transcripts, recordings, and metrics — regardless of which STT/LLM/TTS vendors are underneath."**

Three product invariants follow, and every architectural decision in this document serves them:

- **I1 — Latency is a feature.** p50 voice-to-voice ≤ 1.0 s, p95 ≤ 1.8 s, measured per stage on every turn. Anything that adds unmeasured latency is a defect.
- **I2 — The conversation is observable and controllable, live.** Every session emits a typed event stream in real time and accepts control commands mid-call. A black-box call is a defect.
- **I3 — Providers are plugins, never architecture.** Swapping STT/TTS/LLM/transport touches configuration and one plugin module, nothing else. The Hinglish moat (Sarvam et al.) is a *plugin choice*, not a rewrite.

# 2. Engineering Philosophy

1. **Own the loop.** The audits proved the fatal flaw of renting the conversation loop (Dograh): no events, no control, no metrics, no differentiation. VoxVoice owns the runtime loop end-to-end. Commodity layers underneath (codecs, WebRTC plumbing, model inference) are rented; the *conversation* is not.
2. **Boring core, innovative edges.** The core is deliberately conventional: asyncio, typed events, a state machine, Postgres. Innovation is spent where it differentiates: turn-taking intelligence, latency engineering, Indic-language quality.
3. **Everything measured.** No component ships without emitting its latency, errors, and cost. Observability is designed in (§15), not added later.
4. **Deterministic where it matters.** Conversation *content* is probabilistic (LLM); conversation *behaviour* — lifecycle, timeouts, interruption policy, tool scheduling, failure handling — is a deterministic state machine (§7).
5. **One writer per fact.** Every piece of state has exactly one owning component; everyone else learns of it via events. This kills the Phase-1 class of bugs (uncorrelated session status, config that silently does nothing).
6. **Accepted config must act.** If the API accepts a field, the runtime honours it or the API rejects it. Silent no-ops are Severity-High defects by constitution.
7. **Grow by stages, design for the last stage.** The architecture runs as a single process on a laptop and as a fleet at scale *without changing component boundaries* — only their deployment (§16).

# 3. Foundational Decisions (to be ratified as ADRs)

Each follows the ADR template: Problem · Alternatives · Selected · Trade-offs · Risks · Future impact.

---

### D1 (→ADR-0001): VoxVoice builds and owns a first-party voice runtime; external full-stack engines are dropped from the core.

- **Problem:** Phase 1 bet on renting the runtime (Dograh behind `EngineAdapter`). Phase 1.5 proved the rented path is blind (no live events, no mid-call control, post-call transcripts) and the product vision says *the runtime is the product* — you cannot rent your product.
- **Alternatives:** (a) Deepen Dograh integration — rejected: capabilities unverified, ceiling owned by someone else, differentiation impossible; (b) LiveKit Agents as the runtime — viable substrate but heavier infra (SFU) than needed at this stage, and still a framework to wrap, not a product; (c) Speech-to-speech models (OpenAI Realtime style) — rejected as core: provider lock-in is the opposite of the vision, Indic support immature; revisit as a *provider type* later; (d) **Build the runtime on Pipecat as an internal substrate — selected** (see D2).
- **Trade-offs:** We take on turn-taking, interruption, memory, and concurrency engineering ourselves (8–12 weeks, per audit F14). In exchange: full event/control/metric access, Sarvam integration on our terms, and a product that is actually ours.
- **Risks:** Solo-dev scope. Mitigated by staged delivery (§17) and by Pipecat carrying the commodity layers.
- **Future impact:** The `EngineAdapter` axis (swap whole engines) is **retired** as the primary abstraction; the provider axis (swap STT/TTS/LLM/transport plugins) replaces it. The Dograh adapter and its verified API knowledge are archived to documentation, not carried in the core.

### D2 (→ADR-0002): Python + asyncio, with Pipecat as the audio/streaming substrate behind VoxVoice-owned interfaces.

- **Problem:** Build the frame pipeline (transports, resampling, VAD plumbing, service streaming) from scratch, or stand on a framework?
- **Alternatives:** (a) From-scratch pipeline — maximal control, months of commodity work, rejected (violates KISS for zero differentiation); (b) LiveKit Agents — couples us to LiveKit infra; (c) Rust/Go core — better raw performance, abandons the developer's leverage and the Python voice-AI ecosystem; revisit only if profiling proves Python is the bottleneck (it will not be — network and model inference dominate voice latency); (d) **Pipecat 1.4+ as substrate — selected**: it already provides WebRTC/telephony transports, Silero VAD, smart-turn hooks, streaming service wrappers for every provider we care about (including Sarvam-class vendors), and interruption plumbing.
- **The constitutional constraint:** Pipecat is used **inside** the Runtime Plane only, wrapped by VoxVoice interfaces (`Transport`, `STTProvider`, `TurnDetector`, …). No module outside `runtime/pipeline/` may import `pipecat`. This keeps Pipecat replaceable (it is a substrate, not the architecture) and keeps our provider contracts ours.
- **Trade-offs:** We inherit Pipecat's release cadence and occasional breaking changes; the wrapper layer costs a little indirection. Accepted.
- **Future impact:** If Pipecat is ever outgrown, the blast radius is one directory.

### D3 (→ADR-0003): Two-plane architecture — Control Plane and Runtime Plane — co-located first, separable by design.

- **Problem:** Phase 1 mixed control (CRUD, auth) and runtime concerns (pollers, adapter singletons) in one FastAPI process, creating the single-process traps found in the audit (H4).
- **Alternatives:** (a) Keep a monolith — simple but repeats H4; (b) Microservices per component — absurd overhead for the stage; (c) **Two planes — selected**: a **Control Plane** (FastAPI: auth, agents, sessions, events out, control in) and a **Runtime Plane** (session workers running conversations). They share nothing in memory; they communicate via the database and the event bus. In development they run in one process (two asyncio task groups); in production they are separate processes/hosts *without code changes*.
- **Trade-offs:** A session-routing indirection (which worker owns session X) exists from day one even when there's one worker. That's the price of never rewriting for scale.
- **Future impact:** Horizontal scaling = add Runtime Plane workers. The Control Plane stays stateless and scales trivially.

### D4 (→ADR-0004): Event-driven core with one typed event envelope; the event log is the integration surface.

- **Problem:** Phase 1.5 §11 — no live events; SDK contract unimplementable; components coupled by direct calls.
- **Selected:** every runtime fact is an event (§8): produced once by its owner, fanned out to (a) the client WS channel, (b) consumer webhooks (with retry+DLQ), (c) persistence, (d) metrics. Components subscribe; they do not call each other across layer boundaries.
- **Alternatives:** direct callback wiring (Phase-1 style `on_transcript`) — rejected: N×M coupling, no replay, no audit trail.
- **Trade-offs:** Slight indirection; eventual consistency for read models. Accepted — conversations are inherently event-shaped.
- **Future impact:** Analytics, billing, debugging ("replay session"), and multi-region fan-out all become event consumers, added without touching the runtime.

### D5 (→ADR-0005): Streaming-first ingestion with a dedicated Turn Intelligence layer.

- **Problem:** Phase 1.5 §2/§4 — batch STT + fixed 600 ms silence timer structurally caps latency and naturalness.
- **Selected:** streaming STT with partial hypotheses is **mandatory** for any first-class STT plugin; endpointing is a fusion of VAD + semantic end-of-turn scoring + adaptive delay (`min/max_endpointing_delay` finally honoured); interruption is a *policy engine* over VAD triggers. Batch-only STT vendors may exist as degraded-mode plugins that self-declare the capability gap (§9 capability flags).
- **Trade-offs:** Streaming STT narrows the vendor list (Deepgram now; Sarvam streaming when available — verify; Whisper-class batch becomes fallback-only). Turn intelligence is the hardest single component we own (High complexity, audit F14).
- **Future impact:** This layer *is* the moat alongside Indic quality. It is also exactly the part no rented engine would ever let us tune.

### D6 (→ADR-0006): Postgres is the single source of truth; brokers enter only at the scale stage that needs them.

- **Problem:** where do sessions, transcripts, context snapshots, event log, and the webhook queue live?
- **Selected:** Postgres (Supabase in prod, SQLite in unit tests) for all durable state, including the event outbox and webhook retry queue (`FOR UPDATE SKIP LOCKED` worker pattern). In-process asyncio pub/sub for the live event bus at Stage 1–2; Redis pub/sub swaps in at Stage 3 (§16) behind the same `EventBus` interface.
- **Alternatives:** Kafka/NATS/Redis from day one — operational weight without need; "just in-memory" — repeats Phase-1 data-loss traps.
- **Trade-offs:** Postgres-as-queue has throughput ceilings (~thousands of events/s) — far beyond Stage 1–3 needs; the interface seam makes the later swap mechanical.

### D7 (→ADR-0007): Keep-and-harden the Phase-1 control-plane assets; delete the engine-adapter axis.

- **What remains (verified good in audits):** FastAPI app shape, SQLModel models (extended), org/API-key auth with hashing, org-scoping discipline + its tests, webhook HMAC signing, schema/model separation, `.env` settings pattern, the test-with-fakes approach.
- **What is deleted:** `adapters/` (Dograh + LiveKit + factory), `/v1/engine/dograh/events`, the transcript poller, `Agent.engine_workflow_id`, `webclient`'s embed-script mounting. Dograh API knowledge is preserved in `docs/research/dograh-api-notes.md` (migration step M0) — the code goes, the learning stays.
- **What is rewritten:** the voicebot becomes the seed of the Runtime Plane, not a sidecar demo.
- **Why deletion, not preservation:** constitution over sunk cost. The adapter seam was *well built* but it abstracts the wrong axis for this vision (D1). Keeping it would force every runtime feature through an interface designed to hide exactly what we now need to expose.

---

# 4. High-Level Architecture

```
                 ┌────────────────────────────────────────────────────────────┐
   Consumers     │                      VOXVOICE PLATFORM                     │
 (VoxHire, apps) │                                                            │
                 │  ┌──────────────────────┐      ┌───────────────────────┐   │
  REST (control) ├─►│    CONTROL PLANE     │      │     RUNTIME PLANE     │   │
  WS/SSE (events)◄──┤  api/                │      │  runtime/             │   │
  SDK (client)   │  │  · auth (org keys)   │      │  · SessionWorker(s)   │   │
                 │  │  · agents CRUD       │ DB + │  · Conversation SM    │   │
                 │  │  · sessions API      │ event│  · Turn Intelligence  │   │
                 │  │  · control API ──────┼─bus─►│  · Pipeline (Pipecat  │   │
                 │  │  · event gateway ◄───┼──────┤    substrate)         │   │
                 │  │  · webhook dispatcher│      │  · Memory Engine      │   │
                 │  └──────────┬───────────┘      │  · Tool Runtime       │   │
                 │             │                  └──────────┬────────────┘   │
                 │  ┌──────────▼──────────────────────────── ▼───────────┐    │
                 │  │        POSTGRES (source of truth) + EVENT BUS      │    │
                 │  │  orgs · keys · agents · sessions · turns ·         │    │
                 │  │  transcripts · context snapshots · event log ·     │    │
                 │  │  webhook outbox · metrics · recordings             │    │
                 │  └────────────────────────────────────────────────────┘    │
                 │  ┌────────────────────────────────────────────────────┐    │
                 │  │  PROVIDER PLUGINS (config-selected, capability-    │    │
                 │  │  declared): STT · TTS · LLM · Transport · Storage  │    │
                 │  │  · Analytics    (Deepgram/Sarvam/Groq/Cartesia/…)  │    │
                 │  └────────────────────────────────────────────────────┘    │
                 └────────────────────────────────────────────────────────────┘
        Media path: Browser/Phone ◄──WebRTC/SIP──► Runtime Plane transport (never the Control Plane)
```

Layer rules (enforced by import-linting in CI):

1. `providers/` may import only `core/` (contracts, types, events). Never `runtime/`, never `api/`.
2. `runtime/` may import `core/` and `providers/` *interfaces* (resolution via registry). Only `runtime/pipeline/` may import `pipecat`.
3. `api/` may import `core/` and talk to `runtime/` **only** via the session-control interface and the event bus.
4. `core/` imports nothing above it. It holds contracts, event types, state-machine definitions, errors, ids.

# 5. Component Diagram (Runtime Plane detail)

```
 SessionWorker (one asyncio task-group per live session)
 ┌───────────────────────────────────────────────────────────────────────┐
 │                        Conversation Orchestrator                      │
 │        owns: ConversationStateMachine · LifecycleManager (timeouts,   │
 │        max-duration, idle re-prompt, goodbye) · event emission        │
 │                                                                       │
 │  ┌─────────────┐   ┌──────────────────┐   ┌────────────────────────┐  │
 │  │ AUDIO       │   │ TURN             │   │ AI RUNTIME             │  │
 │  │ RUNTIME     │──►│ INTELLIGENCE     │──►│  ContextBuilder        │  │
 │  │ Transport   │   │  VAD             │   │  PromptRuntime         │  │
 │  │ (in) capture│   │  EndpointScorer  │   │  MemoryEngine          │  │
 │  │ jitter/     │   │  (VAD+semantic+  │   │  LLM stream            │  │
 │  │ resample/   │   │   adaptive delay)│   │  ResponseValidator/    │  │
 │  │ buffers     │   │  InterruptEngine │   │   Formatter            │  │
 │  │             │   │  TimingController│   │  ToolRuntime/Scheduler │  │
 │  │ STT stream ─┤   └──────────────────┘   └──────────┬─────────────┘  │
 │  │ (partials)  │        ▲     │                      ▼                │
 │  │             │        │     │            ┌────────────────────────┐ │
 │  │ Playback ◄──┼────────┼─────┼────────────│ SPEECH OUT             │ │
 │  │ Engine (out)│  barge-in  cancel         │  TTS stream            │ │
 │  │ + Playback  │                           │  sentence chunker      │ │
 │  │   Scheduler │                           │  filler/ack scheduler  │ │
 │  └─────────────┘                           └────────────────────────┘ │
 │            every box: latency taps + typed events → EventBus          │
 └───────────────────────────────────────────────────────────────────────┘
```

Component responsibilities (one line each):

- **SessionWorker** — process-level host; N sessions per worker; spawn/reap task groups; backpressure.
- **Conversation Orchestrator** — the only writer of conversation state; runs the state machine; enforces lifecycle policy; emits all `conversation.*` events.
- **Audio Runtime** — transport in/out, sample-rate normalization (48k↔16k↔8k), ring buffers, playback queue; owns *no* conversation logic.
- **Turn Intelligence** — VAD events + STT partials → end-of-turn score → adaptive endpoint decision; interruption policy (min-words, backchannel tolerance, confidence gates); response-timing hints ("user likely finishing — pre-warm LLM").
- **AI Runtime** — context assembly (system prompt template + memory layers + metadata + tool defs), streaming LLM, output validation/formatting for speech, tool scheduling.
- **Speech Out** — sentence-chunked streaming TTS, playback scheduling, filler/ack audio on predicted long waits, interruption flush.
- **Memory Engine** — §10.
- **Tool Runtime** — §core AI runtime; async executor with timeout/retry, parallel where declared safe, `tool.*` events, speak-while-waiting hook.

# 6. Runtime Lifecycle

```
Consumer: POST /v1/sessions {agent_id, channel, metadata}
   │ Control Plane: auth → load agent → create Session(row) → pick worker → issue client token
   ▼
SessionWorker.accept(session)                                 event: session.created
   │ resolve providers from agent config (registry, capability check — reject impossible configs)
   │ build initial context (prompt template + metadata + memory bootstrap)
   ▼
Transport connect (client SDK ↔ worker, WebRTC offer/answer)  event: session.connected
   ▼
Greeting (pre-cached or streamed TTS)                         event: agent.speaking.started
   ▼
        ┌────────────────  CONVERSATION LOOP (state machine §7) ────────────────┐
        │ listen → endpoint → transcribe(final) → think(stream) → speak(stream) │
        │        ↑__________ barge-in / tools / timeouts / re-prompt __________│
        └───────────────────────────────────────────────────────────────────────┘
   ▼ (hangup | control API end | goodbye detected | max-duration | fatal error)
Teardown: flush transcripts, persist context snapshot, close providers,
          finalize recording, emit metrics                     event: session.ended {reason}
   ▼
Post-session (async, event consumers): webhook fan-out, analytics, billing meter
```

Reconnect: transport drop ≠ session end. Worker holds the session in `SUSPENDED` for a grace window (default 30 s); client SDK reconnects with the session token; context is restored from the live object (or snapshot if the worker died). *This directly kills the reconnect-amnesia defect (Phase 1.5 §7).*

# 7. State Machine

Two nested machines. **All transitions are events; all states are queryable; illegal transitions raise.**

**Session lifecycle:**

```
CREATED ─connect─► CONNECTED ─greeting done─► IN_CONVERSATION ─end/hangup/timeout─► ENDING ─cleanup─► ENDED
   │                   │                            │ transport drop                                ▲
   │ connect timeout   │ fatal error                ▼                                               │
   └────► FAILED ◄─────┘                        SUSPENDED ─reconnect─► IN_CONVERSATION              │
                                                    └─ grace expired ───────────────────────────────┘
```

**Turn machine (inside IN_CONVERSATION):**

```
LISTENING ──vad+partials──► USER_SPEAKING ──endpoint score ≥ θ──► ENDPOINTING(adaptive delay)
    ▲                            │  ▲                                    │ confirmed
    │                            │  └── score < θ / speech resumes ──────┘
    │                            ▼
    │                        (barge-in path: if agent speaking → InterruptEngine policy check
    │                         → pass: cancel speech, truncate context to spoken words, → USER_SPEAKING
    │                         → fail (backchannel): continue speaking, log event)
    │
    │        THINKING (LLM streaming; tool calls may interleave: TOOL_WAIT ⇄ THINKING,
    │                  filler speech if wait > threshold)
    │                            │ first sentence ready
    ▼                            ▼
 idle timeout →              SPEAKING (TTS streaming; interruptible per policy)
 re-prompt / end                 │ playback complete
                                 └────────────► LISTENING
```

Determinism guarantee: content varies; **behaviour does not**. Given the same event sequence, the machine takes the same transitions. Timeouts (idle, max-turn, max-session, tool), interruption policy outcomes, and failure routing are all table-driven configuration on the Agent — accepted-and-honoured (Philosophy #6).

# 8. Event Architecture

**Envelope (one shape for every event, wire and storage):**

```json
{
  "id": "evt_9f3…",             // unique, monotonic per session (ULID)
  "session_id": "ses_…",
  "org_id": "org_…",
  "type": "turn.user.completed",
  "ts": 1789245600.123,          // runtime clock, seconds
  "seq": 41,                     // strict per-session ordering
  "turn": 7,                     // conversation turn index (nullable)
  "data": { … }                  // type-specific payload, versioned schemas
}
```

**Vocabulary (producers → payload essentials):**

| Event | Producer | Payload highlights |
|---|---|---|
| `session.created / connected / suspended / resumed / ended / failed` | Orchestrator | reason, channel, agent_id |
| `speech.user.started / stopped` | Turn Intelligence (VAD) | energy, confidence |
| `transcript.partial` | STT stream | text, stability score |
| `transcript.final` | STT stream | text, ts range, words[] |
| `turn.user.completed` | EndpointScorer | endpoint latency, method (silence/semantic) |
| `agent.thinking.started / finished` | AI Runtime | model, ttft_ms |
| `agent.speaking.started / finished` | Speech Out | chars, audio_ms |
| `agent.interrupted` | InterruptEngine | spoken_prefix (what was actually heard) |
| `tool.called / completed / failed` | Tool Runtime | name, duration_ms, retries |
| `timeout.idle / turn / session / tool` | LifecycleManager | which policy fired |
| `metric.turn` | Latency taps | per-stage ms breakdown (§15) |
| `error.provider / runtime` | any | typed code, retriable? |

**Consumers & delivery:**

- **Client WS channel** (SDK): live, per-session, at-most-once, `seq` lets clients detect gaps; drives the SDK's `state` callback (`listening|thinking|speaking`) — finally implementable.
- **Consumer webhooks**: durable via Postgres outbox → dispatcher with exponential retry (1 m→1 h, 24 h cap) → dead-letter table; HMAC-signed (keep Phase-1 signing); per-org event-type subscription filter.
- **Persistence**: `event_log` table (the session's replayable history; transcripts and metrics are *projections* of it).
- **Metrics pipeline**: consumes `metric.*` into aggregates.

**Ordering:** strict per-session (`seq`); no cross-session ordering promised. **Failure:** producers never block on consumers; the bus is fire-into-outbox; a dead consumer never stalls a conversation.

# 9. Provider Architecture

Providers are plugins registered in a **ProviderRegistry**, selected per-agent by config, instantiated per-session (or pooled where safe).

**Interfaces (contracts live in `core/providers/`):**

```
STTProvider      open_stream(lang, sample_rate) → yields Partial(text, stability) | Final(text, words)
                 capabilities: {streaming: bool, languages[], partials: bool, word_ts: bool}
TTSProvider      open_stream(voice, lang) ; say(text_chunk) → yields audio frames
                 capabilities: {streaming, voices[], sample_rates[], ssml}
LLMProvider      stream(context, tools?) → yields TokenDelta | ToolCallDelta
                 capabilities: {tools, json_mode, context_window}
TransportProvider connect(session) → duplex audio frames + client events (WebRTC now; SIP later)
StorageProvider  put/get recordings & artifacts (Supabase storage / S3)
AnalyticsProvider consume metric events (optional sink)
```

**Rules:**
1. Capability flags are declared, and the Control Plane **validates agent config against them at write time** — configuring Sarvam TTS with an unsupported language fails the `PATCH`, loudly (Philosophy #6).
2. Every provider call is wrapped in the fault-tolerance layer (§14): timeout, typed error, circuit breaker, optional failover chain (`stt: ["sarvam", "deepgram"]`).
3. A provider plugin is one module + one registry entry + one contract test run against the shared provider test-suite. Nothing else changes.
4. Initial plugin set: STT Deepgram (streaming) + Sarvam (Hinglish; verify streaming support — if batch-only, it runs in declared degraded mode) · TTS Cartesia + Sarvam · LLM Groq + Anthropic · Transport SmallWebRTC (via Pipecat), Daily optional, SIP later.

# 10. Memory Architecture

Layered, token-budgeted, persistent. The **ContextBuilder** assembles each LLM call from these layers under an explicit budget (default 8k tokens, per-agent configurable):

| Layer | Content | Budget share | Lifecycle |
|---|---|---|---|
| **System layer** | rendered prompt template (agent `system_prompt` + `{{metadata.*}}` variables + language/persona directives) | fixed | per session, re-renderable mid-call |
| **Entity memory** | extracted stable facts: names, numbers, dates, addresses, commitments ("callback at 3pm") — small structured store, always included | small, capped | updated per turn by a cheap extraction pass; persisted |
| **Rolling summary** | compressed history beyond the window (background summarization every N turns using a small/fast model) | medium | persisted with session |
| **Working window** | verbatim recent turns | largest | trimmed by token count, never by turn count |
| **Tool context** | recent tool results relevant to the current turn | as needed | expires after use |

**Persistence & recovery:** after every completed turn, a compact **context snapshot** (summary + entities + last-k turns) is written asynchronously to Postgres. Reconnect (§6) and worker crash-recovery restore from it. *Transcripts and context are projections of the same event log — they can never diverge again (Phase 1.5 §7 defect class).*

**Cleanup:** snapshots superseded in place per session; full event log retained per org retention policy (default 90 days), then archived to storage.

**Cross-session memory** (returning-caller profiles) is a Stage-4 feature: an opt-in, org-scoped profile store keyed by consumer-supplied identity, injected as an additional layer. Designed-for, not built now.

# 11. Streaming Architecture

The defining constraint: **stages overlap; nothing waits for a whole anything.**

```
user audio ──frames──► STT stream ──partials──► TurnIntelligence (scores EOT continuously)
                             │                        │ pre-warm signal (likely EOT in ~300ms)
                             └final──► ContextBuilder ─► LLM stream ──tokens──►
        sentence chunker ──first sentence──► TTS stream ──audio──► Playback (streams immediately)
```

- STT partials begin flowing while the user speaks → endpointing is *informed*, LLM context can be pre-staged, and live captions/`transcript.partial` events are free.
- On high end-of-turn probability, the LLM call is **pre-warmed** (context serialized, connection open) so TTFT lands during the endpoint delay, not after it.
- LLM tokens are chunked at clause/sentence boundaries into TTS; the first audible audio targets < 400 ms after LLM start.
- Playback is frame-streamed with an interruption flush path measured in one jitter-buffer depth (< 150 ms perceived stop).
- Every arrow above carries a monotonic timestamp → `metric.turn` (§15).

Latency budget per stage (p50 targets, enforced by CI perf tests at Stage 2+): endpoint decision ≤ 250 ms after true end-of-speech (adaptive; vs. 600 ms fixed today) · STT final ≤ 150 ms after endpoint · LLM TTFT ≤ 350 ms (pre-warmed) · first TTS audio ≤ 200 ms · **voice-to-voice p50 ≤ 1.0 s**.

# 12. Audio Architecture

- **Capture:** transport-native (browser `getUserMedia` with AEC/NS/AGC constraints *explicitly requested*, not defaulted; SIP audio at 8 kHz later). Client SDK owns mic UX and permission flows.
- **Normalization:** one resampler at ingress (48 k → 16 k for VAD/STT), one at egress (TTS native → transport rate). Sample-rate is a session property, never an ambient assumption (the 48 k/16 k Silero failure from Phase 1.5 becomes structurally impossible).
- **Buffers/queues:** fixed-size ring buffers with explicit overflow policy (drop-oldest + `error.runtime` event) — no unbounded queues anywhere in the audio path.
- **Server-side conditioning (optional pipeline stages):** noise suppression and gain normalization as insertable stages (Krisp-class plugin later); disabled by default for latency.
- **Playback Engine:** ordered frame queue per session with immediate-flush primitive (barge-in) and priority slots (filler/ack audio can pre-empt queued sentences without killing the LLM stream).
- **Playback Scheduler:** decides *what* enters the queue: greeting pre-cache, filler policy (ack audio if predicted wait > 700 ms), crossfade-on-interrupt.
- **Audio configuration** is per-agent, validated: input rate, VAD profile (sensitivity presets: quiet-room / noisy / telephony), interruption policy, filler style.

# 13. Folder Structure

```
voxvoice/
├── CLAUDE.md                  → thin pointer to docs/ (no duplicated spec)
├── docs/                      → §14
├── platform/                  → the Python backend (project root, uv-managed)
│   ├── core/                  # contracts & shared kernel — imports nothing above it
│   │   ├── events.py          #   envelope + typed event classes (§8)
│   │   ├── states.py          #   state-machine definitions (§7)
│   │   ├── providers/         #   STT/TTS/LLM/Transport/Storage interfaces (§9)
│   │   ├── errors.py          #   PlatformError hierarchy
│   │   └── ids.py, types.py
│   ├── api/                   # CONTROL PLANE (FastAPI) — kept from Phase 1, hardened
│   │   ├── routers/           #   agents, sessions, control, events(WS), webhooks-admin
│   │   ├── security.py        #   org keys (kept) + ephemeral session tokens
│   │   ├── models.py schemas.py config.py db.py bootstrap.py
│   ├── runtime/               # RUNTIME PLANE
│   │   ├── worker.py          #   SessionWorker host, session registry, backpressure
│   │   ├── orchestrator/      #   Conversation Orchestrator, lifecycle, state machine impl
│   │   ├── turn/              #   VAD wrapper, EndpointScorer, InterruptEngine, timing
│   │   ├── ai/                #   ContextBuilder, PromptRuntime, ResponseValidator, ToolRuntime
│   │   ├── memory/            #   MemoryEngine: window, summary, entities, snapshots
│   │   ├── speech/            #   sentence chunker, PlaybackScheduler
│   │   └── pipeline/          #   the ONLY pipecat-importing package (transports, service glue)
│   ├── providers/             # plugin implementations, one module per vendor
│   │   ├── stt/ deepgram.py sarvam.py …
│   │   ├── tts/ cartesia.py sarvam.py …
│   │   ├── llm/ groq.py anthropic.py …
│   │   └── transport/ smallwebrtc.py …
│   ├── events/                # EventBus impls (inproc, redis later), outbox, webhook dispatcher
│   ├── observability/         # metric taps, tracing, cost meter, log config
│   ├── tests/                 # unit + contract (provider suite) + state-machine + e2e-with-fakes
│   └── pyproject.toml
├── sdk/                       # TypeScript client SDK
│   ├── core/                  #   session token → WebRTC connect, event stream, control calls
│   └── react/                 #   useVoxVoice()
├── evals/                     # Hinglish phrase sets, latency benchmarks, turn-detection eval
└── deploy/                    # compose files, systemd units, oracle scripts (from Phase 1 docs)
```

Why each top folder exists: `core/` = the dependency floor, guarantees plugin/runtime decoupling · `api/` = control plane, stateless, scalable · `runtime/` = the product · `providers/` = the replaceable edge · `events/` = the integration surface · `observability/` = invariant I1 made real · `sdk/` = the consumer contract · `evals/` = the moat's measuring stick · `deploy/` = ops knowledge as code. One responsibility each; anything that doesn't fit these is a design smell to be challenged, not accommodated.

# 14. Documentation Structure

Already constituted in [`docs/README.md`](../README.md); this blueprint extends it:

```
docs/
├── INDEX.md                        # central index (exists, mandatory)
├── README.md                       # conventions (exists)
├── SPEC.md                         # superseded-in-part by this document (§22)
├── architecture/
│   ├── engineering-constitution.md # THIS DOCUMENT (binding once ratified)
│   └── <focused designs: event-system.md, memory-engine.md, …>
├── adr/                            # ADR-0001… from §3 on ratification; all future changes
├── audits/                         # immutable, versioned (exists)
├── runtime/                        # per-component runtime docs, state-machine tables
├── apis/                           # REST/WS/SDK reference, event catalogue, webhook guide
├── providers/                      # per-vendor integration notes, capability matrices
├── performance/                    # latency budgets, benchmark results per release
├── security/                       # threat model, key rotation runbook
├── testing/                        # test strategy, provider contract suite guide
├── deployment/                     # runbooks (migrate Phase-1 DEPLOY/ORACLE docs here)
├── roadmap/                        # stage plans, feature sequencing
└── research/                       # dograh-api-notes.md (archived knowledge), sarvam notes, EOT-model research
```

Sync rule (constitutional): a PR that changes architecture/runtime/API behaviour without its doc update is incomplete. Docs carry status headers; superseded docs are marked, never deleted.

# 15. Observability

- **Per-turn structured metric** (`metric.turn` event): timestamps at every stage boundary (speech-end-true, endpoint-decided, stt-final, llm-first-token, tts-first-audio, playback-start) → derived stage latencies + voice-to-voice. Stored; percentiles per agent/org/provider.
- **Tracing:** one trace per turn (OpenTelemetry), session id as trace attribute; provider calls are spans.
- **Logging:** structured JSON (loguru → JSON sink), session/turn ids on every line, token/secret redaction filter at the sink (constitutional: secrets never reach a log line).
- **Health:** liveness (process), readiness (DB + registry), per-worker session-count and saturation gauges; `GET /health` extends the Phase-1 shape.
- **Cost tracking:** every provider call reports units (STT seconds, LLM tokens, TTS characters) → per-session cost meter → per-org aggregates (this is also the Phase-3 billing meter, designed once).
- **Diagnostics:** session replay from the event log (step through a conversation's exact event sequence); last-N-sessions ring buffer of debug-level logs.

# 16. Scalability Strategy (staged, no boundary changes)

| Stage | Who | Deployment | What changes |
|---|---|---|---|
| **1. Local/dev** | solo dev | one process: Control+Runtime co-located, SQLite ok, inproc bus | nothing — this is the default `make dev` |
| **2. First deploy / demos** | VoxHire + pilots | one VM (Oracle A1): one API proc + one worker proc, Postgres (Supabase), inproc bus per proc + DB outbox | systemd units from `deploy/`; TURN via transport provider |
| **3. Startup scale** | ~100s concurrent sessions | N stateless API replicas + M workers; Redis event bus + session→worker routing table in DB; webhook dispatcher as its own proc | `EventBus` impl swap (D6); worker autoscaling on session gauge |
| **4. Enterprise/global** | 1000s concurrent, multi-region | regional worker pools (media latency), global control plane, provider failover chains active, cross-session memory store, SSO/compliance | region-aware session placement; no component redesign — capacity + policy |

The constitutional point: stages differ in *deployment topology and adapter choices*, never in component boundaries. The Phase-1 mistake (in-process state that silently forbids a second worker) is outlawed: **all cross-request state lives in Postgres or the bus from Stage 1.**

# 17. Migration Roadmap (current repo → this architecture)

Incremental; each step leaves a working system and its tests green.

- **M0 — Ratify & preserve knowledge (0.5 day).** Owner approves this document → extract ADR-0001…0007. Move Dograh verified-API notes into `docs/research/dograh-api-notes.md`. Rotate the exposed Groq/Cartesia/Dograh keys, `git init`, first commit (Phase-1 H1/H2 — precondition for everything).
- **M1 — Repo reshape, keepers in place (1–2 days).** Create `core/`, move errors/ids/event types in; keep `api/` (auth, agents, sessions, webhooks signing) and its tests running. Delete `adapters/`, `engine_events` router, poller, `engine_workflow_id`. Sessions endpoint temporarily returns 503 for `start` (honest gap, briefly).
- **M2 — Runtime Plane seed (4–7 days).** Port voicebot into `runtime/` as the first SessionWorker: multi-session, agent-config-driven (prompt honoured), platform-connected (transcripts as events → DB live). Keep Groq/Cartesia as first plugins. *At this milestone VoxVoice already surpasses the Dograh path: live transcripts, real end-session, working local dev calls (native process — no Docker TURN problem).*
- **M3 — Streaming & Turn Intelligence core (1.5–2.5 weeks).** Deepgram streaming STT plugin (partials) · EndpointScorer v1 (adaptive delay honouring agent fields + heuristic semantic completeness) · InterruptEngine v1 (min-words + backchannel list) · latency taps + `metric.turn`. **Exit criterion: measured p50 voice-to-voice ≤ 1.2 s on the Oracle box.**
- **M4 — Event surface & SDK (1–1.5 weeks).** WS event gateway, webhook outbox+retry+DLQ, mid-call control API (end/say/inject), TypeScript SDK core + React hook consuming real events. VoxHire integration becomes possible here.
- **M5 — Memory & prompt runtime (1 week).** ContextBuilder with budget, rolling summary, entity extraction v1, metadata templating, snapshot/reconnect.
- **M6 — Moat & hardening (ongoing).** Sarvam STT/TTS plugins + Hinglish evals (`evals/` with the SPEC's mandated phrases) · smart-turn model evaluation for EndpointScorer v2 · state-machine timeout policies · load test 50 concurrent · tool runtime (design §5/§9 first, then build).

Reuse/replace/delete ledger: **keep** — api auth/CRUD/schemas/models(+extensions)/tests/webhook signing/deploy runbooks(→`deploy/`+`docs/deployment/`) · **rewrite** — voicebot→runtime, sessions router (engine calls→worker dispatch) · **delete** — adapters/, engine_events, poller, webclient embed-mounting (replace with an SDK demo page), `CLAUDE.md` spec duplicate, `demo.db`, vendored toolchains.

# 18–20. Engineering, Coding Standards & Design Patterns

**Engineering standards (constitutional):** trunk-based small commits (git exists as of M0) · CI: ruff + pytest + import-layer-lint + provider contract suite on every push · every feature lands with its events, metrics, and doc update · perf regressions (latency budget CI check) block merge at Stage 2+ · silent config no-ops are release blockers · security: org-scoped keys hashed (keep), ephemeral session tokens for clients (JWT, 15 min, session-scoped), secrets only via env/secret-store, webhook HMAC (keep), audit log = the event log, PII redaction in transcripts as per-org policy (compliance readiness: retention config + export/delete per session — GDPR/DPDP shaped).

**Coding standards:** Python 3.12, full type hints, Pydantic v2 everywhere on wire boundaries, `Literal`/`StrEnum` for all state-like strings (kills the Phase-1 free-string enums), async-first with **no sync I/O in the event loop** (async SQLAlchemy from M1), dataclasses for internal value types, one class per conversation-state writer, tests-with-fakes for every provider interface, docstrings state *why*.

**Sanctioned patterns:** ports-and-adapters at the **provider** seam (the Phase-1 lesson, re-aimed at the right axis) · state machine (explicit tables, not implicit ifs) · event sourcing *lite* (event log as truth for session history; current-state rows as projections) · registry+factory for plugins · outbox for external delivery · actor-per-session (task group owning all session state) · circuit breaker + failover chain on providers · anti-patterns explicitly banned: singletons holding cross-request state, fire-and-forget tasks without handles, catch-all except that swallows typing, config accepted-but-ignored.

# 21. Future Roadmap (post-M6 horizon)

Telephony (SIP transport provider + number management) → tools GA with consumer-function webhooks → knowledge-base retrieval layer (a ContextBuilder source, not a bolt-on) → squads/handoff (orchestrator-level agent switch, context preserved — trivial once context is a first-class object) → speech-to-speech provider type (OpenAI-Realtime-class as a *pipeline alternative* behind the same session/event contract) → multimodal (video track on the same transport, vision events in the same envelope) → cross-session memory & caller profiles → multi-region.

# 22. Relationship to SPEC.md

SPEC.md remains the source for: product goal, consumer model, org/auth contract, Hinglish acceptance phrases, phase-gate discipline. It is **superseded by this constitution** on: the engine-adapter architecture (§5 of SPEC → replaced by provider architecture, D1/D7), the Dograh-primary decision (SPEC §10.1 → resolved: first-party runtime), and the repo layout (SPEC §8 → §13 here). SPEC.md will be annotated with a superseded-sections banner at M0 (documentation sync rule).

---

*This document is analysis and design only — no code was modified. Awaiting owner ratification. On approval: execute M0 (keys, git, ADR extraction) before any other work.*

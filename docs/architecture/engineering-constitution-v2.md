# VoxVoice — Engineering Constitution (Revision v2, Final)

| | |
|---|---|
| **Document** | `docs/architecture/engineering-constitution-v2.md` |
| **Status** | **RATIFIED v2.0 (2026-07-16, owner approval recorded at governance bootstrap).** The binding Engineering Constitution. Supersedes [engineering-constitution.md](engineering-constitution.md) (v1 Draft, retained unmodified as version history). Governance system built around it: [`.voxvoice/`](../../.voxvoice/README.md). Foundational decisions D1–D7 + Part B extracted as [ADR-0001…0007 + P-ADR-0001](../adr/README.md) at milestone M0 (2026-07-16). |
| **Date** | 2026-07-16 |
| **Author** | Claude Code (acting Chief Architect / Technical Owner) |
| **Authority** | Once ratified: every implementation follows this architecture unless a documented ADR explicitly changes it. |
| **Revision purpose** | Make the constitution technology-neutral, provider-agnostic, capability-based, internally consistent, and valid across full technology replacement. All product- and vendor-specific choices are relocated to **Part B — Implementation Profile**, which is versioned and replaceable without amending Part A. |
| **Inputs** | v1 constitution · [Phase 1 audit](../audits/phase-1-repository-audit.md) · [Phase 1.5 runtime audit](../audits/phase-1.5-voice-runtime-audit.md) |

**How to read this document.**
- **Part A** is the Constitution: architecture, contracts, responsibilities, behaviour. It names **no** vendor, framework, database, broker, cloud, or SDK. It is intended to remain valid for 5–10 years.
- **Part B** is the current Implementation Profile: today's concrete technology selections, each mapped to a Part-A capability. Part B changes via lightweight ADRs; Part A changes only via constitutional ADRs.
- **Appendix R** is the Revision Record for v2 (change summary, removed assumptions, impact reports, readiness score).
- Sections marked `〔R2〕` were materially revised from v1; unmarked sections were editorially tightened only.

---

# PART A — THE CONSTITUTION

## A0. Glossary (single name per concept) 〔R2 — new〕

| Term | Meaning (the only name used for it) |
|---|---|
| **Consumer** | An application built on VoxVoice (configures agents, starts sessions, receives events). |
| **Caller** | The human speaking in a session. |
| **Agent** | A configuration object: persona, language, provider selections, policies. |
| **Session** | One conversation instance between a caller and an agent. |
| **Turn** | One caller-utterance → agent-response exchange within a session. |
| **Control Plane** | The stateless service handling auth, configuration, session admission, control commands, and event delivery to consumers. |
| **Runtime Plane** | The stateful service executing live conversations (Session Workers). |
| **Session Worker** | A Runtime Plane host process executing one or more sessions, each as an isolated task group ("actor"). |
| **Provider Plugin** | A replaceable module implementing exactly one capability contract (§A6). |
| **Capability** | A named, versioned interface the runtime consumes (e.g., Streaming Speech-to-Text). |
| **Pipeline Substrate** | Whatever library/framework internally carries media frames between stages. An implementation detail confined to one module (§A6.4). |
| **Event** | A typed, enveloped, ordered fact emitted by its single owning component (§A5). |
| **System of Record** | The single transactional database holding all durable state (§A7). |
| **Event Bus** | The delivery mechanism moving events from producers to subscribers; implementation varies by deployment stage (§A9). |
| **Turn Intelligence** | The layer deciding speech boundaries, endpointing, and interruption policy (§A4). |
| **Implementation Profile** | The versioned Part-B document binding capabilities to current concrete technologies. |

## A1. Product Vision

**VoxVoice is a provider-agnostic, production-grade Voice AI Runtime Platform. The runtime is the product.**

Consumers — interview, sales, support, healthcare, banking, education, and future multimodal agents — configure agents, start sessions, receive live events, and issue control commands. They never touch audio, providers, or conversation mechanics.

The product promise: *"Give me a configured agent and a session, and I get a natural, low-latency, interruptible, context-aware spoken conversation — with live events, mid-call control, transcripts, recordings, and metrics — regardless of which vendors implement the capabilities underneath."*

**Product invariants** (every architectural decision serves these):

- **I1 — Latency is a feature.** Voice-to-voice p50 ≤ 1.0 s, p95 ≤ 1.8 s, measured per stage on every turn. Unmeasured latency is a defect.
- **I2 — Conversations are observable and controllable, live.** Every session emits a typed event stream in real time and accepts mid-call control commands. A black-box call is a defect.
- **I3 — Providers are plugins, never architecture.** 〔R2〕 Replacing any provider — or any *category* of infrastructure (database, broker, pipeline substrate, transport technology, cloud) — touches configuration plus one plugin/adapter module, never business logic. Language/market differentiation is a plugin choice, not a rewrite.

## A2. Engineering Philosophy

1. **Own the loop.** The conversation loop — turn-taking, interruption, memory, timing, events, control — is owned by VoxVoice. Commodity layers (codecs, media plumbing, model inference) are rented behind contracts.
2. **Boring core, innovative edges.** The core is conventional: asynchronous concurrency, typed events, an explicit state machine, one transactional store. Innovation is spent where it differentiates: turn-taking intelligence, latency engineering, target-language conversational quality.
3. **Everything measured.** No component ships without emitting its latency, errors, and cost.
4. **Deterministic where it matters.** Conversation *content* is probabilistic; conversation *behaviour* (lifecycle, timeouts, interruption policy, tool scheduling, failure routing) is a deterministic, table-driven state machine.
5. **One writer per fact.** Every piece of state has exactly one owning component; all others learn of it via events.
6. **Accepted config must act.** If the API accepts a field, the runtime honours it or the API rejects it at write time. Silent no-ops are Severity-High defects.
7. **Grow by stages, design for the last stage.** Deployment topology changes across scale stages (§A9); component boundaries never do.
8. 〔R2〕 **Architecture describes responsibilities, never implementations.** Any sentence in Part A that can only be true for one vendor/framework is a documentation defect.

## A3. Foundational Decisions 〔R2 — generalized〕

To be ratified as ADR-0001…0007. Each records Problem · Alternatives · Selected · Trade-offs · Risks · Future impact (full detail in the ADRs; summarized here).

- **D1 — First-party runtime.** VoxVoice builds and owns its conversation runtime. Renting a complete external voice engine is rejected: it forfeits events, control, metrics, and differentiation (proven by audit). External full-stack engines may exist only as optional, clearly-degraded integration plugins, never as the core path.
- **D2 — Substrate behind contracts.** 〔R2〕 The runtime *may* stand on a third-party pipeline substrate for commodity media plumbing, but the substrate is confined to a single module behind VoxVoice-owned interfaces. No other module may import it. Substrate replacement has a one-directory blast radius. (Current selection: Part B.)
- **D3 — Two planes.** Control Plane (stateless) and Runtime Plane (stateful Session Workers) share nothing in memory and communicate via the System of Record and the Event Bus. Co-located in development, separable in production without code change.
- **D4 — Event-driven core.** Every runtime fact is a typed event with one envelope (§A5), produced once by its owner, fanned out to client channels, consumer webhooks, persistence, and metrics. Cross-layer integration happens through events, not direct calls.
- **D5 — Streaming-first with Turn Intelligence.** First-class Speech-to-Text capability requires streaming partial hypotheses. Endpointing fuses activity detection + semantic end-of-turn scoring + adaptive delay. Interruption is a policy engine. Batch-only providers run in *declared* degraded mode (capability flags, §A6).
- **D6 — One System of Record.** 〔R2〕 All durable state — configuration, sessions, transcripts, context snapshots, event log, delivery outbox, metrics — lives in a single transactional database per deployment. Specialized infrastructure (brokers, caches, queues) enters only at the scale stage that requires it, always behind an existing interface.
- **D7 — Keep-and-harden the control-plane assets; delete the engine-adapter axis.** The Phase-1 auth, configuration CRUD, org-scoping discipline, signed-webhook mechanism, and test approach are retained. The whole-engine adapter abstraction is retired in favour of capability-level provider contracts; removed integrations are archived to documentation.

## A4. Runtime Architecture

### A4.1 High-level structure

```
 Consumers ── control API (request/response) · live event channel (push) · client SDK
                     │
     ┌───────────────┴────────────────┐            ┌──────────────────────────────┐
     │         CONTROL PLANE          │            │        RUNTIME PLANE         │
     │ auth · agents · sessions ·     │◄─ events ──│  Session Workers:            │
     │ control commands · event       │── control ►│  Conversation Orchestrator   │
     │ gateway · webhook dispatcher   │            │  Audio Runtime · Turn        │
     └───────────────┬────────────────┘            │  Intelligence · AI Runtime · │
                     │                             │  Speech Out · Memory ·       │
                     ▼                             │  Tool Runtime                │
     ┌────────────────────────────────┐            └──────────────┬───────────────┘
     │  SYSTEM OF RECORD + EVENT BUS  │◄──────────────────────────┘
     └────────────────────────────────┘
     ┌────────────────────────────────────────────────────────────────────────────┐
     │ PROVIDER PLUGINS (capability-declared, config-selected):                   │
     │ Streaming STT · Streaming TTS · LLM · Real-time Transport · Storage ·      │
     │ Analytics · (future capability types added without structural change)      │
     └────────────────────────────────────────────────────────────────────────────┘
 Media path: caller endpoint ◄─ real-time transport ─► Runtime Plane (never the Control Plane)
```

**Layer rules (CI-enforced):** `providers/` imports only `core/` (contracts). `runtime/` imports `core/` and provider *interfaces* (resolved via registry); only the designated pipeline module imports the substrate. `api/` imports `core/` and reaches `runtime/` only via the session-control interface and the Event Bus. `core/` imports nothing above it.

### A4.2 Session Worker components 〔R2 — component spec table added〕

Every runtime component is defined by the standard specification of §A11.3. Summary:

| Component | Responsibility (single) | Consumes | Produces | Key failure mode → recovery |
|---|---|---|---|---|
| **Session Worker** | Host N session actors; admission, backpressure, reaping | session assignments | worker health gauges | worker crash → sessions recovered from snapshots on another worker |
| **Conversation Orchestrator** | Sole writer of conversation state; runs state machine; lifecycle policy | all component events | `session.*`, state transitions | illegal transition → typed error, session fail-safe teardown |
| **Audio Runtime** | Transport I/O, sample-rate normalization, bounded buffers, playback queue | media frames | frames, overflow events | buffer overflow → drop-oldest + `error.runtime` |
| **Turn Intelligence** | Speech boundaries: activity detection, endpoint scoring (adaptive+semantic), interruption policy, timing hints | audio features, STT partials | `speech.*`, `turn.*`, interrupt decisions | detector failure → conservative silence-based fallback |
| **AI Runtime** | Context assembly, prompt rendering, LLM streaming, response validation/formatting, tool scheduling | turn completions, memory layers, tool defs | `agent.thinking.*`, `tool.*`, token stream | LLM failure → failover chain → apology-and-retry policy |
| **Speech Out** | Sentence-chunked streaming synthesis, playback scheduling, filler/ack policy, interruption flush | validated text stream | `agent.speaking.*`, audio frames | TTS failure → failover chain → text-only event degradation |
| **Memory Engine** | Layered context under token budget; snapshots; recovery | transcripts, tool results | context layers, snapshot writes | snapshot lag → rebuild from event log |
| **Tool Runtime** | Async tool execution: timeout, retry, declared parallelism, result injection | tool call deltas | `tool.*` | tool timeout → policy: skip/retry/inform caller |

### A4.3 Runtime lifecycle

```
Consumer: start session (agent, channel, metadata)
  → Control Plane: authenticate → validate config against declared capabilities (reject impossible)
  → create Session in System of Record → assign Session Worker → issue scoped client credential
  → Worker: resolve Provider Plugins → build initial context → transport connect
  → greeting → CONVERSATION LOOP (§A4.4) → teardown (flush, snapshot, close providers, finalize
    recording, emit metrics) → post-session consumers (webhooks, analytics, metering)
```

**Reconnect:** transport drop ≠ session end. The session enters `SUSPENDED` for a configurable grace window; the client reconnects with its credential; context restores from the live actor or, if the worker died, from the last snapshot.

### A4.4 State machine

**Session lifecycle:**
`CREATED → CONNECTED → IN_CONVERSATION → ENDING → ENDED`, with `FAILED` reachable from CREATED/CONNECTED (connect timeout, fatal error) and `SUSPENDED ⇄ IN_CONVERSATION` on transport drop/reconnect (grace expiry → ENDING).

**Turn machine (inside IN_CONVERSATION):**
`LISTENING → USER_SPEAKING → ENDPOINTING(adaptive) → THINKING (⇄ TOOL_WAIT) → SPEAKING → LISTENING`, with the barge-in path: caller speech during SPEAKING → interruption policy check → pass: cancel synthesis, truncate context to *actually spoken* words, → USER_SPEAKING; fail (backchannel): continue, log event. Idle/turn/session/tool timeouts route per table-driven policy.

**Guarantees:** all transitions are events; all states queryable; illegal transitions raise; given the same event sequence the machine takes the same transitions. All timing/policy parameters are Agent configuration — accepted-and-honoured (Philosophy #6).

## A5. Event Architecture

**One envelope, wire and storage:** `{id (unique, per-session monotonic), session_id, org_id, type, ts, seq (strict per-session order), turn (nullable), data (versioned schema)}`.

**Vocabulary (producer-owned):** `session.created|connected|suspended|resumed|ended|failed` (Orchestrator) · `speech.user.started|stopped` (Turn Intelligence) · `transcript.partial|final` (STT stream) · `turn.user.completed` (Endpoint scorer; includes endpoint latency + method) · `agent.thinking.started|finished` · `agent.speaking.started|finished` · `agent.interrupted` (with spoken prefix actually heard) · `tool.called|completed|failed` · `timeout.idle|turn|session|tool` · `metric.turn` (per-stage latency breakdown) · `error.provider|runtime` (typed, retriable flag). New event types may be added; existing payload schemas evolve by versioning, never by silent mutation (§A11.4).

**Consumers & delivery:**
- **Client channel** (push, per-session, at-most-once; `seq` exposes gaps) — drives the SDK state callback (`listening|thinking|speaking`).
- **Consumer webhooks** — durable outbox in the System of Record → dispatcher with exponential retry and a dead-letter store; payloads signed (keyed MAC); per-org event-type subscription filters.
- **Persistence** — the `event_log` is the session's replayable history; transcripts and metrics are projections of it.
- **Metrics pipeline** — consumes `metric.*`.

**Rules:** strict per-session ordering only; producers never block on consumers; a dead consumer never stalls a conversation.

## A6. Provider Architecture (capability-based) 〔R2 — fully generalized〕

Providers are plugins in a **Provider Registry**, selected per-agent by configuration, instantiated per-session (pooled where safe). A plugin implements exactly one **capability contract**:

| Capability contract | Interface essence | Declared capability flags (examples) |
|---|---|---|
| **Streaming Speech-to-Text** | open stream (language, sample rate) → partial hypotheses (text, stability) and finals (text, word timings) | streaming, partials, languages, word timestamps |
| **Streaming Text-to-Speech** | open stream (voice, language); accept text chunks → audio frames | streaming, voices, sample rates, markup support |
| **Large Language Model** | stream (context, tool definitions) → token deltas and tool-call deltas | tool calling, structured output, context window |
| **Real-time Transport** | duplex media frames + client events between caller endpoint and worker | media kinds, codecs, telephony, reconnection |
| **Storage** | put/get recordings and artifacts | durability class, signed access |
| **Analytics** | consume metric events (optional sink) | delivery semantics |

**Rules:**
1. **Capability flags are declared and validated at configuration write time.** An agent configured against an undeclared capability fails the write, loudly.
2. Every provider call passes through the fault-tolerance layer (§A10): timeout, typed error, circuit breaker, optional ordered failover chain per capability.
3. Adding a provider = one module + one registry entry + passing the shared per-capability contract test suite. Nothing else changes.
4. Degraded modes are explicit: a plugin lacking a first-class capability (e.g., non-streaming STT) must declare it; the runtime adapts behaviour and records the degradation in session metadata.
5. New **capability types** (e.g., speech-to-speech models, vision, voice cloning, denoising) are added as new contracts + registry categories — a purely additive change (§A12).
6. The runtime contains **no provider-specific business logic**; anything vendor-conditional lives inside that vendor's plugin.

## A7. Memory Architecture

Layered, token-budgeted, persistent. The Context Builder assembles every LLM call from these layers under an explicit per-agent budget:

| Layer | Content | Lifecycle |
|---|---|---|
| **System layer** | rendered prompt template (agent persona + `{{metadata.*}}` variables + language/persona directives) | per session; re-renderable mid-call |
| **Entity memory** | extracted stable facts (names, numbers, dates, commitments) — small structured store, always included | updated per turn by an inexpensive extraction pass; persisted |
| **Rolling summary** | compressed history beyond the window (background summarization) | persisted with session |
| **Working window** | verbatim recent turns, trimmed by token count | live |
| **Tool context** | recent tool results relevant to the current turn | expires after use |

**Persistence & recovery:** after each completed turn a compact context snapshot (summary + entities + last-k turns) is written asynchronously to the System of Record; reconnects and worker crash-recovery restore from it. Transcripts and context are projections of the same event log — they cannot diverge.

**Cleanup:** snapshots superseded in place; event log retained per org retention policy, then archived to Storage. **Cross-session memory** (returning-caller profiles, consumer-supplied identity) is a designed-for, later-stage additive layer.

## A8. Streaming & Audio Architecture

**Streaming principle: stages overlap; nothing waits for a whole anything.** STT partials flow during caller speech (informing endpointing and enabling pre-staged context); on high end-of-turn probability the LLM call is pre-warmed so first-token latency lands inside the endpoint delay; LLM tokens are chunked at clause boundaries into TTS; playback streams frame-by-frame with an interruption flush bounded by one jitter-buffer depth. Every stage boundary carries a monotonic timestamp feeding `metric.turn`.

**Per-stage latency budgets (p50, CI-enforced from Stage 2):** endpoint decision ≤ 250 ms after true end-of-speech · STT final ≤ 150 ms after endpoint · LLM first token ≤ 350 ms (pre-warmed) · first synthesized audio ≤ 200 ms · **voice-to-voice ≤ 1.0 s** (I1).

**Audio rules:** capture constraints (echo cancellation, noise suppression, gain) are explicitly requested from the transport, never defaulted · exactly one resampler at ingress and one at egress; sample rate is a session property, never an ambient assumption · all audio queues are fixed-size ring buffers with declared overflow policy · server-side conditioning stages (denoise, gain normalization) are optional insertable plugins, off by default for latency · the Playback Engine provides ordered frames, immediate flush (barge-in), and priority slots (filler/acknowledgment audio pre-empts queued sentences without cancelling generation) · the Playback Scheduler owns filler policy, greeting pre-cache, and interruption crossfade · audio configuration is per-agent and validated (rates, activity-detection profile, interruption policy, filler style).

## A9. Deployment & Scalability Strategy

| Stage | Scale | Topology | What changes |
|---|---|---|---|
| **1 Local/dev** | one developer | both planes in one process; embedded database acceptable; in-process Event Bus | nothing — default dev mode |
| **2 First deploy** | pilots | one host: one Control Plane process + one Session Worker process; managed relational database; in-process bus + durable outbox | service units; transport relay per profile |
| **3 Startup** | ~100s concurrent sessions | N stateless Control Plane replicas + M workers; distributed pub/sub broker behind the same Event Bus interface; session→worker routing table in the System of Record; dispatcher as its own process | Event Bus implementation swap; worker autoscaling |
| **4 Enterprise/global** | 1000s concurrent, multi-region | regional worker pools (media latency), global control plane, active failover chains, cross-session memory store, SSO/compliance | placement policy and capacity — no component redesign |

**Constitutional rule:** stages differ in deployment topology and adapter selection, never in component boundaries. All cross-request state lives in the System of Record or the Event Bus from Stage 1 — in-process state that forbids a second worker is outlawed.

## A10. Fault Tolerance

Timeouts on every external call (provider, webhook, database) with typed errors · retries with exponential backoff where idempotent · circuit breakers per provider instance · ordered failover chains per capability (configuration) · graceful degradation ladder: failover → declared degraded mode → inform-the-caller policy → clean session failure with full event trail · worker crash recovery from snapshots (§A7) · the Event Bus and webhook delivery are at-least-once with consumer-side idempotency via event `id`; the client channel is at-most-once with gap detection via `seq`.

## A11. Engineering Standards 〔R2 — strengthened, permanent rules〕

### A11.1 Constitutional rules
1. Architecture describes **responsibilities, never implementations**.
2. Every architectural decision requires an ADR; every architectural change updates documentation in the same change-set.
3. Every new module defines its **ownership and boundaries** before code is written.
4. Every configuration field is **enforced or rejected**; silent acceptance is prohibited.
5. All architectural assumptions are documented; an undocumented assumption found in code is a defect.
6. Secrets never appear in code, logs, or version control; redaction is enforced at the logging sink.
7. Anti-patterns banned outright: singletons holding cross-request state · fire-and-forget tasks without handles · catch-all exception handling that erases type information · config accepted-but-ignored · unbounded queues in media paths.

### A11.2 Security & compliance
Org-scoped hashed API credentials for consumers · short-lived, session-scoped credentials for callers/clients · authorization scoped to org on every query (audit-proven discipline, retained) · signed webhooks (keyed MAC) · the event log doubles as the audit log · per-org data retention, export, and deletion (data-protection-regulation shaped) · PII redaction in transcripts as per-org policy · threat model maintained under `docs/security/`.

### A11.3 Component specification standard 〔R2 — new〕
Every runtime component must document, in `docs/runtime/<component>.md`: **responsibilities · inputs · outputs · dependencies · lifecycle · emitted events · failure modes · recovery strategy**. A component lacking this specification may not merge.

### A11.4 Interface versioning strategy 〔R2 — new〕
Every public interface — consumer REST/control API, client event channel, webhook payloads, event schemas, provider capability contracts, SDK surface — carries an explicit version. Rules: additive changes within a version; breaking changes require a new version with a deprecation window; event `data` schemas are versioned per event type; capability contracts are versioned so plugins declare which contract version they implement. Nothing breaks silently.

### A11.5 Quality gates
Trunk-based small commits · CI: lint + tests + import-layer rules + per-capability provider contract suites on every push · every feature lands with its events, metrics, and documentation · latency-budget regression checks block merge from Stage 2 · state-machine transition tables have exhaustive tests · fakes exist for every capability contract.

## A12. Future-Proofing (additive-only extension points) 〔R2〕

The following require **new modules or plugins only**, never structural change: additional providers per capability (registry entry) · new transport technologies incl. telephony (Real-time Transport plugin) · new AI models (LLM plugin) or model *kinds* (new capability contract, e.g., speech-to-speech as an alternative pipeline behind the same session/event surface) · new memory implementations (Memory Engine strategy) · new storage engines / analytics systems (capability plugins) · voice cloning (TTS capability flag/contract extension) · video & multimodal (additional media kinds on the transport contract; new event types in the same envelope) · multi-agent collaboration (an Orchestrator-level policy over first-class context objects) · new deployment models (Stage table row, topology only). If a proposed feature *cannot* be expressed this way, it is a constitutional amendment and requires an ADR against Part A.

## A13. Documentation Constitution

As established in [`docs/README.md`](../README.md): everything linked from `INDEX.md` · audits immutable and versioned · ADRs for every architectural decision (Problem, Context, Alternatives, Selected Solution, Trade-offs, Risks, Future Impact) · superseded documents marked, never deleted · every doc carries a status header · a change affecting architecture/runtime/API behaviour is incomplete without its documentation update. Structure: `architecture/ · adr/ · audits/ · runtime/ · apis/ · providers/ · performance/ · security/ · testing/ · deployment/ · roadmap/ · research/`.

## A14. Folder Structure (responsibility map) 〔R2 — generalized labels〕

```
voxvoice/
├── docs/            # the documentation constitution (§A13)
├── platform/        # backend
│   ├── core/        # dependency floor: contracts, event types, state tables, errors, ids
│   ├── api/         # Control Plane (stateless)
│   ├── runtime/     # Runtime Plane: worker, orchestrator/, turn/, ai/, memory/, speech/,
│   │                #   pipeline/ ← the ONLY module importing the pipeline substrate
│   ├── providers/   # capability plugins: stt/, tts/, llm/, transport/, storage/, analytics/
│   ├── events/      # Event Bus implementations, outbox, webhook dispatcher
│   ├── observability/  # metric taps, tracing, cost meter, log configuration
│   └── tests/       # unit · capability contract suites · state-machine · e2e-with-fakes
├── sdk/             # client SDK: core (transport-agnostic session client) + UI-framework bindings
├── evals/           # language-quality phrase sets, latency benchmarks, turn-detection evals
└── deploy/          # topology-as-code for the deployment stages (§A9)
```

One responsibility per folder. Anything that fits none of these is a design smell to challenge, not accommodate.

---

# PART B — IMPLEMENTATION PROFILE v1 〔R2 — new; relocated from v1 Part A〕

> **Status:** current concrete selections. Each row binds a Part-A capability/role to today's technology. Changing a row = lightweight ADR + this table's update; Part A is untouched. Nothing in this table is architecture.

| Part-A role | Current selection | Notes / replacement path |
|---|---|---|
| Backend language & concurrency | Python 3.12, asyncio | Rewrite-in-place per module if ever profiled as bottleneck (unlikely: network + inference dominate) |
| Control Plane HTTP framework | FastAPI + Pydantic v2 | Any async HTTP framework satisfying §A11.4 |
| Pipeline substrate (§A6.4) | Pipecat ≥ 1.4 | Confined to `runtime/pipeline/`; replaceable with any frame-pipeline library or in-house |
| System of Record | Postgres (managed: Supabase) — prod; SQLite — unit tests | Any transactional RDBMS; outbox pattern is plain SQL |
| Event Bus (Stage 1–2 / Stage 3+) | in-process async pub/sub / Redis pub-sub | Any distributed pub/sub broker behind the `EventBus` interface |
| Streaming STT plugins | Deepgram (streaming); Sarvam for Hinglish (verify streaming; else declared degraded mode) | Any vendor passing the STT contract suite |
| Streaming TTS plugins | Cartesia; Sarvam (Hinglish) | Any vendor passing the TTS contract suite |
| LLM plugins | Groq (low-latency); Anthropic (quality) | Any vendor passing the LLM contract suite |
| Real-time Transport plugins | WebRTC via substrate's small-WebRTC transport; hosted-room vendor optional; SIP/telephony later | Any duplex media transport implementing the contract |
| Storage plugin | Supabase storage | Any object store |
| Client SDK | TypeScript core + React binding | Additional language bindings additive |
| ORM / models | SQLModel | Implementation detail of `api/` + persistence |
| Deploy Stage 2 target | One cloud VM (current: Oracle A1 class) + systemd + managed Postgres | Any host with a public IP for media relay |
| Observability sink | Structured JSON logs + OpenTelemetry traces; metrics in System of Record initially | Any OTel-compatible backend |
| Language moat evals | Hinglish phrase set from SPEC (three mandated test phrases included) | Additional languages additive under `evals/` |

**Profile-specific migration roadmap (M0–M6)** — unchanged in substance from v1 §17, now understood as *profile* execution detail: M0 ratify + rotate exposed keys + initialize version control + extract ADRs + archive external-engine research notes → M1 repo reshape (create `core/`, delete engine-adapter axis) → M2 Runtime Plane seed (port the local voicebot prototype into `runtime/` as the first Session Worker) → M3 streaming STT + Turn Intelligence v1 + latency taps (exit: measured p50 ≤ 1.2 s) → M4 event gateway + webhook outbox + mid-call control + client SDK → M5 memory + prompt runtime → M6 language-moat plugins + evals + hardening + tool runtime. Keep/rewrite/delete ledger as v1 §17.

---

# APPENDIX R — Revision Record (v1 → v2)

## R1. Summary of changes
1. Split into **Part A (timeless constitution)** and **Part B (versioned Implementation Profile)** — the structural fix that makes every other rule enforceable.
2. Added **Glossary (A0)** — one name per concept; unified "Session Worker", "System of Record", "Event Bus", "Provider Plugin", "capability contract", "consumer vs caller" across all sections (v1 mixed "worker/process", "Postgres/DB", "plugin/provider/adapter").
3. Rewrote all provider references into **capability language** (A6); vendors now appear only in Part B and as parenthetical examples.
4. Generalized D2 (was "Pipecat as substrate") and D6 (was "Postgres is the source of truth") into substrate-behind-contracts and single-System-of-Record principles; concrete selections moved to Part B.
5. Added permanent engineering rules (A11): component specification standard (A11.3), interface versioning strategy (A11.4) — both absent in v1.
6. Added explicit **future-proofing contract** (A12): the additive-only extension list, with the amendment trigger.
7. Folder structure relabeled by responsibility; the substrate-confinement rule kept, vendor names removed from the tree.
8. Migration roadmap reclassified as Implementation-Profile execution detail (it names today's vendors by necessity) rather than constitutional content.
9. Latency SLOs, state machines, event vocabulary, memory layers, scalability stages: retained from v1 (already technology-neutral), editorially tightened.

## R2. Removed technology-specific assumptions (from Part A)
Pipecat as a named architectural dependency (D2, component diagram, folder tree) · Postgres/`FOR UPDATE SKIP LOCKED`/Supabase/SQLite as architecture (D6, §8, §16) · Redis as the named Stage-3 bus · FastAPI/SQLModel/Pydantic as architectural elements · Deepgram/Sarvam/Cartesia/Groq/Anthropic/Daily as named pipeline stages (§9.4, §11, M3) · "SmallWebRTC" and WebRTC-only transport phrasing (now: Real-time Transport capability; WebRTC is a profile row) · Oracle A1 as the named deploy target · TypeScript/React as the named SDK stack · loguru/OpenTelemetry as named observability choices · Python 3.12 coding standards (moved to profile-level standards) · "uv-managed" packaging note.

## R3. Generalized architectural principles (new or newly explicit)
Architecture-describes-responsibilities rule (A2.8) · capability contracts with declared flags + write-time validation (A6) · degraded-mode declaration duty (A6.4) · substrate confinement with one-directory blast radius (D2) · single System of Record with stage-gated specialization (D6) · additive-only extension contract with amendment trigger (A12) · component specification standard (A11.3) · universal interface versioning (A11.4) · consumer/caller terminology separation (A0).

## R4. Documentation impact
`INDEX.md` updated (this document indexed; v1 marked superseded) · v1 constitution retained unmodified except a Superseded status banner (version history preserved) · future focused designs (`architecture/event-system.md` etc.) must follow Part A/Part B separation · `docs/runtime/` gains one spec per component as A11.3 is executed (starts at M2) · SPEC.md supersession note (v1 §22) carries over unchanged.

## R5. ADR impact
ADR-0001…0007 (to be extracted at M0) are drafted from **v2's D1–D7 wording**, not v1's — specifically ADR-0002 (substrate, generalized) and ADR-0006 (System of Record, generalized). Two additional lightweight **profile ADRs** are created at M0: P-ADR-0001 "Implementation Profile v1 adoption" (binds Part B) and the rule that profile changes need only profile ADRs. No ratified ADRs exist yet, so no historical ADR is affected.

## R6. Migration impact
Zero change to the M0–M6 sequence, estimates, or exit criteria; the roadmap is reclassified (constitution → profile execution plan). One M0 addition: create the two profile ADRs (R5). Risk to timeline: none.

## R7. Risks introduced by this revision
1. **Abstraction cost:** capability contracts + substrate confinement add indirection a solo developer must maintain; mitigated by keeping contracts minimal (one page each) and letting the contract test suite be the enforcement, not ceremony.
2. **Premature generality:** designing contracts before the second implementation exists risks wrong abstractions; mitigated by extracting contracts *from* the first working plugin at M2/M3, then freezing.
3. **Two-document drift:** Part B could silently diverge from reality; mitigated by the A11 rule that config/dependency changes require the profile table update in the same change-set.
4. **Softer language, weaker urgency:** vendor-neutral prose can hide hard commitments; mitigated by keeping the numeric SLOs (I1, A8) and CI gates fully binding.

## R8. Benefits
The constitution survives replacement of any vendor, framework, database, broker, transport, or cloud without amendment · new contributors can learn the platform from Part A alone · provider negotiations/switches become configuration events, not architecture events · capability flags + write-time validation structurally eliminate the audit's "accepted-but-ignored config" defect class · versioned interfaces + component specs make the 5–10-year maintenance horizon realistic · profile ADRs give fast, low-ceremony evolution where change is cheap, and constitutional ADRs protect where change is expensive.

## R9. Validation performed
Checked every Part-A section for vendor/framework names (none remain outside parenthetical examples explicitly labeled as such) · cross-checked consistency chains: runtime components ↔ folder structure ↔ event vocabulary ↔ state machine ↔ memory ↔ providers ↔ migration ↔ documentation rules (no contradictions found; v1's one inconsistency — migration steps naming vendors inside a "timeless" document — resolved by the Part B split) · confirmed every v1 rule survives either in Part A (generalized) or Part B (profiled), with none dropped · confirmed no new undocumented decisions were introduced (all new rules are in R3 and traceable to the revision mandate).

## R10. Final Architecture Readiness Score

**88 / 100**

| Dimension | Score | Note |
|---|---|---|
| Technology neutrality | 95 | Part A vendor-free; examples clearly labeled |
| Internal consistency | 92 | Single glossary, cross-checked chains |
| Future-proofing | 90 | Additive extension contract + amendment trigger |
| Completeness | 85 | Component specs (A11.3) exist as a standard but individual specs are written during M2+ |
| Enforceability | 82 | CI import-rules, contract suites, and latency gates specified but not yet running (pre-M0) |
| Risk posture | 85 | R7 risks identified with mitigations |

The remaining 12 points are earned by execution, not writing: running CI gates, extracted ADRs, and per-component specs — all scheduled in M0–M3.

---

*Documentation-only revision. No code, runtime logic, or source files were modified. Awaiting owner ratification of v2; on approval, M0 proceeds under Part B.*

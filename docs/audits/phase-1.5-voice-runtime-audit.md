# VoxVoice — Voice Runtime Engineering Audit (Phase 1.5)

| | |
|---|---|
| **Document** | `docs/audits/phase-1.5-voice-runtime-audit.md` |
| **Status** | Final — v1.0 |
| **Audit date** | 2026-07-16 |
| **Auditor** | Claude Code (acting Voice AI Runtime Architect) |
| **Scope** | The voice runtime only: audio pipeline, streaming, VAD, turn detection, interruption, conversation runtime, memory, prompts, response pipeline, tools, events, latency, scalability, Vapi comparison. Code style/docs/structure excluded (covered in Phase 1). |
| **Benchmark** | Production platforms: Vapi, OpenAI Realtime API, Retell, Bland, LiveKit Agents, Deepgram Voice Agent, Pipecat |
| **Verification performed** | Read both runtime surfaces end-to-end; inspected installed Pipecat 1.4.0 package source to confirm STT service class hierarchy (`GroqSTTService → BaseWhisperSTTService → SegmentedSTTService`) and interruption machinery location. No files modified. |
| **Supersedes** | — (extends, does not replace, [phase-1-repository-audit.md](phase-1-repository-audit.md) §12/§13 and the Voice Runtime Maturity score) |
| **Related** | [SPEC](../SPEC.md) §5 (adapter), `platform/adapters/dograh.py`, `platform/voicebot/bot.py`, `platform/api/ingest.py` |

---

## 0. The Central Finding

**VoxVoice does not currently have a voice runtime. It has a control plane over someone else's runtime, plus a disconnected proof-of-concept.**

There are exactly two runtime surfaces in the repository, and neither is owned:

1. **The platform path (production-intended):** all audio, STT, LLM, TTS, VAD, turn-taking, and interruption happen **inside Dograh**. The browser's WebRTC media flows directly to Dograh's bot; VoxVoice never touches a single audio frame. VoxVoice's runtime involvement is: create a workflow, create a run, mint an embed token, then **poll a run report every 5 seconds**. Per the adapter's own comments (`dograh.py`, `TODO(transcript-shape)`), the transcript artifact is only produced **after the call completes** — meaning during a live call the platform has *zero* visibility and *zero* control. It cannot see a transcript item, cannot end the call (`end_session` is a documented no-op), cannot inject context, cannot fire a tool.

2. **The voicebot path (`voicebot/bot.py`, Pipecat 1.4.0):** a real STT→LLM→TTS loop that VoxVoice *could* own, but it is a 135-line local demo: not behind the `EngineAdapter`, not multi-session, not connected to agents/sessions/transcripts, using **batch (non-streaming) STT**, and — per its own README and test history — never validated with working VAD on the dev machine.

Every section below therefore audits three things: what Dograh provides (rented, black-box), what the voicebot provides (owned, prototype), and what VoxVoice-the-platform can observe or control (almost nothing). The Vapi comparison in §15 is the heart of this report.

---

## 1. Audio Pipeline

| Concern | Platform path (Dograh) | Voicebot path (Pipecat) |
|---|---|---|
| Capture | Browser `getUserMedia` inside Dograh's embed widget — invisible to VoxVoice | Browser via SmallWebRTC transport |
| Buffering/queues | Dograh-internal, unobservable | Pipecat frame queues (per-processor asyncio queues) |
| Chunking | Unknown | Pipecat default frame chunking (20 ms-class frames from WebRTC) |
| Sample rate | Unknown | **Forced 16 kHz input** (`audio_in_sample_rate=16000`) — a correct, deliberate fix because Silero VAD only supports 8/16 kHz and WebRTC-native 48 kHz made VAD detect nothing (`bot.py:75-81`) |
| Echo cancellation | Whatever the browser default AEC is; nothing configured | Same — browser AEC only; no server-side AEC |
| Noise suppression | Browser default only | Browser default only; **`min_volume` lowered to 0.05** because default 0.6 discarded all mic input — this is compensating for absent gain control |
| Gain control (AGC) | Browser default | None server-side; the min_volume hack is evidence of the gap |
| Jitter | WebRTC stack (browser ↔ Dograh) | WebRTC stack (SmallWebRTC) |

**Assessment.** The voicebot's audio front-end is browser-grade, not product-grade: it relies entirely on `getUserMedia` defaults for AEC/NS/AGC and has no server-side audio conditioning, no configurable input gain, no per-device calibration, and no telephony-grade (8 kHz) path. The 16 kHz fix and the `min_volume=0.05` change are good field debugging but also proof that the audio chain was fighting the developer: **VAD never actually fired successfully on the dev machine** (voicebot README + project history), so the entire downstream pipeline is untested with real speech. The platform path's audio pipeline is unauditable — it is Dograh's.

**Verdict: not yet suitable as a low-latency production audio pipeline that VoxVoice can stand behind.** It's suitable as a lab bench.

## 2. Streaming Architecture

This is the most consequential technical finding.

- **STT is NOT streaming.** Verified in the installed package: `GroqSTTService` extends `BaseWhisperSTTService` extends `SegmentedSTTService` — Pipecat's *segment* service, which "uses VAD events to detect speech segments and runs speech-to-text only on those segments." The full utterance is buffered until VAD declares end-of-speech (0.6 s of silence), then shipped as one HTTP request to Groq Whisper. Consequences:
  - No partial hypotheses → no early LLM start, no predictive endpointing, no live captions.
  - The turn-latency floor is `stop_secs + full-utterance upload + batch inference` **before the LLM sees a single token**.
  - Long user turns get *worse*: a 20-second utterance uploads 20 seconds of audio post-hoc.
- **LLM is streaming** (GroqLLMService streams tokens). ✅
- **TTS is streaming** (CartesiaTTSService streams audio over websocket; Pipecat feeds it sentence-ish chunks from the LLM stream). ✅
- **Playback is streaming** (WebRTC output track). ✅
- **Platform path:** unknown; Dograh's internals are Pipecat-based and presumably stream, but VoxVoice can neither verify nor tune it. VoxVoice's own "streaming" surface — the transcript — is a **5-second poll of a post-call artifact**, i.e., not streaming at all, arguably not even live.

**Verdict: the pipeline *appears* streaming but has a hard batch stage at its entrance.** Half-duplex: listen fully → transcribe fully → then stream. Real-time platforms (Deepgram Agent, Vapi, Retell) run streaming STT with interim results so LLM/endpointing work can begin while the user is still speaking.

## 3. Voice Activity Detection

- **Voicebot:** Silero VAD (`confidence=0.5, start_secs=0.2, stop_secs=0.6, min_volume=0.05`) at 16 kHz.
  - Speech start: 200 ms of voiced audio above thresholds — reasonable.
  - Speech end: 600 ms of silence — the sole endpointing signal (see §4).
  - Hangover logic: only what `stop_secs` provides; no adaptive hangover, no energy-vs-model disagreement handling.
  - **False negatives are the documented live failure mode:** with defaults, VAD detected *nothing* (48 kHz mismatch, then `min_volume`); post-fix behaviour was never confirmed on the target environment (Linux validation is an open item; there's also a known `numpy<2` pin issue in this stack).
  - False positives: `min_volume=0.05` is very permissive — keyboard clicks, breath, room noise near the mic can now cross the gate; `confidence=0.5` is the model's coin-flip point. Untuned, untested.
- **Platform path:** VAD is Dograh's; VoxVoice stores `min_endpointing_delay`/`max_endpointing_delay` on the Agent **and never transmits them** (confirmed unapplied in Phase 1 audit, M7). The knobs consumers can see are disconnected from the runtime they think they're tuning.

**Verdict: one binary silence gate, parameters chosen to make a broken setup produce *anything*, never validated end-to-end.** No noise-robustness testing, no per-environment adaptation, no confidence telemetry.

## 4. Turn Detection

**There is no turn detection. There is a 600 ms silence timer.**

- Thinking pauses: a user who pauses >0.6 s mid-thought ("my budget is… hmm…") **loses the turn** — the agent will transcribe the fragment and respond to it.
- Natural/sentence pauses: indistinguishable from turn-completion; same failure.
- "Conversation finished": no concept — no semantic end-of-turn model, no prosody analysis, no punctuation/completeness heuristics on the (nonexistent) interim transcript.
- "User still speaking": known only via VAD; a quiet trailing phrase below `min_volume` ends the turn early.
- The stored `min/max_endpointing_delay` fields imply an adaptive endpointing design (short wait when the utterance looks complete, long wait when it looks unfinished) — **the schema of a real turn-taking engine with no engine behind it.**
- Modern comparators run a dedicated end-of-turn model (Pipecat's smart-turn, LiveKit's turn detector, Vapi's endpointing plans) fed by streaming STT partials. Nothing of the sort is wired here; batch STT (§2) makes the best techniques *impossible* until it's replaced.
- Dograh path: unknown quality, unconfigurable from VoxVoice, and unverified because no real call has ever been completed (Phase 1 finding H3).

**Verdict: the single biggest conversational-quality gap. Fixed-silence endpointing is the #1 reason voice agents feel robotic and interruptive.**

## 5. Interruptions (Barge-in)

- **Voicebot:** Pipecat 1.4 handles interruption in the input transport: VAD `UserStartedSpeaking` while the bot is speaking triggers an interruption (cancel LLM/TTS generation, flush queued audio), enabled by default; `allow_interrupt` flags are also set in the Dograh node graph on the platform path. So the *mechanism* exists in both engines. However:
  - It is **purely VAD-triggered**: a cough, "hmm", or backchannel ("haan", "right") kills the agent's sentence. No interruption *strategies* are configured (e.g., minimum-words-before-interrupt, confidence gating) even though Pipecat 1.4 supports them.
  - **Client-side playback flush is unverified** — cancellation stops generation server-side, but whatever audio is already buffered in the WebRTC playout path determines perceived interruption latency; never measured, because no working call.
  - Because local VAD never fired, barge-in has **never once been observed working** in this codebase.
- **Platform path:** `allow_interruptions` is the *only* runtime parameter VoxVoice actually forwards to Dograh. Interruption latency, grace periods, false-barge-in filtering: all Dograh's, all unmeasured.
- Response cancellation accounting (what the agent *actually said* vs. what it generated) — needed for accurate transcripts and context — is not handled anywhere VoxVoice can see; interrupted-utterance truncation in the conversation context is an unexamined correctness risk.

**Verdict: mechanism present (rented), policy absent, latency unmeasured, zero successful live validations.**

## 6. Conversation Runtime (Orchestrator / State Machine / Lifecycle)

- **VoxVoice-owned orchestration:** none. The platform's "conversation lifecycle" is two DB statuses (`created→active→ended/failed`) set at HTTP-request time. `active` doesn't mean audio is flowing (the user may never click the widget); `ended` doesn't mean the call ended (caller hang-up never updates status — Phase 1 M9). The platform's state machine and the actual call state are **uncorrelated**.
- **Dograh path:** conversation structure is the 4-node workflow graph the adapter generates (`global → startCall → agentNode → endCall`) with LLM-evaluated edge conditions ("The caller has stated their need"). That makes conversation *progression* dependent on an LLM judging free-text conditions — **non-deterministic by construction**, and VoxVoice has no visibility into which node is active.
- **Voicebot path:** no states at all — one unconditioned loop (greet → converse forever until disconnect). No timeout handling (silent user forever = session forever), no max-duration, no re-engagement prompts, no goodbye detection, no failure states (STT provider down mid-call = undefined behaviour).
- Event flow: Pipecat's internal frame flow is sound (typed frames through a DAG), but nothing maps it to a platform-level conversation lifecycle.

**Verdict: conversation behaviour is not deterministic, not observable, and not owned.** No orchestrator exists to hold policy (timeouts, retries, escalation, handoff).

## 7. Memory

- **Working/conversation memory (voicebot):** a single in-process `LLMContext` (system prompt + append-only message list). It is:
  - **Unbounded** — a 45-minute call grows the prompt until provider context limits or cost kill it; no windowing, no summarization, no token budget.
  - **Volatile** — a WebRTC reconnect (common on mobile) starts an empty context; the agent forgets everything mid-conversation.
  - **Disconnected** — it never syncs with the platform's `Transcript` rows; the platform's stored transcript and the agent's lived context are separate universes.
- **Entity memory:** none (names, numbers, addresses mentioned by the caller are not extracted or pinned — critical for the interview/sales use cases VoxVoice targets).
- **Cross-session memory:** none (a returning caller is a stranger).
- **Session memory (platform):** `Session.meta` JSON is stored but never injected into the conversation — a consumer passing `{"candidate": "123", "role": "SDE-2"}` today changes *nothing* the agent knows (context injection gap, see §8).
- **Dograh path:** whatever Dograh does internally; unobservable, unconfigurable.

**Verdict: insufficient for long conversations by design, and the platform's context-passing promise (SPEC: "VoxHire passes interview context via metadata") is currently a stored-but-inert field.**

## 8. Prompt Runtime

- Prompt generation is **static assembly at session start**: voicebot uses one env-var system prompt; the Dograh path bakes `system_prompt` into the workflow's global node **at workflow creation time** — and since workflows are created once per agent and reused (`engine_workflow_id`), **editing an agent's `system_prompt` via `PATCH /v1/agents` never reaches the engine again**. This is a live correctness bug at the prompt layer: the API accepts prompt updates that silently don't apply.
- Session `metadata` is not templated into the prompt (no variable substitution like Vapi's `{{customer.name}}`).
- No dynamic mid-call prompt updates, no state-dependent prompt sections, no retrieved-knowledge injection (KB is Phase 3), no tool-result injection (no tools), no language/persona switching mid-call.

**Verdict: prompts do not evolve during conversation, and on the primary path they don't even evolve between conversations after the first one.**

## 9. Response Pipeline

- LLM output → speech: voicebot relies on Pipecat's default sentence aggregation feeding Cartesia streaming TTS — reasonable defaults, nothing tuned: no speech-oriented output validation (markdown/emoji/list stripping is requested *in the prompt only*), no number/date normalization for TTS ("₹50,000" → "fifty thousand rupees"), no SSML/prosody control, no length governor beyond prompt pleading, no profanity/safety filter on the way out.
- Playback queue exists (transport output) but there is no **playback scheduler** in the product sense: no filler/acknowledgment audio during long LLM or tool latency ("let me check that…"), no pre-cached greeting audio, no crossfade on interruption.
- Platform path: response shaping is delegated entirely to Dograh node prompts; VoxVoice adds nothing.

**Verdict: functional-by-framework-default, optimized-by-nobody.**

## 10. Tool Execution

**Nothing exists.** The `tools` JSON column, the `tool.called` webhook name, and SPEC Phase 3 are the entire tool story. There is no scheduling, no parallelism, no blocking analysis, no retry strategy, no failure handling — because there is no execution path. Note the runtime implication now, before it's built: consumer-defined tools mean a **network round-trip to the consumer's server mid-turn**; without an async tool executor plus speak-while-waiting behaviour (§9), every tool call will insert seconds of dead air. This must be designed into the runtime, not bolted onto the API.

## 11. Event System

Required vocabulary vs. reality:

| Event | Dograh path | Voicebot | Exposed to consumers? |
|---|---|---|---|
| SpeechStarted / SpeechEnded | internal to Dograh | Pipecat frames (logged by `DebugLogger` only) | ❌ |
| ThinkingStarted / Finished | ✗ observable | derivable from frames, not derived | ❌ |
| SpeakingStarted / Finished | ✗ observable | Pipecat `BotStarted/StoppedSpeaking` frames, unused | ❌ |
| Interrupted | ✗ observable | frame exists, unused | ❌ |
| ToolExecuted | n/a | n/a | ❌ (name reserved) |
| Timeout | doesn't exist anywhere | doesn't exist | ❌ |
| transcript.item | post-call, 5 s poll granularity | not connected | ⚠ webhook, not live |
| session.started / ended | HTTP-request-time, not call-time | n/a | ⚠ misleading semantics |

The SPEC's own SDK design promises `call.on("state", s => "listening"|"thinking"|"speaking")` — **there is no source anywhere in the system that could feed that callback today.** No WebSocket/SSE event channel exists on the platform; webhooks are fire-once with no retry (Phase 1 finding).

**Verdict: the runtime is not event-driven at the platform level; the promised SDK contract is currently unimplementable.**

## 12. Runtime Latency Analysis

No latency has ever been measured in this project (no working call). Engineering estimates for the **voicebot path** as configured, per conversational turn:

| Stage | Estimate | Source of latency |
|---|---|---|
| VAD endpoint wait | **600 ms fixed** | `stop_secs=0.6` — pure dead time on every turn |
| Utterance upload + Groq Whisper batch | **300–900 ms** (scales with utterance length) | Segmented STT (§2): nothing starts until the whole clip is transcribed |
| LLM TTFT (Groq Llama-3.3-70B) | 150–350 ms | fast inference, but starts cold — no pre-speculation during user speech |
| Sentence aggregation before TTS | 100–300 ms | first sentence must complete |
| Cartesia TTS TTFB | 100–200 ms | websocket streaming |
| WebRTC playout buffer | 40–120 ms | jitter buffer |
| **Total (voice-to-first-voice)** | **≈ 1.3 – 2.5 s typical** | |

Comparators: Vapi advertises/achieves ~700–1050 ms; OpenAI Realtime and speech-to-speech stacks reach 300–600 ms; a well-tuned Pipecat pipeline with *streaming* STT (Deepgram) lands 800–1100 ms. **VoxVoice's owned pipeline is roughly 2× too slow before network variance, and its structure (batch STT + fixed endpoint wait) caps improvement — you cannot tune your way out; you must re-architect the STT stage.**

Platform-path latency: unknown and unmeasurable from VoxVoice (no timestamps, no metrics, no events). Additional platform-added latencies that *are* measurable: transcript availability lag = call duration + poll interval (up to end-of-call + 5 s); session-start adds 3 sequential Dograh HTTP round-trips (workflow ensure → run create → embed token), each on a fresh TCP+TLS connection (no client pooling, Phase 1 M6).

Scheduling/context-building latencies are negligible today only because the runtime does no scheduling and builds no context.

## 13. Voice Quality Assessment

| Property | Verdict | Why |
|---|---|---|
| Natural | ❌ unknown/unlikely | Never heard; fixed 600 ms endpointing + batch STT guarantees sluggish turn exchange |
| Human | ❌ | No backchannels, no fillers, no prosody control, no thinking-pause tolerance |
| Interruptible | ⚠ mechanically yes, behaviourally untested | VAD barge-in exists (rented); zero policy; zero validation |
| Fast | ❌ | ~1.3–2.5 s turns (§12) vs. sub-second competition |
| Responsive | ❌ | No acknowledgment audio during waits; dead air on any slow stage |
| Context-aware | ❌ | Session metadata never reaches the agent; prompt updates don't propagate; no entity memory |
| Predictive | ❌ | Nothing anticipates end-of-turn or pre-computes; architecture (batch STT) forbids it |

## 14. Scalability of the Runtime

- **Concurrent sessions:** platform API can *create* many sessions (modulo Phase 1's single-process constraints); the **voicebot cannot run them** — it's a single-pipeline dev runner with no session multiplexing, no worker pool, no per-session resource isolation, no autoscaling story. Dograh's concurrency ceiling is unknown and untested (one small VM).
- **Multiple providers:** the *schema* is ready (`stt_provider/tts_provider/llm_model`); the *runtime* honours none of it on either path. There is no provider abstraction layer VoxVoice owns — Pipecat *is* that abstraction and it's not integrated.
- **Multiple agents:** ✅ platform-side yes; voicebot ignores agents entirely (one env prompt).
- **Provider switching mid-call / failover:** nonexistent (Vapi does automatic provider fallback).
- **Plugin architecture:** none beyond the engine adapter seam.
- **Future multimodal:** WebRTC transports make video *possible*; nothing in the design accounts for it.

## 15. Comparison Against Vapi (and peers)

| Capability | Vapi / peers | VoxVoice today | Gap severity |
|---|---|---|---|
| Streaming STT with partials | ✅ (Deepgram et al.) | ❌ batch Whisper per utterance | **Critical** |
| Adaptive/semantic turn detection | ✅ (endpointing plans, smart-turn models) | ❌ fixed 600 ms silence | **Critical** |
| Interrupt engine w/ policies (min-words, grace, filler-tolerance) | ✅ | ⚠ raw VAD barge-in, rented, untested | High |
| Sub-second voice-to-voice latency + per-stage metrics | ✅ (~700–1050 ms, dashboards) | ❌ ~1.3–2.5 s est., zero metrics | **Critical** |
| Live call events (WebSocket/server events: speech, state, transcript deltas) | ✅ | ❌ post-call 5 s polling | **Critical** |
| Mid-call control API (say/inject/mute/transfer/end) | ✅ | ❌ none — can't even end a call | **Critical** |
| Conversation state machine + timeouts (silence timeout, max duration, idle re-prompt) | ✅ | ❌ | High |
| Memory engine (windowing, summarization, persistence across reconnect) | ✅ | ❌ unbounded in-process list | High |
| Dynamic prompts + variable templating ({{metadata}}) | ✅ | ❌ static; updates silently don't propagate to engine | High |
| Tool/function calling (async, streaming results, webhooks) | ✅ | ❌ schema only | High (Phase 3 planned) |
| Provider abstraction + per-agent provider config honoured | ✅ (50+ combos) | ❌ stored, ignored | High |
| Provider failover mid-call | ✅ | ❌ | Medium |
| Telephony (in/out, transfer, DTMF, voicemail detection) | ✅ | ❌ (Phase 3 planned) | Medium (roadmapped) |
| Response validator / speech-formatting layer | ✅ | ❌ prompt-pleading only | Medium |
| Playback scheduler (fillers, ack audio, pre-cache) | ✅ | ❌ | Medium |
| Background noise/denoise options | ✅ (e.g., Krisp-class) | ❌ browser defaults | Medium |
| Call recordings + artifacts | ✅ | ⚠ Dograh produces `recording_url`; VoxVoice ignores it | Low (easy win) |
| Analytics/metering | ✅ | ❌ (Phase 3 planned) | Medium |
| Squads/multi-agent handoff | ✅ | ❌ (Phase 3 planned) | Low for now |
| Latency optimizer (geo routing, warm pools) | ✅ | ❌ | Low for now |

**Honest framing:** Vapi is ~4 years of runtime engineering. VoxVoice's *strategic* answer — rent Dograh's runtime — is legitimate, but the audit shows the rented runtime is currently rented **blind**: no events, no control, no metrics, no verified call. Either the Dograh integration must be deepened until those exist (if Dograh's API even allows it — unverified), or VoxVoice must own a first-party runtime (the voicebot's Pipecat lineage) where all of the above become buildable. The current position — a control plane with neither visibility nor a first-party engine — is the worst of both.

---

# Final Report Artifacts

## F1. Runtime Architecture Review
See §0–§11. Summary: control-plane-only platform; black-box rented runtime (Dograh) with post-hoc polling as the sole feedback channel; a promising but disconnected Pipecat prototype with a batch-STT bottleneck; no VoxVoice-owned orchestrator, event bus, memory engine, or metrics anywhere.

## F2. Runtime Flow Diagrams (current reality)

```
PLATFORM PATH (production-intended)
Browser ──WebRTC media──────────────► Dograh bot (STT→LLM→TTS, VAD, barge-in)   [black box]
Browser ◄─WebRTC audio───────────────┘
   │ embed widget bootstrap
   ▼
VoxVoice API ──create workflow/run/embed-token──► Dograh REST
VoxVoice API ◄─run report (poll, 5 s)────────────┘   ← transcript exists only AFTER call ends
   │
   └─► DB (Session/Transcript) ─► webhooks (fire-once)
   ✗ no live events   ✗ no mid-call control   ✗ no metrics   ✗ hangup detection → status

VOICEBOT PATH (prototype, disconnected from platform)
Browser mic ─WebRTC─► Silero VAD ─(0.6 s silence)─► [BUFFER FULL UTTERANCE]
  ─HTTP batch─► Groq Whisper ─► LLMContext (unbounded) ─► Groq LLM (stream)
  ─sentences─► Cartesia TTS (stream) ─► WebRTC playout
  ↑ VAD-triggered barge-in (Pipecat default)      ✗ no sessions ✗ no agents ✗ no events out
```

## F3. Missing Runtime Components
Conversation orchestrator/state machine · live event bus (WS/SSE + reliable webhooks) · mid-call control API · streaming STT stage · turn-detection engine (adaptive endpointing + semantic EOT) · interruption policy engine · memory engine (window/summarize/persist/entities) · prompt runtime (templating, propagation, mid-call updates) · response validator & speech formatter · playback/filler scheduler · tool executor · provider abstraction honouring per-agent config · latency metrics pipeline · session multiplexer/worker pool · recording ingestion · timeout/failure policies.

## F4. Voice Quality Analysis
See §13. Nothing about current VoxVoice produces a conversation that feels natural, fast, or context-aware; the honest statement is that **no conversation has ever been completed**, so quality is a prediction: the architecture predicts sluggish (≥1.3 s turns), interruptive-on-pauses (600 ms cutoff), amnesiac-on-reconnect, and context-blind (inert metadata).

## F5. Latency Analysis
See §12. Structural floor ≈ 1.15 s (600 ms endpoint + ~300 ms batch STT + ~250 ms first speech) before network; typical 1.3–2.5 s. The two structural fixes are streaming STT (removes the batch stage, enables overlap) and adaptive endpointing (cuts the fixed 600 ms when the turn is semantically complete). Everything else is tuning.

## F6. Conversation Behaviour Analysis
See §4–§8. Deterministic? No — platform lifecycle is decoupled from call reality; Dograh progression rides LLM-judged edge conditions; the voicebot has no states, no timeouts, no failure behaviour. Predictable failure cases today: mid-thought pauses stolen; reconnect amnesia; prompt edits that never apply; sessions immortal after hangup.

## F7. Runtime Bottlenecks (ranked)
1. Batch STT stage (structural).
2. Fixed 600 ms endpointing (structural).
3. Transcript-after-call-only + 5 s polling (visibility).
4. Three serial unpooled HTTP calls at session start (setup latency).
5. Unbounded context growth → rising LLM latency over call duration.
6. Single-pipeline voicebot runner (concurrency = 1).

## F8. Critical Engineering Gaps
(a) No real call ever completed — the whole runtime is unvalidated theory; (b) zero live visibility or control over the production path; (c) the owned prototype's STT architecture forbids competitive latency; (d) no turn-detection beyond a silence timer; (e) config the API accepts (endpointing, providers, prompt updates) silently does nothing — a trust-destroying class of bug for a platform product.

## F9. Components Missing vs. Vapi
See table §15.

## F10. Runtime Maturity Score

**22 / 100** (refines Phase 1's coarse 34/100 with runtime-only depth)

| Sub-dimension | Score | Note |
|---|---|---|
| Audio pipeline | 30 | Rented/browser-default; owned path bench-only |
| Streaming architecture | 35 | LLM/TTS stream; STT batch; platform surface polls |
| VAD | 25 | Present, mistuned-by-necessity, unvalidated |
| Turn detection | 5 | Silence timer only |
| Interruption | 25 | Rented mechanism, no policy, untested |
| Conversation runtime | 10 | No orchestrator/state machine/timeouts |
| Memory | 10 | Unbounded volatile list |
| Prompt runtime | 10 | Static; updates don't propagate |
| Response pipeline | 30 | Framework defaults |
| Tools | 0 | Absent |
| Events | 5 | Post-hoc webhooks only |
| Observability/latency | 0 | Nothing measured, nothing measurable |

## F11. Top 20 Improvements Required (priority order)
1. Complete one real spoken call (Oracle deploy) — validates or falsifies everything below.
2. Decide the strategic fork: deepen Dograh (verify what events/control its API can expose) **or** promote a first-party Pipecat engine. (ADR required.)
3. Replace batch STT with streaming STT (Deepgram now; Sarvam streaming for Hinglish when available) — partials + interim results.
4. Build the turn-detection engine: adaptive endpointing driven by `min/max_endpointing_delay` + semantic end-of-turn (smart-turn-class model) on STT partials.
5. Stand up a session-scoped live event bus (WS/SSE) with the §11 vocabulary; make the SDK's promised `state` callback real.
6. Mid-call control API: end/say/inject-context/mute/transfer.
7. Latency instrumentation: per-stage timestamps on every turn, percentile dashboards; make sub-1.1 s p50 an explicit SLO.
8. Memory engine v1: token-budgeted window + rolling summary; persist context keyed to Session; survive reconnect.
9. Prompt runtime v1: metadata templating into system prompt at session start; fix prompt-update propagation (per-session workflow config or engine update call).
10. Interruption policy layer: min-words/backchannel tolerance, measured cancel-to-silence latency, truncated-utterance context correction.
11. Conversation state machine with timeout policies (silence, max duration, idle re-prompt, goodbye detection).
12. Honour per-agent provider config end-to-end or reject it at the API (kill the silent-no-op class).
13. Tool executor design (async, timeout, retry, filler speech during waits) — before Phase 3 code.
14. Session multiplexing worker for the first-party engine (N pipelines per process, process pool).
15. Playback scheduler: acknowledgment fillers on >700 ms waits; pre-cached greeting audio.
16. Response validator: speech formatting, number/date normalization, length governor.
17. Reliable webhook delivery (retry + DLQ) and recording URL ingestion onto Session.
18. Hangup → session status reconciliation (poller or engine event).
19. Server-side audio conditioning options (denoise/AGC) and telephony sample-rate path.
20. Load test: 50 concurrent sessions with latency SLO held.

## F12. Recommended Runtime Architecture (target)

```
Consumers ── REST (control) + WS/SSE (events) + SDK
                    │
        VoxVoice Conversation Orchestrator        ← owns state machine, timeouts,
          │ events │ control │ metrics              policies, memory, prompts, tools
                    │ EngineAdapter (unchanged seam)
   ┌────────────────┴───────────────┐
   │ First-party Pipecat engine     │ DograhAdapter (kept as alt/fallback)
   │  transport: WebRTC/telephony   │
   │  streaming STT (partials) ─► turn-detection engine (adaptive+semantic)
   │  LLM stream ─► response validator ─► TTS stream ─► playback scheduler
   │  interruption policy engine · tool executor · per-stage latency taps
   └────────────────────────────────┘
        Postgres: sessions, transcripts (live), context snapshots, metrics
```

The Phase-1 adapter seam is the correct foundation — this architecture *fills* it rather than replacing it. "Own the interface, rent the engine" graduates to "own the interface **and the reference engine**, rent alternatives."

## F13. Recommended Development Order
1. F11-1/2 (validate + ADR on the fork) — days.
2. Promote voicebot → `PipecatAdapter` behind the existing ABC, multi-agent, platform-connected (transcripts live via in-process events — instantly better than polling) — this makes every later item buildable in-house.
3. Streaming STT + turn-detection engine (the latency/naturalness core).
4. Event bus + mid-call control + latency metrics (the platform-product core).
5. Memory + prompt runtime (the context core).
6. Interruption policies, state machine, response/playback polish.
7. Tools, telephony, concurrency hardening, failover (Phase 3 alignment).

## F14. Estimated Engineering Complexity (solo dev, focused)

| Component | Complexity | Estimate |
|---|---|---|
| One real call via Oracle runbook | Low | 0.5–1 day |
| PipecatAdapter (engine-behind-ABC, sessions/agents/transcripts wired) | Medium | 4–7 days |
| Streaming STT swap (Deepgram) | Low-Med | 1–2 days |
| Turn-detection engine (adaptive endpointing + smart-turn model) | **High** | 1–2 weeks, ongoing tuning |
| Live event bus + SDK state events | Medium | 3–5 days |
| Mid-call control API | Medium | 2–4 days |
| Latency instrumentation | Low-Med | 2–3 days |
| Memory engine v1 (window+summary+persist) | Medium | 4–6 days |
| Prompt runtime v1 (templating + propagation fix) | Low-Med | 2–3 days |
| Interruption policy layer | Medium-High | 4–7 days + tuning |
| Conversation state machine + timeouts | Medium | 3–5 days |
| Tool executor | Medium-High | 1–1.5 weeks |
| Session multiplexing/worker pool | High | 1–2 weeks |
| Provider abstraction honoured E2E | Medium | 3–5 days per provider pair |
| Telephony | High | 2+ weeks (or rent via Dograh path) |
| Reliable webhooks + recordings | Low-Med | 2–3 days |
| Load/concurrency hardening | Medium-High | 1 week + infra |

Rough total to credible Vapi-comparable core (items 1–11): **8–12 focused weeks solo**. The Dograh-deepening alternative is cheaper *if and only if* Dograh's API exposes live events and mid-call control — verifying that (F11-2) should happen before committing either way.

---

*End of Phase 1.5 audit. Analysis only; no code was modified. The strategic fork in F11-2 requires an owner decision and an ADR before any implementation.*

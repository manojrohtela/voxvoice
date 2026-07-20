# Reasoning Runtime (Epic 5, M5.5) — Design

| | |
|---|---|
| **Scope** | The provider-neutral, deterministic, event-driven runtime that **owns inference orchestration only** · **Status:** 🟢 Core implemented (M5.5B-1); intelligence M5.5B-2 |
| **Milestone** | M5.5A — Design (approved) · M5.5B-1 — Core (2026-07-20, additive) · M5.5B-2 — Intelligence (planned) |
| **Basis** | The frozen M5.0 [AI Runtime Foundation](ai-runtime-foundation.md) (`core.ports.ReasoningPort`, `core.ports.ToolPort`); the closed [Memory](memory-runtime.md)/[Knowledge](knowledge-runtime.md)/[Context](context-runtime.md)/[Prompt](prompt-runtime.md) Runtime patterns |
| **Governs** | `platform/reasoning/` |

> **Design-only milestone.** No implementation, no runtime code. This is the approved
> architecture the M5.5B milestones build to. It redesigns **nothing** already closed
> (Foundation, Memory, Knowledge, Context, Prompt, Ports, Repositories, Events); where a
> closed runtime seems insufficient it is classified as an *additive extension* or a *bug*.

---

## 1. Executive Summary
The **Reasoning Runtime** takes a finished prompt (as a neutral `InferenceRequest` projected
from a `PromptPackage`) and **orchestrates the model-call loop** — provider selection,
streaming, tool-decision coordination, retry, cancellation, and response assembly — into
**one immutable, self-sufficient `ResponsePackage`** the future Agent/Workflow runtimes
consume without re-running inference. It owns **inference orchestration and nothing else**:
it never stores memory, owns knowledge, assembles context, constructs prompts, holds
templates, talks to a provider SDK, opens a socket, executes a tool, or persists durably.
The **intelligence lives in the model behind an injected `ModelProvider` port**, not in the
runtime; tools execute behind the injected `core.ports.ToolPort`; every input arrives as a
neutral, immutable object. So Reasoning imports no sibling runtime and stays acyclic
(`reasoning → core, tenant`). It implements the **frozen** `core.ports.ReasoningPort` and
reuses the closed-runtime shape: a `BusConsumer`/`RuntimeModule`, a repository over the store
contracts (**temporary** working-set storage only), `core.policies`-based deterministic
retry/selection, injected `ModelProvider`/`ToolPort` ports for anything vendor- or
network-specific, and `reasoning.*`/`model.*`/`tool.*` standard envelopes Replay records and
the Gateway streams for free. After M5.5B it will be **architecturally closed** except
additive extensions and bug fixes.

## 2. Reasoning Runtime Vision
One loop, one contract, one package.

- **The clean input boundary.** Reasoning consumes a neutral **`InferenceRequest`** — the
  rendered messages + generation parameters + provider/model refs + prompt hash/fingerprint —
  never the `prompt` `PromptPackage` (which would couple sibling runtimes). The
  Prompt→Reasoning handoff is a **composition-root bridge** implementing Prompt's
  `PromptConsumer`, so neither runtime imports the other.
- **The "reason once" guarantee.** The `ResponsePackage` carries the assembled response +
  usage + finish reason + tool/retry/streaming history + full reasoning lineage, so **no
  downstream runtime re-runs inference** — Agent/Workflow read the package.
- **Intelligence behind a port.** The model is an injected `ModelProvider` (streaming
  inference); tool execution is the injected `ToolPort`. Reasoning **coordinates**; it never
  knows an SDK, an HTTP client, a transport, or a tool implementation. A new provider is a
  new adapter — never a Reasoning change.
- **Deterministic orchestration.** The *orchestration* is byte-for-byte reproducible (content
  hash ids, injected clock, deterministic retry/backoff, stable tie-breaks). The model is the
  non-deterministic seam — injected + replayable; Replay records the produced
  `ResponsePackage`, not a provider call.

## 3. Component Architecture
All new, inside the planned `platform/reasoning/` package. Grouped **core** (M5.5B-1) vs
**intelligence** (M5.5B-2), mirroring Memory/Knowledge/Context/Prompt.

```
reasoning/
  domain.py          InferenceRequest*/ResponsePackage*/ResponseChunk/ToolDecision/
                     RetryRecord/CancellationRecord/ReasoningLineage (immutable)
  ids.py             content-hash ids (request hash, response hash)
  lifecycle.py       core.fsm session lifecycle: REQUESTED→STARTED→STREAMING⇄TOOL_WAIT→
                     COMPLETED / FAILED / CANCELLED / EXPIRED
  ports.py           runtime-internal seams: ModelProvider, CancellationToken
  session.py         ReasoningSession + ReasoningSessionManager (temporary working set)
  engine.py          InferenceEngine — the model-call loop orchestrator
  streaming.py       StreamingController (chunk lifecycle / partial→final / backpressure)  [I]
  tools.py           ToolDecisionCoordinator (over the injected ToolPort)                  [I]
  retry.py           RetryController (backoff / budget / circuit-breaker hook)             [I]
  cancellation.py    CancellationManager + tokens                                          [I]
  scheduler.py       ReasoningScheduler (deterministic, caller-driven)                     [I]
  assembler.py       ResponseAssembler (chunks + tool/retry → ResponsePackage)
  policies.py        ReasoningPolicyEngine + policy value objects → verdicts               [I]
  tenancy.py         ReasoningTenancy (org/session isolation; provider allow-list)          [I]
  repositories/      ReasoningRepository (TEMPORARY response/session store) — sole store-toucher
  stores/            in-memory Document/Metadata adapters + a deterministic EchoProvider (dev)
  extensions.py      interfaces only: ModelProvider/ResponseConsumer/ResponseOptimizer/
                     ReasoningOptimizer/MultiModalResponse/CircuitBreaker                   [I]
  observability.py   ReasoningStatistics + Metric names + per-stage stats
  events.py          ReasoningPhase (phase discriminator on reasoning.*/model.* envelopes)
  config.py          ReasoningConfiguration (timeouts, retry, streaming, collections)
  factory.py         ReasoningFactory (composition root; injects the provider/tool ports)
  runtime.py         ReasoningRuntime(BusConsumer, ReasoningPort)
  _run.py            run_coro_sync (sync↔async bridge, local adapters only)
```
`[I]` = intelligence (M5.5B-2). The **runtime depends only on injected ports**
(`ModelProvider`, `core.ports.ToolPort`) and the neutral `InferenceRequest` — never on
`prompt`, `context`, `memory`, `knowledge`, a provider SDK, or a transport.

## 4. Ownership Matrix
| Concern | Owner | Not owned by |
|---|---|---|
| Inference orchestration | **ReasoningRuntime / InferenceEngine** | — |
| Provider selection | **ProviderRegistry** (in the runtime) | — |
| Streaming lifecycle | **StreamingController** | — |
| Tool-call coordination | **ToolDecisionCoordinator** (over `ToolPort`) | Reasoning (never executes) |
| Retry / backoff | **RetryController** | Reasoning (never networks) |
| Cancellation | **CancellationManager** | — |
| Response assembly | **ResponseAssembler** | — |
| Temporary response/session storage | **ReasoningRepository** | Memory (durable is Memory's) |
| Tenant isolation | **ReasoningTenancy** | — |
| **Model inference** | injected **`ModelProvider`** (the model) | Reasoning (coordinates only) |
| **Tool execution** | injected **`core.ports.ToolPort`** (Tool Runtime) | Reasoning (coordinates only) |
| **Prompt / messages** | neutral **`InferenceRequest`** (from Prompt via bridge) | Reasoning (consumes only) |
| Final immutable package | **ResponsePackage** | downstream (read-only) |

**Reasoning Runtime never owns:** Memory · Knowledge · Context · Prompt construction ·
Templates · Providers · Provider SDKs · LLMs · Transport · Networking · Tools · Workflows ·
Agents · (durable) Persistence. It *coordinates the model-call loop and hands off*.

## 5. Reasoning Pipeline
Async-first (the Memory M5.1C decision); the sync `ReasoningPort.run` iterator drives it via
the non-blocking bridge (local adapters only; real streaming providers use the async API).
Every stage is deterministic **given the injected provider**; retrieval/prompting/execution
are delegated.

| # | Stage | Owner | Behaviour |
|---|---|---|---|
| 0 | **Input** | ReasoningRuntime | Validate the neutral `InferenceRequest`; **tenancy guard first** (isolation + provider allow-list before any model call). |
| 1 | **InferenceRequest** | (built by the composition-root `PromptConsumer` bridge) | The self-sufficient neutral request (messages + params + provider/model + prompt hash/fingerprint + policies + cancellation token). |
| 2 | **Provider Selection** | ProviderRegistry | Resolve the injected `ModelProvider` by `request.provider` (deterministic; unknown → `ConfigError`). |
| 3 | **Streaming Session** | StreamingController | Open a stream via the provider; consume `ResponseChunk`s; emit `model.token`; apply backpressure/timeout. |
| 4 | **Tool Decision** | ToolDecisionCoordinator | On a `TOOL_CALL` decision: policy-approve → `ToolPort.invoke` → feed the `ToolResult` back into the loop; repeat until `FINAL`. |
| 5 | **Retry** | RetryController | On a retryable provider/tool failure: deterministic backoff within the retry budget (circuit-breaker hook); re-invoke. |
| 6 | **Cancellation** | CancellationManager | Check the cancellation token at each step (manual/timeout/provider/tool/session/streaming) → terminate cleanly. |
| 7 | **Response Assembly** | ResponseAssembler | Merge chunks + tool decisions + retry/cancel records → the immutable `ResponsePackage` (+ lineage + usage + finish reason). |
| 8 | **Response Package** | ReasoningRuntime | The self-sufficient artifact. |
| 9 | **Delivery** | ReasoningRuntime | Persist to the **temporary** repository; emit `reasoning.completed`/`model.usage`; hand to Agent/Workflow via the `ResponseConsumer` seam (composition root). |

A terminal transition (COMPLETED / FAILED / CANCELLED) is always reached; the FSM guarantees
no dangling session.

## 6. InferenceRequest Design
`InferenceRequest` (frozen, slotted, deterministic) — the self-sufficient neutral input.

- **Prompt projection:** `messages` (the provider-neutral `PromptMessage`-shaped tuples from
  the `PromptPackage`), `generation` (the carried `GenerationParameters`), `provider`,
  `model`, `prompt_hash`, `prompt_fingerprint`.
- **Control:** `timeout_s`, `retry_policy`, `streaming` (mode), `tool_policy`,
  `cancellation_token` (id), `request_metadata`, `conversation_metadata`, `runtime_metadata`.
- **Identity:** `request_hash` (content hash over messages+params+provider+model),
  `version`, `session_id`, `scope`.
- **Self-sufficient:** everything the loop needs is on the request; Reasoning never re-queries
  Prompt/Context/Memory/Knowledge. Built by the composition-root bridge from a `PromptPackage`
  (Reasoning does not import `prompt`).

## 7. ResponsePackage Design
`ResponsePackage` (frozen, slotted, deterministic) — the reason-once artifact.

- **Content:** the assembled `text`/`messages` (final), streamed `chunks` (optional, for
  replay), `tool_decisions` (each: call id, name, arguments, result ref, timing).
- **Metadata:** `provider_metadata`, `model_metadata`, `completion_metadata`, `token_usage`
  (prompt/completion/total), `finish_reason`, `streaming_metadata` (chunk count, first-token
  latency), `retry_metadata` (attempts + backoffs), `cancellation_metadata`, `timing`
  (start/end, per-stage), `quality_metadata` (hooks; empty by default).
- **Identity + lineage:** `response_hash`, `response_version`, and **`ReasoningLineage`** —
  `prompt_hash`, `prompt_fingerprint`, `inference_request_hash`, `provider`, `model`,
  `retry_history`, `tool_history`, `streaming_metadata`, `transformation`. So **no downstream
  runtime loses reasoning history**.
- **Self-sufficient:** Agent/Workflow read the package (+ token usage + finish reason) and
  proceed; they never re-run inference.

## 8. Streaming Architecture
`StreamingController` owns **orchestration only**:
- **Chunk lifecycle** — `ResponseChunk` (index, delta text / tool-call delta, done flag);
  each chunk emits `model.token`.
- **Partial → final** — accumulates deltas; `ResponseAssembler` produces the final.
- **Backpressure** — a bounded in-process buffer; a slow consumer applies deterministic
  drop/pause policy (no unbounded growth). The provider is pulled, never pushed-to-block.
- **Cancellation / timeout** — the controller checks the cancellation token + deadline between
  chunks; on trip it stops pulling and records a cancellation.
- **Replay / Gateway** — every chunk is a standard `model.token` envelope; a non-streaming
  request collapses to a single final chunk. Streaming is a *mode*, not a fork.

## 9. Provider Abstraction
Provider-neutral **ports only**. The runtime defines an injected **`ModelProvider`** seam
(streaming inference): given an `InferenceRequest`, it yields `ResponseChunk`s /
`Decision`s. **Reasoning never knows an SDK, HTTP client, or transport** — a provider adapter
(Anthropic/OpenAI/Gemini/local) implements `ModelProvider` outside this package and is a
**Non-goal** here (no SDKs, no HTTP, no transport). A deterministic **`EchoProvider`** ships
as the dev/test default (streams a canned response from the request — no model, fully
deterministic), so the runtime is testable end-to-end without a real model. A `ProviderRegistry`
maps `request.provider` → the injected `ModelProvider`; a new provider is a new adapter,
never a Reasoning change.

## 10. Tool Coordination
`ToolDecisionCoordinator` owns **coordination only** over the injected `core.ports.ToolPort`:
- **Tool call requests** — a `TOOL_CALL` decision from the provider stream → a `ToolCall`
  (call id + name + arguments).
- **Approval** — the `ToolPolicy` (allow-list, per-call approval hook) admits/denies (verdict
  only); denial short-circuits with a recorded decision.
- **Execution requests** — approved calls go to `ToolPort.invoke` (the **Tool Runtime
  executes**; Reasoning never runs a tool).
- **Results** — the `ToolResult` is fed back into the provider loop (the next model turn);
  recorded in `tool_decisions`.
- **Retry / timeouts** — a failed/timed-out tool call is retried per the retry budget or
  surfaced as a recorded failure. Emits `tool.requested`/`tool.completed`/`tool.failed`.

## 11. Retry Architecture
`RetryController` — deterministic, on `core.policies.RetryPolicy` (exponential backoff, no
jitter in-core): **provider retry** (retryable provider failures), **tool retry**, **network
retry** (surfaced by the provider adapter as a retryable error — Reasoning re-invokes the
port, it **never networks**), **backoff** (deterministic delays; recorded as `RetryRecord`s),
**retry budget** (max attempts / max cumulative delay), and **circuit-breaker hooks** (an
injected `CircuitBreaker` port; open → fail fast). Delays are *recorded* (not slept) in the
deterministic local profile; a real scheduler applies them — same convention as the Turn
engines (DL-0016).

## 12. Cancellation Architecture
`CancellationManager` + a `CancellationToken` (id-addressed, checked at every stage):
**manual cancel** (caller trips the token), **timeout** (deadline from `timeout_s`),
**provider cancel** (provider signals unrecoverable), **tool cancel**, **session cancel**
(cancel all inferences in a session), **streaming cancel** (stop pulling chunks). Cancellation
is deterministic and always drains cleanly to the `CANCELLED` state with a
`CancellationRecord` on the package; it never leaks a partial into delivery.

## 13. Policy Architecture
`ReasoningPolicyEngine` turns immutable policy value objects into **verdicts** (no I/O, no
side effects), reusing `core.policies` where one fits: **RetryPolicy · ToolPolicy ·
StreamingPolicy · CancellationPolicy · ProviderPolicy · SafetyPolicy · ReasoningPolicy**
(max turns/tool-rounds, temperature clamps as *carried* guards — never interpreted for
inference). Bundled as `ReasoningPolicies`; validated at construction.

## 14. Event Flow
Reasoning is the **sole producer** of `reasoning.*`/`model.*` events and (as tool
coordinator) `tool.*` events, publishing standard envelopes (Replay/Gateway free). **No Event
Bus change, no new EventType**: the eleven logical events map onto the **existing** M5.0
members with a `phase` discriminator — the Memory/Knowledge/Context/Prompt precedent.

| Logical | Emitted as | phase |
|---|---|---|
| ReasoningRequested / InferenceStarted | `REASONING_STARTED` | `requested` / `started` |
| StreamingStarted / ChunkReceived | `MODEL_TOKEN` | `started` / (per-chunk) |
| ToolRequested | `TOOL_REQUESTED` | — |
| ToolCompleted | `TOOL_COMPLETED` (or `TOOL_FAILED`) | — |
| RetryStarted | `REASONING_STARTED` | `retry` |
| Cancelled | `REASONING_FAILED` | `cancelled` |
| InferenceCompleted / ResponseDelivered | `REASONING_COMPLETED` | `completed` / `delivered` |
| ReasoningExpired | `REASONING_COMPLETED` | `expired` |
| (token usage) | `MODEL_USAGE` | — |

Reasoning is **request-driven (pull)** — it does not ingest content events; default bus
subscription is empty and it never ingests its own `reasoning.*` event (self-loop safe).

## 15. Tenancy
`ReasoningTenancy.guard(scope, tenant, provider=…)` runs **before inference** (isolation-first),
wiring the M5.0 `tenant` hooks (`Authorizer`/`QuotaEnforcer`/`AuditSink`) with permissive
defaults (`reasoning → tenant` leaf foundation dep; acyclic — the DL-0024/0028/0032 precedent):
**organization isolation** (never cross org), **session isolation** (an inference belongs to
one session/scope), **provider allow-list** (a per-tenant guard on `request.provider`),
**quota hooks** (per-tenant inference/token budgets), **audit hooks**, and **future federated
sessions** (a registered provider/session source, never a boundary widening). Isolation
executes before any `ModelProvider` call.

## 16. Observability
- **Metrics** — started/completed/failed/cancelled/expired counts; inference latency;
  first-token latency; tokens (prompt/completion); retry attempts; tool calls; chunks.
- **Tracing** — a span per stage (select → stream → tool → retry → assemble → deliver).
- **Health** — module `health()` (RuntimeModule).
- **Sub-statistics** — `InferenceStatistics`, `StreamingStatistics`, `RetryStatistics`,
  `ToolStatistics`, `ProviderStatistics`, `LatencyStatistics`.
- **Replay / Gateway** — standard `reasoning.*`/`model.*` envelopes; deterministic full-cycle
  replay given a deterministic provider.

## 17. Extension Points (interfaces only — no implementations)
All future coupling is a port; **no direct runtime dependency**.
- **`ModelProvider`** — the inference seam (Anthropic/OpenAI/Gemini/local land as adapters;
  SDK/HTTP/transport live *inside the adapter*, never in Reasoning).
- **`core.ports.ToolPort`** — tool execution (the Tool Runtime); injected.
- **`CircuitBreaker`** — retry circuit-breaking.
- **`ResponseConsumer`** — the delivery seam for the future **Agent / Workflow / MCP / A2A**
  runtimes: they consume the finished `ResponsePackage` (and decide next steps) — they never
  re-run inference.
- **`ResponseOptimizer` / `ReasoningOptimizer`** — future post-processing / loop-optimization.
- **`MultiModalResponse`** — future non-text response parts.

These make Tool/Workflow/Agent/MCP/A2A/Optimizer/Multi-modal land as
`ModelProvider`/`ToolPort`/`ResponseConsumer` implementations **without changing** the engine,
streaming, retry, or repository.

## 18. Risks
| # | Risk | Mitigation |
|---|---|---|
| 1 | **Determinism vs a real LLM.** | The *orchestration* is deterministic (ids, injected clock, deterministic retry); the model is the injected `ModelProvider` seam. Replay records the produced `ResponsePackage`, not a provider call; the `EchoProvider` gives deterministic end-to-end tests. |
| 2 | **Coupling to `prompt`** if Reasoning imported `PromptPackage`. | Consume the neutral **`InferenceRequest`**; the Prompt→Reasoning handoff is a composition-root `PromptConsumer` bridge — neither runtime imports the other. |
| 3 | **Streaming + async + the sync `ReasoningPort`.** | Async-first engine; the sync `run` iterator drives non-suspending local providers via `run_coro_sync`; real streaming providers use the async API. |
| 4 | **Tool loop non-termination.** | `ReasoningPolicy` caps max turns / tool rounds; the FSM + retry budget guarantee a terminal state. |
| 5 | **Networking/transport creeping in.** | The `ModelProvider`/`ToolPort` adapters own all I/O; Reasoning re-invokes ports only (never opens a socket) — enforced by the dependency test (no `httpx`/SDK imports). |
| 6 | **"Temporary repository" vs "never owns persistence".** | The repository holds a **short-TTL working set** (responses/sessions) for delivery/replay/expiry — *not* durable persistence (Memory's job); documented + TTL-swept. |
| 7 | **Event vocabulary** — fixed M5.0 set. | Phase discriminator on existing `reasoning.*`/`model.*`/`tool.*` (established pattern); granular types deferred additive. |

## 19. Architectural Self-Review
- **Ownership:** inference orchestration only; model/tools/prompt/context/memory/knowledge are
  consumed or coordinated via ports, never owned. ✔
- **Dependency direction:** `reasoning → core, tenant` only; **no** import of `prompt`,
  `context`, `memory`, `knowledge`, any runtime, a provider SDK, or a transport — acyclic. ✔
- **Repository boundaries:** `ReasoningRepository` is the sole store-toucher and holds
  **temporary** responses/sessions only (durable persistence stays Memory's). ✔
- **Provider neutrality:** inference + tools + circuit-breaking are injected ports; no SDK,
  HTTP, transport, or LLM in-runtime. ✔
- **Replay / Gateway / Streaming:** standard `reasoning.*`/`model.*` envelopes; streaming is a
  mode not a fork; bus unmodified. ✔
- **Determinism:** content-hash ids, injected clock, deterministic retry/backoff + the
  `EchoProvider`; the model is the injected non-deterministic seam. ✔
- **Future compatibility:** Tool/Workflow/Agent/MCP/A2A/Optimizer/Multi-modal land as
  `ModelProvider`/`ToolPort`/`ResponseConsumer` implementations — no engine/streaming/retry/
  repository change. ✔
- **No ownership leakage:** never owns Memory/Knowledge/Context/Prompt/Templates/Providers/
  Transport/Networking/Tools/Workflow/Agents/durable-Persistence. ✔
- **Closed runtimes untouched:** Foundation + Memory + Knowledge + Context + Prompt + Ports +
  Events + Repositories unchanged; the frozen `ReasoningPort`/`ToolPort` are honoured, not
  edited (the richer `InferenceRequest`/`ResponsePackage` are additive; `run` narrows to
  `Iterator[Decision]`). ✔

**No future runtime requires a redesign of Reasoning Runtime.** After M5.5B it will be
**architecturally closed except for additive extensions and bug fixes.**

## 20. Implementation Plan
Mirrors the Memory/Knowledge/Context/Prompt core→intelligence split; each step additive
within `reasoning/`, foundation untouched.

- **M5.5B-1 — Reasoning Runtime Core.** Domain (`InferenceRequest`/`ResponsePackage`/
  `ResponseChunk`/`ToolDecision`/`ReasoningLineage`) + content-hash ids + `core.fsm`
  lifecycle; the `InferenceEngine` (single-shot + basic streaming loop over the injected
  `ModelProvider`); `ResponseAssembler`; the deterministic `EchoProvider` + `ProviderRegistry`;
  the **temporary** `ReasoningRepository` + local adapters; serializer, config, factory;
  `ReasoningRuntime` implementing `ReasoningPort.run` + a richer `infer`; `reasoning.*`/
  `model.*` events; core tests. *Non-goals:* tool coordination, retry, cancellation,
  scheduler, policies, tenancy, provider adapters.
- **M5.5B-2 — Reasoning Runtime Intelligence.** `StreamingController` (backpressure/timeout),
  `ToolDecisionCoordinator` (over `ToolPort`), `RetryController`, `CancellationManager`,
  `ReasoningScheduler`, `ReasoningPolicyEngine`, `ReasoningTenancy`, reasoning-lineage
  completion, full observability, extension interfaces (`ModelProvider`/`ResponseConsumer`/
  `CircuitBreaker`/`ResponseOptimizer`/`ReasoningOptimizer`/`MultiModalResponse`); intelligence
  tests + architecture-review closure.
- **M5.5C — Validation & Closure (audit-only).**

Then the Agent/Workflow runtimes consume the `ResponsePackage` via `ResponseConsumer` (and
decide next steps) — domain-only on the M5.0 substrate.

---

## What landed in M5.5B-1 (core — all additive)
The Reasoning Runtime **core** is implemented in `platform/reasoning/`. Nothing outside
`reasoning/` (+ one new test file) changed; `core`, `runtime`, `events`, `gateway`, `replay`,
`memory`, `knowledge`, `context`, and `prompt` are untouched. **`reasoning/` imports only
`core`** (verified by an AST dependency test) — its input is a neutral `InferenceRequest`, its
model is the injected `ModelProvider` port, so no sibling runtime is imported. **535 tests**
pass (27 new); new code is ruff-clean.

1. **Domain (`domain.py`) + ids (`ids.py`).** Frozen/slotted `InferenceRequest` (messages +
   `GenerationParameters` + provider/model + prompt hash/fingerprint + streaming/timeout +
   metadata + request hash + version + scope), `ResponsePackage`, `ResponseChunk`,
   `ReasoningLineage`, `RetryRecord`, `CancellationRecord`, `ResponseMetadata`,
   `ReasoningMetadata`, `InferenceResponse`, `TokenUsage`, `StreamingMetadata`,
   `ReasoningSession`. **Content-hash ids** (`request_hash_for`/`response_hash_for`/
   `chunk_id_for`/`package_id_for`).
2. **Lifecycle (`lifecycle.py`) — dogfoods `core.fsm`.** `REQUESTED → STARTED → STREAMING →
   COMPLETED` (+ single-shot `STARTED→COMPLETED`, tool loop `STREAMING⇄TOOL_WAIT`,
   `→FAILED`/`→CANCELLED` from any active state, `COMPLETED→EXPIRED`); illegal moves raise
   typed `FSMError`.
3. **Inference engine (`engine.py`) + `ProviderRegistry`.** Deterministic orchestration:
   provider select → stream (pull the provider's chunks) → assemble. Single-shot collapses to
   one final chunk; streaming keeps all chunks. Unknown provider → `ConfigError`.
4. **EchoProvider (`stores/echo_provider.py`) — deterministic, offline.** Derives a response
   *from the request* (echoes the last user message) and streams word-delta chunks. **Never
   calls an API/SDK/network** — the dev/test/replay default `ModelProvider`.
5. **Response assembler (`assembler.py`).** Joins chunk deltas → final `text`; computes the
   deterministic `response_hash` + streaming metadata + token usage (word-count counter) +
   full `ReasoningLineage` (prompt hash/fingerprint + inference-request hash + provider/model)
   + finish reason.
6. **Session (`session.py`) + temporary repository (`repositories/`) + local adapters
   (`stores/`).** `ReasoningSessionManager` (get-or-create + response history) over the
   `ReasoningRepository` — the sole store-toucher; **temporary** response + session records
   over in-memory Document/Metadata stores.
7. **Runtime (`runtime.py`).** `ReasoningRuntime(BusConsumer, ReasoningPort)` — the frozen
   `run(ReasoningRequest) → Iterator[Decision]` (streams token/final Decisions) + a richer
   `infer(InferenceRequest) → ResponsePackage` (drives the lifecycle + emits `reasoning.*`/
   `model.*` events + persists), `get`/`remove`/`history`, `expire` (TTL sweep),
   `statistics`/`health`.
8. **Serializer / config / factory / observability / events.** `ReasoningSerializer`
   (round-trippable request/chunk/package/session ↔ storage + event/API views);
   `ReasoningConfiguration` (validated store/TTL/default-provider knobs — **no provider
   config**); `ReasoningFactory` (composition root — registers the `EchoProvider` + any
   injected providers); `Metric` names + `ReasoningPhase`.

### Event mapping (bus unmodified — the design's §14 realized)
Lifecycle phases ride the **existing** `REASONING_STARTED` (`requested`/`started`/`streaming`),
`REASONING_COMPLETED` (`completed`/`delivered`/`expired`), and `REASONING_FAILED` (`failed`);
streamed chunks ride `MODEL_TOKEN`; usage rides `MODEL_USAGE` — no new `EventType`, no bus
change (Memory/Knowledge/Context/Prompt precedent).

### Deferred to M5.5B-2 (all additive — no core shape change)
`StreamingController` (backpressure/timeout) · `ToolDecisionCoordinator` (over `ToolPort`) ·
`RetryController` · `CancellationManager` · `ReasoningScheduler` · `ReasoningPolicyEngine` ·
`ReasoningTenancy` (adds the `tenant` foundation dep) · reasoning-lineage tool/retry history ·
real `ModelProvider` adapters (Anthropic/OpenAI/Gemini/local) · extension interfaces
(`ResponseConsumer`/`CircuitBreaker`/`ResponseOptimizer`/`ReasoningOptimizer`/
`MultiModalResponse`) · observability intelligence.

# VoxVoice — Provider Adapter Binding (Epic 8, M8.14)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/provider-adapter-binding.md` |
| **Status** | 🟢 **Implemented (M8.14)** — binds the Conversation Runtime to **real execution providers** via the existing adapter interfaces. Provider / Execution-pipeline / Streaming / Retry / Memory architecture. |
| **Date** | 2026-07-31 |
| **Scope** | A new `api.adapters` layer: production LLM adapters (Anthropic/OpenAI/Gemini/Groq/Azure), voice adapters (Deepgram/ElevenLabs/Cartesia/OpenAI), tool executors (HTTP/Function/Webhook/MCP/Database/Custom), a durable memory adapter (message store), a `ProviderExecutionEngine` (execute + streaming), and resilience (retry/timeout/circuit-breaker/rate-limit/cancellation/graceful-degradation). Bound into the M8.12 runtime via the existing adapter interfaces; API stream endpoint + provider diagnostics; Studio provider views. **The network egress is an injectable transport seam — deterministic + offline in tests; real HTTP in production.** **No RAG, no deployment, no monitoring.** |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) under ADR-0021/0022. **No new ADR, no architectural redesign, no ownership change.** All prior 1698 tests still green; frozen Epics 5/6/7 untouched. |
| **Related** | [Conversation Runtime](conversation-runtime.md) · [Conversation Playground](conversation-playground.md) · [Tool Framework](tool-framework.md) · [Agent Builder](agent-builder.md) · [Audit & Activity](audit-activity-platform.md) · Constitution §A2.4/§A5/§A11.2 · [INDEX](../../INDEX.md) |

## Problem & approach

M8.13 drove the runtime through **mock** adapters. M8.14 converts the deterministic execution
**plan** (M8.12) into **real execution** through provider adapters — reusing the runtime, tool
framework and playground **exactly as implemented** (no redesign). The golden rules are the axis
of the design: **Runtime owns orchestration; Providers own execution; Adapters own translation;
no provider-specific logic enters the Runtime.**

**How "real providers" coexists with the frozen boundary + offline tests.** The dependency tests
forbid the control-plane layers (`foundation`/`application`/`persistence`/`customer`) from
importing provider SDKs or the frozen Epic 5/6/7 runtime, and tests must run offline and
deterministically. So the provider adapters live in a **new `api.adapters` layer** that
implements the M8.12 adapter **interfaces** with **real provider-specific translation** (endpoint
/ auth header / request body / response parsing / token-usage extraction) over an **injectable
`Transport` seam**. The socket is the only injected part — the deterministic `EchoTransport`
(offline) runs the real translation code in tests; a production wiring injects an HTTP-backed
transport + real credentials. No provider SDK is a dependency, and no frozen Epic is imported.

## Provider architecture

- **Layer boundary.** `api.adapters → api.foundation, core, tenant` (+ stdlib). It never imports a
  frozen Epic 5/6/7 runtime, never imports `api.customer`, and no lower control-plane layer
  imports it — the **customer Container** is the composition root that injects the engine into the
  Foundation (via `build_persistent_application(engine_factory=…)`). Persistence stays
  provider-agnostic (it receives a callable, not `api.adapters`).
- **LLM adapters (5).** `AnthropicLLMAdapter` / `OpenAILLMAdapter` / `GeminiLLMAdapter` /
  `GroqLLMAdapter` / `AzureOpenAILLMAdapter` — each implements the M8.12 `LLMAdapter` interface,
  shaping the provider's real request (endpoint, `x-api-key`/`Bearer`/`api-key`, body schema) and
  parsing its real response (assistant text + token usage), with a defensive `EchoTransport`
  fallback. Registered in `LLM_PROVIDERS`; **no Runtime change** — the runtime still only plans.
- **Voice adapters (4).** Deepgram / ElevenLabs / Cartesia / OpenAI-Voice implement the `VoiceAdapter`
  interface (metadata-only in M8.14 — real audio is a production-transport concern).
- **Tool executors (6).** HTTP/Webhook (via transport), Function/Custom (in-process callable),
  Database/MCP (injected connector seams — **no real DB, no real MCP**). The dispatcher picks by
  the tool's **category** and applies the tool's **timeout + retry** while respecting the plan's
  ordering / enable / config-override / dependencies (already resolved by the Tool Framework).
- **Provider-specific code stays inside adapters** (golden rule) — the engine and runtime are
  provider-agnostic.

## Execution pipeline

```
Conversation Runtime (plans)  →  Execution Plan (M8.12)
   →  ProviderExecutionEngine (selects provider from the published config; adapters own execution)
   →  Execution Result (assistant + tool results + provider metadata + token usage + latency + errors)
   →  Conversation State (turn appended) + durable Message Store (memory adapter)
   →  Audit (execution.started / provider.selected / tool.executed / execution.completed / …)
```

**No planning logic moves into adapters.** The Foundation `ConversationExecutionService` reuses the
M8.12 session + the published version's provider config to select the provider, drives the injected
engine, persists messages + the turn, and emits the audit facts. The engine runs the LLM under
resilience, executes each enabled tool step under the tool's timeout/retry, applies voice metadata
when configured, and assembles the `ExecutionResult`.

## Streaming architecture

`ProviderExecutionEngine.stream` yields `StreamEvent`s — **token** chunks (from the transport's
streaming seam), **tool** events (one per executed step), a **voice** event (when voice is enabled),
then a terminal **done** event carrying the full result. **Cancellation** is a cooperative
`CancelToken` checked between events; **backpressure** is a `max_events` cap. The API `/stream`
endpoint serializes the events as NDJSON (`StreamingResponse`). Deterministic offline via
`EchoTransport.stream`.

## Memory architecture

A durable **message store** (`cp_conversation_message`, append-only, ordered by `seq`) backs the
`DurableMemoryAdapter` (implements the M8.12 `MemoryAdapter` interface). The Foundation execution
service is the **single writer** (user + assistant + tool messages per turn); the adapter provides
multi-turn **retrieval** (`history`) the engine uses to build conversation context. **No vector
search, no embeddings, no RAG.** The conversation session (M8.12) also keeps a per-turn summary in
its state — messages persist across resume.

## Error handling & resilience

`api.adapters.resilience`: **retry** (bounded, retryable-only), **timeout** (latency-budget check
against the transport's reported latency — deterministic, no threads), **circuit breaker** (opens
after N consecutive failures), **rate limiter** (deterministic token bucket), **cancellation**
(`CancelToken`), **provider-unavailable** + **graceful degradation** (a *tool* failure is captured
in `tool_results`/`errors` and is non-fatal; an *LLM* failure after retries raises
`ProviderUnavailableError`, audited as `provider.failed` + `execution.failed_real`).

## API changes (additive; reuses runtime APIs)

The runtime/playground APIs are reused unchanged. Added under the existing conversation path:

```
POST   …/playground/conversations/{sid}/execute     execute one turn through the real provider
POST   …/playground/conversations/{sid}/stream      stream the turn (NDJSON: token/tool/voice/done)
GET    …/playground/conversations/{sid}/messages    durable message store (memory adapter)
GET    …/playground/conversations/{sid}/provider    provider diagnostics (selected provider + breaker)
```

Controllers stay transport-only.

## Layer changes (every Foundation change justified)

- **Foundation (additive, required — the seam only):** `execution_domain.py` (`ExecutionResult`/
  `StreamEvent`/`TokenUsage`/`ConversationMessage`), `execution.py` (`ExecutionEngine` ABC +
  offline `MockExecutionEngine` + `ConversationExecutionService`); a `ConversationMessageRepository`
  (+ in-memory); `MessageView`/`ExecutionResultView` DTOs; additive `FoundationEventType`
  (execution/provider/tool/streaming); the factory gains a defaulted `execution_engine` + message
  repo. **Why required:** the Application must drive real execution through an injected engine
  **without importing `api.adapters`** — so the seam (ABC + result shapes) must live in the
  Foundation. **Why safe:** additive/defaulted; the default is the offline mock engine, so
  `services.py`, all existing services, and the M8.12 runtime/M8.13 playground are untouched (all
  1698 prior tests pass); **no frozen Epic and no provider SDK imported** (boundary tests green).
- **`api.adapters` (new layer):** all provider adapters + engine + resilience + memory + transport.
- **Persistence (additive):** `cp_conversation_message` table + repo + **migration 0009**; the
  composition threads an `engine_factory` callable (stays provider-agnostic).
- **Application (additive):** `execution.py` — `ExecuteTurn` (+stream) command, messages/provider
  queries; one Unit of Work per turn.
- **API (additive):** the four execution endpoints. **Customer Container** injects the real engine.
- **Studio (additive):** Streaming View, Provider Inspector, Token Usage, Latency, Execution/Retry
  Timeline, Provider Diagnostics — extending the playground. No business logic in the UI.

## Testing

`tests/test_provider_adapter_binding.py` — 10 tests: **all five LLM providers** translate+parse
(deterministic, offline), **all four voice** providers, **all six tool** categories, resilience
(retry-exhaustion / breaker / rate-limit / cancellation), engine execute + **streaming** + provider
failure, API + persistence **Definition-of-Done** end-to-end (publish → start → execute through a
real provider → stream → tools → persist messages → resume → inspect provider metadata +
diagnostics), **execution audited**, isolation (cross-org 403, viewer 403), **determinism**, and
the Studio provider screens served. **10/10 pass; 1708/1708 repository-wide; ruff clean; frozen
Epics 5/6/7 untouched; every prior M8.2–M8.13 test green.**

## Architecture compliance

- No new ADR, no redesign, no ownership change. **Golden rules:** runtime owns orchestration (still
  only plans — unchanged); providers own execution; adapters own translation; **no provider-specific
  logic in the runtime**. The runtime (M8.12), tool framework (M8.11), playground (M8.13) and agent
  builder (M8.10) are reused exactly and are byte-for-byte unchanged.
- **Boundary preserved:** the control-plane layers import no provider SDK and no frozen Epic; the
  provider layer sits behind the M8.12 adapter interfaces and is injected at the composition root.
  The real network egress is an **injectable transport** — offline/deterministic in tests, real HTTP
  in production.
- Determinism (§A2.4 — pure adapters + injected transport/clock), one typed event per fact (§A5),
  org+workspace+project isolation (§A11.2). Frozen Epics 5/6/7 byte-for-byte unchanged.

## Definition of Done

A customer can: publish an agent, start a conversation, execute through a real provider, stream
responses, execute configured tools, persist messages, resume the conversation, inspect provider
metadata, and view execution diagnostics — using only the Studio UI + Control Plane API. Proven by
`test_definition_of_done_end_to_end`, `test_all_llm_providers_translate_and_parse`,
`test_engine_execute_and_stream_offline`, and `test_execution_is_deterministic`.

## Known limitations / later hardening

- **Transport is offline by default** (`EchoTransport`) — production injects an HTTP-backed
  transport (e.g. httpx) + real API keys; the real translation code is already exercised in tests.
- **Voice is metadata-only** and **Database/MCP tools use fake connectors** in M8.14 — real audio /
  drivers / MCP servers are production-transport/connector concerns behind the same seams.
- **No RAG / no vector store** — the memory adapter is a plain message store (as scoped).
- Async/true-streaming transport + secrets-manager-backed credentials + per-provider rate-limit
  tuning are the next production-hardening steps. No monitoring/deployment (out of scope).

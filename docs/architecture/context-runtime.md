# Context Runtime (Epic 5, M5.3) — Design

| | |
|---|---|
| **Scope** | The provider-neutral, deterministic, event-driven runtime that **owns context assembly only** · **Status:** 🟢 Core implemented (M5.3B-1); intelligence M5.3B-2 |
| **Milestone** | M5.3A — Design (2026-07-20, approved) · M5.3B-1 — Core (2026-07-20, additive) · M5.3B-2 — Intelligence (planned) |
| **Basis** | The frozen M5.0 [AI Runtime Foundation](ai-runtime-foundation.md) (`core.ports.ContextPort`); the closed [Memory Runtime](memory-runtime.md) + [Knowledge Runtime](knowledge-runtime.md) patterns |
| **Governs** | `platform/context/` |

> Sections 1–18 below are the **approved M5.3A design**. The **"What landed in M5.3B-1"**
> section (after §18) records the implemented core. The design redesigns **nothing**
> already closed (Memory, Knowledge, Foundation, Ports, Events, Repositories); where a
> closed runtime seemed insufficient it is an *additive extension* or a *bug*, never a
> redesign.

---

## 1. Executive Summary
The **Context Runtime** is the single assembly seam that turns the scattered facts of a
turn — conversation history, working/long-term memory, knowledge passages, tool/workflow/
agent outputs, and system/developer/user instructions — into **one immutable, budgeted,
ranked, provenance-complete `ContextPackage`** that downstream runtimes (Prompt,
Reasoning, Agent) consume **without ever querying Memory or Knowledge again**. It owns
**context assembly and nothing else**: it never stores memory, owns knowledge, renders
prompts, reasons, calls tools, or talks to an LLM. It consumes every input **through a
`core` port or immutable data** (dependency inversion), so it imports no sibling runtime
and the dependency graph stays acyclic (`context → core, tenant`). It implements the
**frozen** `core.ports.ContextPort` and reuses the closed-runtime shape: a `BusConsumer`/
`RuntimeModule`, a repository layer over the store contracts (for **temporary** package
storage only), `core.policies`-based deterministic ranking/budgeting, injected extension
ports for anything AI-authored, and `context.*` standard envelopes that Replay records and
the Gateway streams for free. After M5.3B it will be **architecturally closed** except for
additive extensions and bug fixes.

## 2. Context Runtime Vision
One choke point, one contract, one package.

- **Choke point for isolation.** The summit named Context Runtime the natural place to
  enforce tenant isolation once. Every source pull is scoped *before* assembly; isolation
  propagates into each `MemoryQuery`/`KnowledgeQuery`, giving defence in depth.
- **The "assemble once" guarantee.** The `ContextPackage` carries complete provenance for
  every item, so **no downstream runtime re-queries Memory or Knowledge** — the package is
  self-sufficient (mirrors Knowledge's provenance-complete `KnowledgeHit`).
- **Deterministic + replayable.** Given the same request + same port responses + injected
  clock, assembly is byte-for-byte reproducible; every stage emits a standard envelope.
- **Provider-neutral + composable.** Tokenization, summarization, compression, and any new
  source (tool/workflow/agent/federated) are **injected ports** — the runtime authors no
  intelligence and hard-codes no vendor.

## 3. Component Architecture
All new, all inside the planned `platform/context/` package. Grouped **core** (M5.3B-1) vs
**intelligence** (M5.3B-2), mirroring the Memory/Knowledge split.

```
context/
  domain.py          ContextItem*/ContextPackage/ContextWindow/ContextSession (immutable)
  ids.py             content-hash ids (item id, package id) — deterministic dedup
  lifecycle.py       core.fsm package lifecycle: REQUESTED→ASSEMBLED→VALIDATED→
                     DELIVERED / REJECTED / EXPIRED
  ports.py           runtime-internal seams: Tokenizer, ContextSource (vendor/format)
  sources.py         SourceSelector + adapters that wrap injected MemoryPort/KnowledgePort/
                     PromptPort + ContextSource extensions into a uniform pull
  builder.py         ContextBuilder — merge → normalize → assemble the ContextPackage
  repositories/      ContextRepository (TEMPORARY package store) — sole store-toucher
  stores/            in-memory Document/Metadata adapters + Tokenizer default (dev only)
  session.py         ContextSession (per conv/session working set + rolling window)
  window.py          ContextWindow (bounded ordered view under budget)
  budget.py          BudgetManager (global/per-source/reserved/emergency/adaptive)      [I]
  ranking.py         ContextRankingEngine (deterministic multi-signal)                  [I]
  dedup.py           DeduplicationEngine (content-hash + semantic near-dup hook)         [I]
  compression.py     CompressionEngine (dedup/trim + injected summarizer/compressor)     [I]
  ordering.py        OrderingEngine (deterministic final sequence)                       [I]
  validation.py      ValidationEngine (context/budget/citation/privacy/policy/safety)    [I]
  policies.py        ContextPolicyEngine + policy value objects → verdicts               [I]
  tenancy.py         ContextTenancy (org/conversation/session isolation, tenant hooks)   [I]
  assembly.py        ContextAssembler — orchestrates the whole pipeline                  [I]
  extensions.py      interfaces only: Summarizer/Compressor/Redactor/Tokenizer/
                     ContextSource/StreamingContextSink/ContextConsumer                  [I]
  observability.py   ContextStatistics + Metric names + per-stage stats
  events.py          ContextPhase (phase discriminator on context.* envelopes)
  config.py          ContextConfiguration (budgets, weights, policies, collections)
  factory.py         ContextFactory (composition root; injects the ports)
  runtime.py         ContextRuntime(BusConsumer, ContextPort)
  _run.py            run_coro_sync (sync↔async bridge, local adapters only)
```
`[I]` = intelligence layer (M5.3B-2). The **runtime depends only on the injected ports**
(`MemoryPort`/`KnowledgePort`/`PromptPort`/`ContextSource`) — never on the `memory`,
`knowledge`, or `prompt` packages.

## 4. Ownership Matrix
| Concern | Owner | Not owned by |
|---|---|---|
| Context assembly orchestration | **ContextAssembler / ContextRuntime** | — |
| Which sources to pull | **SourceSelector** (policy) | — |
| Memory retrieval | injected `MemoryPort` (**Memory Runtime**) | Context (consumes only) |
| Knowledge retrieval | injected `KnowledgePort` (**Knowledge Runtime**) | Context (consumes only) |
| System/dev/user instructions | injected `PromptPort` + request data | Context (never renders prompts) |
| Tool/Workflow/Agent outputs | injected `ContextSource` extensions | Context (never runs them) |
| Merge / normalize into items | **ContextBuilder** | — |
| Dedup / rank / compress / order | **Context intelligence engines** | — |
| Budget allocation | **BudgetManager** | — |
| Validation gate | **ValidationEngine** | — |
| Temporary package storage | **ContextRepository** | Memory (long-term is Memory's) |
| Tenant isolation (assembly-time) | **ContextTenancy** | — |
| Tokenization | injected `Tokenizer` port | Context (no vendor) |
| Summarization / AI compression | injected `Summarizer`/`Compressor` ports | Context (no LLM) |
| Final immutable package | **ContextPackage** | downstream (read-only) |

**Context Runtime never owns:** Memory · Knowledge · Prompts · Reasoning · LLMs · Tools ·
Workflows · Agents. It *orchestrates their outputs*.

## 5. Context Assembly Pipeline
Async-first (per the Memory M5.1C decision); the sync `ContextPort.assemble` drives it via
the non-blocking bridge (local adapters only). Every stage is deterministic and owned by
Context; **retrieval is delegated** to the injected ports.

| # | Stage | Owner | Behaviour |
|---|---|---|---|
| 0 | **Input Request** | ContextRuntime | Validate `ContextRequest`; **tenancy guard first** (isolation before any fetch); build the internal `ContextAssemblyRequest` (scope + immutable conversation/session/system snapshots + env metadata). |
| 1 | **Source Selection** | SourceSelector | `SourceSelectionPolicy` decides which sources are active (memory tiers, knowledge, prompt, registered `ContextSource`s) from the request flags + policy. |
| 2 | **Retrieval** | injected ports | Pull from `MemoryPort.retrieve` (per tier), `KnowledgePort.retrieve`, `PromptPort`, and each `ContextSource.fetch` — **scope injected into every query**. Context owns orchestration, not the data. |
| 3 | **Merge** | ContextBuilder | Normalize every hit into a `ContextItem` (uniform shape) with full **provenance** (source runtime, source id, original score, scope, content hash). |
| 4 | **Deduplication** | DeduplicationEngine | Drop content-hash duplicates across sources; optional semantic near-dup via injected hook. Kept item records `merged_from` provenance. |
| 5 | **Validation** | ValidationEngine | Structural + citation + privacy + policy + safety verdicts (drop/redact invalid items). |
| 6 | **Ranking** | ContextRankingEngine | Deterministic multi-signal score per item; stable tie-break. |
| 7 | **Compression** | CompressionEngine | Dedup already applied; trim/summarize (injected `Summarizer`/`Compressor`) to fit; **provenance preserved** (`compressed_from`). |
| 8 | **Budget Allocation** | BudgetManager | Fit ranked items into global/per-source/reserved budgets; apply overflow + priority strategy; may trigger compression (threshold) or drop lowest-value. |
| 9 | **Ordering** | OrderingEngine | Deterministic final sequence (`OrderingPolicy`: system → developer → memory → knowledge → tools → conversation → user, or configured). |
| 10 | **Final Context Package** | ContextBuilder | Assemble the immutable `ContextPackage` (+ budget/compression/ordering metadata + provenance + confidence). Persist to the **temporary** `ContextRepository`; emit `context.assembled` (delivered). |

A final **validation gate** (post-order) guarantees *no context leaves the runtime
unvalidated*; failure → `ContextRejected`.

## 6. Context Package Design
`ContextPackage` (frozen, slotted, deterministic) — the assemble-once artifact.

- **Sectioned items** (each a `ContextItem` with its `kind`): `conversation_history`,
  `working_memory`, `long_term_memory`, `knowledge_passages`, `tool_results`,
  `workflow_results`, `agent_outputs`, `system_instructions`, `developer_instructions`,
  `user_instructions`, `environment`.
- **Per item:** `id` (content-hash), `kind`, `text`, `score`, `confidence`,
  `provenance` (source runtime + source id + original score + scope + content hash +
  `merged_from`/`compressed_from`), `token_estimate`, `order_index`.
- **Package-level:** `request`, `package_id`, `created_at`, `items` (ordered tuple),
  `budget_usage` (global + per-source used/limit/reserved/emergency), `ordering_metadata`,
  `compression_metadata` (ratios, what was summarized/dropped), `truncated`, `confidence`
  (aggregate), `scope`.
- **Interop:** `to_assembled()` narrows to the **frozen** `core.ports.AssembledContext`
  (`items: tuple[ContextItem,...]`, `truncated`, `used_tokens`) — the port contract is
  honoured unchanged; the rich package is the additive internal/extension surface. **No
  downstream runtime queries Memory or Knowledge again** — everything it needs, including
  citations, is in the package.

The core `ContextItem` (kind/text/score/provenance) is **frozen**; the runtime's richer
item lives internally and maps down to it — no foundation change.

## 7. Budget Architecture
`BudgetManager` owns budgeting end-to-end, on `core.policies.BudgetPolicy` where it fits.

- **Global budget** — total token ceiling (`ContextRequest.budget_tokens`; `0` → config
  default / unbounded).
- **Per-source budget** — caps per kind (e.g. knowledge ≤ 40%, memory ≤ 30%) so one source
  can't starve others.
- **Reserved budget** — always-kept allocation for system/developer instructions (never
  compressed away).
- **Emergency budget** — a small floor released only when validation would otherwise reject
  an empty/over-budget package (guarantees a minimally viable context).
- **Compression threshold** — utilization above which `CompressionEngine` is invoked before
  dropping.
- **Overflow strategy** (`OverflowPolicy`) — `compress_then_drop` (default) · `drop_lowest`
  · `truncate_oldest` · `reject`.
- **Priority strategy** — reserved > system > developer > ranked(memory/knowledge/tools) >
  conversation > user-supplied, tunable via policy.
- **Adaptive budgeting** — redistribute unused per-source budget to high-demand sources
  deterministically (no randomness).
- **Future model-aware budgeting** — an injected `Tokenizer`/`ModelProfile` port supplies
  exact per-model token counts + context-window size; default is a deterministic estimator.
  Additive — no runtime change.

Token counting is always through the `Tokenizer` port → provider-neutral + deterministic.

## 8. Ranking Architecture
`ContextRankingEngine` — deterministic weighted blend via `core.policies.RankingPolicy`
(the engine names the signals; the policy weights them; a profile/config supplies weights).
Signals: **conversation_relevance, semantic_relevance, knowledge_score, memory_score,
freshness** (half-life decay, injected clock), **importance, confidence, source_priority,
user_priority, system_priority**. No system randomness; **tie-break by
(kind_priority, item content-hash id)**. An optional injected reranker may reorder last
(extension). Source scores arrive already-normalized from Memory/Knowledge (their own
ranking) and are blended, never recomputed against their stores.

## 9. Compression Architecture
`CompressionEngine` — shrink to fit **without destroying provenance**.

- **Deduplication** — exact (content-hash) done in the pipeline; semantic near-dup via an
  injected hook.
- **Semantic compression** — injected `Compressor` port (lossy/lossless); no implementation
  in-runtime.
- **Conversation compression** — deterministic windowing/rollup of old turns; a rolled-up
  item cites the turn ids it summarizes.
- **Knowledge compression** — passage trimming to salient spans (offsets preserved in
  provenance).
- **Summarization hooks** — injected `Summarizer` port; the runtime never calls an LLM.
- **Future AI compression** — new `Compressor`/`Summarizer` implementations, additive.

Every compressed/summarized item carries `compressed_from` (original item ids + hashes) so
citations survive — provenance is invariant under compression.

## 10. Validation Architecture
`ValidationEngine` — **no context leaves the runtime unvalidated**. Verdicts only (the
runtime acts); mirrors the Knowledge policy-engine shape.

- **Context validation** — structural well-formedness, non-empty, kinds recognized.
- **Budget validation** — final package within global + per-source budgets.
- **Citation validation** — knowledge/tool items carry required provenance (source uri/id).
- **Privacy validation** — redaction (injected `Redactor`) of flagged spans before delivery.
- **Policy validation** — configured `ContextPolicies` satisfied.
- **Safety validation** — a `SafetyValidator` **hook** (interface only; no classifier here)
  gates disallowed content.

A failed gate → drop/redact the item, or reject the package (`ContextRejected`) if the
result is non-viable after the emergency budget.

## 11. Policy Architecture
`ContextPolicyEngine` turns immutable policy value objects into **verdicts** (no I/O, no
side effects), reusing `core.policies` where one fits:
**BudgetPolicy · RankingPolicy · CompressionPolicy · OrderingPolicy · ValidationPolicy ·
PrivacyPolicy · OverflowPolicy · CitationPolicy · SourceSelectionPolicy.** Bundled in a
`ContextPolicies` config; deterministic; validated at construction (fail fast, no silent
config).

## 12. Event Flow
The Context Runtime is the **sole producer** of `context.*` events and publishes standard
envelopes (Replay records + Gateway streams them for free). The **Event Bus is not
modified** and no new `EventType` is added in design: the seven logical events map onto the
**existing** M5.0 `CONTEXT_ASSEMBLED` (+ a `phase` discriminator) and `CONTEXT_TRUNCATED`
— the exact pattern Memory (M5.1B-1) and Knowledge (M5.2B-1/-2) used. Granular per-phase
`EventType` members are a **deferred additive vocabulary extension**.

| Logical event | Emitted as | Phase |
|---|---|---|
| ContextRequested | `CONTEXT_ASSEMBLED` | `requested` |
| ContextBuilt | `CONTEXT_ASSEMBLED` | `built` |
| ContextValidated | `CONTEXT_ASSEMBLED` | `validated` |
| ContextCompressed | `CONTEXT_TRUNCATED` | `compressed` |
| ContextRejected | `CONTEXT_ASSEMBLED` | `rejected` |
| ContextDelivered | `CONTEXT_ASSEMBLED` | `delivered` |
| ContextExpired | `CONTEXT_TRUNCATED` | `expired` |

**Ingestion (optional, additive):** the runtime is primarily **request-driven (pull)**. It
*may* passively observe `INPUT_PROCESSED`/`TRANSCRIPT_COMPLETED`/`SESSION_STARTED` to
maintain the `ContextSession` rolling window — but **never ingests a `context.*` event**
(structural self-loop prevention, like Knowledge) and its default subscription is empty.

## 13. Tenancy
`ContextTenancy.guard(scope, tenant)` runs **before assembly** (isolation before any
source pull), wiring the M5.0 `tenant` hooks (`Authorizer`/`QuotaEnforcer`/`AuditSink`)
with permissive no-op defaults — reusing the exact seam Memory/Knowledge use (`context →
tenant` is a foundation dependency, see the closed runtimes; acyclic). Levels:

- **Organization isolation** — a request may never widen past its `TenantContext` org.
- **Conversation / Session isolation** — scope keys restrict every downstream query.
- **Private vs Shared context** — private items are owner-scoped; shared/system items are
  org-visible (the resolved scope is injected into `MemoryQuery`/`KnowledgeQuery` so each
  runtime *also* enforces its own isolation — defence in depth).
- **Future Multi-Agent / Federated context** — a resolved scope plus registered
  `ContextSource`s; federation never widens the org boundary (additive source, not a
  boundary change).

## 14. Observability
Read-side vocabulary + snapshots (the runtime emits spans/events; this is the dashboard
surface), mirroring Memory/Knowledge:

- **Metrics** — assembled/rejected/expired counts; assembly latency; per-stage timers;
  budget used/limit; compression ratio; items in/out; cache/repository size.
- **Tracing** — a span per stage (source-selection → retrieval → merge → dedup → validate →
  rank → compress → budget → order).
- **Health** — module `health()` (RuntimeModule).
- **Sub-statistics** — `BudgetStatistics`, `CompressionStatistics`, `RankingStatistics`,
  `AssemblyStatistics`, `PipelineStatistics`.
- **Replay / Gateway** — standard `context.*` envelopes; deterministic full-cycle replay.

## 15. Extension Points (interfaces only — no implementations)
All future coupling is a port; **no direct runtime dependency**.

- **`ContextSource`** — the universal input seam: **Future Tool / Workflow / Agent /
  Multi-Agent / MCP / A2A / Federated** results all land as registered `ContextSource`s
  returning provenance-tagged contributions. New sources need **no engine change**.
- **`Tokenizer` / `ModelProfile`** — model-aware budgeting (future).
- **`Summarizer` / `Compressor` / `Redactor`** — AI compression + privacy (future; no LLM
  in-runtime).
- **`ContextConsumer` / `ContextSink`** — the delivery seam for the **Future Prompt /
  Reasoning / Agent** runtimes (they consume the `ContextPackage`; they never call back into
  Memory/Knowledge).
- **`StreamingContextSink`** — future incremental/streaming context delivery.

These make Prompt, Reasoning, Tool, Workflow, Agent, Multi-Agent, MCP, A2A, and Streaming
land **without redesigning** the assembler, builder, ranking, budget, or repositories.

## 16. Risks
| # | Risk | Mitigation |
|---|---|---|
| 1 | **Sync↔async bridge** — `ContextPort`/`Memory`/`Knowledge` ports are sync; the assembly core + `ContextRepository` are async. | Same proven `run_coro_sync` (Memory/Knowledge); local adapters never suspend; network stores must use the async API. |
| 2 | **Frozen `ContextItem`/`AssembledContext` too thin** for full provenance. | Rich internal `ContextPackage`/item; `to_assembled()` narrows to the frozen shape — additive, no port change (classified: additive extension). |
| 3 | **No token metering upstream yet** → budgeting on estimates. | `Tokenizer` **port** with a deterministic default estimator; model-exact counts are an additive injected implementation. |
| 4 | **Determinism vs injected Summarizer/Compressor** (may be non-deterministic). | Core pipeline is deterministic; injected AI ports are optional + off by default; replay records the produced package, not the model. |
| 5 | **Fan-out cost** (many sources per turn). | Per-source budgets + SourceSelectionPolicy + the temporary `ContextRepository` (reuse a fresh package within its short TTL). |
| 6 | **Double isolation** (Context + each runtime). | Intentional defence in depth; Context guards first and injects scope; downstream re-checks. |
| 7 | **Event vocabulary** — only 2 context EventTypes exist. | Phase discriminator on `CONTEXT_ASSEMBLED`/`CONTEXT_TRUNCATED` (established pattern); granular types deferred additive. |

## 17. Architectural Self-Review
- **Ownership:** assembly only; retrieval/knowledge/prompt/reason/tools are consumed via
  ports, never owned. ✔
- **Dependency direction:** `context → core, tenant` only; **no** import of `memory`,
  `knowledge`, `prompt`, or any runtime (dependency inversion) — acyclic. ✔
- **Repository boundaries:** `ContextRepository` is the sole store-toucher and holds
  **temporary** packages only (long-term storage stays Memory's). ✔
- **Provider neutrality:** tokenization/summarization/compression/sources are ports;
  no vendor, no LLM in-runtime. ✔
- **Replay / Gateway:** standard `context.*` envelopes; deterministic; bus unmodified. ✔
- **Determinism:** injected clock, content-hash ids, stable tie-breaks, deterministic
  estimator. ✔
- **Future compatibility:** Prompt/Reasoning/Tool/Workflow/Agent/Multi-Agent/MCP/A2A/
  Streaming all land as `ContextSource`/`ContextConsumer`/`Tokenizer` implementations —
  no assembler/builder/ranking/budget/repository change. ✔
- **No ownership leakage:** Context never owns Memory, Knowledge, Reasoning, Prompts, LLMs,
  Tools. ✔
- **Closed runtimes untouched:** Memory + Knowledge + Foundation + Ports + Events +
  Repositories unchanged; the thin frozen `ContextItem`/`AssembledContext` is honoured, not
  edited (richer package is additive). ✔

**No future runtime requires a redesign of Context Runtime.** After M5.3B the Context
Runtime will be **architecturally closed except for additive extensions and bug fixes.**

## 18. Implementation Plan
Mirrors the Memory/Knowledge core→intelligence split; each step additive within `context/`,
foundation untouched.

- **M5.3B-1 — Context Runtime Core.** Domain (`ContextPackage`/`ContextItem`/`ContextWindow`/
  `ContextSession`) + content-hash ids + `core.fsm` lifecycle; `ContextBuilder`;
  `SourceSelector` + source adapters over injected `MemoryPort`/`KnowledgePort`/`PromptPort`;
  **temporary** `ContextRepository` + local adapters + `Tokenizer` default; serializer,
  config, factory; `ContextRuntime` implementing `ContextPort.assemble` (deterministic
  merge/estimate/order, naive budget by truncation, no advanced intelligence);
  `context.*` events; core tests. *Non-goals:* ranking/compression/adaptive-budget/policies.
- **M5.3B-2 — Context Runtime Intelligence.** `BudgetManager` (per-source/reserved/emergency/
  adaptive), `ContextRankingEngine`, `DeduplicationEngine`, `CompressionEngine` (+ injected
  Summarizer/Compressor), `OrderingEngine`, `ValidationEngine`, `ContextPolicyEngine`,
  `ContextTenancy`, extension interfaces (`ContextSource`/`ContextConsumer`/`Redactor`/
  `StreamingContextSink`/`ModelProfile`), full observability; intelligence tests +
  architecture-review closure.
- **M5.3C — Validation & Closure (audit-only).** Certify closed except additive
  extensions/bug fixes.

Then the downstream runtimes (Prompt, Reasoning, Tool, Workflow, Agent) consume the
`ContextPackage` via `ContextConsumer` and observe `context.*` events — each domain-only on
the M5.0 substrate.

---

## What landed in M5.3B-1 (core — all additive)
The Context Runtime **core** is implemented in `platform/context/`. Nothing outside
`context/` (+ one new test file) changed; `core`, `runtime`, `events`, `gateway`,
`replay`, `memory`, and `knowledge` are untouched. **`context/` imports only `core`**
(verified by an AST dependency test) — every source is consumed through an injected
`core` port wrapped as a `ContextSource`, so no sibling runtime is imported. **423 tests**
pass (26 new); new code is ruff-clean.

1. **Domain (`domain.py`) + ids (`ids.py`).** Frozen/slotted `ContextItem` (with
   `Provenance`, `BudgetMetadata`, `CompressionMetadata`, confidence + confidence source),
   `ContextPackage`, `ContextWindow`, `ContextSession`, `ContextResponse`, `ContextScope`,
   `BudgetUsage`, `ContextMetadata`. **Content-hash ids** (`item_id_for`/`package_id_for`)
   — deterministic, dedup-friendly.
2. **Lifecycle (`lifecycle.py`) — dogfoods `core.fsm`.** `REQUESTED → ASSEMBLED →
   VALIDATED → DELIVERED` (+ `REJECTED`, `DELIVERED → EXPIRED`). Illegal moves raise typed
   `FSMError`. Validation is a formal FSM stage (trivial always-pass in the core; the
   ValidationEngine slots in at M5.3B-2).
3. **Temporary repository (`repositories/`) — the sole store-toucher.** `ContextRepository`
   over the `DocumentStore` (package + session records) + `MetadataStore` contracts;
   **temporary storage only** (long-term stays Memory's). The runtime never touches a store.
4. **Local adapters (`stores/`) + default tokenizer.** In-memory Document/Metadata stores +
   the deterministic `WordTokenizer` (`ceil(chars/cpt)` bounded by word count — no vendor).
   `ContextStore` bundles them behind the `Tokenizer` port.
5. **Session + window (`session.py`).** `ContextSessionManager` — get-or-create, push to the
   bounded rolling `ContextWindow`, record package history, clear; temporary state only.
6. **Sources (`sources.py`) — the uniform input seam.** `MemorySource`/`KnowledgeSource`/
   `PromptSource` wrap the injected `MemoryPort`/`KnowledgePort`/`PromptPort`;
   `StaticContextSource` carries immutable data (user input / system / environment / future
   tool-workflow-agent outputs). `SourceSelector` applies the naive include-flag policy.
7. **Assembly (`assembly.py` + `builder.py`) — deterministic.** `ContextAssembler` runs
   select → fetch (delegated to sources) → build. `ContextBuilder` merges/normalizes into
   `ContextItem`s (deterministic id + full provenance + token estimate), applies the **fixed
   source order** (config `DEFAULT_SOURCE_ORDER`, then score desc, then id), and enforces
   the global budget by **naive truncation only**. No ranking, no compression, no adaptive
   budget (M5.3B-2).
8. **Runtime (`runtime.py`).** `ContextRuntime(BusConsumer, ContextPort)` — `assemble`
   (drives the lifecycle + emits `context.*` phases + persists + updates the session),
   `get`/`remove`/`history`, `expire` (TTL sweep), `statistics`/`health`. `to_assembled()`
   narrows the rich `ContextPackage` to the **frozen** `core.ports.AssembledContext` — the
   port contract is honoured unchanged; provenance rides along in each item.
9. **Serializer / config / factory / observability / events.** `ContextSerializer`
   (round-trippable package/session ↔ storage + event/API views); `ContextConfiguration`
   (validated store/tokenizer/window/TTL/budget knobs); `ContextFactory` (composition root —
   the only place adapter kinds are named + where the **ports are injected**);
   `ContextStatistics` + `Metric` names + `ContextPhase`.

### Event mapping (bus unmodified — the design's §12 realized)
Lifecycle phases ride the **existing** `CONTEXT_ASSEMBLED` (`requested`/`built`/
`validated`/`delivered`/`rejected`) and `CONTEXT_TRUNCATED` (`compressed`/`expired`) with a
`phase` discriminator — no new `EventType`, no bus change (Memory/Knowledge precedent).

### Deferred to M5.3B-2 (all additive — no core shape change)
BudgetManager (per-source/reserved/emergency/adaptive) · ContextRankingEngine ·
DeduplicationEngine · CompressionEngine · OrderingEngine · ValidationEngine ·
ContextPolicyEngine · ContextTenancy (adds the `tenant` foundation dep) · extension
interfaces (`ContextSource` is already the seam; Summarizer/Compressor/Redactor/ModelProfile/
ContextConsumer/StreamingContextSink) · observability intelligence · streaming context.

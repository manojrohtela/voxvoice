# Knowledge Runtime (Epic 5, M5.2)

| | |
|---|---|
| **Scope** | The provider-neutral, deterministic, event-driven runtime that **owns the knowledge lifecycle + retrieval** · **Status:** ✅ Complete — **architecturally closed** (M5.2B-2), additive extensions + bug fixes only |
| **Milestone** | M5.2A — Design (approved) · M5.2B-1 — Core (2026-07-19) · M5.2B-2 — Intelligence (2026-07-20, additive) |
| **Basis** | The approved M5.2A Knowledge Runtime Design; the M5.0 [AI Runtime Foundation](ai-runtime-foundation.md); the closed [Memory Runtime](memory-runtime.md) pattern |
| **Governs** | `platform/knowledge/` |

## Purpose
A runtime that ingests documents through a deterministic pipeline, versions them, and
serves retrievable chunks — and does **nothing else**. It never stores memory, assembles
context, generates prompts, reasons, executes tools, or manages workflows. Built entirely
on the M5.0 substrate and the closed Memory Runtime shape: a `BusConsumer`/`RuntimeModule`
implementing `core.ports.KnowledgePort`, `core.fsm` lifecycles, a repository layer over
the `core.providers.stores` contracts, and `knowledge.*` events that Replay records and
the Gateway streams for free.

## What landed in M5.2B-1 (core only — all additive)
Nothing outside `platform/knowledge/` (+ one new test file) changed; `core`, `runtime`,
`events`, `gateway`, `replay`, and `memory` are untouched. `knowledge/` depends only on
`core` (verified by an AST dependency test). 345 prior tests unchanged; 367 total.

### 1. Domain model (`domain.py`) + deterministic ids (`ids.py`)
`Document`, `DocumentVersion`, `Chunk` (frozen/slotted) + `KnowledgeType`,
`DocumentState`, `ChunkState`, `KnowledgeScope` (org/user/**department**),
`SourceDescriptor`, `KnowledgeMetadata`. **Content-hash ids** (SHA-256, not salted
`hash()`): `document_id_for(uri)` is stable per logical source (so re-import versions the
*same* document), `version_id_for`, `chunk_id_for` (content-derived → free dedup).

### 2. Two lifecycles (`lifecycle.py`) — dogfood `core.fsm`
Document `IMPORTED → VALIDATED → PARSED → CHUNKED → EMBEDDED → INDEXED → ACTIVE →
ARCHIVED → DELETED`; Chunk `CREATED → EMBEDDED → INDEXED → ARCHIVED → DELETED`. Illegal
transitions raise typed `FSMError`s.

### 3. Repository layer (`repositories/`) — the anti-corruption boundary
`DocumentRepository` (documents + raw blobs via `ObjectStore` + version records),
`ChunkRepository`, `EmbeddingRepository` (write-path embed/upsert; `search` deferred),
and the coordinating `KnowledgeRepository` facade. The **only** code binding the store
contracts; the runtime/pipelines never touch a provider.

### 4. Local adapters (`stores/`, dev only) + `KnowledgeStore` bundle
Deterministic `InMemoryObjectStore/DocumentStore/VectorStore/MetadataStore` +
`DeterministicLocalEmbedder`. Self-contained (Knowledge never imports Memory); extracting
a shared local-adapter set to `providers/storage/` is a future additive refactor.

### 5. Document pipeline (`pipeline/`) — async-first
`DocumentPipeline.ingest` runs import → validate → normalize → parse → metadata → chunk →
embed → index → store → version → activate deterministically, advancing the FSM at each
stage and using repositories only. `Parser` and `ChunkStrategy` are **ports**
(`PlainTextParser` + `SlidingWindowChunker` are the local defaults — no vendor libraries);
`EmbeddingPipeline` dedups identical chunks by content hash. Archival + deletion hooks
included. It returns an `IngestResult`; the **runtime is the sole event producer**.

### 6. Versioning (`version.py`) — `VersionManager`
Immutable versions, monotonic numbers, current pointer, supersede-on-update, history,
rollback. Persists only through `DocumentRepository`.

### 7. `KnowledgeIndex` (`index.py`) — write path
An in-process keyword + metadata inverted index that ingestion registers into
(`register`/`remove`); querying it is retrieval intelligence (M5.2B-2). Derived structure
— holds no store handle (no boundary violation).

### 8. `KnowledgeRuntime` (`runtime.py`)
`BusConsumer` implementing `core.ports.KnowledgePort`. Public surface: `index()` (async
pipeline via the sync bridge), `get()`, `remove()`, `retrieve()` **placeholder** (returns
no passages — intelligence is M5.2B-2), `sources()`, `statistics()`, `health()`. Emits
`knowledge.*` standard envelopes.

### 9. Serializer / Config / Factory / Observability
`KnowledgeSerializer` (document/chunk/version ↔ storage + event/API views, round-trippable);
`KnowledgeConfiguration` (store kinds, embedder, chunking, collections — validated);
`KnowledgeFactory` (the only place adapter kinds are named); `KnowledgeStatistics` +
`Metric` constants + `KnowledgePhase`.

## Design decisions carried from M5.2A / Memory closure
- **Async-first pipeline.** Per the M5.2A risk review (Memory debt #1), the pipeline is
  async; the sync `KnowledgePort`/`index`/`get`/`remove` drive it via the non-blocking
  bridge for local adapters only. Network stores must use the async API.
- **Event vocabulary reuse.** The core milestone reuses the existing M5.0 `knowledge.*`
  `EventType`s (`KNOWLEDGE_INDEXED`/`KNOWLEDGE_RETRIEVED`/`KNOWLEDGE_SOURCE_UPDATED`), with
  a `phase` discriminator on lifecycle events — **no core/bus change**. Granular
  per-lifecycle `EventType` members remain a deferred additive extension.

## Dependency graph (acyclic, verified by test)
```
core (events · errors · fsm · module · ports · providers/stores)
  ▲
knowledge/  ── ids · domain · lifecycle · serializer · config · index · version
               · ports · stores · repositories · pipeline · observability · runtime · factory
```
`knowledge/` imports only `core` (+ its own package) — never a sibling runtime, the bus
implementation, `memory`, `api`, `gateway`, or `replay`.

## What landed in M5.2B-2 (intelligence — all additive)
The intelligence layer completes the runtime. Nothing outside `platform/knowledge/`
(+ one new test file) changed; `core`, `runtime`, `events`, `gateway`, `replay`, and
`memory` are untouched. `knowledge/` now depends on `core` **and `tenant`** (the same
leaf-foundation isolation seam Memory uses — see [DL-0024]; verified by the AST test).
367 → **397 tests** (30 new intelligence tests); new code is ruff-clean.

### 10. Retrieval query + result (`query.py`, `results.py`)
`KnowledgeRetrievalQuery` is the rich internal request (scope, profile, source/tag/type
filters, metadata filters, version pin) built from the frozen port `KnowledgeQuery`.
`KnowledgeResult` returns **provenance-complete** `KnowledgeHit`s — document id + version
+ chunk id, `Citation` (source uri/title/author/offsets), retrieval score, full
`RankingBreakdown`, knowledge scope, content hash — so a consumer never re-hits a
repository. `KnowledgeHit.to_passage()` narrows to the frozen `KnowledgePassage`.

### 11. Query Planner (`planner.py`) — pure decision layer
`KnowledgeQueryPlanner.plan()` → an immutable `RetrievalPlan` choosing
KEYWORD / SEMANTIC / HYBRID / METADATA / VERSION. Deterministic; **never retrieves,
never touches a repository** (asserted). The extension seam for GraphRAG/MultiVector as
new strategies behind `choose_strategy`.

### 12. Retrieval profiles (`profiles.py`) — configuration, not branching
Fast / Balanced / High-Precision / Compliance / Research are **immutable `ProfileConfig`
bundles** that bias planner strategy + ranking weights + caps. Selected by data; the
planner and engine never special-case a profile by name (no runtime branching).

### 13. Retrieval Engine (`retrieval.py`) — orchestration only
`KnowledgeRetrievalEngine` executes **tenancy → planner → repositories → merge →
version/metadata/tenant filtering → policy → ranking → selection → `KnowledgeResult`**.
Isolation is enforced **first** (asserted). It touches repositories only — never a
provider (asserted). Semantic matches (content-hash keyed) map back onto chunks; keyword
uses the inverted index; both feed the ranker.

### 14. Ranking Engine (`ranking.py`) — deterministic
Weighted linear blend via `core.policies.RankingPolicy` over eight neutral signals
(semantic, keyword, freshness half-life decay under an injected clock, popularity,
version/org/document/chunk priority). Chunk-id breaks every tie; an optional injected
`Reranker` runs last. No randomness.

### 15. Policy Engine (`policies.py`) — verdicts only
`KnowledgePolicyEngine` turns eleven policy value objects (Retention, Version, Validation,
Privacy, Deduplication, Chunk, Embedding, Archive, Deletion, Retrieval, Citation) into
**verdicts** the runtime/engine execute. No I/O, no side effects. Privacy redaction is an
injected `Redactor` port (no implementation, no LLM).

### 16. Cache (`cache.py`) — repository-owned
A deterministic LRU (`KnowledgeCache` + `LRUCacheBackend`, read-through/write-through/
invalidate/stats) that lives **inside `ChunkRepository`**; the runtime never sees it, so a
Redis `CacheBackend` swaps in below the repository boundary.

### 17. Tenancy (`tenancy.py`) — isolation before retrieval
`KnowledgeTenancy.guard()` enforces org/user isolation + the `tenant` hooks (permissive
no-op defaults); `admits()` gives per-document visibility (ORG/SHARED/STATIC/REFERENCE
visible org-wide; PRIVATE owner-only; `department` narrows). Federated cross-org knowledge
is a future additive `KnowledgeSource`, never a widening of this boundary.

### 18. Event ingestion (`ingestion.py`) — self-loop-safe by construction
`KnowledgeIngestor` maps *other* producers' events (`REASONING_COMPLETED`/`TOOL_COMPLETED`,
register-on-flag) to intents. `INGEST_TYPES` **structurally excludes every `knowledge.*`
type**, and the runtime double-guards on `EventSource.KNOWLEDGE` — no self-loop (asserted).

### 19. Extension interfaces (`extensions.py`) — interfaces only
Reranker · Summarizer · Redactor · Compressor · KnowledgeSource · GraphStore ·
GraphRetriever · MultiVectorRetriever. No implementations; the runtime never calls a model.
These make **GraphRAG / MultiVector / Federated Knowledge** land as new retrieval
strategies/sources **without changing the planner, engine, ranking, or repositories**.

## Closure (M5.2B-2)
The Knowledge Runtime is **architecturally closed** except for additive extensions and
bug fixes. Ownership (knowledge lifecycle + retrieval, nothing else), dependency direction
(`knowledge → core, tenant` only — acyclic), the repository boundary (sole store-toucher),
provider neutrality, determinism, and replay/gateway compatibility are all verified by
test. No future runtime (Context/Reasoning/Tool/Workflow/Agent) requires a redesign of
Knowledge — they consume `core.ports.KnowledgePort` and observe `knowledge.*` events.

## Deferred (all additive — no runtime shape change)
Real store adapters (Postgres/pgvector/Qdrant/Redis) · reranker/summarizer/redactor/
compressor implementations · GraphRAG + MultiVector + Federated Knowledge sources · real
RBAC/quota/audit · richer parsers/chunkers · durable index · a shared `providers/storage/`
local-adapter set (extracting today's Memory/Knowledge duplication) · granular
per-lifecycle `knowledge.*` EventTypes.

[DL-0024]: ../../.voxvoice/DECISION_LOG.md

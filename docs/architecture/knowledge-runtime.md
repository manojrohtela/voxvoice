# Knowledge Runtime (Epic 5, M5.2)

| | |
|---|---|
| **Scope** | The provider-neutral, deterministic, event-driven runtime that **owns the knowledge lifecycle + retrieval** · **Status:** Core implemented (M5.2B-1) |
| **Milestone** | M5.2A — Design (approved) · M5.2B-1 — Core (2026-07-19, additive) |
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

## Non-goals (M5.2B-2+)
KnowledgeQueryPlanner · KnowledgeRetrievalEngine · KnowledgeRanking · KnowledgePolicies ·
KnowledgeCache · content-event ingestion · advanced retrieval (Graph/GraphRAG/multi-vector)
· compression/summarization/reranking · real store/parser adapters. All additive behind
the contracts and ports already in place.

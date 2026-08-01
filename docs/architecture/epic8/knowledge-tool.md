# VoxVoice — Knowledge Tool / RAG Foundation (Epic 8, M8.15)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/knowledge-tool.md` |
| **Status** | 🟢 **Implemented (M8.15)** — Knowledge as a **Tool Framework capability**. The Runtime invokes it through the existing Tool Executor; retrieval reaches the Runtime only as a Tool Result. Knowledge / Chunking / Embedding / Vector Store / Retrieval architecture. |
| **Date** | 2026-07-31 |
| **Scope** | Knowledge assets (collection/document/chunk + metadata/version/status/soft-delete/archive/restore); document pipeline (upload/replace/delete/archive/restore/version/chunk/checksum/duplicate/MIME/size); **deterministic chunking**; embedding providers (OpenAI/Gemini/Voyage/Cohere/Local); vector stores (FAISS/Qdrant/PgVector/Pinecone/Chroma); the retrieval pipeline (query→embedding→search→ranking→filtering→selection→context package); the **Knowledge Tool** executor; API; Studio. **No OCR, no image parsing, no LLM reranking, no monitoring, no deployment.** |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) under ADR-0021/0022. **No new ADR, no architectural redesign, no ADR.** All prior 1708 tests still green; **Runtime (M8.12) / Tool Framework (M8.11) / Provider Binding (M8.14) / Playground (M8.13) reused exactly and unchanged**; frozen Epics 5/6/7 untouched. |
| **Related** | [Tool Framework](tool-framework.md) · [Provider Adapter Binding](provider-adapter-binding.md) · [Conversation Runtime](conversation-runtime.md) · [Agent Builder](agent-builder.md) · [Audit & Activity](audit-activity-platform.md) · Constitution §A2.4/§A5/§A11.2 · [INDEX](../../INDEX.md) |

## Problem & approach

Knowledge (RAG retrieval) is delivered **strictly as a Tool Framework capability** — **not** part
of the Conversation Runtime, the LLM adapter, or Memory. A `knowledge`-category tool attached to
an agent is invoked by the existing Tool Executor (M8.14) during a turn; it retrieves and returns
a **Context Package** as its Tool Result. **No retrieval logic enters the Runtime** (golden rule:
Knowledge owns retrieval, Runtime owns orchestration, LLM owns generation, Adapters own execution).

Consistent with M8.14, the embedding providers + vector stores live in the **`api.adapters`** layer
behind provider-independent seams over an injectable transport — **deterministic + offline in tests
** (a hash embedder + cosine vector store), real providers in production. The knowledge **assets +
deterministic chunking** live in the Foundation (control-plane authoring state).

## Knowledge architecture

- **Assets (Foundation, `knowledge_domain.py`):** `KnowledgeCollection` → `KnowledgeDocument` →
  `KnowledgeChunk`. A "Knowledge Base" is the collection's project-scoped `base` **namespace**
  (kept lightweight — a fourth aggregate would add repos without behavioural gain; noted honestly).
  Collections carry their embedding provider/model + chunking config; documents carry MIME/size/
  checksum/version/status; chunks carry the deterministic embedding vector + embedding/index status.
- **Services (Foundation):** `KnowledgeService` owns assets + the document pipeline + chunking;
  `KnowledgeSearchService` owns embedding generation, vector indexing, and retrieval over the
  injected `KnowledgeRetriever` seam. Writes authorize via the existing workspace-scoped
  `PROJECT_MANAGE` (**no new RBAC scope**).
- **Reuse-first:** the Tool Framework (M8.11), Runtime (M8.12), Playground (M8.13) and Provider
  Binding (M8.14) are reused exactly and byte-for-byte unchanged. Retrieval history reuses the
  durable Audit Platform (M8.9).

## Chunking architecture

`knowledge_chunking.chunk_text` is a **pure, deterministic** fixed-size + overlap chunker: `step =
size − overlap`; each chunk carries its `index`, `text`, offset metadata, and a **checksum** (chunk
integrity). Document validation is here too — supported **MIME** (text only; no OCR/image),
**maximum size**, content **checksum**, and **duplicate detection** (by checksum within a
collection). Identical input always yields identical chunks (tested).

## Embedding architecture

Provider-independent `KnowledgeEmbedder` seam; the `api.adapters` implementations
(`OpenAI/Gemini/Voyage/Cohere/Local`) shape each provider's embeddings request over the transport
and return the vector. **Offline the vector is a deterministic hash embedding** (bag-of-hashed-tokens,
normalized — similar text → similar vectors); a production transport returns real vectors the
adapter parses. **No embeddings inside the Runtime.** Registry: `EMBEDDING_PROVIDERS`.

## Vector store architecture

Provider-independent `VectorStore` seam; the `api.adapters` implementations
(`FAISS/Qdrant/PgVector/Pinecone/Chroma`) all resolve offline to a deterministic brute-force cosine
search over the persisted chunk vectors (local stores locally; external stores over the transport in
production). **No dedicated vector column type** — chunk embeddings persist as a portable JSON list
in `cp_knowledge_chunk`, so retrieval needs no external index in this build. **No provider logic
outside adapters.** Registry: `VECTOR_STORES`.

## Retrieval pipeline

```
Knowledge Tool (Tool Executor invokes it)
   Query → Embedding (embedder) → Vector Search (vector store) → Ranking (cosine)
        → Filtering (metadata + collection filters + threshold) → Chunk Selection (top_k)
        → Context Package → Tool Result   →   Runtime receives ONLY the Tool Result
```

Ranking is by **similarity score** (cosine), with **max chunks (top_k)**, **metadata filters**,
**collection filter**, and a **threshold** — **no LLM reranking**. The `KnowledgeToolExecutor`
(adapters) reads the collection's *indexed* chunks (org/project-scoped for **isolation**), runs the
injected retriever, and returns the `ContextPackage` as the Tool Result. The Foundation
`KnowledgeSearchService.search` exposes the identical pipeline for the Studio Search Playground.

## Knowledge Tool execution (binding into M8.14)

The `knowledge` executor category (M8.14 `default_executors`) is wired to the real
`KnowledgeToolExecutor` when the knowledge repos + retriever are injected (the customer Container).
The M8.14 engine threads org/project context into tool arguments so the Knowledge Tool can enforce
isolation and resolve its `collection_id` from the tool config. **No Runtime change, no Tool
Framework change** — a knowledge tool is attached, enabled, ordered and configured exactly like any
other tool (M8.11), and executed exactly like any other tool (M8.14).

## Layer changes (every Foundation change justified)

- **Foundation (additive, required):** `knowledge_domain.py`, `knowledge_chunking.py` (pure),
  `knowledge_adapters.py` (the `KnowledgeEmbedder`/`VectorStore`/`KnowledgeRetriever` seams +
  deterministic `MockKnowledgeRetriever`/`HashEmbedder`/`InMemoryVectorStore`), `knowledge.py`
  (`KnowledgeService` + `KnowledgeSearchService`); three repositories (+ in-memory); DTOs; additive
  `FoundationEventType.knowledge.*`, id prefixes, diagnostic codes; the factory gains defaulted
  knowledge repos + retriever. **Why required:** knowledge assets + deterministic chunking are new
  control-plane domain, and the Application must reach embedding/vector providers through an injected
  seam without importing `api.adapters`. **Why safe:** additive/defaulted; the default is the offline
  retriever, so all existing services + the M8.11–M8.14 stack are untouched (all 1708 prior tests
  pass); **no frozen Epic and no provider SDK imported** (boundary tests green).
- **`api.adapters` (extended):** embedding providers + vector stores + `ProviderKnowledgeRetriever`
  + `KnowledgeToolExecutor`, wired into the engine's executors for the `knowledge` category.
- **Persistence (additive):** `cp_knowledge_collection`/`cp_knowledge_document`/`cp_knowledge_chunk`
  + repos + **migration 0010**; composition threads the knowledge repos + retriever
  (provider-agnostic).
- **Application (additive):** `knowledge.py` commands/queries/use cases.
- **API (additive):** the knowledge router. **Container** injects one retriever backing both the
  Studio search and the Knowledge Tool.
- **Studio (additive):** Knowledge Dashboard / Collections / Documents / Upload / Chunk Viewer /
  Metadata Viewer / Vector Status / Retrieval Inspector / Search Playground.

## API surface (all additive)

```
POST/GET/PATCH/DELETE  …/knowledge/collections[/{id}]         collections (+archive/restore)
POST   …/knowledge/collections/{id}/embeddings               generate embeddings
POST   …/knowledge/collections/{id}/index                    index vectors
GET    …/knowledge/collections/{id}/vector-status            vector status
POST   …/knowledge/collections/{id}/search                   knowledge search (retrieval inspector)
POST/GET/PATCH/DELETE  …/collections/{id}/documents[/{did}]  documents (+archive/restore)
POST   …/collections/{id}/documents/{did}/chunk              chunk a document
GET    …/collections/{id}/documents/{did}/chunks             chunk + metadata viewer
```

## Testing

`tests/test_knowledge_tool.py` — 9 tests: deterministic chunking + MIME/embedding helpers, **all 5
embedding providers + all 5 vector stores**, the Foundation pipeline (upload→chunk→embed→index→search
incl. duplicate + MIME rejection + determinism), the provider retriever, API + persistence
**Definition-of-Done** end-to-end (create collection → upload → chunk → embed → index → search →
inspect → **attach Knowledge Tool to an agent → receive retrieved context through Tool execution**),
knowledge pipeline **audited**, isolation (cross-project 404, viewer 403), and the Studio knowledge
screens served. **9/9 pass; 1717/1717 repository-wide; ruff clean; frozen Epics 5/6/7 untouched;
every prior M8.2–M8.14 test green.**

## Architecture compliance

- No new ADR, no redesign. **Knowledge remains a Tool Framework capability** — invoked through the
  existing Tool Executor, reaching the Runtime only as a Tool Result. **No retrieval logic in the
  Runtime**; no provider-specific retrieval outside adapters; **no LLM reranking**; no OCR/image
  parsing. Runtime/Tool-Framework/Provider-Binding/Playground reused exactly and unchanged.
- **Boundary preserved:** the control-plane layers import no provider SDK and no frozen Epic; the
  embedding/vector providers sit behind the knowledge seams and are injected at the composition
  root; the egress is the injectable transport (offline in tests).
- Determinism (§A2.4 — pure chunking + hash embeddings + cosine), one typed event per fact (§A5),
  org+workspace+project isolation (§A11.2). Frozen Epics 5/6/7 byte-for-byte unchanged.

## Definition of Done

A customer can: create a Knowledge Collection, upload documents, chunk documents, generate
embeddings, index vectors, execute Knowledge Search, inspect retrieved chunks, attach a Knowledge
Tool to an Agent, and **receive retrieved context through Tool execution** — using only the Studio
UI + Control Plane API. Proven by `test_definition_of_done_end_to_end` (which ends by executing a
turn whose knowledge tool returns the retrieved context), `test_pipeline_upload_chunk_embed_index_search`,
and `test_all_embedding_providers_and_vector_stores`.

## Known limitations / later hardening

- **Offline embeddings/vectors by default** (hash embedder + cosine) — production injects a real
  embedding transport + a real vector store behind the same seams (no domain change).
- **JSON-column vectors** (no `pgvector`/ANN index) — fine for this build; a real ANN index is the
  scale hardening, plugged in at the vector-store adapter.
- **No OCR / image parsing / LLM reranking / monitoring / deployment** — all out of scope.
- No JS test runner for the Studio knowledge screens (verified via served-artifact + API tests).

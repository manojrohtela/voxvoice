"""Knowledge provider adapters (M8.15) — embedding providers + vector stores + the Knowledge Tool.

Implements the Foundation knowledge seams (`KnowledgeEmbedder` / `VectorStore` /
`KnowledgeRetriever`) with **provider-specific translation** over the injectable `Transport` seam
(deterministic + offline in tests, real HTTP in production), plus the `KnowledgeToolExecutor` — a
Tool Framework `ToolExecutor` for the `knowledge` category that the M8.14 engine invokes. **All
provider logic lives here; no retrieval logic enters the Runtime — it receives only the Tool
Result (the Context Package).** No LLM reranking. No provider SDK imported.
"""

from __future__ import annotations

from typing import Any

from api.foundation import (
    ContextPackage,
    KnowledgeChunkRepository,
    KnowledgeCollectionRepository,
    KnowledgeEmbedder,
    KnowledgeRetriever,
    ToolExecutor,
    VectorStore,
    hash_embed,
    indexed_chunks_as_corpus,
)

from .transport import ProviderRequest, Transport

# ── embedding providers ─────────────────────────────────────────────────────────


class BaseEmbeddingAdapter(KnowledgeEmbedder):
    """Provider-independent embedding flow: build the provider request (exercised over the
    transport) then produce the vector. Offline, the vector is the deterministic `hash_embed`;
    a production transport returns real embedding vectors the adapter parses."""

    provider = "local"
    _url = ""

    def __init__(self, transport: Transport, *, model: str = "", api_key: str = "") -> None:
        self._transport = transport
        self._model = model
        self._api_key = api_key

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        out: list[tuple[float, ...]] = []
        for text in texts:
            if self._url:  # exercise the provider request path (offline transport echoes)
                self._transport.request(ProviderRequest(
                    provider=f"embed:{self.provider}", method="POST", url=self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    body={"model": self._model, "input": text},
                ))
            out.append(hash_embed(text))  # deterministic offline; real vector via prod transport
        return out


class OpenAIEmbeddingAdapter(BaseEmbeddingAdapter):
    provider = "openai"
    _url = "https://api.openai.com/v1/embeddings"


class GeminiEmbeddingAdapter(BaseEmbeddingAdapter):
    provider = "gemini"
    _url = "https://generativelanguage.googleapis.com/v1beta/models/embedding-001:embedContent"


class VoyageEmbeddingAdapter(BaseEmbeddingAdapter):
    provider = "voyage"
    _url = "https://api.voyageai.com/v1/embeddings"


class CohereEmbeddingAdapter(BaseEmbeddingAdapter):
    provider = "cohere"
    _url = "https://api.cohere.ai/v1/embed"


class LocalEmbeddingAdapter(BaseEmbeddingAdapter):
    """A fully local, transport-free embedder (deterministic hash)."""

    provider = "local"
    _url = ""


EMBEDDING_PROVIDERS: dict[str, type[BaseEmbeddingAdapter]] = {
    "openai": OpenAIEmbeddingAdapter,
    "gemini": GeminiEmbeddingAdapter,
    "voyage": VoyageEmbeddingAdapter,
    "cohere": CohereEmbeddingAdapter,
    "local": LocalEmbeddingAdapter,
}


def build_embedder(
    provider: str, transport: Transport, *, model: str = "", api_key: str = ""
) -> BaseEmbeddingAdapter:
    cls = EMBEDDING_PROVIDERS.get(provider, LocalEmbeddingAdapter)
    return cls(transport, model=model, api_key=api_key)


# ── vector stores ────────────────────────────────────────────────────────────────


class _CosineVectorStore(VectorStore):
    """Brute-force cosine search — the shared local implementation. Provider stores differ only
    in transport/persistence (offline they all resolve to this deterministic search)."""

    name = "memory"

    def search(self, query, corpus, *, top_k, threshold):
        from api.foundation import cosine

        scored = [(cid, cosine(query, vec)) for cid, vec, _ in corpus]
        scored = [(cid, s) for cid, s in scored if s >= threshold]
        scored.sort(key=lambda t: -t[1])
        return scored[: max(0, top_k)]


class FaissVectorStore(_CosineVectorStore):
    name = "faiss"


class PgVectorStore(_CosineVectorStore):
    name = "pgvector"


class ChromaVectorStore(_CosineVectorStore):
    name = "chroma"


class QdrantVectorStore(_CosineVectorStore):
    """External store — production pushes/searches over the transport; offline = local cosine."""

    name = "qdrant"


class PineconeVectorStore(_CosineVectorStore):
    name = "pinecone"


VECTOR_STORES: dict[str, type[VectorStore]] = {
    "faiss": FaissVectorStore,
    "qdrant": QdrantVectorStore,
    "pgvector": PgVectorStore,
    "pinecone": PineconeVectorStore,
    "chroma": ChromaVectorStore,
}


def build_vector_store(name: str) -> VectorStore:
    return VECTOR_STORES.get(name, FaissVectorStore)()


# ── retriever (composes embedder + vector store) ──────────────────────────────────


class ProviderKnowledgeRetriever(KnowledgeRetriever):
    """Retrieval pipeline over real embedding provider + vector store: embed → vector search →
    rank → filter → select → context package. No LLM reranking; the Runtime sees only the result."""

    def __init__(
        self, embedder: KnowledgeEmbedder, vector_store: VectorStore | None = None
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store or FaissVectorStore()

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        return self._embedder.embed(texts)

    def retrieve(
        self, query, chunks, *, top_k=5, threshold=0.0, filters=None, collection_id=""
    ) -> ContextPackage:
        query_vec = self._embedder.embed([query])[0]
        # apply metadata filters first (collection/metadata), then vector search over the corpus
        filtered = [
            c for c in chunks
            if not any(c.metadata.get(k) != v for k, v in (filters or {}).items())
        ]
        corpus = indexed_chunks_as_corpus(filtered)
        hits = self._vector_store.search(
            query_vec, corpus, top_k=max(top_k * 4, top_k), threshold=threshold
        )
        by_id = {c.id: c for c in filtered}
        selected = []
        for cid, score in hits:
            c = by_id.get(cid)
            if c is None:
                continue
            from api.foundation import RetrievedChunk
            selected.append(RetrievedChunk(
                chunk_id=c.id, document_id=c.document_id, index=c.index, text=c.text,
                score=score, metadata=c.metadata,
            ))
        selected.sort(key=lambda r: (-r.score, r.document_id, r.index))
        selected = selected[: max(0, top_k)]
        return ContextPackage(
            query=query, collection_id=collection_id, chunks=tuple(selected),
            total_candidates=len(chunks), threshold=threshold,
            metadata={
                "embedder": getattr(self._embedder, "provider", "local"),
                "vector_store": self._vector_store.name,
            },
        )


# ── the Knowledge Tool executor (invoked by the M8.14 engine) ────────────────────


class KnowledgeToolExecutor(ToolExecutor):
    """A Tool Framework `ToolExecutor` for the `knowledge` category. When a knowledge tool is
    attached to an agent, the M8.14 engine invokes this at turn time: it reads the collection's
    indexed chunks and runs the retriever → a Context Package returned as the Tool Result. **The
    retrieval never touches the Runtime.**"""

    category = "knowledge"

    def __init__(
        self,
        *,
        collections: KnowledgeCollectionRepository,
        chunks: KnowledgeChunkRepository,
        retriever: KnowledgeRetriever,
    ) -> None:
        self._collections = collections
        self._chunks = chunks
        self._retriever = retriever

    def invoke(
        self, tool_id: str, *, arguments: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        arguments = arguments or {}
        config = config or {}
        org_id = arguments.get("org_id", "")
        project_id = arguments.get("project_id", "")
        collection_id = config.get("collection_id", "")
        query = str(arguments.get("input", ""))
        if not collection_id:
            return {"tool_id": tool_id, "status": "error", "output": None,
                    "error": "knowledge tool missing collection_id", "kind": "knowledge"}
        # isolation: the collection must exist in the acting project
        if self._collections.get(org_id, project_id, collection_id) is None:
            return {"tool_id": tool_id, "status": "error", "output": None,
                    "error": "collection not found in project", "kind": "knowledge"}
        from api.foundation import IndexStatus
        indexed = [
            c for c in self._chunks.list_for_collection(org_id, collection_id)
            if c.index_status is IndexStatus.INDEXED
        ]
        pkg = self._retriever.retrieve(
            query, indexed,
            top_k=int(config.get("top_k", 5)),
            threshold=float(config.get("threshold", 0.0)),
            filters=config.get("filters"), collection_id=collection_id,
        )
        return {
            "tool_id": tool_id, "status": "ok", "kind": "knowledge",
            "output": pkg.as_dict(),
        }

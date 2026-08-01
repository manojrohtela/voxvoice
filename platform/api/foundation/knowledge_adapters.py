"""Knowledge retrieval adapter seams (M8.15) — interfaces + deterministic default.

The Knowledge Tool retrieves through two provider-independent seams the `api.adapters` layer
implements: a `KnowledgeEmbedder` (embedding provider — OpenAI/Gemini/Voyage/Cohere/Local) and a
`VectorStore` (FAISS/Qdrant/PgVector/Pinecone/Chroma). `KnowledgeRetriever` composes them into the
retrieval pipeline (embed → search → rank → filter → select → context package). The Foundation
defines the seams + a **deterministic default** (`MockKnowledgeRetriever`, a hash embedder + cosine
search) so the in-memory Foundation retrieves offline with no providers wired. **No provider logic
here; no LLM reranking; the Runtime never sees any of this — only the Tool Result.**
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod

from .knowledge_domain import ContextPackage, KnowledgeChunk, RetrievedChunk

EMBED_DIM = 32


def hash_embed(text: str, *, dim: int = EMBED_DIM) -> tuple[float, ...]:
    """A deterministic, provider-free bag-of-hashed-tokens embedding (normalized). Similar text
    → similar vectors, so cosine similarity is meaningful offline. This is what the default
    retriever + the mock provider transport use in place of a real embedding API."""
    vec = [0.0] * dim
    for token in text.lower().split():
        h = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return tuple(v / norm for v in vec)


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=False))


class KnowledgeEmbedder(ABC):
    """Embedding-provider seam (provider-independent). Real providers call their API via the
    adapter transport; the default hashes deterministically."""

    provider = "local"

    @abstractmethod
    def embed(self, texts: list[str]) -> list[tuple[float, ...]]: ...


class VectorStore(ABC):
    """Vector-store seam. `search` ranks a corpus of (id, vector, payload) by similarity to the
    query vector, applying threshold + top_k. Real stores (FAISS/Qdrant/…) live in adapters."""

    name = "memory"

    @abstractmethod
    def search(
        self, query: tuple[float, ...], corpus: list[tuple[str, tuple[float, ...], dict]],
        *, top_k: int, threshold: float,
    ) -> list[tuple[str, float]]: ...


class KnowledgeRetriever(ABC):
    """The retrieval pipeline seam. `embed` generates chunk embeddings (the indexing step);
    `retrieve` runs the full pipeline over a collection's indexed chunks → a `ContextPackage`."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[tuple[float, ...]]: ...

    @abstractmethod
    def retrieve(
        self, query: str, chunks: list[KnowledgeChunk], *,
        top_k: int = 5, threshold: float = 0.0, filters: dict | None = None,
        collection_id: str = "",
    ) -> ContextPackage: ...


def rank_and_select(
    query_vec: tuple[float, ...], chunks: list[KnowledgeChunk], *,
    top_k: int, threshold: float, filters: dict | None,
) -> list[RetrievedChunk]:
    """Deterministic ranking + filtering + selection (no LLM reranking): score by cosine, apply
    metadata filters + threshold, sort by score desc (tie-break by chunk order), take top_k."""
    filters = filters or {}
    scored: list[RetrievedChunk] = []
    for c in chunks:
        if any(c.metadata.get(k) != v for k, v in filters.items()):
            continue
        score = cosine(query_vec, c.embedding)
        if score < threshold:
            continue
        scored.append(RetrievedChunk(
            chunk_id=c.id, document_id=c.document_id, index=c.index, text=c.text,
            score=score, metadata=c.metadata,
        ))
    scored.sort(key=lambda r: (-r.score, r.document_id, r.index))
    return scored[: max(0, top_k)]


class MockKnowledgeRetriever(KnowledgeRetriever):
    """The deterministic default retriever — a hash embedder + cosine ranking. Offline, no
    providers. Used when no real retriever is injected (so `Foundation.default()` retrieves)."""

    provider = "local"

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [hash_embed(t) for t in texts]

    def retrieve(
        self, query: str, chunks: list[KnowledgeChunk], *,
        top_k: int = 5, threshold: float = 0.0, filters: dict | None = None,
        collection_id: str = "",
    ) -> ContextPackage:
        query_vec = hash_embed(query)
        selected = rank_and_select(
            query_vec, chunks, top_k=top_k, threshold=threshold, filters=filters
        )
        return ContextPackage(
            query=query, collection_id=collection_id, chunks=tuple(selected),
            total_candidates=len(chunks), threshold=threshold,
            metadata={"embedder": self.provider, "vector_store": "memory"},
        )


class InMemoryVectorStore(VectorStore):
    """The deterministic default vector store — brute-force cosine over the provided corpus."""

    name = "memory"

    def search(self, query, corpus, *, top_k, threshold):
        scored = [(cid, cosine(query, vec)) for cid, vec, _ in corpus]
        scored = [(cid, s) for cid, s in scored if s >= threshold]
        scored.sort(key=lambda t: -t[1])
        return scored[: max(0, top_k)]


class HashEmbedder(KnowledgeEmbedder):
    """The deterministic default embedder (bag-of-hashed-tokens)."""

    provider = "local"

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [hash_embed(t) for t in texts]


def build_context_package(
    query: str, retriever: KnowledgeRetriever, chunks: list[KnowledgeChunk], *,
    top_k: int, threshold: float, filters: dict | None, collection_id: str,
) -> ContextPackage:
    """Shared helper so the Foundation search service and the adapters' Knowledge Tool executor
    run the identical pipeline over the same injected retriever."""
    return retriever.retrieve(
        query, chunks, top_k=top_k, threshold=threshold, filters=filters,
        collection_id=collection_id,
    )


def indexed_chunks_as_corpus(chunks: list[KnowledgeChunk]) -> list[tuple[str, tuple, dict]]:
    """Adapt indexed chunks to the `VectorStore.search` corpus shape."""
    return [(c.id, c.embedding, dict(c.metadata)) for c in chunks]

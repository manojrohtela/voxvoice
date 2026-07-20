"""Knowledge Runtime extension points (M5.2B-2) — interfaces only.

The seams where *intelligence the Knowledge Runtime must never author itself* plugs in.
The runtime/engine call these ports **only if one is injected**; by default none is, and
behaviour stays deterministic, AI-free, and provider-neutral. Implementations (a
cross-encoder reranker, an LLM summarizer, a PII redactor, a GraphRAG retriever, a
multi-vector store) land outside this package in later milestones — the Knowledge Runtime
never calls a model or a vendor SDK directly.

No implementations here (non-goal). Neutral DTOs only. Crucially, these interfaces make
GraphRAG / MultiVector / Federated Knowledge land as **new retrieval strategies behind
the planner** without any change to the planner, retrieval engine, ranking engine, or
repositories.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from .domain import Chunk, Document
from .results import KnowledgeHit


# ── retrieval-quality extensions (applied around the deterministic core) ──────
class Reranker(ABC):
    """Re-order ranked hits with a stronger (possibly learned) signal. Applied *after*
    deterministic ranking; must itself be deterministic for a given model."""

    @abstractmethod
    def rerank(self, query_text: str, hits: Sequence[KnowledgeHit]) -> Sequence[KnowledgeHit]: ...


class Summarizer(ABC):
    """Fold many chunks into one synthesized answer/summary. Typically LLM-backed — hence
    a port, never called by the runtime's own logic."""

    @abstractmethod
    def summarize(self, query_text: str, hits: Sequence[KnowledgeHit]) -> str: ...


class Redactor(ABC):
    """Remove/mask sensitive spans from content (privacy policy) before it is persisted
    or returned in a citation."""

    @abstractmethod
    def redact(self, content: str) -> str: ...


class Compressor(ABC):
    """Shrink a chunk's content (lossy or lossless) for cold storage or context packing."""

    @abstractmethod
    def compress(self, content: str) -> str: ...

    @abstractmethod
    def decompress(self, content: str) -> str: ...


# ── retrieval-source extensions (new strategies land behind the planner) ──────
class KnowledgeSource(ABC):
    """A pluggable external corpus (federated / remote knowledge). Returns candidate hits
    for a query; the retrieval engine merges + re-ranks them alongside local candidates.
    Federated Knowledge lands as an implementation of this — no engine redesign."""

    @abstractmethod
    def fetch(self, query_text: str, *, limit: int) -> Sequence[KnowledgeHit]: ...


class GraphStore(ABC):
    """A knowledge-graph store (entities + relations derived from documents). Write path
    for GraphRAG; populated from chunks. No implementation in this milestone."""

    @abstractmethod
    def upsert(self, document: Document, chunks: Sequence[Chunk]) -> None: ...

    @abstractmethod
    def remove(self, document_id: str) -> None: ...


class GraphRetriever(ABC):
    """Graph-expansion retrieval (GraphRAG). Given seed chunk ids, walks the graph to
    related chunks. Lands as a new `RetrievalStrategy.GRAPH` behind the planner."""

    @abstractmethod
    def expand(self, seed_chunk_ids: Sequence[str], *, hops: int) -> Sequence[str]: ...


class MultiVectorRetriever(ABC):
    """Multi-vector retrieval (e.g. ColBERT-style late interaction, or per-chunk multiple
    embeddings). Lands as a new `RetrievalStrategy.MULTI_VECTOR` behind the planner."""

    @abstractmethod
    def search(self, query_text: str, *, k: int) -> Sequence[tuple[str, float]]: ...

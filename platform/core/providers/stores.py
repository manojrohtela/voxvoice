"""Storage capability contracts (M5.0 — interfaces only, no implementations).

Provider-neutral persistence seams the AI runtime modules bind to (Memory, Knowledge,
Workflow durable state). Adapters (S3/Postgres/pgvector/Pinecone/…) implement these
in `providers/` and are resolved via the Provider Registry — nothing here couples to
a database. No implementation lands at M5.0 (non-goal).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class StoreCapabilities:
    durable: bool = False
    transactional: bool = False
    queryable: bool = False


class ObjectStore(ABC):
    """Opaque blob storage keyed by id (audio, artifacts, exports)."""

    name: str
    capabilities: StoreCapabilities

    @abstractmethod
    async def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None: ...
    @abstractmethod
    async def get(self, key: str) -> bytes | None: ...
    @abstractmethod
    async def delete(self, key: str) -> None: ...


class DocumentStore(ABC):
    """Structured document storage (JSON-ish records) with id lookup + simple query."""

    name: str
    capabilities: StoreCapabilities

    @abstractmethod
    async def upsert(self, collection: str, doc_id: str, document: Mapping[str, Any]) -> None: ...
    @abstractmethod
    async def get(self, collection: str, doc_id: str) -> Mapping[str, Any] | None: ...
    @abstractmethod
    async def query(
        self, collection: str, filter: Mapping[str, Any]
    ) -> Sequence[Mapping[str, Any]]: ...
    @abstractmethod
    async def delete(self, collection: str, doc_id: str) -> None: ...


class MetadataStore(ABC):
    """Small, fast key/value metadata (counters, cursors, config)."""

    name: str

    @abstractmethod
    async def get(self, key: str) -> str | None: ...
    @abstractmethod
    async def set(self, key: str, value: str) -> None: ...
    @abstractmethod
    async def delete(self, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class VectorMatch:
    id: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore(ABC):
    """Similarity search over embedding vectors (semantic memory + knowledge)."""

    name: str
    capabilities: StoreCapabilities

    @abstractmethod
    async def upsert(
        self, id: str, vector: Sequence[float], *, metadata: Mapping[str, Any] | None = None
    ) -> None: ...
    @abstractmethod
    async def search(self, vector: Sequence[float], *, k: int = 10) -> Sequence[VectorMatch]: ...
    @abstractmethod
    async def remove(self, id: str) -> None: ...


class Embedder(ABC):
    """Text → embedding vector. A deterministic local embedder or a vendor model —
    both behind this one contract."""

    name: str
    dimensions: int

    @abstractmethod
    def embed(self, text: str) -> Sequence[float]: ...

"""In-memory storage adapters (M5.1B-1, development only).

Deterministic dict-backed implementations of `DocumentStore`, `VectorStore`, and
`MetadataStore`. Every read returns a deep copy so stored state can never be mutated
through an aliased reference (determinism + isolation). Async signatures satisfy the
contracts but never actually suspend — the sync driver (`memory._run`) relies on that.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from typing import Any

from core.providers.stores import (
    DocumentStore,
    MetadataStore,
    StoreCapabilities,
    VectorMatch,
    VectorStore,
)


class InMemoryDocumentStore(DocumentStore):
    """Dict-of-dicts keyed by (collection, doc_id). Query = exact-match on top-level
    keys, in insertion order (the repository imposes any further ordering)."""

    def __init__(self) -> None:
        self.name = "in_memory_document"
        self.capabilities = StoreCapabilities(durable=False, transactional=False, queryable=True)
        self._data: dict[str, dict[str, dict[str, Any]]] = {}

    async def upsert(self, collection: str, doc_id: str, document: Mapping[str, Any]) -> None:
        self._data.setdefault(collection, {})[doc_id] = copy.deepcopy(dict(document))

    async def get(self, collection: str, doc_id: str) -> Mapping[str, Any] | None:
        doc = self._data.get(collection, {}).get(doc_id)
        return copy.deepcopy(doc) if doc is not None else None

    async def query(
        self, collection: str, filter: Mapping[str, Any]
    ) -> Sequence[Mapping[str, Any]]:
        out: list[Mapping[str, Any]] = []
        for doc in self._data.get(collection, {}).values():
            if all(doc.get(k) == v for k, v in filter.items()):
                out.append(copy.deepcopy(doc))
        return out

    async def delete(self, collection: str, doc_id: str) -> None:
        self._data.get(collection, {}).pop(doc_id, None)  # idempotent


class InMemoryVectorStore(VectorStore):
    """Cosine-similarity search over stored vectors. NOTE: `search` implements the
    contract so the adapter is a valid `VectorStore`, but the Memory Runtime does not
    call it in M5.1B-1 — semantic retrieval is a later milestone (non-goal here)."""

    def __init__(self) -> None:
        self.name = "in_memory_vector"
        self.capabilities = StoreCapabilities(durable=False, transactional=False, queryable=True)
        self._vectors: dict[str, tuple[tuple[float, ...], dict[str, Any]]] = {}

    async def upsert(
        self, id: str, vector: Sequence[float], *, metadata: Mapping[str, Any] | None = None
    ) -> None:
        self._vectors[id] = (tuple(vector), dict(metadata or {}))

    async def search(self, vector: Sequence[float], *, k: int = 10) -> Sequence[VectorMatch]:
        q = tuple(vector)
        scored = [
            VectorMatch(id=vid, score=_cosine(q, vec), metadata=copy.deepcopy(meta))
            for vid, (vec, meta) in self._vectors.items()
        ]
        # deterministic order: score desc, then id asc to break ties
        scored.sort(key=lambda m: (-m.score, m.id))
        return scored[: max(0, k)]

    async def remove(self, id: str) -> None:
        self._vectors.pop(id, None)  # idempotent


class InMemoryMetadataStore(MetadataStore):
    """Flat string key/value map (counters, cursors, per-tier config)."""

    def __init__(self) -> None:
        self.name = "in_memory_metadata"
        self._kv: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(self, key: str, value: str) -> None:
        self._kv[key] = value

    async def delete(self, key: str) -> None:
        self._kv.pop(key, None)  # idempotent


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)

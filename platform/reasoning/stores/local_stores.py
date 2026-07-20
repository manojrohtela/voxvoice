"""In-memory storage adapters (M5.5B-1, development only).

Deterministic dict-backed implementations of the two `core.providers.stores` contracts the
Reasoning Runtime binds to for **temporary** response + session storage: `DocumentStore`
(response + session records) and `MetadataStore` (cursors). Every read returns a deep copy
(isolation). Async signatures satisfy the contracts but never actually suspend — the sync
driver relies on that.

Self-contained (Reasoning imports no sibling runtime); extracting a shared local-adapter set
to `providers/storage/` is a future additive refactor.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from core.providers.stores import DocumentStore, MetadataStore, StoreCapabilities


class InMemoryDocumentStore(DocumentStore):
    """Dict-of-dicts keyed by (collection, doc_id). Query = exact-match on top-level keys."""

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


class InMemoryMetadataStore(MetadataStore):
    """Flat string key/value map (cursors, counters, pointers)."""

    def __init__(self) -> None:
        self.name = "in_memory_metadata"
        self._kv: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(self, key: str, value: str) -> None:
        self._kv[key] = value

    async def delete(self, key: str) -> None:
        self._kv.pop(key, None)  # idempotent

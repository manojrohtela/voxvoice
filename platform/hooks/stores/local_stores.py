"""In-memory storage adapters (M6.3B, development only).

Deterministic dict-backed implementation of the `core.providers.stores.DocumentStore`
contract the Hook Framework binds to for temporary registration/replay/trace
storage. Every read returns a deep copy (isolation). Self-contained — duplicated
from the identical pattern in `tool/stores/local_stores.py` / `skill/stores/
local_stores.py`, not imported.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from core.providers.stores import DocumentStore, StoreCapabilities


class InMemoryDocumentStore(DocumentStore):
    """Dict-of-dicts keyed by (collection, doc_id). Query = exact-match on
    top-level keys."""

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

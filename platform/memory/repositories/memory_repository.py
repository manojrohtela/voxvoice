"""MemoryRepository — records over a `DocumentStore` (M5.1B-1).

Translates `MemoryRecord` ↔ document (via `MemorySerializer`) and evaluates a
`MemoryFilter` deterministically. Exact-attribute matching only — **no ranking, no
similarity, no keyword search** (the Retrieval Engine, M5.1B-2, owns that). Depends on
the `DocumentStore` *contract*, never a concrete provider.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.providers.stores import DocumentStore

from ..cache import MemoryCache
from ..domain import MemoryFilter, MemoryId, MemoryRecord, MemoryScope
from ..serializer import MemorySerializer


class MemoryRepository:
    def __init__(
        self,
        store: DocumentStore,
        serializer: MemorySerializer,
        *,
        collection: str = "memories",
        cache: MemoryCache | None = None,
    ) -> None:
        self._store = store
        self._serializer = serializer
        self._collection = collection
        self._cache = cache  # optional hot-object cache (behind this layer)

    async def add(self, record: MemoryRecord) -> None:
        doc = self._serializer.to_document(record)
        await self._store.upsert(self._collection, record.id.value, doc)
        if self._cache is not None:
            self._cache.put(record.id.value, doc)  # write-through

    async def get(self, memory_id: MemoryId) -> MemoryRecord | None:
        if self._cache is not None:
            cached = self._cache.get(memory_id.value)  # read-through
            if cached is not None:
                return self._serializer.from_document(cached)
        doc = await self._store.get(self._collection, memory_id.value)
        if doc is None:
            return None
        if self._cache is not None:
            self._cache.put(memory_id.value, doc)
        return self._serializer.from_document(doc)

    async def update(self, record: MemoryRecord) -> None:
        doc = self._serializer.to_document(record)
        await self._store.upsert(self._collection, record.id.value, doc)
        if self._cache is not None:
            self._cache.put(record.id.value, doc)  # write-through

    async def remove(self, memory_id: MemoryId) -> None:
        await self._store.delete(self._collection, memory_id.value)
        if self._cache is not None:
            self._cache.invalidate(memory_id.value)

    async def query(self, filter: MemoryFilter) -> Sequence[MemoryRecord]:
        """Fetch records matching `filter`. Store-level equality does the coarse cut
        (tier/state); scope/tags/limit are applied here deterministically."""
        coarse: dict[str, object] = {}
        if filter.tier is not None:
            coarse["tier"] = filter.tier.value
        if len(filter.states) == 1:
            coarse["state"] = filter.states[0].value

        docs = await self._store.query(self._collection, coarse)
        records = [self._serializer.from_document(d) for d in docs]

        result = [r for r in records if self._matches(r, filter)]
        # deterministic order: newest first, id as tie-break
        result.sort(key=lambda r: (-r.metadata.created_at, r.id.value))
        if filter.limit is not None:
            result = result[: max(0, filter.limit)]
        return result

    async def count(self, filter: MemoryFilter) -> int:
        return len(await self.query(filter))

    # ── deterministic in-memory filtering (what the store can't express) ─────
    def _matches(self, record: MemoryRecord, filter: MemoryFilter) -> bool:
        if filter.states and record.state not in filter.states:
            return False
        if filter.type is not None and record.metadata.type is not filter.type:
            return False
        if filter.agent_id is not None and record.metadata.agent_id != filter.agent_id:
            return False
        if filter.scope is not None and not _scope_matches(record.scope, filter.scope):
            return False
        if filter.tags and not set(filter.tags).issubset(set(record.metadata.tags)):
            return False
        return True


def _scope_matches(record_scope: MemoryScope, wanted: MemoryScope) -> bool:
    """A record matches iff it agrees on every scope key the filter *specifies*
    (a `None` key in the filter means "don't care"). This is the isolation cut."""
    for got, want in (
        (record_scope.org_id, wanted.org_id),
        (record_scope.user_id, wanted.user_id),
        (record_scope.conversation_id, wanted.conversation_id),
        (record_scope.session_id, wanted.session_id),
    ):
        if want is not None and got != want:
            return False
    return True

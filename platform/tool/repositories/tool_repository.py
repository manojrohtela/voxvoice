"""ToolRepository — the temporary-storage anti-corruption boundary (M6.1B-1).

The **only** code that binds the `DocumentStore` contract for result records.
Translates `ToolResult` ↔ storage documents via the serializer. Storage is
**temporary only** (short-TTL working set) — durable persistence belongs to a
future Workflow Runtime's durable exception (M6.3), never this runtime's. No
orchestration, no adapter access — pure persistence. The runtime never touches a
store directly.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.providers.stores import DocumentStore

from ..config import CollectionsConfig
from ..domain import ToolResult
from ..serializer import ToolSerializer


class ToolRepository:
    def __init__(
        self,
        store: DocumentStore,
        serializer: ToolSerializer,
        *,
        collections: CollectionsConfig | None = None,
    ) -> None:
        self._store = store
        self._serializer = serializer
        self._c = collections or CollectionsConfig()

    async def add_result(self, result: ToolResult) -> None:
        await self._store.upsert(
            self._c.results, result.request_id, self._serializer.result_to_document(result)
        )

    async def get_result(self, request_id: str) -> ToolResult | None:
        d = await self._store.get(self._c.results, request_id)
        return self._serializer.result_from_document(d) if d is not None else None

    async def update_result(self, result: ToolResult) -> None:
        await self.add_result(result)

    async def remove_result(self, request_id: str) -> None:
        await self._store.delete(self._c.results, request_id)  # idempotent

    async def results_for_session(self, session_id: str) -> Sequence[ToolResult]:
        rows = await self._store.query(self._c.results, {"session_id": session_id})
        results = [self._serializer.result_from_document(r) for r in rows]
        results.sort(key=lambda r: (r.created_at, r.request_id))
        return results

    async def all_results(self) -> Sequence[ToolResult]:
        rows = await self._store.query(self._c.results, {})
        return [self._serializer.result_from_document(r) for r in rows]

    async def result_count(self) -> int:
        return len(await self._store.query(self._c.results, {}))

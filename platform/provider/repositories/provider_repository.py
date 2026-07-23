"""ProviderRepository — the temporary-storage anti-corruption boundary (M5.6B-1).

The **only** code that binds the store contracts (`DocumentStore` for response + session
records, `MetadataStore` for cursors). Translates `ProviderResponse`/`ProviderSession` ↔
storage documents via the serializer. Storage is **temporary only** (short-TTL working set) —
durable persistence is the Memory Runtime's, never this runtime's. No orchestration, no
adapter access — pure persistence. The runtime never touches a store directly.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..config import CollectionsConfig
from ..domain import ProviderResponse, ProviderSession
from ..serializer import ProviderSerializer
from ..stores import ProviderStore


class ProviderRepository:
    def __init__(
        self,
        store: ProviderStore,
        serializer: ProviderSerializer,
        *,
        collections: CollectionsConfig | None = None,
    ) -> None:
        self._store = store.document_store
        self._meta = store.metadata_store
        self._serializer = serializer
        self._c = collections or CollectionsConfig()

    # ── responses (temporary) ──────────────────────────────────────────────────
    async def add_response(self, response: ProviderResponse) -> None:
        await self._store.upsert(
            self._c.responses, response.id, self._serializer.response_to_document(response)
        )

    async def get_response(self, response_id: str) -> ProviderResponse | None:
        d = await self._store.get(self._c.responses, response_id)
        return self._serializer.response_from_document(d) if d is not None else None

    async def update_response(self, response: ProviderResponse) -> None:
        await self.add_response(response)

    async def remove_response(self, response_id: str) -> None:
        await self._store.delete(self._c.responses, response_id)  # idempotent

    async def responses_for_session(self, session_id: str) -> Sequence[ProviderResponse]:
        rows = await self._store.query(self._c.responses, {"session_id": session_id})
        responses = [self._serializer.response_from_document(r) for r in rows]
        responses.sort(key=lambda r: (r.created_at, r.id))
        return responses

    async def all_responses(self) -> Sequence[ProviderResponse]:
        rows = await self._store.query(self._c.responses, {})
        return [self._serializer.response_from_document(r) for r in rows]

    async def response_count(self) -> int:
        return len(await self._store.query(self._c.responses, {}))

    # ── sessions (temporary working set) ──────────────────────────────────────
    async def add_session(self, session: ProviderSession) -> None:
        await self._store.upsert(
            self._c.sessions, session.session_id, self._serializer.session_to_document(session)
        )

    async def get_session(self, session_id: str) -> ProviderSession | None:
        d = await self._store.get(self._c.sessions, session_id)
        return self._serializer.session_from_document(d) if d is not None else None

    async def update_session(self, session: ProviderSession) -> None:
        await self.add_session(session)

    async def remove_session(self, session_id: str) -> None:
        await self._store.delete(self._c.sessions, session_id)  # idempotent

    async def session_count(self) -> int:
        return len(await self._store.query(self._c.sessions, {}))

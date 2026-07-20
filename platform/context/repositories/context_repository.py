"""ContextRepository — the temporary-storage anti-corruption boundary (M5.3B-1).

The **only** code that binds the store contracts (`DocumentStore` for package + session
records, `MetadataStore` for cursors). Translates `ContextPackage`/`ContextSession` ↔
storage documents via the serializer. Storage is **temporary only** (short-TTL working
set) — long-term persistence is the Memory Runtime's, never Context's. No ranking, no
budgeting — pure persistence. The runtime never touches a store directly.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..config import CollectionsConfig
from ..domain import ContextPackage, ContextSession
from ..serializer import ContextSerializer
from ..stores import ContextStore


class ContextRepository:
    def __init__(
        self,
        store: ContextStore,
        serializer: ContextSerializer,
        *,
        collections: CollectionsConfig | None = None,
    ) -> None:
        self._store = store.document_store
        self._meta = store.metadata_store
        self._serializer = serializer
        self._c = collections or CollectionsConfig()

    # ── packages (temporary) ──────────────────────────────────────────────────
    async def add_package(self, package: ContextPackage) -> None:
        await self._store.upsert(
            self._c.packages, package.id, self._serializer.package_to_document(package)
        )

    async def get_package(self, package_id: str) -> ContextPackage | None:
        d = await self._store.get(self._c.packages, package_id)
        return self._serializer.package_from_document(d) if d is not None else None

    async def update_package(self, package: ContextPackage) -> None:
        await self.add_package(package)

    async def remove_package(self, package_id: str) -> None:
        await self._store.delete(self._c.packages, package_id)  # idempotent

    async def packages_for_session(self, session_id: str) -> Sequence[ContextPackage]:
        rows = await self._store.query(self._c.packages, {"session_id": session_id})
        packages = [self._serializer.package_from_document(r) for r in rows]
        packages.sort(key=lambda p: (p.created_at, p.id))
        return packages

    async def all_packages(self) -> Sequence[ContextPackage]:
        rows = await self._store.query(self._c.packages, {})
        return [self._serializer.package_from_document(r) for r in rows]

    async def package_count(self) -> int:
        return len(await self._store.query(self._c.packages, {}))

    # ── sessions (temporary working set) ──────────────────────────────────────
    async def add_session(self, session: ContextSession) -> None:
        await self._store.upsert(
            self._c.sessions, session.session_id, self._serializer.session_to_document(session)
        )

    async def get_session(self, session_id: str) -> ContextSession | None:
        d = await self._store.get(self._c.sessions, session_id)
        return self._serializer.session_from_document(d) if d is not None else None

    async def update_session(self, session: ContextSession) -> None:
        await self.add_session(session)

    async def remove_session(self, session_id: str) -> None:
        await self._store.delete(self._c.sessions, session_id)  # idempotent

    async def session_count(self) -> int:
        return len(await self._store.query(self._c.sessions, {}))

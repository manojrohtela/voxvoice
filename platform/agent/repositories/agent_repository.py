"""AgentRepository — the temporary registration-record boundary (M6.5B-1).

Storage is **temporary only** — a registration-record log, not the
authoritative catalog (that is `AgentRegistry`, an in-memory dict that never
expires on its own; mirrors `WorkflowRepository`'s/`SkillRepository`'s own
convention exactly). Unlike Workflow Runtime, Agent Runtime's own
goal-execution repository (Bundle 3) is **not** a durable-repository
exception (§10.3 of the M6.5A design) — a goal's state is fully
reconstructible from its content-hash-chained decision history plus the
delegated workflow's own durable snapshot — so this temporary convention
will keep applying there too.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.providers.stores import DocumentStore

from ..config import AgentCollectionsConfig
from ..domain import AgentDescriptor
from ..ids import registration_id_for
from ..serializer import AgentSerializer


class AgentRepository:
    def __init__(
        self,
        store: DocumentStore,
        serializer: AgentSerializer,
        *,
        collections: AgentCollectionsConfig | None = None,
    ) -> None:
        self._store = store
        self._serializer = serializer
        self._c = collections or AgentCollectionsConfig()

    async def add_registration(self, descriptor: AgentDescriptor) -> str:
        reg_id = registration_id_for(
            descriptor.manifest.namespace, descriptor.manifest.id, descriptor.manifest.version,
            descriptor.registered_at,
        )
        await self._store.upsert(
            self._c.registrations, reg_id, self._serializer.descriptor_to_document(descriptor)
        )
        return reg_id

    async def get_registration(self, registration_id: str) -> AgentDescriptor | None:
        d = await self._store.get(self._c.registrations, registration_id)
        return self._serializer.descriptor_from_document(d) if d is not None else None

    async def all_registrations(self) -> Sequence[AgentDescriptor]:
        rows = await self._store.query(self._c.registrations, {})
        descriptors = [self._serializer.descriptor_from_document(r) for r in rows]
        descriptors.sort(key=lambda d: (d.registered_at, d.qualified_name))
        return descriptors

    async def registration_count(self) -> int:
        return len(await self._store.query(self._c.registrations, {}))

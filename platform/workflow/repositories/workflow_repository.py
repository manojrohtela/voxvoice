"""WorkflowRepository — the temporary registration-record boundary
(M6.4B-1).

Storage is **temporary only** — a registration-record log, not the
authoritative catalog (that is `WorkflowRegistry`, an in-memory dict that
never expires on its own; mirrors `SkillRepository`'s/`HookRepository`'s own
convention exactly). Note: this is distinct from the *durable* workflow-
execution-state store the M6.4A design reserves as the platform's one
deliberate exception (§7) — that store is Bundle 2 scope (checkpoints/
snapshots), not this bundle's registration log.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.providers.stores import DocumentStore

from ..config import WorkflowCollectionsConfig
from ..domain import WorkflowDescriptor
from ..ids import registration_id_for
from ..serializer import WorkflowSerializer


class WorkflowRepository:
    def __init__(
        self,
        store: DocumentStore,
        serializer: WorkflowSerializer,
        *,
        collections: WorkflowCollectionsConfig | None = None,
    ) -> None:
        self._store = store
        self._serializer = serializer
        self._c = collections or WorkflowCollectionsConfig()

    async def add_registration(self, descriptor: WorkflowDescriptor) -> str:
        reg_id = registration_id_for(
            descriptor.manifest.namespace, descriptor.manifest.id, descriptor.manifest.version,
            descriptor.registered_at,
        )
        await self._store.upsert(
            self._c.registrations, reg_id, self._serializer.descriptor_to_document(descriptor)
        )
        return reg_id

    async def get_registration(self, registration_id: str) -> WorkflowDescriptor | None:
        d = await self._store.get(self._c.registrations, registration_id)
        return self._serializer.descriptor_from_document(d) if d is not None else None

    async def all_registrations(self) -> Sequence[WorkflowDescriptor]:
        rows = await self._store.query(self._c.registrations, {})
        descriptors = [self._serializer.descriptor_from_document(r) for r in rows]
        descriptors.sort(key=lambda d: (d.registered_at, d.qualified_name))
        return descriptors

    async def registration_count(self) -> int:
        return len(await self._store.query(self._c.registrations, {}))

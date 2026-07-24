"""SkillRepository — the temporary registration-record boundary (M6.2B-1).

Storage is **temporary only** — a short-TTL registration log, not the
authoritative catalog (that is `SkillRegistry`, an in-memory dict that never
expires on its own; see the M6.2A design's Risks §18 for why conflating the
two would be a mistake). The runtime records one entry per successful
registration here for audit/statistics purposes; it never reads from this
repository to answer "is this skill registered" — that question is always
answered by the registry itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.providers.stores import DocumentStore

from ..config import CollectionsConfig
from ..domain import SkillDescriptor
from ..ids import registration_id_for
from ..serializer import SkillSerializer


class SkillRepository:
    def __init__(
        self,
        store: DocumentStore,
        serializer: SkillSerializer,
        *,
        collections: CollectionsConfig | None = None,
    ) -> None:
        self._store = store
        self._serializer = serializer
        self._c = collections or CollectionsConfig()

    async def add_registration(self, descriptor: SkillDescriptor) -> str:
        reg_id = registration_id_for(
            descriptor.manifest.namespace, descriptor.manifest.id, descriptor.manifest.version,
            descriptor.registered_at,
        )
        await self._store.upsert(
            self._c.registrations, reg_id, self._serializer.descriptor_to_document(descriptor)
        )
        return reg_id

    async def get_registration(self, registration_id: str) -> SkillDescriptor | None:
        d = await self._store.get(self._c.registrations, registration_id)
        return self._serializer.descriptor_from_document(d) if d is not None else None

    async def all_registrations(self) -> Sequence[SkillDescriptor]:
        rows = await self._store.query(self._c.registrations, {})
        descriptors = [self._serializer.descriptor_from_document(r) for r in rows]
        descriptors.sort(key=lambda d: (d.registered_at, d.qualified_name))
        return descriptors

    async def registration_count(self) -> int:
        return len(await self._store.query(self._c.registrations, {}))

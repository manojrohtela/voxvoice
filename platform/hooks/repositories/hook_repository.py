"""HookRepository — the temporary storage boundary (M6.3B).

Storage is **temporary only** — a short-TTL log, not the authoritative
catalog (that is `HookRegistry`, an in-memory dict that never expires on its
own; mirrors `SkillRepository`'s own convention exactly). Covers all three
of this bundle's temporary collections in one repository: registration
records, replay records, and execution traces.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.providers.stores import DocumentStore

from ..config import HookCollectionsConfig
from ..domain import HookDescriptor, HookExecutionTrace, HookReplayRecord
from ..ids import registration_id_for
from ..serializer import HookSerializer


class HookRepository:
    def __init__(
        self,
        store: DocumentStore,
        serializer: HookSerializer,
        *,
        collections: HookCollectionsConfig | None = None,
    ) -> None:
        self._store = store
        self._serializer = serializer
        self._c = collections or HookCollectionsConfig()

    # ── registration records ──────────────────────────────────────────────────
    async def add_registration(self, descriptor: HookDescriptor) -> str:
        reg_id = registration_id_for(
            descriptor.manifest.namespace, descriptor.manifest.id, descriptor.manifest.version,
            descriptor.registered_at,
        )
        await self._store.upsert(
            self._c.registrations, reg_id, self._serializer.descriptor_to_document(descriptor)
        )
        return reg_id

    async def all_registrations(self) -> Sequence[HookDescriptor]:
        rows = await self._store.query(self._c.registrations, {})
        descriptors = [self._serializer.descriptor_from_document(r) for r in rows]
        descriptors.sort(key=lambda d: (d.registered_at, d.qualified_name))
        return descriptors

    async def registration_count(self) -> int:
        return len(await self._store.query(self._c.registrations, {}))

    # ── replay records ───────────────────────────────────────────────────────
    async def add_replay(self, record: HookReplayRecord) -> None:
        await self._store.upsert(
            self._c.replays, record.replay_id,
            self._serializer.replay_record_to_document(record),
        )

    async def get_replay(self, replay_id: str) -> HookReplayRecord | None:
        d = await self._store.get(self._c.replays, replay_id)
        return self._serializer.replay_record_from_document(d) if d is not None else None

    async def replay_count(self) -> int:
        return len(await self._store.query(self._c.replays, {}))

    # ── execution traces ──────────────────────────────────────────────────────
    async def add_trace(self, trace: HookExecutionTrace) -> None:
        await self._store.upsert(
            self._c.traces, trace.trace_id,
            {
                "trace_id": trace.trace_id, "event_id": trace.event_id, "point": trace.point,
                "scope": trace.scope, "hook_count": trace.hook_count,
                "failure_count": trace.failure_count,
                "aggregated_outcome": trace.aggregated_outcome, "created_at": trace.created_at,
                "metadata": dict(trace.metadata),
            },
        )

    async def trace_count(self) -> int:
        return len(await self._store.query(self._c.traces, {}))

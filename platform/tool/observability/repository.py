"""ReplayRepository (M6.1B-3) — temporary storage only, mirrors `ToolRepository`/
`GovernanceRepository`. No durable persistence.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.providers.stores import DocumentStore

from .config import ObservabilityCollectionsConfig
from .domain import ReplayRecord, ToolAuditRecord
from .serializer import ObservabilitySerializer


class ReplayRepository:
    def __init__(
        self,
        store: DocumentStore,
        serializer: ObservabilitySerializer,
        *,
        collections: ObservabilityCollectionsConfig | None = None,
    ) -> None:
        self._store = store
        self._serializer = serializer
        self._c = collections or ObservabilityCollectionsConfig()

    # ── replay records ───────────────────────────────────────────────────────
    async def add_replay(self, record: ReplayRecord) -> None:
        await self._store.upsert(
            self._c.replays, record.replay_id, self._serializer.replay_record_to_document(record)
        )

    async def get_replay(self, replay_id: str) -> ReplayRecord | None:
        d = await self._store.get(self._c.replays, replay_id)
        return self._serializer.replay_record_from_document(d) if d is not None else None

    async def all_replays(self) -> Sequence[ReplayRecord]:
        rows = await self._store.query(self._c.replays, {})
        return [self._serializer.replay_record_from_document(r) for r in rows]

    async def replay_count(self) -> int:
        return len(await self._store.query(self._c.replays, {}))

    # ── audit records ─────────────────────────────────────────────────────────
    async def add_audit(self, record: ToolAuditRecord) -> None:
        await self._store.upsert(
            self._c.audits, record.audit_id, self._serializer.audit_record_to_document(record)
        )

    async def get_audit(self, audit_id: str) -> ToolAuditRecord | None:
        d = await self._store.get(self._c.audits, audit_id)
        return self._serializer.audit_record_from_document(d) if d is not None else None

    async def all_audits(self) -> Sequence[ToolAuditRecord]:
        rows = await self._store.query(self._c.audits, {})
        return [self._serializer.audit_record_from_document(r) for r in rows]

    async def audit_count(self) -> int:
        return len(await self._store.query(self._c.audits, {}))

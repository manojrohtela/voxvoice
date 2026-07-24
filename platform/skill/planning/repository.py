"""PlanningRepository (M6.2B-2) — temporary storage only, mirrors
`tool.observability.repository.ReplayRepository`. No durable persistence;
the registry (not this repository) remains authoritative for "is this skill
registered" — this repository only ever logs planning replay + trace
records.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.providers.stores import DocumentStore

from .config import PlanningCollectionsConfig
from .domain import SkillPlanningTrace, SkillPlanReplayRecord
from .serializer import PlanningSerializer


class PlanningRepository:
    def __init__(
        self,
        store: DocumentStore,
        serializer: PlanningSerializer,
        *,
        collections: PlanningCollectionsConfig | None = None,
    ) -> None:
        self._store = store
        self._serializer = serializer
        self._c = collections or PlanningCollectionsConfig()

    # ── planning replay records ──────────────────────────────────────────────
    async def add_replay(self, record: SkillPlanReplayRecord) -> None:
        await self._store.upsert(
            self._c.replays, record.replay_id,
            self._serializer.replay_record_to_document(record),
        )

    async def get_replay(self, replay_id: str) -> SkillPlanReplayRecord | None:
        d = await self._store.get(self._c.replays, replay_id)
        return self._serializer.replay_record_from_document(d) if d is not None else None

    async def all_replays(self) -> Sequence[SkillPlanReplayRecord]:
        rows = await self._store.query(self._c.replays, {})
        return [self._serializer.replay_record_from_document(r) for r in rows]

    async def replay_count(self) -> int:
        return len(await self._store.query(self._c.replays, {}))

    # ── planning traces ───────────────────────────────────────────────────────
    async def add_trace(self, trace: SkillPlanningTrace) -> None:
        await self._store.upsert(
            self._c.traces, trace.trace_id,
            {
                "trace_id": trace.trace_id, "plan_id": trace.plan_id,
                "qualified_name": trace.qualified_name, "step_count": trace.step_count,
                "capability_count": trace.capability_count, "created_at": trace.created_at,
                "metadata": dict(trace.metadata),
            },
        )

    async def trace_count(self) -> int:
        return len(await self._store.query(self._c.traces, {}))

"""MemoryRuntime — the Memory Runtime core (M5.1B-1).

A `BusConsumer` (hence a `RuntimeModule`) that implements `core.ports.MemoryPort`. It
manages memory and nothing else: it never reasons, renders prompts, or executes tools.

What lands in M5.1B-1:
- **store** — create → lifecycle-advance → persist (via the repository layer), index
  semantic tiers, and emit `MEMORY_STORED`.
- **retrieve** — a *naive*, deterministic attribute filter over the repository (no
  ranking, no similarity, no keyword search — the Retrieval Engine is M5.1B-2). Emits
  `MEMORY_RETRIEVED` (ids only, privacy-lean).
- **forget** — lifecycle-advance to DELETED, remove from both stores, emit
  `MEMORY_CLEARED`. Idempotent.

Async internals (`astore`/`aretrieve`/`aforget`) are the real path; the synchronous
`MemoryPort` methods drive them through the non-blocking sync bridge (`memory._run`).
The runtime works with or without a bus attached — the bus is for observability
(Replay records and the Gateway streams every `memory.*` event for free). Content-event
**ingestion is deferred** (M5.1B-2/3): the runtime attaches to publish only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.events import Event, EventSource, EventType
from core.module import BusConsumer
from core.ports.memory import MemoryHit, MemoryPort, MemoryQuery, MemoryScope, MemoryTier

from ._run import run_coro_sync
from .config import MemoryConfiguration
from .domain import (
    MemoryFilter,
    MemoryId,
    MemoryMetadata,
    MemoryRecord,
    MemoryResult,
    MemoryState,
    MemoryType,
)
from .lifecycle import MemoryTrigger, advance
from .repositories import MemoryRepository, MetadataRepository, VectorRepository
from .serializer import MemorySerializer


class MemoryRuntime(BusConsumer, MemoryPort):
    def __init__(
        self,
        *,
        config: MemoryConfiguration,
        memory_repo: MemoryRepository,
        vector_repo: VectorRepository,
        metadata_repo: MetadataRepository,
        serializer: MemorySerializer | None = None,
        id_factory: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> None:
        # No content-event ingestion yet → subscribe to nothing (empty filter). The
        # runtime still attaches so it can *publish* memory.* envelopes.
        super().__init__(
            name=config.name,
            source=EventSource.MEMORY,
            types=frozenset(),
            id_factory=id_factory,
            **kwargs,
        )
        self._config = config
        self._memory = memory_repo
        self._vectors = vector_repo
        self._meta = metadata_repo
        self._serializer = serializer or MemorySerializer()

    # ── MemoryPort (synchronous) — drive the async core deterministically ────
    def store(
        self, content: str, *, tier: MemoryTier, scope: MemoryScope, importance: float = 0.5
    ) -> str:
        return run_coro_sync(self.astore(content, tier=tier, scope=scope, importance=importance))

    def retrieve(self, query: MemoryQuery) -> Sequence[MemoryHit]:
        return run_coro_sync(self.aretrieve(query)).hits

    def forget(self, memory_id: str) -> None:
        return run_coro_sync(self.aforget(memory_id))

    # ── async core (the real implementation) ─────────────────────────────────
    async def astore(
        self,
        content: str,
        *,
        tier: MemoryTier,
        scope: MemoryScope,
        importance: float = 0.5,
        type: MemoryType = MemoryType.NOTE,
        tags: Sequence[str] = (),
        source: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        now = self._clock()
        record = MemoryRecord(
            id=MemoryId.new(self._id_factory),
            content=content,
            tier=tier,
            scope=scope,
            state=MemoryState.CREATED,
            metadata=MemoryMetadata(
                type=type,
                importance=importance,
                tags=tuple(tags),
                source=source,
                correlation_id=correlation_id,
                created_at=now,
                updated_at=now,
            ),
        )
        # lifecycle: CREATED → STORED → ACTIVE (each transition is FSM-validated)
        record = advance(record, MemoryTrigger.STORE, clock=self._clock)
        record = advance(record, MemoryTrigger.ACTIVATE, clock=self._clock)
        await self._memory.add(record)

        if self._config.tier(tier).vectorize:
            await self._vectors.index(record.id.value, content, metadata={"tier": tier.value})
            record = record.with_embedded(True)
            await self._memory.update(record)

        self.metrics.incr("stored")
        self.meter.incr("memory.stored", 1, tier=tier.value)
        self._emit(EventType.MEMORY_STORED, record)
        return record.id.value

    async def aretrieve(self, query: MemoryQuery) -> MemoryResult:
        """Naive attribute retrieval — NOT the Retrieval Engine. Filters ACTIVE records
        by the query's scope/tier/tags and returns them unranked (score = importance,
        gated by `min_score`). Semantic/keyword/ranking arrive in M5.1B-2."""
        records = await self._memory.query(
            MemoryFilter(
                scope=query.scope,
                tier=query.tier,
                tags=query.tags,
                states=(MemoryState.ACTIVE,),
                limit=query.limit,
            )
        )
        hits = tuple(
            self._serializer.to_hit(r, score=r.metadata.importance)
            for r in records
            if r.metadata.importance >= query.min_score
        )
        self.metrics.incr("retrieved")
        self.meter.incr("memory.retrieved", 1)
        self._publish(
            EventType.MEMORY_RETRIEVED,
            {
                "count": len(hits),
                "ids": [h.id for h in hits],
                "tier": query.tier.value if query.tier else None,
            },
            scope=query.scope,
        )
        return MemoryResult(hits=hits, total=len(hits), query=query)

    async def aforget(self, memory_id: str) -> None:
        record = await self._memory.get(MemoryId(memory_id))
        if record is None:
            return  # idempotent
        record = advance(record, MemoryTrigger.DELETE, clock=self._clock)
        await self._memory.remove(record.id)
        await self._vectors.remove(memory_id)  # best-effort; harmless if not indexed
        self.metrics.incr("forgotten")
        self.meter.incr("memory.cleared", 1, tier=record.tier.value)
        self._emit(EventType.MEMORY_CLEARED, record)

    # ── event emission (Replay/Gateway observe these for free) ───────────────
    def _emit(self, type: EventType, record: MemoryRecord) -> None:
        self._publish(
            type,
            self._serializer.to_event_payload(record),
            scope=record.scope,
            correlation_id=record.metadata.correlation_id,
        )

    def _publish(
        self,
        type: EventType,
        data: dict[str, Any],
        *,
        scope: MemoryScope,
        correlation_id: str | None = None,
    ) -> Event | None:
        return self.publish(
            type,
            data,
            session_id=scope.session_id or "",
            org_id=scope.org_id or "",
            conversation_id=scope.conversation_id,
            correlation_id=correlation_id,
        )

    # ── ingestion is deferred: never react to observed events yet ────────────
    def should_handle(self, event: Event) -> bool:
        return False

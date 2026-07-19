"""MemoryRuntime — the complete Memory Runtime (M5.1B-1 core + M5.1B-2 intelligence).

A `BusConsumer` (hence a `RuntimeModule`) that implements `core.ports.MemoryPort`. It
manages memory and nothing else: it never reasons, renders prompts, or executes tools.

M5.1B-2 completes it: intelligent retrieval (planner → repos → ranking → selection via
the `RetrievalEngine`), the `PolicyEngine` (validation/privacy/merge/expiry/capacity),
tenant isolation (`MemoryTenancy`), a hot-object cache (behind the repository), and
**content-event ingestion** (Conversation/Semantic/Episodic memory formed from the bus).

Async internals (`astore`/`aretrieve`/`aforget`) are the real path; the synchronous
`MemoryPort` methods drive them through the non-blocking sync bridge (`memory._run`).
The runtime works with or without a bus — publishing standard `memory.*` envelopes means
Replay records and the Gateway streams them for free.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.events import Event, EventSource, EventType
from core.module import BusConsumer
from core.ports.memory import MemoryHit, MemoryPort, MemoryQuery, MemoryScope, MemoryTier
from tenant import TenantContext

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
from .ingestion import MemoryIngestor
from .lifecycle import MemoryTrigger, advance
from .observability import MemoryStatistics, Metric
from .planner import MemoryQueryPlanner
from .policies import PolicyEngine
from .ranking import RankingEngine
from .repositories import MemoryRepository, MetadataRepository, VectorRepository
from .retrieval import RetrievalEngine
from .serializer import MemorySerializer
from .tenancy import MemoryTenancy


class MemoryRuntime(BusConsumer, MemoryPort):
    def __init__(
        self,
        *,
        config: MemoryConfiguration,
        memory_repo: MemoryRepository,
        vector_repo: VectorRepository,
        metadata_repo: MetadataRepository,
        serializer: MemorySerializer | None = None,
        retrieval: RetrievalEngine | None = None,
        policy: PolicyEngine | None = None,
        tenancy: MemoryTenancy | None = None,
        ingestor: MemoryIngestor | None = None,
        id_factory: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> None:
        self._ingestor = ingestor or MemoryIngestor()
        # Subscribe to the content events we ingest (never our own memory.* events).
        super().__init__(
            name=config.name,
            source=EventSource.MEMORY,
            types=self._ingestor.INGEST_TYPES,
            id_factory=id_factory,
            **kwargs,
        )
        self._config = config
        self._memory = memory_repo
        self._vectors = vector_repo
        self._meta = metadata_repo
        self._serializer = serializer or MemorySerializer()
        self._policy = policy or PolicyEngine()
        self._tenancy = tenancy or MemoryTenancy(clock=self._clock)
        self._retrieval = retrieval or RetrievalEngine(
            memory_repo=memory_repo,
            vector_repo=vector_repo,
            planner=MemoryQueryPlanner(),
            ranking=RankingEngine(clock=self._clock),
            tenancy=self._tenancy,
            serializer=self._serializer,
        )

    # ── MemoryPort (synchronous) — drive the async core deterministically ────
    def store(
        self, content: str, *, tier: MemoryTier, scope: MemoryScope, importance: float = 0.5
    ) -> str:
        return run_coro_sync(self.astore(content, tier=tier, scope=scope, importance=importance))

    def retrieve(self, query: MemoryQuery) -> Sequence[MemoryHit]:
        return run_coro_sync(self.aretrieve(query)).hits

    def forget(self, memory_id: str) -> None:
        return run_coro_sync(self.aforget(memory_id))

    # ── async core: store ────────────────────────────────────────────────────
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
        origin: str | None = None,
        agent_id: str | None = None,
        correlation_id: str | None = None,
        tenant: TenantContext | None = None,
    ) -> str:
        # policy: privacy redaction then validation (drop invalid, never persist)
        content = self._policy.apply_privacy(content)
        verdict = self._policy.validate(content)
        if not verdict.ok:
            self.metrics.incr("rejected")
            self.meter.incr(Metric.REJECTED, 1)
            return ""

        resolved = self._tenancy.guard(scope, tenant, action="store")
        scope = resolved.scope
        agent_id = agent_id or resolved.agent_id
        now = self._clock()

        # draft the record once (a single id is minted)
        record = self._draft(content, tier, scope, importance, type, tags, agent_id, now, source,
                             origin, correlation_id)

        # policy: merge identical content in the same scope+tier (dedupe/conflict)
        existing = await self._memory.query(
            MemoryFilter(scope=scope, tier=tier, states=(MemoryState.ACTIVE,))
        )
        merge = self._policy.resolve_merge(record, existing)
        if merge.merge_into is not None:
            return await self._apply_merge(merge.merge_into, merge.importance, now)

        record = advance(record, MemoryTrigger.STORE, clock=self._clock)
        record = advance(record, MemoryTrigger.ACTIVATE, clock=self._clock)
        await self._memory.add(record)

        if self._config.tier(tier).vectorize:
            await self._vectors.index(record.id.value, content, metadata={"tier": tier.value})
            record = record.with_embedded(True)
            await self._memory.update(record)

        self.metrics.incr("stored")
        self.meter.incr(Metric.STORED, 1, tier=tier.value)
        self._emit(EventType.MEMORY_STORED, record)

        await self._enforce_capacity(tier, scope, now)
        return record.id.value

    # ── async core: retrieve (delegates to the Retrieval Engine) ─────────────
    async def aretrieve(
        self, query: MemoryQuery, *, tenant: TenantContext | None = None
    ) -> MemoryResult:
        with self.meter.timer(Metric.RETRIEVAL_LATENCY):
            result = await self._retrieval.retrieve(query, tenant=tenant)
        self.metrics.incr("retrieved")
        self.meter.incr(Metric.RETRIEVED, 1)
        self.meter.observe(Metric.RANK_CANDIDATES, float(result.total))
        self._publish(
            EventType.MEMORY_RETRIEVED,
            {
                "count": result.total,
                "ids": [h.id for h in result.hits],
                "tier": query.tier.value if query.tier else None,
            },
            scope=query.scope,
        )
        return result

    # ── async core: forget ───────────────────────────────────────────────────
    async def aforget(self, memory_id: str) -> None:
        record = await self._memory.get(MemoryId(memory_id))
        if record is None:
            return  # idempotent
        record = advance(record, MemoryTrigger.DELETE, clock=self._clock)
        await self._memory.remove(record.id)
        await self._vectors.remove(memory_id)  # best-effort; harmless if not indexed
        self.metrics.incr("forgotten")
        self.meter.incr(Metric.CLEARED, 1, tier=record.tier.value)
        self._emit(EventType.MEMORY_CLEARED, record)

    # ── maintenance: expiration sweep (deterministic; caller-driven) ─────────
    async def expire(self, *, tier: MemoryTier | None = None, now: float | None = None) -> int:
        """Expire TTL-elapsed records in scope. Deterministic — no background timer;
        the caller (or a scheduler) invokes it. Returns the count expired."""
        now = self._clock() if now is None else now
        records = await self._memory.query(MemoryFilter(tier=tier, states=(MemoryState.ACTIVE,)))
        count = 0
        for r in records:
            if self._policy.is_expired(r, now):
                expired = advance(r, MemoryTrigger.ARCHIVE, clock=self._clock)
                expired = advance(expired, MemoryTrigger.EXPIRE, clock=self._clock)
                await self._memory.remove(r.id)
                await self._vectors.remove(r.id.value)
                self.metrics.incr("expired")
                self.meter.incr(Metric.EXPIRED, 1, tier=r.tier.value)
                self._emit(EventType.MEMORY_EXPIRED, expired)
                count += 1
        return count

    # ── observability ────────────────────────────────────────────────────────
    def statistics(self) -> MemoryStatistics:
        cache = getattr(self._memory, "_cache", None)
        return MemoryStatistics(
            name=self.name,
            state=self.state.value,
            healthy=self.health().healthy,
            metrics=self.metrics.snapshot(),
            cache=cache.snapshot() if cache is not None else None,
        )

    # ── event ingestion (Conversation / Semantic / Episodic memory) ──────────
    def should_handle(self, event: Event) -> bool:
        return event.type in self._ingestor.INGEST_TYPES

    def handle_event(self, event: Event) -> None:
        run_coro_sync(self._ingest(event))

    async def _ingest(self, event: Event) -> None:
        result = self._ingestor.plan(event)
        scope = self._scope_from_event(event)
        agent_id = event.metadata.get("agent_id")
        for si in result.stores:
            await self.astore(
                si.content, tier=si.tier, scope=scope, importance=si.importance, type=si.type,
                tags=si.tags, source=event.id, origin=si.origin, agent_id=agent_id,
                correlation_id=event.correlation_id or event.id,
            )
            self.metrics.incr("ingested")
            self.meter.incr(Metric.INGESTED, 1)
        for ai in result.accesses:
            await self._bump_access(ai.memory_ids)

    async def _bump_access(self, memory_ids: Sequence[str]) -> None:
        now = self._clock()
        for mid in memory_ids:
            record = await self._memory.get(MemoryId(mid))
            if record is None:
                continue
            record = record.accessed(now)
            await self._memory.update(record)
            self.meter.incr(Metric.UPDATED, 1)
            self._emit(EventType.MEMORY_UPDATED, record)

    # ── internals ──────────────────────────────────────────────────────────────
    def _draft(
        self, content, tier, scope, importance, type, tags, agent_id, now,
        source=None, origin=None, correlation_id=None,
    ) -> MemoryRecord:
        return MemoryRecord(
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
                origin=origin,
                agent_id=agent_id,
                correlation_id=correlation_id,
                created_at=now,
                updated_at=now,
                expires_at=self._policy.expiry_for(tier, now),
            ),
        )

    async def _apply_merge(self, existing_id: str, importance: float, now: float) -> str:
        record = await self._memory.get(MemoryId(existing_id))
        if record is None:
            return existing_id
        bumped = record.accessed(now).touched(now)
        merged = bumped.with_metadata(_with_importance(bumped.metadata, importance))
        await self._memory.update(merged)
        self.metrics.incr("merged")
        self.meter.incr(Metric.UPDATED, 1, tier=record.tier.value)
        self._emit(EventType.MEMORY_UPDATED, merged)
        return existing_id

    async def _enforce_capacity(self, tier: MemoryTier, scope: MemoryScope, now: float) -> None:
        records = await self._memory.query(
            MemoryFilter(scope=scope, tier=tier, states=(MemoryState.ACTIVE,))
        )
        for victim in self._policy.evictions(records, tier, now):
            evicted = advance(victim, MemoryTrigger.DELETE, clock=self._clock)
            await self._memory.remove(victim.id)
            await self._vectors.remove(victim.id.value)
            self.metrics.incr("evicted")
            self.meter.incr(Metric.EVICTED, 1, tier=tier.value)
            self._emit(EventType.MEMORY_EVICTED, evicted)

    def _scope_from_event(self, event: Event) -> MemoryScope:
        return MemoryScope(
            session_id=event.session_id or None,
            conversation_id=event.conversation_id,
            user_id=event.metadata.get("user_id"),
            org_id=event.org_id or None,
        )

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


def _with_importance(metadata: MemoryMetadata, importance: float) -> MemoryMetadata:
    from dataclasses import replace

    return replace(metadata, importance=importance)

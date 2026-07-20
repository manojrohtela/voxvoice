"""KnowledgeRuntime — the complete Knowledge Runtime (M5.2B-1 core + M5.2B-2 intelligence).

A `BusConsumer` (hence a `RuntimeModule`) implementing `core.ports.KnowledgePort`. It
owns the knowledge lifecycle + retrieval and nothing else: it never stores memory,
assembles context, generates prompts, reasons, executes tools, or manages workflows.

M5.2B-1 delivered the lifecycle core (index/get/remove + the async pipeline). M5.2B-2
completes it:
- **retrieve / aquery** — intelligent retrieval via the `KnowledgeRetrievalEngine`
  (tenancy → planner → repositories → ranking → policy → selection → `KnowledgeResult`),
  returning provenance-complete hits.
- **content-event ingestion** — GENERATED/IMPORTED documents formed from *other*
  producers' events; never re-ingests `knowledge.*` (structural self-loop prevention).

The pipeline is async-first (M5.2A decision); the synchronous `KnowledgePort` +
`index/get/remove` drive it via the non-blocking sync bridge (local adapters only).
Publishing standard envelopes means Replay records and the Gateway streams them for free.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.events import Event, EventSource, EventType
from core.module import BusConsumer
from core.ports.knowledge import KnowledgePassage, KnowledgePort, KnowledgeQuery
from tenant import TenantContext

from ._run import run_coro_sync
from .config import KnowledgeConfiguration
from .domain import Document, ImportOrigin, KnowledgeScope, KnowledgeType, SourceDescriptor
from .events import KnowledgePhase
from .ids import document_id_for
from .ingestion import IngestAction, KnowledgeIngestor
from .observability import KnowledgeStatistics, Metric
from .pipeline import DocumentPipeline, IngestResult
from .planner import KnowledgeQueryPlanner
from .policies import KnowledgePolicyEngine
from .profiles import RetrievalProfile
from .query import KnowledgeRetrievalQuery
from .ranking import KnowledgeRankingEngine
from .repositories import KnowledgeRepository
from .results import KnowledgeResult
from .retrieval import KnowledgeRetrievalEngine
from .serializer import KnowledgeSerializer
from .tenancy import KnowledgeTenancy


class KnowledgeRuntime(BusConsumer, KnowledgePort):
    def __init__(
        self,
        *,
        config: KnowledgeConfiguration,
        repository: KnowledgeRepository,
        pipeline: DocumentPipeline,
        serializer: KnowledgeSerializer | None = None,
        retrieval: KnowledgeRetrievalEngine | None = None,
        tenancy: KnowledgeTenancy | None = None,
        ingestor: KnowledgeIngestor | None = None,
        default_profile: RetrievalProfile = RetrievalProfile.BALANCED,
        id_factory: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> None:
        self._ingestor = ingestor or KnowledgeIngestor()
        # Subscribe only to the content events we ingest — never a knowledge.* type
        # (structural self-loop prevention; the ingestor also guards on EventSource).
        super().__init__(
            name=config.name,
            source=EventSource.KNOWLEDGE,
            types=self._ingestor.INGEST_TYPES,
            id_factory=id_factory,
            **kwargs,
        )
        self._config = config
        self._repo = repository
        self._pipeline = pipeline
        self._serializer = serializer or KnowledgeSerializer()
        self._tenancy = tenancy or KnowledgeTenancy(clock=self._clock)
        self._default_profile = default_profile
        self._retrieval = retrieval or KnowledgeRetrievalEngine(
            repository=repository,
            planner=KnowledgeQueryPlanner(),
            ranking=KnowledgeRankingEngine(clock=self._clock),
            policy=KnowledgePolicyEngine(),
            tenancy=self._tenancy,
        )

    # ── synchronous surface (drives the async core) ─────────────────────────
    def index(
        self,
        content: str | bytes,
        *,
        uri: str,
        scope: KnowledgeScope,
        knowledge_type: KnowledgeType = KnowledgeType.ORGANIZATION,
        mime: str = "text/plain",
        title: str | None = None,
        origin: ImportOrigin = ImportOrigin.IMPORTED,
    ) -> str:
        return run_coro_sync(
            self.aindex(content, uri=uri, scope=scope, knowledge_type=knowledge_type,
                        mime=mime, title=title, origin=origin)
        )

    def get(self, document_id: str) -> Document | None:
        return run_coro_sync(self._repo.get_document(document_id))

    def remove(self, document_id: str) -> None:
        return run_coro_sync(self.aremove(document_id))

    def retrieve(self, query: KnowledgeQuery) -> Sequence[KnowledgePassage]:
        return run_coro_sync(self.aretrieve(query))

    def query(
        self,
        request: KnowledgeRetrievalQuery,
        *,
        tenant: TenantContext | None = None,
    ) -> KnowledgeResult:
        """Rich retrieval: a provenance-complete `KnowledgeResult` (the internal query
        shape). `retrieve` narrows this to the frozen `KnowledgePort` passage list."""
        return run_coro_sync(self.aquery(request, tenant=tenant))

    def sources(self, *, org_id: str | None = None) -> Sequence[str]:
        docs = run_coro_sync(self._repo.list_documents(org_id=org_id))
        return [d.id for d in docs]

    # ── async core ───────────────────────────────────────────────────────────
    async def aindex(
        self,
        content: str | bytes,
        *,
        uri: str,
        scope: KnowledgeScope,
        knowledge_type: KnowledgeType = KnowledgeType.ORGANIZATION,
        mime: str = "text/plain",
        title: str | None = None,
        origin: ImportOrigin = ImportOrigin.IMPORTED,
    ) -> str:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        descriptor = SourceDescriptor(uri=uri, mime=mime, origin=origin, title=title)
        existing = await self._repo.get_document(document_id_for(uri))
        with self.meter.timer(Metric.INGEST_LATENCY):
            result = await self._pipeline.ingest(
                raw, descriptor, scope, knowledge_type, existing=existing
            )
        self._emit_ingest(result)
        return result.document.id

    async def aremove(self, document_id: str) -> None:
        doc = await self._repo.get_document(document_id)
        if doc is None:
            return  # idempotent
        deleted = await self._pipeline.delete(doc)
        self.metrics.incr("deleted")
        self.meter.incr(Metric.DELETED, 1)
        self._emit_document(deleted, KnowledgePhase.DELETED)

    async def aretrieve(
        self, query: KnowledgeQuery, *, tenant: TenantContext | None = None
    ) -> Sequence[KnowledgePassage]:
        """Port retrieval: translate the frozen `KnowledgeQuery` into the internal query
        (default profile) and return provenance-complete passages."""
        request = KnowledgeRetrievalQuery.from_port(query, profile=self._default_profile)
        result = await self.aquery(request, tenant=tenant)
        return result.passages()

    async def aquery(
        self, request: KnowledgeRetrievalQuery, *, tenant: TenantContext | None = None
    ) -> KnowledgeResult:
        """The intelligent retrieval path: engine orchestrates tenancy → planner →
        repositories → ranking → policy → selection. Emits a standard retrieval event."""
        with self.meter.timer(Metric.RETRIEVAL_LATENCY):
            result = await self._retrieval.retrieve(request, tenant=tenant)
        self.metrics.incr("retrieved")
        self.meter.incr(Metric.RETRIEVED, 1)
        self.meter.incr(Metric.RETRIEVAL_HITS, result.total)
        self.meter.observe(Metric.RETRIEVAL_CANDIDATES, float(result.candidates))
        self._publish(
            EventType.KNOWLEDGE_RETRIEVED,
            {
                "count": result.total,
                "candidates": result.candidates,
                "strategy": result.strategy,
                "profile": result.profile,
                "ids": [h.chunk_id for h in result.hits],
            },
            org_id=request.scope.org_id or "",
        )
        return result

    # ── observability ────────────────────────────────────────────────────────
    def statistics(self) -> KnowledgeStatistics:
        cache = self._repo.chunks.cache
        return KnowledgeStatistics(
            name=self.name,
            state=self.state.value,
            healthy=self.health().healthy,
            documents=run_coro_sync(self._repo.document_count()),
            metrics=self.metrics.snapshot(),
            index=self._repo.index.statistics(),
            embeddings={"dimensions": self._repo.embeddings.dimensions},
            cache=cache.snapshot() if cache is not None else None,
        )

    # ── content-event ingestion (GENERATED/IMPORTED docs; never our own events) ─
    def should_handle(self, event: Event) -> bool:
        # structural self-loop guard: knowledge.* is never in INGEST_TYPES, and we
        # additionally never react to an event we ourselves produced.
        return (
            event.source is not EventSource.KNOWLEDGE
            and event.type in self._ingestor.INGEST_TYPES
        )

    def handle_event(self, event: Event) -> None:
        run_coro_sync(self._ingest(event))

    async def _ingest(self, event: Event) -> None:
        result = self._ingestor.plan(event)
        scope = self._scope_from_event(event)
        for ri in result.registers:
            await self.aindex(
                ri.content, uri=ri.uri, scope=scope, knowledge_type=ri.knowledge_type,
                mime=ri.mime, title=ri.title, origin=ri.origin,
            )
            self.metrics.incr("ingested")
            self.meter.incr(Metric.INGESTED, 1)
        for mi in result.maintenance:
            if mi.action is IngestAction.DELETE:
                await self.aremove(mi.document_id)
            elif mi.action is IngestAction.ARCHIVE:
                await self._archive(mi.document_id)

    async def _archive(self, document_id: str) -> None:
        doc = await self._repo.get_document(document_id)
        if doc is None:
            return
        archived = await self._pipeline.archive(doc)
        self.metrics.incr("archived")
        self.meter.incr(Metric.ARCHIVED, 1)
        self._emit_document(archived, KnowledgePhase.ARCHIVED)

    def _scope_from_event(self, event: Event) -> KnowledgeScope:
        return KnowledgeScope(
            org_id=event.org_id or None,
            user_id=event.metadata.get("user_id"),
            department=event.metadata.get("department"),
        )

    # ── event emission (Replay/Gateway observe these for free) ───────────────
    def _emit_ingest(self, result: IngestResult) -> None:
        if result.phase == KnowledgePhase.DEDUPED.value:
            self.metrics.incr("deduped")
            self.meter.incr(Metric.DEDUPED, 1)
            self._emit_document(result.document, KnowledgePhase.DEDUPED)
            return
        phase = KnowledgePhase.UPDATED if result.phase == "updated" else KnowledgePhase.IMPORTED
        self.metrics.incr(result.phase)
        self.meter.incr(Metric.UPDATED if phase is KnowledgePhase.UPDATED else Metric.IMPORTED, 1)
        self.meter.incr(Metric.CHUNKS_CREATED, len(result.chunks))
        self._emit_document(result.document, phase)
        self._publish(
            EventType.KNOWLEDGE_INDEXED,
            {"id": result.document.id, "version": result.document.version,
             "chunks": len(result.chunks)},
            org_id=result.document.scope.org_id or "",
        )
        self.meter.incr(Metric.INDEXED, 1)
        if result.version is not None:
            self._emit_document(result.document, KnowledgePhase.VERSION_CREATED)

    def _emit_document(self, document: Document, phase: KnowledgePhase) -> None:
        payload = self._serializer.document_event_payload(document, phase=phase.value)
        self._publish(
            EventType.KNOWLEDGE_SOURCE_UPDATED, payload, org_id=document.scope.org_id or ""
        )

    def _publish(
        self, type: EventType, data: dict[str, Any], *, org_id: str = "", session_id: str = ""
    ) -> Event | None:
        return self.publish(type, data, session_id=session_id, org_id=org_id)

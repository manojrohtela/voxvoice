"""KnowledgeRuntime — the Knowledge Runtime core (M5.2B-1).

A `BusConsumer` (hence a `RuntimeModule`) implementing `core.ports.KnowledgePort`. It
owns the knowledge lifecycle + retrieval and nothing else: it never stores memory,
assembles context, generates prompts, reasons, executes tools, or manages workflows.

What lands in M5.2B-1 (core):
- **index** — run a document through the async pipeline (import→…→version→active),
  emitting `knowledge.*` events; content-hash dedup + versioning on re-import.
- **get / remove** — fetch / delete a document (+ chunks, versions, blob, index entry).
- **retrieve** — a **placeholder** returning no passages (intelligence is M5.2B-2).
- **sources / statistics / health**.

The pipeline is async-first (M5.2A decision); the synchronous `KnowledgePort` +
`index/get/remove` drive it via the non-blocking sync bridge (local adapters only).
Publishing standard envelopes means Replay records and the Gateway streams them for free.
Content-event ingestion is a non-goal here (the runtime attaches to publish only).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.events import Event, EventSource, EventType
from core.module import BusConsumer
from core.ports.knowledge import KnowledgePassage, KnowledgePort, KnowledgeQuery

from ._run import run_coro_sync
from .config import KnowledgeConfiguration
from .domain import Document, ImportOrigin, KnowledgeScope, KnowledgeType, SourceDescriptor
from .events import KnowledgePhase
from .ids import document_id_for
from .observability import KnowledgeStatistics, Metric
from .pipeline import DocumentPipeline, IngestResult
from .repositories import KnowledgeRepository
from .serializer import KnowledgeSerializer


class KnowledgeRuntime(BusConsumer, KnowledgePort):
    def __init__(
        self,
        *,
        config: KnowledgeConfiguration,
        repository: KnowledgeRepository,
        pipeline: DocumentPipeline,
        serializer: KnowledgeSerializer | None = None,
        id_factory: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> None:
        # No content-event ingestion in the core milestone → subscribe to nothing.
        super().__init__(
            name=config.name,
            source=EventSource.KNOWLEDGE,
            types=frozenset(),
            id_factory=id_factory,
            **kwargs,
        )
        self._config = config
        self._repo = repository
        self._pipeline = pipeline
        self._serializer = serializer or KnowledgeSerializer()

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

    async def aretrieve(self, query: KnowledgeQuery) -> Sequence[KnowledgePassage]:
        """Placeholder — retrieval intelligence (planner/ranking/search) is M5.2B-2.
        Emits a retrieval event and returns no passages."""
        self.metrics.incr("retrieved")
        self.meter.incr(Metric.RETRIEVED, 1)
        self._publish(
            EventType.KNOWLEDGE_RETRIEVED,
            {"count": 0, "query_length": len(query.text)},
            org_id=query.org_id or "",
        )
        return ()

    # ── observability ────────────────────────────────────────────────────────
    def statistics(self) -> KnowledgeStatistics:
        return KnowledgeStatistics(
            name=self.name,
            state=self.state.value,
            healthy=self.health().healthy,
            documents=run_coro_sync(self._repo.document_count()),
            metrics=self.metrics.snapshot(),
            index=self._repo.index.statistics(),
            embeddings={"dimensions": self._repo.embeddings.dimensions},
        )

    # ── ingestion is deferred: never react to observed events yet ────────────
    def should_handle(self, event: Event) -> bool:
        return False

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

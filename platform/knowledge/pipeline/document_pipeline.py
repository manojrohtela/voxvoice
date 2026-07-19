"""Document pipeline (M5.2B-1) — the async-first ingestion lifecycle.

Orchestrates: import → validate → normalize → parse → metadata → chunk → embed → index →
store → version → activate (and archival/deletion hooks). Deterministic; advances the
document lifecycle FSM through each stage; uses repositories only (never a provider) and
the injected `Parser`/`ChunkStrategy`/`Embedder` ports. It returns an `IngestResult`; the
**runtime** is the sole event producer (it emits from the result).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.errors import PlatformError

from ..config import ChunkConfig
from ..domain import (
    Chunk,
    Document,
    DocumentState,
    DocumentVersion,
    KnowledgeMetadata,
    KnowledgeScope,
    KnowledgeType,
    SourceDescriptor,
)
from ..ids import content_hash, document_id_for, version_id_for
from ..index import KnowledgeIndex
from ..lifecycle import ChunkTrigger, DocumentTrigger, advance_chunk, advance_document
from ..repositories.chunk_repository import ChunkRepository
from ..repositories.document_repository import DocumentRepository
from ..version import VersionManager
from .chunk_pipeline import ChunkPipeline
from .embedding_pipeline import EmbeddingPipeline
from .parser import Parser


class KnowledgeValidationError(PlatformError):
    """A document failed validation during ingestion."""


@dataclass(frozen=True, slots=True)
class IngestResult:
    document: Document
    chunks: tuple[Chunk, ...]
    version: DocumentVersion | None
    phase: str  # "imported" | "updated" | "deduped"


def _normalize(text: str) -> str:
    """Deterministic whitespace normalization (collapse runs, strip)."""
    return " ".join(text.split())


class DocumentPipeline:
    def __init__(
        self,
        *,
        document_repo: DocumentRepository,
        chunk_repo: ChunkRepository,
        index: KnowledgeIndex,
        version_manager: VersionManager,
        parser: Parser,
        chunk_pipeline: ChunkPipeline,
        embedding_pipeline: EmbeddingPipeline,
        chunk_config: ChunkConfig,
        clock: Callable[[], float],
        max_size: int = 5_000_000,
    ) -> None:
        self._docs = document_repo
        self._chunks = chunk_repo
        self._index = index
        self._versions = version_manager
        self._parser = parser
        self._chunker = chunk_pipeline
        self._embedder = embedding_pipeline
        self._chunk_config = chunk_config
        self._clock = clock
        self._max_size = max_size

    async def ingest(
        self,
        raw: bytes,
        descriptor: SourceDescriptor,
        scope: KnowledgeScope,
        knowledge_type: KnowledgeType,
        *,
        existing: Document | None = None,
    ) -> IngestResult:
        now = self._clock()
        document_id = document_id_for(descriptor.uri)

        # 1. import (blob) + 2. validate
        if not raw or len(raw) > self._max_size:
            raise KnowledgeValidationError(f"invalid document size for {descriptor.uri!r}")
        await self._docs.put_blob(document_id, raw, content_type=descriptor.mime)

        # 3. parse + 4. normalize
        text = _normalize(self._parser.parse(raw, mime=descriptor.mime))
        if not text:
            raise KnowledgeValidationError(f"empty document content for {descriptor.uri!r}")
        chash = content_hash(text)

        # dedup: same source + same content → no new version
        if existing is not None and existing.content_hash == chash:
            current = await self._versions.current(document_id)
            return IngestResult(existing, (), current, phase="deduped")

        number = (existing.version + 1) if existing is not None else 1

        # 5. metadata extraction
        metadata = KnowledgeMetadata(
            title=descriptor.title,
            source_uri=descriptor.uri,
            mime=descriptor.mime,
            origin=descriptor.origin.value,
            content_hash=chash,
            created_at=(existing.metadata.created_at if existing else now),
            updated_at=now,
        )
        document = Document(
            id=document_id,
            scope=scope,
            knowledge_type=knowledge_type,
            source=descriptor,
            text=text,
            content_hash=chash,
            version=number,
            version_id=version_id_for(document_id, number),
            state=DocumentState.IMPORTED,
            metadata=metadata,
        )

        # advance through the lifecycle, doing each stage's work
        document = advance_document(document, DocumentTrigger.VALIDATE, clock=self._clock)
        document = advance_document(document, DocumentTrigger.PARSE, clock=self._clock)

        chunks = self._chunker.chunk(text, document_id=document_id, version=number)
        document = document.with_chunks(tuple(c.id for c in chunks))
        document = advance_document(document, DocumentTrigger.CHUNK, clock=self._clock)

        chunks = await self._embedder.embed(chunks)
        document = advance_document(document, DocumentTrigger.EMBED, clock=self._clock)

        self._index.register(document, chunks)
        chunks = [advance_chunk(c, ChunkTrigger.INDEX, clock=self._clock) for c in chunks]
        document = advance_document(document, DocumentTrigger.INDEX, clock=self._clock)

        # 9. storage
        await self._chunks.add_many(chunks)

        # 10. versioning
        if existing is not None:
            version = await self._versions.create_next(document, number=number)
        else:
            version = await self._versions.create_initial(document)

        # activate + persist current
        document = advance_document(document, DocumentTrigger.ACTIVATE, clock=self._clock)
        await self._docs.add(document)

        phase = "updated" if existing is not None else "imported"
        return IngestResult(document, tuple(chunks), version, phase=phase)

    # ── archival + deletion hooks ─────────────────────────────────────────────
    async def archive(self, document: Document) -> Document:
        archived = advance_document(document, DocumentTrigger.ARCHIVE, clock=self._clock)
        await self._docs.update(archived)
        return archived

    async def delete(self, document: Document) -> Document:
        chunks = await self._chunks.for_document(document.id)
        self._index.remove(document, chunks)
        await self._chunks.remove_for_document(document.id)
        await self._docs.remove_versions(document.id)
        await self._docs.delete_blob(document.id)
        await self._docs.remove(document.id)
        # FSM permits DELETE from ACTIVE or ARCHIVED
        return advance_document(document, DocumentTrigger.DELETE, clock=self._clock)

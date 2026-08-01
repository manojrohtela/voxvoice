"""Knowledge Tool application layer (M8.15) — additive.

Commands + queries + use cases for the Knowledge Tool vertical slice, in a separate module so the
M8.3 modules stay untouched (mirrors `tool_framework.py`). Use cases coordinate the additive
Foundation `knowledge` (assets + pipeline) + `knowledge_search` (embed/index/retrieve) services —
no domain logic is duplicated. Mutations run inside a Unit of Work (durable rows + audit commit
atomically). Knowledge remains a Tool Framework capability; the Studio search here is for
inspection — at conversation time the Runtime invokes the Knowledge Tool via the Tool Executor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from api.foundation import (
    Foundation,
    KnowledgeChunkView,
    KnowledgeCollectionView,
    KnowledgeDocumentView,
)

from .authorization import AuthorizationOrchestrator
from .dispatcher import CommandBus, QueryBus
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory
from .use_cases import _UseCaseBase

# ── commands ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CreateKnowledgeCollection:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    name: str
    base: str = "default"
    description: str = ""
    embedding_provider: str = "local"
    embedding_model: str = "local-embed"
    chunk_size: int = 800
    chunk_overlap: int = 100


@dataclass(frozen=True, slots=True)
class UpdateKnowledgeCollection:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    collection_id: str
    changes: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _CollectionOp:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    collection_id: str


class ArchiveKnowledgeCollection(_CollectionOp): ...


class RestoreKnowledgeCollection(_CollectionOp): ...


class DeleteKnowledgeCollection(_CollectionOp): ...


class GenerateEmbeddings(_CollectionOp): ...


class IndexKnowledgeCollection(_CollectionOp): ...


@dataclass(frozen=True, slots=True)
class UploadDocument:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    collection_id: str
    name: str
    content: str
    mime_type: str = "text/plain"
    metadata: dict | None = None


@dataclass(frozen=True, slots=True)
class ReplaceDocument:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    collection_id: str
    document_id: str
    content: str
    mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class _DocumentOp:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    collection_id: str
    document_id: str


class ArchiveDocument(_DocumentOp): ...


class RestoreDocument(_DocumentOp): ...


class DeleteDocument(_DocumentOp): ...


class ChunkDocument(_DocumentOp): ...


# ── queries ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ListKnowledgeCollections:
    org_id: str
    project_id: str


@dataclass(frozen=True, slots=True)
class GetKnowledgeCollection:
    org_id: str
    project_id: str
    collection_id: str


@dataclass(frozen=True, slots=True)
class ListDocuments:
    org_id: str
    collection_id: str


@dataclass(frozen=True, slots=True)
class ListChunks:
    org_id: str
    document_id: str


@dataclass(frozen=True, slots=True)
class VectorStatus:
    org_id: str
    project_id: str
    collection_id: str


@dataclass(frozen=True, slots=True)
class SearchKnowledge:
    actor_user_id: str
    org_id: str
    workspace_id: str
    project_id: str
    collection_id: str
    query: str
    top_k: int = 5
    threshold: float = 0.0
    filters: dict | None = None


# ── use cases (write side) ─────────────────────────────────────────────────────────


class KnowledgeUseCases(_UseCaseBase):
    def _stage(self, uow, type_, use_case, *, org_id, actor, **data):
        uow.stage(
            ApplicationEvent(
                id=self._ids.new(PREFIX_APP_EVENT),
                type=type_,
                ts=self._clock.now(),
                use_case=use_case,
                outcome="completed",
                org_id=org_id,
                actor=actor,
                data=dict(data),
            )
        )

    def create_collection(self, cmd: CreateKnowledgeCollection) -> dict:
        with self._uow.new() as uow:
            c = self._f.knowledge.create_collection(
                cmd.actor_user_id,
                cmd.org_id,
                cmd.workspace_id,
                cmd.project_id,
                name=cmd.name,
                base=cmd.base,
                description=cmd.description,
                embedding_provider=cmd.embedding_provider,
                embedding_model=cmd.embedding_model,
                chunk_size=cmd.chunk_size,
                chunk_overlap=cmd.chunk_overlap,
            )
            self._stage(
                uow,
                ApplicationEventType.KNOWLEDGE_COLLECTION_CHANGED,
                "create_collection",
                org_id=cmd.org_id,
                actor=cmd.actor_user_id,
                collection_id=c.id,
            )
        return asdict(KnowledgeCollectionView.of(c))

    def update_collection(self, cmd: UpdateKnowledgeCollection) -> dict:
        with self._uow.new() as uow:
            c = self._f.knowledge.update_collection(
                cmd.actor_user_id,
                cmd.org_id,
                cmd.workspace_id,
                cmd.project_id,
                cmd.collection_id,
                **cmd.changes,
            )
            self._stage(
                uow,
                ApplicationEventType.KNOWLEDGE_COLLECTION_CHANGED,
                "update_collection",
                org_id=cmd.org_id,
                actor=cmd.actor_user_id,
                collection_id=c.id,
            )
        return asdict(KnowledgeCollectionView.of(c))

    def _collection_lifecycle(self, cmd: _CollectionOp, fn, use_case) -> dict:
        with self._uow.new() as uow:
            c = fn(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.collection_id
            )
            self._stage(
                uow,
                ApplicationEventType.KNOWLEDGE_COLLECTION_CHANGED,
                use_case,
                org_id=cmd.org_id,
                actor=cmd.actor_user_id,
                collection_id=cmd.collection_id,
            )
        return asdict(KnowledgeCollectionView.of(c))

    def archive_collection(self, cmd: ArchiveKnowledgeCollection) -> dict:
        return self._collection_lifecycle(
            cmd, self._f.knowledge.archive_collection, "archive_collection"
        )

    def restore_collection(self, cmd: RestoreKnowledgeCollection) -> dict:
        return self._collection_lifecycle(
            cmd, self._f.knowledge.restore_collection, "restore_collection"
        )

    def delete_collection(self, cmd: DeleteKnowledgeCollection) -> dict:
        return self._collection_lifecycle(
            cmd, self._f.knowledge.delete_collection, "delete_collection"
        )

    def upload_document(self, cmd: UploadDocument) -> dict:
        with self._uow.new() as uow:
            d = self._f.knowledge.upload_document(
                cmd.actor_user_id,
                cmd.org_id,
                cmd.workspace_id,
                cmd.project_id,
                cmd.collection_id,
                name=cmd.name,
                content=cmd.content,
                mime_type=cmd.mime_type,
                metadata=cmd.metadata,
            )
            self._stage(
                uow,
                ApplicationEventType.KNOWLEDGE_DOCUMENT_CHANGED,
                "upload_document",
                org_id=cmd.org_id,
                actor=cmd.actor_user_id,
                document_id=d.id,
            )
        return asdict(KnowledgeDocumentView.of(d))

    def replace_document(self, cmd: ReplaceDocument) -> dict:
        with self._uow.new() as uow:
            d = self._f.knowledge.replace_document(
                cmd.actor_user_id,
                cmd.org_id,
                cmd.workspace_id,
                cmd.project_id,
                cmd.collection_id,
                cmd.document_id,
                content=cmd.content,
                mime_type=cmd.mime_type,
            )
            self._stage(
                uow,
                ApplicationEventType.KNOWLEDGE_DOCUMENT_CHANGED,
                "replace_document",
                org_id=cmd.org_id,
                actor=cmd.actor_user_id,
                document_id=d.id,
            )
        return asdict(KnowledgeDocumentView.of(d))

    def _document_lifecycle(self, cmd: _DocumentOp, fn, use_case) -> dict:
        with self._uow.new() as uow:
            d = fn(
                cmd.actor_user_id,
                cmd.org_id,
                cmd.workspace_id,
                cmd.project_id,
                cmd.collection_id,
                cmd.document_id,
            )
            self._stage(
                uow,
                ApplicationEventType.KNOWLEDGE_DOCUMENT_CHANGED,
                use_case,
                org_id=cmd.org_id,
                actor=cmd.actor_user_id,
                document_id=cmd.document_id,
            )
        return asdict(KnowledgeDocumentView.of(d))

    def archive_document(self, cmd: ArchiveDocument) -> dict:
        return self._document_lifecycle(cmd, self._f.knowledge.archive_document, "archive_document")

    def restore_document(self, cmd: RestoreDocument) -> dict:
        return self._document_lifecycle(cmd, self._f.knowledge.restore_document, "restore_document")

    def delete_document(self, cmd: DeleteDocument) -> dict:
        return self._document_lifecycle(cmd, self._f.knowledge.delete_document, "delete_document")

    def chunk_document(self, cmd: ChunkDocument) -> dict:
        with self._uow.new() as uow:
            chunks = self._f.knowledge.chunk_document(
                cmd.actor_user_id,
                cmd.org_id,
                cmd.workspace_id,
                cmd.project_id,
                cmd.collection_id,
                cmd.document_id,
            )
            self._stage(
                uow,
                ApplicationEventType.KNOWLEDGE_DOCUMENT_CHANGED,
                "chunk_document",
                org_id=cmd.org_id,
                actor=cmd.actor_user_id,
                document_id=cmd.document_id,
                chunks=len(chunks),
            )
        return {"document_id": cmd.document_id, "chunks": len(chunks)}

    def generate_embeddings(self, cmd: GenerateEmbeddings) -> dict:
        with self._uow.new() as uow:
            n = self._f.knowledge_search.generate_embeddings(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.collection_id
            )
            self._stage(
                uow,
                ApplicationEventType.KNOWLEDGE_INDEXED,
                "generate_embeddings",
                org_id=cmd.org_id,
                actor=cmd.actor_user_id,
                collection_id=cmd.collection_id,
            )
        return {"collection_id": cmd.collection_id, "embedded": n}

    def index_collection(self, cmd: IndexKnowledgeCollection) -> dict:
        with self._uow.new() as uow:
            n = self._f.knowledge_search.index_collection(
                cmd.actor_user_id, cmd.org_id, cmd.workspace_id, cmd.project_id, cmd.collection_id
            )
            self._stage(
                uow,
                ApplicationEventType.KNOWLEDGE_INDEXED,
                "index_collection",
                org_id=cmd.org_id,
                actor=cmd.actor_user_id,
                collection_id=cmd.collection_id,
            )
        return {"collection_id": cmd.collection_id, "indexed": n}

    def search(self, cmd: SearchKnowledge) -> dict:
        with self._uow.new() as uow:
            pkg = self._f.knowledge_search.search(
                cmd.actor_user_id,
                cmd.org_id,
                cmd.workspace_id,
                cmd.project_id,
                cmd.collection_id,
                cmd.query,
                top_k=cmd.top_k,
                threshold=cmd.threshold,
                filters=cmd.filters,
            )
            self._stage(
                uow,
                ApplicationEventType.KNOWLEDGE_SEARCHED,
                "search_knowledge",
                org_id=cmd.org_id,
                actor=cmd.actor_user_id,
                collection_id=cmd.collection_id,
                hits=len(pkg.chunks),
            )
        return pkg.as_dict()


# ── queries (read side) ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class KnowledgeQueries:
    foundation: Foundation

    def collections(self, q: ListKnowledgeCollections) -> list[dict]:
        return [
            asdict(KnowledgeCollectionView.of(c))
            for c in self.foundation.knowledge.list_collections(q.org_id, q.project_id)
        ]

    def collection(self, q: GetKnowledgeCollection) -> dict:
        return asdict(
            KnowledgeCollectionView.of(
                self.foundation.knowledge.get_collection(q.org_id, q.project_id, q.collection_id)
            )
        )

    def documents(self, q: ListDocuments) -> list[dict]:
        return [
            asdict(KnowledgeDocumentView.of(d))
            for d in self.foundation.knowledge.list_documents(q.org_id, q.collection_id)
        ]

    def chunks(self, q: ListChunks) -> list[dict]:
        return [
            asdict(KnowledgeChunkView.of(c))
            for c in self.foundation.knowledge.list_chunks(q.org_id, q.document_id)
        ]

    def vector_status(self, q: VectorStatus) -> dict:
        return self.foundation.knowledge_search.vector_status(
            q.org_id, q.project_id, q.collection_id
        )


def build_and_register(
    *,
    foundation,
    authz: AuthorizationOrchestrator,
    uow_factory: UnitOfWorkFactory,
    ids,
    clock,
    command_bus: CommandBus,
    query_bus: QueryBus,
) -> None:
    """Wire the M8.15 use cases + handlers into the existing buses (additive)."""
    uc = KnowledgeUseCases(
        foundation=foundation, authz=authz, uow_factory=uow_factory, ids=ids, clock=clock
    )
    q = KnowledgeQueries(foundation=foundation)
    command_bus.register(CreateKnowledgeCollection, uc.create_collection)
    command_bus.register(UpdateKnowledgeCollection, uc.update_collection)
    command_bus.register(ArchiveKnowledgeCollection, uc.archive_collection)
    command_bus.register(RestoreKnowledgeCollection, uc.restore_collection)
    command_bus.register(DeleteKnowledgeCollection, uc.delete_collection)
    command_bus.register(UploadDocument, uc.upload_document)
    command_bus.register(ReplaceDocument, uc.replace_document)
    command_bus.register(ArchiveDocument, uc.archive_document)
    command_bus.register(RestoreDocument, uc.restore_document)
    command_bus.register(DeleteDocument, uc.delete_document)
    command_bus.register(ChunkDocument, uc.chunk_document)
    command_bus.register(GenerateEmbeddings, uc.generate_embeddings)
    command_bus.register(IndexKnowledgeCollection, uc.index_collection)
    command_bus.register(SearchKnowledge, uc.search)
    query_bus.register(ListKnowledgeCollections, q.collections)
    query_bus.register(GetKnowledgeCollection, q.collection)
    query_bus.register(ListDocuments, q.documents)
    query_bus.register(ListChunks, q.chunks)
    query_bus.register(VectorStatus, q.vector_status)

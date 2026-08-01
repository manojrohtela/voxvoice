"""Knowledge Tool domain services (M8.15) — additive.

`KnowledgeService` owns the knowledge **assets + document pipeline + deterministic chunking**
(collections, documents, chunks; upload/replace/archive/restore/soft-delete/version; MIME + size
+ checksum + duplicate validation). `KnowledgeSearchService` owns **embedding generation, vector
indexing, and retrieval** through the injected `KnowledgeRetriever` seam (the real embedding
providers + vector stores live in `api.adapters`; the default is deterministic + offline).

**Knowledge is a Tool Framework capability.** These services manage assets and expose a search
for the Studio playground; at conversation time the Runtime invokes the Knowledge Tool through
the existing Tool Executor (M8.14) — no retrieval logic enters the Runtime. Reuse-first: writes
authorize via the existing workspace-scoped `PROJECT_MANAGE` (no new RBAC scope).
"""

from __future__ import annotations

from .diagnostics import (
    Diagnostic,
    FoundationDiagnosticCode,
    FoundationDiagnostics,
    Severity,
)
from .errors import ConflictError, FoundationValidationError, NotFoundError
from .events import FoundationEventType
from .knowledge_adapters import KnowledgeRetriever
from .knowledge_chunking import (
    MAX_DOCUMENT_BYTES,
    checksum,
    chunk_text,
    is_supported_mime,
)
from .knowledge_domain import (
    ContextPackage,
    DocumentStatus,
    EmbeddingStatus,
    IndexStatus,
    KnowledgeChunk,
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeStatus,
)
from .rbac import Permission
from .repositories import (
    KnowledgeChunkRepository,
    KnowledgeCollectionRepository,
    KnowledgeDocumentRepository,
    ProjectRepository,
)
from .seams import (
    PREFIX_KNOWLEDGE_CHUNK,
    PREFIX_KNOWLEDGE_COLLECTION,
    PREFIX_KNOWLEDGE_DOCUMENT,
)
from .services import _Base


def _diag(path, code, msg, fix) -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, msg, fix)


class KnowledgeService(_Base):
    """Collections + documents + deterministic chunking. Every write authorizes via the
    workspace-scoped `PROJECT_MANAGE` permission (reuse-first)."""

    def __init__(
        self,
        *,
        projects: ProjectRepository,
        collections: KnowledgeCollectionRepository,
        documents: KnowledgeDocumentRepository,
        chunks: KnowledgeChunkRepository,
        **base,
    ) -> None:
        super().__init__(**base)
        self._projects = projects
        self._collections = collections
        self._documents = documents
        self._chunks = chunks

    # ── collections ───────────────────────────────────────────────────────────────
    def _load_project(self, org_id, workspace_id, project_id):
        p = self._projects.get(org_id, workspace_id, project_id)
        if p is None:
            raise NotFoundError(f"project {project_id!r} not found in workspace {workspace_id!r}")
        return p

    def _load_collection(self, org_id, project_id, collection_id) -> KnowledgeCollection:
        c = self._collections.get(org_id, project_id, collection_id)
        if c is None or c.status is KnowledgeStatus.DELETED:
            raise NotFoundError(f"knowledge collection {collection_id!r} not found")
        return c

    def create_collection(
        self,
        actor_user_id,
        org_id,
        workspace_id,
        project_id,
        *,
        name,
        base="default",
        description="",
        embedding_provider="local",
        embedding_model="local-embed",
        chunk_size=800,
        chunk_overlap=100,
    ) -> KnowledgeCollection:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE, org_id=org_id, workspace_id=workspace_id
        )
        self._load_project(org_id, workspace_id, project_id)
        if not name.strip():
            raise FoundationValidationError(
                FoundationDiagnostics.of(
                    [
                        _diag(
                            "name",
                            FoundationDiagnosticCode.MISSING_REQUIRED_FIELD,
                            "collection name must be non-empty",
                            "give the collection a name",
                        )
                    ]
                )
            )
        if self._collections.by_name(org_id, project_id, name) is not None:
            raise ConflictError(f"knowledge collection {name!r} already exists in this project")
        collection = KnowledgeCollection(
            id=self._ids.new(PREFIX_KNOWLEDGE_COLLECTION),
            org_id=org_id,
            workspace_id=workspace_id,
            project_id=project_id,
            name=name,
            base=base,
            description=description,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            created_by=actor_user_id,
            created_at=self._clock.now(),
            updated_at=self._clock.now(),
        )
        self._collections.add(collection)
        self._emit_c(FoundationEventType.KNOWLEDGE_COLLECTION_CREATED, actor_user_id, collection)
        return collection

    def update_collection(
        self, actor_user_id, org_id, workspace_id, project_id, collection_id, **changes
    ) -> KnowledgeCollection:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE, org_id=org_id, workspace_id=workspace_id
        )
        c = self._load_collection(org_id, project_id, collection_id)
        updated = self._collections.update(c.with_updates(updated_at=self._clock.now(), **changes))
        self._emit_c(FoundationEventType.KNOWLEDGE_COLLECTION_UPDATED, actor_user_id, updated)
        return updated

    def _set_collection_status(self, actor, org, ws, project, cid, status, event, guard=None):
        self._require(actor, Permission.PROJECT_MANAGE, org_id=org, workspace_id=ws)
        c = self._load_collection(org, project, cid)
        if guard is not None and guard(c):
            raise ConflictError(f"collection {cid!r} invalid transition")
        updated = self._collections.update(
            c.with_updates(status=status, updated_at=self._clock.now())
        )
        self._emit_c(event, actor, updated)
        return updated

    def archive_collection(self, actor, org, ws, project, cid):
        return self._set_collection_status(
            actor,
            org,
            ws,
            project,
            cid,
            KnowledgeStatus.ARCHIVED,
            FoundationEventType.KNOWLEDGE_COLLECTION_ARCHIVED,
            guard=lambda c: c.status is KnowledgeStatus.ARCHIVED,
        )

    def restore_collection(self, actor, org, ws, project, cid):
        return self._set_collection_status(
            actor,
            org,
            ws,
            project,
            cid,
            KnowledgeStatus.ACTIVE,
            FoundationEventType.KNOWLEDGE_COLLECTION_RESTORED,
            guard=lambda c: c.status is not KnowledgeStatus.ARCHIVED,
        )

    def delete_collection(self, actor, org, ws, project, cid):
        return self._set_collection_status(
            actor,
            org,
            ws,
            project,
            cid,
            KnowledgeStatus.DELETED,
            FoundationEventType.KNOWLEDGE_COLLECTION_DELETED,
        )

    def list_collections(self, org_id, project_id, *, include_archived=True):
        out = []
        for c in self._collections.list(org_id, project_id):
            if c.status is KnowledgeStatus.DELETED:
                continue
            if not include_archived and c.status is KnowledgeStatus.ARCHIVED:
                continue
            out.append(c)
        return tuple(out)

    def get_collection(self, org_id, project_id, collection_id):
        return self._load_collection(org_id, project_id, collection_id)

    # ── documents (pipeline) ──────────────────────────────────────────────────────
    def _validate_document(self, mime_type: str, content: str) -> None:
        out = []
        if not is_supported_mime(mime_type):
            out.append(
                _diag(
                    "mime_type",
                    FoundationDiagnosticCode.UNSUPPORTED_MIME,
                    f"unsupported MIME type {mime_type!r}",
                    "use a supported text MIME type",
                )
            )
        if len(content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            out.append(
                _diag(
                    "content",
                    FoundationDiagnosticCode.DOCUMENT_TOO_LARGE,
                    f"document exceeds {MAX_DOCUMENT_BYTES} bytes",
                    "upload a smaller document",
                )
            )
        d = FoundationDiagnostics.of(out)
        if d.has_errors:
            raise FoundationValidationError(d)

    def upload_document(
        self,
        actor_user_id,
        org_id,
        workspace_id,
        project_id,
        collection_id,
        *,
        name,
        content,
        mime_type="text/plain",
        metadata=None,
    ) -> KnowledgeDocument:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE, org_id=org_id, workspace_id=workspace_id
        )
        self._load_collection(org_id, project_id, collection_id)
        self._validate_document(mime_type, content)
        cs = checksum(content)
        if self._documents.by_checksum(org_id, collection_id, cs) is not None:
            raise FoundationValidationError(
                FoundationDiagnostics.of(
                    [
                        _diag(
                            "content",
                            FoundationDiagnosticCode.DUPLICATE_DOCUMENT,
                            "an identical document already exists in this collection",
                            "upload distinct content or replace the existing document",
                        )
                    ]
                )
            )
        doc = KnowledgeDocument(
            id=self._ids.new(PREFIX_KNOWLEDGE_DOCUMENT),
            org_id=org_id,
            workspace_id=workspace_id,
            project_id=project_id,
            collection_id=collection_id,
            name=name,
            mime_type=mime_type,
            size_bytes=len(content.encode("utf-8")),
            checksum=cs,
            content=content,
            version=1,
            status=DocumentStatus.ACTIVE,
            created_by=actor_user_id,
            created_at=self._clock.now(),
            updated_at=self._clock.now(),
            metadata=dict(metadata or {}),
        )
        self._documents.add(doc)
        self._emit_d(FoundationEventType.KNOWLEDGE_DOCUMENT_UPLOADED, actor_user_id, doc)
        return doc

    def _load_document(self, org_id, collection_id, document_id) -> KnowledgeDocument:
        d = self._documents.get(org_id, collection_id, document_id)
        if d is None or d.status is DocumentStatus.DELETED:
            raise NotFoundError(f"document {document_id!r} not found")
        return d

    def replace_document(
        self,
        actor_user_id,
        org_id,
        workspace_id,
        project_id,
        collection_id,
        document_id,
        *,
        content,
        mime_type=None,
        metadata=None,
    ) -> KnowledgeDocument:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE, org_id=org_id, workspace_id=workspace_id
        )
        d = self._load_document(org_id, collection_id, document_id)
        mt = mime_type or d.mime_type
        self._validate_document(mt, content)
        updated = self._documents.update(
            d.with_updates(
                content=content,
                mime_type=mt,
                checksum=checksum(content),
                size_bytes=len(content.encode("utf-8")),
                version=d.version + 1,
                metadata=metadata,
                updated_at=self._clock.now(),
            )
        )
        self._chunks.delete_for_document(org_id, document_id)  # stale chunks removed on replace
        self._emit_d(FoundationEventType.KNOWLEDGE_DOCUMENT_VERSIONED, actor_user_id, updated)
        return updated

    def _set_document_status(self, actor, org, ws, project, cid, did, status, event):
        self._require(actor, Permission.PROJECT_MANAGE, org_id=org, workspace_id=ws)
        d = self._load_document(org, cid, did)
        updated = self._documents.update(
            d.with_updates(status=status, updated_at=self._clock.now())
        )
        self._emit_d(event, actor, updated)
        return updated

    def archive_document(self, actor, org, ws, project, cid, did):
        return self._set_document_status(
            actor,
            org,
            ws,
            project,
            cid,
            did,
            DocumentStatus.ARCHIVED,
            FoundationEventType.KNOWLEDGE_DOCUMENT_ARCHIVED,
        )

    def restore_document(self, actor, org, ws, project, cid, did):
        return self._set_document_status(
            actor,
            org,
            ws,
            project,
            cid,
            did,
            DocumentStatus.ACTIVE,
            FoundationEventType.KNOWLEDGE_DOCUMENT_RESTORED,
        )

    def delete_document(self, actor, org, ws, project, cid, did):
        self._chunks.delete_for_document(org, did)
        return self._set_document_status(
            actor,
            org,
            ws,
            project,
            cid,
            did,
            DocumentStatus.DELETED,
            FoundationEventType.KNOWLEDGE_DOCUMENT_DELETED,
        )

    def list_documents(self, org_id, collection_id):
        return tuple(
            d
            for d in self._documents.list(org_id, collection_id)
            if d.status is not DocumentStatus.DELETED
        )

    def get_document(self, org_id, collection_id, document_id):
        return self._load_document(org_id, collection_id, document_id)

    # ── deterministic chunking ────────────────────────────────────────────────────
    def chunk_document(
        self, actor_user_id, org_id, workspace_id, project_id, collection_id, document_id
    ) -> tuple[KnowledgeChunk, ...]:
        self._require(
            actor_user_id, Permission.PROJECT_MANAGE, org_id=org_id, workspace_id=workspace_id
        )
        collection = self._load_collection(org_id, project_id, collection_id)
        d = self._load_document(org_id, collection_id, document_id)
        self._chunks.delete_for_document(org_id, document_id)
        pieces = chunk_text(d.content, size=collection.chunk_size, overlap=collection.chunk_overlap)
        created = []
        for piece in pieces:
            chunk = KnowledgeChunk(
                id=self._ids.new(PREFIX_KNOWLEDGE_CHUNK),
                org_id=org_id,
                collection_id=collection_id,
                document_id=document_id,
                index=piece["index"],
                text=piece["text"],
                checksum=piece["checksum"],
                embedding_status=EmbeddingStatus.PENDING,
                index_status=IndexStatus.UNINDEXED,
                created_at=self._clock.now(),
                metadata={**piece["metadata"], "document": d.name},
            )
            self._chunks.add(chunk)
            created.append(chunk)
        self._emit(
            FoundationEventType.KNOWLEDGE_CHUNK_CREATED,
            org_id=org_id,
            actor=actor_user_id,
            resource=document_id,
            workspace_id=workspace_id,
            project_id=project_id,
            collection_id=collection_id,
            chunks=len(created),
        )
        return tuple(created)

    def list_chunks(self, org_id, document_id):
        return self._chunks.list_for_document(org_id, document_id)

    def get_chunk(self, org_id, chunk_id):
        c = self._chunks.get(org_id, chunk_id)
        if c is None:
            raise NotFoundError(f"chunk {chunk_id!r} not found")
        return c

    # ── events ────────────────────────────────────────────────────────────────────
    def _emit_c(self, event, actor, c: KnowledgeCollection):
        self._emit(
            event,
            org_id=c.org_id,
            actor=actor,
            resource=c.id,
            workspace_id=c.workspace_id,
            project_id=c.project_id,
            name=c.name,
        )

    def _emit_d(self, event, actor, d: KnowledgeDocument):
        self._emit(
            event,
            org_id=d.org_id,
            actor=actor,
            resource=d.id,
            workspace_id=d.workspace_id,
            project_id=d.project_id,
            collection_id=d.collection_id,
            name=d.name,
        )


class KnowledgeSearchService(_Base):
    """Embedding generation, vector indexing, and retrieval over the injected `KnowledgeRetriever`
    seam. The retrieval pipeline (embed → search → rank → filter → select → context package) is
    the retriever's; this service persists embeddings/index status + audits + authorizes."""

    def __init__(
        self,
        *,
        projects: ProjectRepository,
        collections: KnowledgeCollectionRepository,
        chunks: KnowledgeChunkRepository,
        retriever: KnowledgeRetriever,
        **base,
    ) -> None:
        super().__init__(**base)
        self._projects = projects
        self._collections = collections
        self._chunks = chunks
        self._retriever = retriever

    def _collection(self, org_id, project_id, collection_id) -> KnowledgeCollection:
        c = self._collections.get(org_id, project_id, collection_id)
        if c is None or c.status is KnowledgeStatus.DELETED:
            raise NotFoundError(f"knowledge collection {collection_id!r} not found")
        return c

    def generate_embeddings(self, actor, org, ws, project, collection_id) -> int:
        self._require(actor, Permission.PROJECT_MANAGE, org_id=org, workspace_id=ws)
        self._collection(org, project, collection_id)
        chunks = list(self._chunks.list_for_collection(org, collection_id))
        vectors = self._retriever.embed([c.text for c in chunks])
        for chunk, vec in zip(chunks, vectors, strict=False):
            self._chunks.update(chunk.with_embedding(tuple(vec)))
        self._emit(
            FoundationEventType.KNOWLEDGE_EMBEDDING_GENERATED,
            org_id=org,
            actor=actor,
            resource=collection_id,
            workspace_id=ws,
            project_id=project,
            chunks=len(chunks),
        )
        return len(chunks)

    def index_collection(self, actor, org, ws, project, collection_id) -> int:
        self._require(actor, Permission.PROJECT_MANAGE, org_id=org, workspace_id=ws)
        self._collection(org, project, collection_id)
        indexed = 0
        for chunk in self._chunks.list_for_collection(org, collection_id):
            if chunk.embedding_status is EmbeddingStatus.GENERATED:
                self._chunks.update(chunk.indexed())
                indexed += 1
        self._emit(
            FoundationEventType.KNOWLEDGE_INDEXED,
            org_id=org,
            actor=actor,
            resource=collection_id,
            workspace_id=ws,
            project_id=project,
            chunks=indexed,
        )
        return indexed

    def search(
        self,
        actor,
        org,
        ws,
        project,
        collection_id,
        query,
        *,
        top_k=5,
        threshold=0.0,
        filters=None,
        audit=True,
    ) -> ContextPackage:
        self._require(actor, Permission.PROJECT_MANAGE, org_id=org, workspace_id=ws)
        self._collection(org, project, collection_id)
        indexed = [
            c
            for c in self._chunks.list_for_collection(org, collection_id)
            if c.index_status is IndexStatus.INDEXED
        ]
        pkg = self._retriever.retrieve(
            query,
            indexed,
            top_k=top_k,
            threshold=threshold,
            filters=filters,
            collection_id=collection_id,
        )
        if audit:
            self._emit(
                FoundationEventType.KNOWLEDGE_RETRIEVED,
                org_id=org,
                actor=actor,
                resource=collection_id,
                workspace_id=ws,
                project_id=project,
                hits=len(pkg.chunks),
            )
        return pkg

    def vector_status(self, org, project, collection_id) -> dict:
        chunks = self._chunks.list_for_collection(org, collection_id)
        embedded = sum(1 for c in chunks if c.embedding_status is EmbeddingStatus.GENERATED)
        indexed = sum(1 for c in chunks if c.index_status is IndexStatus.INDEXED)
        return {
            "collection_id": collection_id,
            "total_chunks": len(chunks),
            "embedded": embedded,
            "indexed": indexed,
        }

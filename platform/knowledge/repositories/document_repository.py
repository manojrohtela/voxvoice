"""DocumentRepository — documents, raw blobs, and version records (M5.2B-1).

The only code that binds the `ObjectStore` (raw bytes) and the document/version
collections of the `DocumentStore`. Translates `Document`/`DocumentVersion` ↔ storage
documents via the serializer. No chunking, no embedding, no ranking — pure persistence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.providers.stores import DocumentStore, ObjectStore

from ..config import CollectionsConfig
from ..domain import Document, DocumentVersion
from ..serializer import KnowledgeSerializer


class DocumentRepository:
    def __init__(
        self,
        document_store: DocumentStore,
        object_store: ObjectStore,
        serializer: KnowledgeSerializer,
        *,
        collections: CollectionsConfig | None = None,
    ) -> None:
        self._store = document_store
        self._objects = object_store
        self._serializer = serializer
        self._c = collections or CollectionsConfig()

    # ── documents ────────────────────────────────────────────────────────────
    async def add(self, document: Document) -> None:
        await self._store.upsert(
            self._c.documents, document.id, self._serializer.document_to_document(document)
        )

    async def get(self, document_id: str) -> Document | None:
        doc = await self._store.get(self._c.documents, document_id)
        return self._serializer.document_from_document(doc) if doc is not None else None

    async def update(self, document: Document) -> None:
        await self.add(document)

    async def remove(self, document_id: str) -> None:
        await self._store.delete(self._c.documents, document_id)

    async def query(self, filter: Mapping[str, object]) -> Sequence[Document]:
        docs = await self._store.query(self._c.documents, filter)
        return [self._serializer.document_from_document(d) for d in docs]

    # ── raw blobs ────────────────────────────────────────────────────────────
    async def put_blob(self, document_id: str, data: bytes, *, content_type: str) -> None:
        await self._objects.put(document_id, data, content_type=content_type)

    async def get_blob(self, document_id: str) -> bytes | None:
        return await self._objects.get(document_id)

    async def delete_blob(self, document_id: str) -> None:
        await self._objects.delete(document_id)

    # ── version records ──────────────────────────────────────────────────────
    async def add_version(self, version: DocumentVersion) -> None:
        await self._store.upsert(
            self._c.versions, version.version_id, self._serializer.version_to_document(version)
        )

    async def get_version(self, version_id: str) -> DocumentVersion | None:
        v = await self._store.get(self._c.versions, version_id)
        return self._serializer.version_from_document(v) if v is not None else None

    async def list_versions(self, document_id: str) -> Sequence[DocumentVersion]:
        rows = await self._store.query(self._c.versions, {"document_id": document_id})
        versions = [self._serializer.version_from_document(r) for r in rows]
        versions.sort(key=lambda v: v.number)
        return versions

    async def remove_versions(self, document_id: str) -> None:
        for v in await self.list_versions(document_id):
            await self._store.delete(self._c.versions, v.version_id)

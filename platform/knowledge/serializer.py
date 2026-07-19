"""Knowledge serialization (M5.2B-1).

Deterministic, provider-neutral translation between the domain aggregates (`Document`,
`Chunk`, `DocumentVersion`) and the plain dict shapes used by storage, events, and the
future API. Round-trippable; no I/O. Raw document bytes are **not** serialized here
(they live in the object store); the document record carries the normalized text.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import (
    Chunk,
    ChunkState,
    Document,
    DocumentState,
    DocumentVersion,
    ImportOrigin,
    KnowledgeMetadata,
    KnowledgeScope,
    KnowledgeType,
    SourceDescriptor,
)


def _scope_to_dict(s: KnowledgeScope) -> dict[str, Any]:
    return {"org_id": s.org_id, "user_id": s.user_id, "department": s.department}


def _scope_from_dict(d: Mapping[str, Any]) -> KnowledgeScope:
    return KnowledgeScope(
        org_id=d.get("org_id"), user_id=d.get("user_id"), department=d.get("department")
    )


class KnowledgeSerializer:
    """Stateless, deterministic (de)serializer. Safe to share across runtimes."""

    # ── Document ─────────────────────────────────────────────────────────────
    def document_to_document(self, doc: Document) -> dict[str, Any]:
        m = doc.metadata
        return {
            "id": doc.id,
            "scope": _scope_to_dict(doc.scope),
            "knowledge_type": doc.knowledge_type.value,
            "state": doc.state.value,
            "version": doc.version,
            "version_id": doc.version_id,
            "content_hash": doc.content_hash,
            "text": doc.text,
            "chunk_ids": list(doc.chunk_ids),
            "source": {
                "uri": doc.source.uri,
                "mime": doc.source.mime,
                "origin": doc.source.origin.value,
                "title": doc.source.title,
                "extra": dict(doc.source.extra),
            },
            "metadata": {
                "title": m.title,
                "author": m.author,
                "lang": m.lang,
                "source_uri": m.source_uri,
                "mime": m.mime,
                "origin": m.origin,
                "content_hash": m.content_hash,
                "confidence": m.confidence,
                "tags": list(m.tags),
                "created_at": m.created_at,
                "updated_at": m.updated_at,
                "extra": dict(m.extra),
            },
        }

    def document_from_document(self, d: Mapping[str, Any]) -> Document:
        src = d.get("source", {})
        md = d.get("metadata", {})
        return Document(
            id=d["id"],
            scope=_scope_from_dict(d.get("scope", {})),
            knowledge_type=KnowledgeType(d["knowledge_type"]),
            source=SourceDescriptor(
                uri=src.get("uri", ""),
                mime=src.get("mime", "text/plain"),
                origin=ImportOrigin(src.get("origin", ImportOrigin.IMPORTED.value)),
                title=src.get("title"),
                extra=dict(src.get("extra", {})),
            ),
            text=d.get("text", ""),
            content_hash=d.get("content_hash", ""),
            version=d.get("version", 1),
            version_id=d.get("version_id", ""),
            state=DocumentState(d.get("state", DocumentState.IMPORTED.value)),
            metadata=KnowledgeMetadata(
                title=md.get("title"),
                author=md.get("author"),
                lang=md.get("lang", "und"),
                source_uri=md.get("source_uri"),
                mime=md.get("mime"),
                origin=md.get("origin"),
                content_hash=md.get("content_hash"),
                confidence=md.get("confidence", 1.0),
                tags=tuple(md.get("tags", ())),
                created_at=md.get("created_at", 0.0),
                updated_at=md.get("updated_at", 0.0),
                extra=dict(md.get("extra", {})),
            ),
            chunk_ids=tuple(d.get("chunk_ids", ())),
        )

    # ── Chunk ────────────────────────────────────────────────────────────────
    def chunk_to_document(self, c: Chunk) -> dict[str, Any]:
        return {
            "id": c.id,
            "document_id": c.document_id,
            "version": c.version,
            "index": c.index,
            "content": c.content,
            "content_hash": c.content_hash,
            "start": c.start,
            "end": c.end,
            "token_count": c.token_count,
            "strategy": c.strategy,
            "state": c.state.value,
            "embedded": c.embedded,
            "parent_id": c.parent_id,
            "prev_id": c.prev_id,
            "next_id": c.next_id,
            "metadata": dict(c.metadata),
        }

    def chunk_from_document(self, d: Mapping[str, Any]) -> Chunk:
        return Chunk(
            id=d["id"],
            document_id=d["document_id"],
            version=d["version"],
            index=d["index"],
            content=d["content"],
            content_hash=d["content_hash"],
            start=d["start"],
            end=d["end"],
            token_count=d["token_count"],
            strategy=d.get("strategy", "sliding_window"),
            state=ChunkState(d.get("state", ChunkState.CREATED.value)),
            embedded=d.get("embedded", False),
            parent_id=d.get("parent_id"),
            prev_id=d.get("prev_id"),
            next_id=d.get("next_id"),
            metadata=dict(d.get("metadata", {})),
        )

    # ── DocumentVersion ──────────────────────────────────────────────────────
    def version_to_document(self, v: DocumentVersion) -> dict[str, Any]:
        return {
            "version_id": v.version_id,
            "document_id": v.document_id,
            "number": v.number,
            "content_hash": v.content_hash,
            "created_at": v.created_at,
            "state": v.state.value,
            "chunk_ids": list(v.chunk_ids),
        }

    def version_from_document(self, d: Mapping[str, Any]) -> DocumentVersion:
        return DocumentVersion(
            version_id=d["version_id"],
            document_id=d["document_id"],
            number=d["number"],
            content_hash=d["content_hash"],
            created_at=d.get("created_at", 0.0),
            state=DocumentState(d.get("state", DocumentState.ACTIVE.value)),
            chunk_ids=tuple(d.get("chunk_ids", ())),
        )

    # ── event payload (privacy-lean) + API view ──────────────────────────────
    def document_event_payload(self, doc: Document, *, phase: str) -> dict[str, Any]:
        return {
            "phase": phase,
            "id": doc.id,
            "version": doc.version,
            "state": doc.state.value,
            "knowledge_type": doc.knowledge_type.value,
            "scope": _scope_to_dict(doc.scope),
            "chunks": len(doc.chunk_ids),
            "content_length": len(doc.text),
        }

    def document_to_api(self, doc: Document) -> dict[str, Any]:
        return {
            "id": doc.id,
            "knowledge_type": doc.knowledge_type.value,
            "state": doc.state.value,
            "version": doc.version,
            "scope": _scope_to_dict(doc.scope),
            "title": doc.metadata.title,
            "source_uri": doc.source.uri,
            "chunks": len(doc.chunk_ids),
        }

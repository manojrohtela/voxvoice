"""Memory serialization (M5.1B-1).

One deterministic, provider-neutral translation between `MemoryRecord` and the plain
dict shapes used by the four consumers:

- **storage**  — `to_document`/`from_document` (round-trippable; DocumentStore form)
- **events**   — `to_event_payload` (the `data` of a `memory.*` event → Replay/Gateway)
- **API**      — `to_api` (a stable public view; the future HTTP surface)
- **hits**     — `to_hit` (a `MemoryHit` for the port's retrieval result)

No I/O, no ranking. The embedding vector is **not** serialized here — it lives in the
vector store; the document only records whether the record was indexed (`embedded`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import (
    MemoryHit,
    MemoryId,
    MemoryMetadata,
    MemoryRecord,
    MemoryScope,
    MemoryState,
    MemoryTier,
    MemoryType,
)


def _scope_to_dict(scope: MemoryScope) -> dict[str, Any]:
    return {
        "session_id": scope.session_id,
        "conversation_id": scope.conversation_id,
        "user_id": scope.user_id,
        "org_id": scope.org_id,
    }


def _scope_from_dict(d: Mapping[str, Any]) -> MemoryScope:
    return MemoryScope(
        session_id=d.get("session_id"),
        conversation_id=d.get("conversation_id"),
        user_id=d.get("user_id"),
        org_id=d.get("org_id"),
    )


class MemorySerializer:
    """Stateless, deterministic (de)serializer. Safe to share across runtimes."""

    # ── storage form (round-trippable) ───────────────────────────────────────
    def to_document(self, record: MemoryRecord) -> dict[str, Any]:
        m = record.metadata
        return {
            "id": record.id.value,
            "content": record.content,
            "tier": record.tier.value,
            "state": record.state.value,
            "scope": _scope_to_dict(record.scope),
            "embedded": record.embedded,
            "metadata": {
                "type": m.type.value,
                "importance": m.importance,
                "confidence": m.confidence,
                "tags": list(m.tags),
                "source": m.source,
                "origin": m.origin,
                "created_by": m.created_by,
                "agent_id": m.agent_id,
                "correlation_id": m.correlation_id,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
                "expires_at": m.expires_at,
                "access_count": m.access_count,
                "last_access_at": m.last_access_at,
                "extra": dict(m.extra),
            },
        }

    def from_document(self, doc: Mapping[str, Any]) -> MemoryRecord:
        md = doc.get("metadata", {})
        metadata = MemoryMetadata(
            type=MemoryType(md.get("type", MemoryType.NOTE.value)),
            importance=md.get("importance", 0.5),
            confidence=md.get("confidence", 1.0),
            tags=tuple(md.get("tags", ())),
            source=md.get("source"),
            origin=md.get("origin"),
            created_by=md.get("created_by"),
            agent_id=md.get("agent_id"),
            correlation_id=md.get("correlation_id"),
            created_at=md.get("created_at", 0.0),
            updated_at=md.get("updated_at", 0.0),
            expires_at=md.get("expires_at"),
            access_count=md.get("access_count", 0),
            last_access_at=md.get("last_access_at"),
            extra=dict(md.get("extra", {})),
        )
        return MemoryRecord(
            id=MemoryId(doc["id"]),
            content=doc["content"],
            tier=MemoryTier(doc["tier"]),
            scope=_scope_from_dict(doc.get("scope", {})),
            state=MemoryState(doc.get("state", MemoryState.CREATED.value)),
            metadata=metadata,
            embedded=doc.get("embedded", False),
        )

    # ── event payload (privacy-lean; Replay/Gateway see this) ────────────────
    def to_event_payload(self, record: MemoryRecord) -> dict[str, Any]:
        return {
            "id": record.id.value,
            "tier": record.tier.value,
            "state": record.state.value,
            "type": record.metadata.type.value,
            "scope": _scope_to_dict(record.scope),
            "importance": record.metadata.importance,
            "tags": list(record.metadata.tags),
            "content_length": len(record.content),
        }

    # ── public/API view (stable shape for a later HTTP surface) ──────────────
    def to_api(self, record: MemoryRecord) -> dict[str, Any]:
        return {
            "id": record.id.value,
            "content": record.content,
            "tier": record.tier.value,
            "state": record.state.value,
            "type": record.metadata.type.value,
            "scope": _scope_to_dict(record.scope),
            "importance": record.metadata.importance,
            "tags": list(record.metadata.tags),
            "created_at": record.metadata.created_at,
            "updated_at": record.metadata.updated_at,
            "embedded": record.embedded,
        }

    # ── retrieval hit (the port's result DTO) ────────────────────────────────
    def to_hit(self, record: MemoryRecord, *, score: float) -> MemoryHit:
        return MemoryHit(
            id=record.id.value,
            content=record.content,
            tier=record.tier,
            score=score,
            metadata={
                "type": record.metadata.type.value,
                "tags": list(record.metadata.tags),
                "importance": record.metadata.importance,
            },
        )

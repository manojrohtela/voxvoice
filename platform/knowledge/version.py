"""VersionManager (M5.2B-1) — immutable document versions.

Owns version identity, the current-version pointer, supersede-on-update, history, and
rollback. It orchestrates; the `DocumentRepository` is the only thing that persists
version records (repositories are the sole store-touchers). Deterministic: version
numbers are monotonic, version ids are content-derived (`version_id_for`), timestamps
are injected-clock values.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .domain import Document, DocumentState, DocumentVersion
from .ids import version_id_for
from .repositories.document_repository import DocumentRepository


class VersionManager:
    def __init__(self, document_repo: DocumentRepository, *, clock: Callable[[], float]) -> None:
        self._docs = document_repo
        self._clock = clock

    async def create_initial(self, document: Document) -> DocumentVersion:
        version = DocumentVersion(
            version_id=version_id_for(document.id, 1),
            document_id=document.id,
            number=1,
            content_hash=document.content_hash,
            created_at=self._clock(),
            state=DocumentState.ACTIVE,
            chunk_ids=document.chunk_ids,
        )
        await self._docs.add_version(version)
        return version

    async def create_next(self, document: Document, *, number: int) -> DocumentVersion:
        """Archive the prior current version and record a new ACTIVE version."""
        for v in await self._docs.list_versions(document.id):
            if v.state is DocumentState.ACTIVE:
                await self._docs.add_version(v.with_state(DocumentState.ARCHIVED))
        version = DocumentVersion(
            version_id=version_id_for(document.id, number),
            document_id=document.id,
            number=number,
            content_hash=document.content_hash,
            created_at=self._clock(),
            state=DocumentState.ACTIVE,
            chunk_ids=document.chunk_ids,
        )
        await self._docs.add_version(version)
        return version

    async def current(self, document_id: str) -> DocumentVersion | None:
        active = [v for v in await self._docs.list_versions(document_id)
                  if v.state is DocumentState.ACTIVE]
        return max(active, key=lambda v: v.number) if active else None

    async def history(self, document_id: str) -> Sequence[DocumentVersion]:
        return await self._docs.list_versions(document_id)

    async def rollback(self, document_id: str, *, to_number: int) -> DocumentVersion | None:
        """Re-activate a historical version and archive the rest. Returns the version
        rolled back to (or None if absent). Chunks of every version remain stored."""
        target: DocumentVersion | None = None
        versions = await self._docs.list_versions(document_id)
        for v in versions:
            if v.number == to_number:
                target = v.with_state(DocumentState.ACTIVE)
                await self._docs.add_version(target)
            elif v.state is DocumentState.ACTIVE:
                await self._docs.add_version(v.with_state(DocumentState.ARCHIVED))
        return target

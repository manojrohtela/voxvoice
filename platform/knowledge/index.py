"""KnowledgeIndex (M5.2B-1) — keyword + metadata inverted index (write path).

A deterministic in-process index that ingestion **registers** chunks/documents into:
term → chunk-ids (keyword) and metadata field/value → document-ids. Only the write path
(`register`/`remove`) exists in the core milestone; querying it is retrieval
intelligence (M5.2B-2). It is a *derived* structure (rebuildable from the repositories),
so it holds no store handle — no repository-boundary violation.

In-process only in the core milestone; a durable index adapter is a future additive
extension.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .domain import Chunk, Document

_TOKEN = re.compile(r"[a-z0-9]+")


class KnowledgeIndex:
    def __init__(self) -> None:
        self._terms: dict[str, set[str]] = {}  # term → chunk ids
        self._chunk_terms: dict[str, set[str]] = {}  # chunk id → its terms (for removal)
        self._meta: dict[tuple[str, str], set[str]] = {}  # (field, value) → doc ids
        self._doc_meta_keys: dict[str, set[tuple[str, str]]] = {}  # doc id → its meta keys

    def register(self, document: Document, chunks: Sequence[Chunk]) -> None:
        # metadata index (document-level)
        keys = {
            ("knowledge_type", document.knowledge_type.value),
            ("org_id", document.scope.org_id or ""),
            ("lang", document.metadata.lang),
        } | {("tag", t) for t in document.metadata.tags}
        for key in keys:
            self._meta.setdefault(key, set()).add(document.id)
        self._doc_meta_keys.setdefault(document.id, set()).update(keys)

        # keyword index (chunk-level)
        for chunk in chunks:
            terms = set(_TOKEN.findall(chunk.content.lower()))
            self._chunk_terms.setdefault(chunk.id, set()).update(terms)
            for t in terms:
                self._terms.setdefault(t, set()).add(chunk.id)

    def remove(self, document: Document, chunks: Sequence[Chunk]) -> None:
        for key in self._doc_meta_keys.pop(document.id, set()):
            bucket = self._meta.get(key)
            if bucket is not None:
                bucket.discard(document.id)
                if not bucket:
                    del self._meta[key]
        for chunk in chunks:
            for t in self._chunk_terms.pop(chunk.id, set()):
                bucket = self._terms.get(t)
                if bucket is not None:
                    bucket.discard(chunk.id)
                    if not bucket:
                        del self._terms[t]

    # ── read path (M5.2B-2) — deterministic keyword + metadata lookup ─────────
    def match_terms(self, terms: Sequence[str]) -> set[str]:
        """Chunk ids whose content contains ANY of the query terms (lexical OR).
        Empty terms → empty set (the caller falls back to a broader candidate cut)."""
        out: set[str] = set()
        for t in terms:
            bucket = self._terms.get(t.lower())
            if bucket:
                out |= bucket
        return out

    def match_metadata(self, filters: Mapping[str, str]) -> set[str] | None:
        """Document ids matching ALL (field, value) filters (metadata AND). Returns None
        when there are no filters (meaning 'no metadata constraint')."""
        if not filters:
            return None
        result: set[str] | None = None
        for field, value in filters.items():
            bucket = self._meta.get((field, value), set())
            result = set(bucket) if result is None else (result & bucket)
            if not result:
                return set()
        return result or set()

    def statistics(self) -> dict[str, int]:
        return {
            "terms": len(self._terms),
            "indexed_chunks": len(self._chunk_terms),
            "metadata_keys": len(self._meta),
            "indexed_documents": len(self._doc_meta_keys),
        }

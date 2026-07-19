"""KnowledgeStore (M5.2B-1) — the resolved storage bundle.

A small immutable holder for the four `core.providers.stores` contracts + the embedder
that the Knowledge Runtime binds to. The factory builds it (choosing adapters); the
repositories receive the individual stores from it. Provider-neutral: nothing here names
a vendor — only the contract types.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.providers.stores import DocumentStore, Embedder, MetadataStore, ObjectStore, VectorStore


@dataclass(frozen=True, slots=True)
class KnowledgeStore:
    object_store: ObjectStore
    document_store: DocumentStore
    vector_store: VectorStore
    metadata_store: MetadataStore
    embedder: Embedder

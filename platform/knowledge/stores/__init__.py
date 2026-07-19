"""knowledge.stores — deterministic local storage adapters + the store bundle
(M5.2B-1, development only). Provider-neutral: the runtime binds to the contracts; a
production adapter swaps in via the registry with no runtime change.
"""

from .embedder import DeterministicLocalEmbedder
from .knowledge_store import KnowledgeStore
from .local_stores import (
    InMemoryDocumentStore,
    InMemoryMetadataStore,
    InMemoryObjectStore,
    InMemoryVectorStore,
)

__all__ = [
    "KnowledgeStore",
    "InMemoryObjectStore",
    "InMemoryDocumentStore",
    "InMemoryVectorStore",
    "InMemoryMetadataStore",
    "DeterministicLocalEmbedder",
]

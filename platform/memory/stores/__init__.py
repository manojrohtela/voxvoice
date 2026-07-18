"""memory.stores — deterministic local storage adapters (M5.1B-1, development only).

Reference implementations of the `core.providers.stores` contracts backed by in-process
dicts, plus a `DeterministicLocalEmbedder`. They exist so the Memory Runtime is fully
runnable and testable with **zero infrastructure** — no database, no network, no
randomness. Provider-neutral by construction: the runtime binds to the contracts, and a
production adapter (Postgres/pgvector/Qdrant/…) swaps in behind the same interfaces via
the Provider Registry with no runtime change.

Not for production: non-durable, single-process.
"""

from .embedder import DeterministicLocalEmbedder
from .memory_stores import (
    InMemoryDocumentStore,
    InMemoryMetadataStore,
    InMemoryVectorStore,
)

__all__ = [
    "InMemoryDocumentStore",
    "InMemoryVectorStore",
    "InMemoryMetadataStore",
    "DeterministicLocalEmbedder",
]

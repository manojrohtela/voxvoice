"""knowledge.repositories — the anti-corruption boundary (M5.2B-1).

The Knowledge Runtime and pipelines speak a domain vocabulary; these repositories are
the *only* code binding the `core.providers.stores` contracts and translating domain ↔
storage. Provider- and database-neutral; testable with in-memory stores.
"""

from .chunk_repository import ChunkRepository
from .document_repository import DocumentRepository
from .embedding_repository import EmbeddingRepository
from .knowledge_repository import KnowledgeRepository

__all__ = [
    "KnowledgeRepository",
    "DocumentRepository",
    "ChunkRepository",
    "EmbeddingRepository",
]

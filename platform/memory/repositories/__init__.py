"""memory.repositories — the anti-corruption boundary (M5.1B-1).

The Memory Runtime never touches a store or a provider directly. It speaks a domain
vocabulary (`MemoryRecord`, `MemoryFilter`) to three thin repositories, which are the
*only* code that binds to the `core.providers.stores` contracts and translates
domain ↔ storage. This keeps the runtime provider-neutral, database-neutral, and
deterministically testable (inject in-memory stores). Repositories carry no ranking
and no policy — pure translation + storage.
"""

from .memory_repository import MemoryRepository
from .metadata_repository import MetadataRepository
from .vector_repository import VectorRepository

__all__ = ["MemoryRepository", "VectorRepository", "MetadataRepository"]

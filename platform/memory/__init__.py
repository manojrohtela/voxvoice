"""memory — the Memory Runtime (Epic 5, M5.1B-1: core).

A provider-neutral, deterministic, event-driven runtime that **only manages memory** —
it never reasons, renders prompts, or executes tools. Built entirely on the M5.0
foundation: a `BusConsumer`/`RuntimeModule` implementing `core.ports.MemoryPort`, a
`core.fsm` record lifecycle, a repository layer over the `core.providers.stores`
contracts, and `memory.*` events that Replay/Gateway observe for free.

M5.1B-1 delivers the core only: domain model, record lifecycle, repositories, local
adapters, serializer, configuration, factory, and store/retrieve/forget with event
publishing. Retrieval intelligence, ranking, policies, expiration, cache, and
content-event ingestion are explicit non-goals (later milestones).
"""

from .config import (
    CacheConfig,
    EmbedderConfig,
    MemoryConfiguration,
    RepositoryConfig,
    StoreConfig,
    TierConfig,
)
from .domain import (
    MemoryFilter,
    MemoryHit,
    MemoryId,
    MemoryMetadata,
    MemoryQuery,
    MemoryRecord,
    MemoryResult,
    MemoryScope,
    MemoryState,
    MemoryTier,
    MemoryType,
)
from .factory import MemoryFactory
from .lifecycle import LIFECYCLE, MemoryTrigger, advance, machine_for
from .repositories import MemoryRepository, MetadataRepository, VectorRepository
from .runtime import MemoryRuntime
from .serializer import MemorySerializer

__all__ = [
    # runtime + composition
    "MemoryRuntime",
    "MemoryFactory",
    "MemorySerializer",
    # domain
    "MemoryId",
    "MemoryType",
    "MemoryState",
    "MemoryTier",
    "MemoryScope",
    "MemoryQuery",
    "MemoryHit",
    "MemoryMetadata",
    "MemoryRecord",
    "MemoryFilter",
    "MemoryResult",
    # lifecycle
    "MemoryTrigger",
    "LIFECYCLE",
    "advance",
    "machine_for",
    # repositories
    "MemoryRepository",
    "VectorRepository",
    "MetadataRepository",
    # config
    "MemoryConfiguration",
    "RepositoryConfig",
    "StoreConfig",
    "EmbedderConfig",
    "CacheConfig",
    "TierConfig",
]

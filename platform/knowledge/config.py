"""Knowledge Runtime configuration (M5.2B-1) — frozen, validated value objects.

Declarative wiring: store kinds, embedder, chunking, and collection names. Validated at
construction (fail fast). `default()` yields an all-in-memory development configuration.
Cache settings are declared but inert in the core milestone (the cache is intelligence).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from core.errors import ConfigError


@dataclass(frozen=True, slots=True)
class StoreConfig:
    kind: str = "in_memory"
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmbedderConfig:
    kind: str = "local"
    dimensions: int = 64

    def __post_init__(self) -> None:
        if self.dimensions <= 0:
            raise ConfigError("EmbedderConfig.dimensions must be positive")


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    """Sliding-window defaults (tokens). Semantic/hierarchical strategies are additive."""

    strategy: str = "sliding_window"
    size: int = 64
    overlap: int = 16

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ConfigError("ChunkConfig.size must be positive")
        if not (0 <= self.overlap < self.size):
            raise ConfigError("ChunkConfig.overlap must be in [0, size)")


@dataclass(frozen=True, slots=True)
class CollectionsConfig:
    documents: str = "documents"
    chunks: str = "chunks"
    versions: str = "versions"


@dataclass(frozen=True, slots=True)
class CacheConfig:
    """Repository-owned hot-chunk cache (M5.2B-2). `enabled=False` → no cache."""

    enabled: bool = True
    max_items: int = 512

    def __post_init__(self) -> None:
        if self.max_items < 0:
            raise ConfigError("CacheConfig.max_items must be >= 0")


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    """Retrieval intelligence defaults (M5.2B-2). The default profile the runtime uses
    when a caller does not specify one; the ranking freshness half-life (seconds)."""

    default_profile: str = "balanced"
    half_life_s: float = 86_400.0

    def __post_init__(self) -> None:
        if self.half_life_s <= 0:
            raise ConfigError("RetrievalConfig.half_life_s must be > 0")


@dataclass(frozen=True, slots=True)
class KnowledgeConfiguration:
    name: str = "knowledge"
    object_store: StoreConfig = field(default_factory=StoreConfig)
    document_store: StoreConfig = field(default_factory=StoreConfig)
    vector_store: StoreConfig = field(default_factory=StoreConfig)
    metadata_store: StoreConfig = field(default_factory=StoreConfig)
    embedder: EmbedderConfig = field(default_factory=EmbedderConfig)
    chunking: ChunkConfig = field(default_factory=ChunkConfig)
    collections: CollectionsConfig = field(default_factory=CollectionsConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    vectorize: bool = True  # embed chunks during ingestion (semantic index)

    @classmethod
    def default(cls) -> KnowledgeConfiguration:
        return cls()

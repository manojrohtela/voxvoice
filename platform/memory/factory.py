"""MemoryFactory — the composition root (M5.1B-1).

Assembles a complete `MemoryRuntime` from a `MemoryConfiguration`: it resolves store
kinds to adapters, builds the three repositories, and injects them. This is the *only*
place adapter kinds are named — everything else is contract-typed. An unknown kind
fails fast with `ConfigError`.

Only the deterministic local adapters ship in M5.1B-1; production kinds (postgres,
qdrant, redis, …) register here in later milestones with no change to the runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.errors import ConfigError
from core.providers.stores import DocumentStore, Embedder, MetadataStore, VectorStore

from .config import EmbedderConfig, MemoryConfiguration, StoreConfig
from .repositories import MemoryRepository, MetadataRepository, VectorRepository
from .runtime import MemoryRuntime
from .serializer import MemorySerializer
from .stores import (
    DeterministicLocalEmbedder,
    InMemoryDocumentStore,
    InMemoryMetadataStore,
    InMemoryVectorStore,
)

_LOCAL_KINDS = frozenset({"in_memory", "local"})


class MemoryFactory:
    @staticmethod
    def create(
        config: MemoryConfiguration,
        *,
        id_factory: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> MemoryRuntime:
        serializer = MemorySerializer()
        doc_store = _build_document_store(config.repository.document)
        vec_store = _build_vector_store(config.repository.vector)
        meta_store = _build_metadata_store(config.repository.metadata)
        embedder = _build_embedder(config.repository.embedder)

        return MemoryRuntime(
            config=config,
            memory_repo=MemoryRepository(
                doc_store, serializer, collection=config.repository.document.collection
            ),
            vector_repo=VectorRepository(vec_store, embedder),
            metadata_repo=MetadataRepository(meta_store),
            serializer=serializer,
            id_factory=id_factory,
            **kwargs,
        )

    @staticmethod
    def default(
        *, id_factory: Callable[[], str] | None = None, **kwargs: Any
    ) -> MemoryRuntime:
        return MemoryFactory.create(
            MemoryConfiguration.default(), id_factory=id_factory, **kwargs
        )


def _build_document_store(cfg: StoreConfig) -> DocumentStore:
    if cfg.kind in _LOCAL_KINDS:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")


def _build_vector_store(cfg: StoreConfig) -> VectorStore:
    if cfg.kind in _LOCAL_KINDS:
        return InMemoryVectorStore()
    raise ConfigError(f"unknown vector store kind {cfg.kind!r}")


def _build_metadata_store(cfg: StoreConfig) -> MetadataStore:
    if cfg.kind in _LOCAL_KINDS:
        return InMemoryMetadataStore()
    raise ConfigError(f"unknown metadata store kind {cfg.kind!r}")


def _build_embedder(cfg: EmbedderConfig) -> Embedder:
    if cfg.kind in _LOCAL_KINDS or cfg.kind == "deterministic":
        return DeterministicLocalEmbedder(dimensions=cfg.dimensions)
    raise ConfigError(f"unknown embedder kind {cfg.kind!r}")

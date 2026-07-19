"""KnowledgeFactory — the composition root (M5.2B-1).

Assembles a complete `KnowledgeRuntime` from a `KnowledgeConfiguration`: resolves store
kinds to adapters, builds the store bundle, repositories, version manager, index,
parser/chunker/embedder ports, and the pipelines, then injects them. The *only* place
adapter kinds are named; an unknown kind fails fast with `ConfigError`. Only local
deterministic adapters ship in M5.2B-1.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.errors import ConfigError
from core.providers.stores import (
    DocumentStore,
    Embedder,
    MetadataStore,
    ObjectStore,
    VectorStore,
)

from .config import EmbedderConfig, KnowledgeConfiguration, StoreConfig
from .index import KnowledgeIndex
from .pipeline import ChunkPipeline, DocumentPipeline, EmbeddingPipeline, PlainTextParser
from .pipeline.chunk_pipeline import SlidingWindowChunker
from .repositories import (
    ChunkRepository,
    DocumentRepository,
    EmbeddingRepository,
    KnowledgeRepository,
)
from .runtime import KnowledgeRuntime
from .serializer import KnowledgeSerializer
from .stores import (
    DeterministicLocalEmbedder,
    InMemoryDocumentStore,
    InMemoryMetadataStore,
    InMemoryObjectStore,
    InMemoryVectorStore,
    KnowledgeStore,
)
from .version import VersionManager

_LOCAL = frozenset({"in_memory", "local"})


class KnowledgeFactory:
    @staticmethod
    def create(
        config: KnowledgeConfiguration,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> KnowledgeRuntime:
        import time

        the_clock = clock or time.time
        serializer = KnowledgeSerializer()
        store = KnowledgeStore(
            object_store=_object_store(config.object_store),
            document_store=_document_store(config.document_store),
            vector_store=_vector_store(config.vector_store),
            metadata_store=_metadata_store(config.metadata_store),
            embedder=_embedder(config.embedder),
        )

        document_repo = DocumentRepository(
            store.document_store, store.object_store, serializer, collections=config.collections
        )
        chunk_repo = ChunkRepository(
            store.document_store, serializer, collections=config.collections
        )
        embedding_repo = EmbeddingRepository(store.vector_store, store.embedder)
        index = KnowledgeIndex()
        version_manager = VersionManager(document_repo, clock=the_clock)

        repository = KnowledgeRepository(
            documents=document_repo, chunks=chunk_repo, embeddings=embedding_repo,
            versions=version_manager, index=index,
        )
        pipeline = DocumentPipeline(
            document_repo=document_repo,
            chunk_repo=chunk_repo,
            index=index,
            version_manager=version_manager,
            parser=PlainTextParser(),
            chunk_pipeline=ChunkPipeline(
                SlidingWindowChunker(size=config.chunking.size, overlap=config.chunking.overlap)
            ),
            embedding_pipeline=EmbeddingPipeline(
                embedding_repo, vectorize=config.vectorize, clock=the_clock
            ),
            chunk_config=config.chunking,
            clock=the_clock,
        )
        return KnowledgeRuntime(
            config=config, repository=repository, pipeline=pipeline, serializer=serializer,
            id_factory=id_factory, clock=the_clock, **kwargs,
        )

    @staticmethod
    def default(
        *, id_factory: Callable[[], str] | None = None, **kwargs: Any
    ) -> KnowledgeRuntime:
        return KnowledgeFactory.create(
            KnowledgeConfiguration.default(), id_factory=id_factory, **kwargs
        )


def _object_store(cfg: StoreConfig) -> ObjectStore:
    if cfg.kind in _LOCAL:
        return InMemoryObjectStore()
    raise ConfigError(f"unknown object store kind {cfg.kind!r}")


def _document_store(cfg: StoreConfig) -> DocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")


def _vector_store(cfg: StoreConfig) -> VectorStore:
    if cfg.kind in _LOCAL:
        return InMemoryVectorStore()
    raise ConfigError(f"unknown vector store kind {cfg.kind!r}")


def _metadata_store(cfg: StoreConfig) -> MetadataStore:
    if cfg.kind in _LOCAL:
        return InMemoryMetadataStore()
    raise ConfigError(f"unknown metadata store kind {cfg.kind!r}")


def _embedder(cfg: EmbedderConfig) -> Embedder:
    if cfg.kind in _LOCAL or cfg.kind == "deterministic":
        return DeterministicLocalEmbedder(dimensions=cfg.dimensions)
    raise ConfigError(f"unknown embedder kind {cfg.kind!r}")

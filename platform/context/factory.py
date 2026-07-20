"""ContextFactory — the composition root (M5.3B-1).

Assembles a complete `ContextRuntime` from a `ContextConfiguration` + the **injected
ports** (the concrete Memory/Knowledge/Prompt runtimes the composition root supplies).
Resolves store/tokenizer kinds to local adapters, builds the temporary repository, session
manager, source adapters, assembler, and runtime. The *only* place adapter kinds are named;
an unknown kind fails fast with `ConfigError`. Only local deterministic adapters ship in
M5.3B-1.

Dependency inversion: the factory takes `MemoryPort`/`KnowledgePort`/`PromptPort`
*instances* (never the packages), so `context` imports no sibling runtime.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.errors import ConfigError
from core.ports.knowledge import KnowledgePort
from core.ports.memory import MemoryPort
from core.ports.prompt import PromptPort
from core.providers.stores import DocumentStore, MetadataStore

from .assembly import ContextAssembler
from .budget import ContextBudgetManager
from .builder import ContextBuilder
from .compression import ContextCompressionEngine
from .config import ContextConfiguration, StoreConfig, TokenizerConfig
from .dedup import ContextDeduplicationEngine
from .extensions import Compressor, Redactor, SemanticDeduplicator, Summarizer
from .ordering import ContextOrderingEngine
from .policies import ContextPolicyEngine
from .ports import ContextSource, Tokenizer
from .ranking import ContextRankingEngine
from .repositories import ContextRepository
from .runtime import ContextRuntime
from .serializer import ContextSerializer
from .session import ContextSessionManager
from .sources import (
    KnowledgeSource,
    MemorySource,
    PromptDirective,
    PromptSource,
    SourceSelector,
)
from .stores import ContextStore, InMemoryDocumentStore, InMemoryMetadataStore, WordTokenizer
from .tenancy import ContextTenancy
from .validation import ContextValidationEngine

_LOCAL = frozenset({"in_memory", "local"})


class ContextFactory:
    @staticmethod
    def create(
        config: ContextConfiguration,
        *,
        memory_port: MemoryPort | None = None,
        knowledge_port: KnowledgePort | None = None,
        prompt_port: PromptPort | None = None,
        prompt_directives: Sequence[PromptDirective] = (),
        extra_sources: Sequence[ContextSource] = (),
        intelligence: bool = False,
        redactor: Redactor | None = None,
        summarizer: Summarizer | None = None,
        compressor: Compressor | None = None,
        semantic_dedup: SemanticDeduplicator | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> ContextRuntime:
        import time

        the_clock = clock or time.time
        serializer = ContextSerializer()
        store = ContextStore(
            document_store=_document_store(config.document_store),
            metadata_store=_metadata_store(config.metadata_store),
            tokenizer=_tokenizer(config.tokenizer),
        )
        repository = ContextRepository(store, serializer, collections=config.collections)
        sessions = ContextSessionManager(repository, window=config.window, clock=the_clock)

        # source adapters over the injected ports (dependency inversion) + any extras
        sources: list[ContextSource] = []
        if memory_port is not None:
            sources.append(MemorySource(memory_port))
        if knowledge_port is not None:
            sources.append(KnowledgeSource(knowledge_port))
        if prompt_port is not None:
            sources.append(PromptSource(prompt_port, prompt_directives))
        sources.extend(extra_sources)

        builder = ContextBuilder(store.tokenizer)
        # ── intelligence layer (M5.3B-2), wired only when enabled ─────────────
        engines: dict[str, Any] = {}
        tenancy = validation = None
        if intelligence:
            p = config.policies
            engines = {
                "dedup": ContextDeduplicationEngine(semantic=semantic_dedup),
                "ranking": ContextRankingEngine(policy=p.ranking, clock=the_clock),
                "compression": ContextCompressionEngine(
                    store.tokenizer, policy=p.compression,
                    summarizer=summarizer, compressor=compressor,
                ),
                "budget": ContextBudgetManager(policy=p.budget),
                "ordering": ContextOrderingEngine(policy=p.ordering),
            }
            tenancy = ContextTenancy(clock=the_clock)
            validation = ContextValidationEngine(ContextPolicyEngine(p))

        assembler = ContextAssembler(
            sources=sources, selector=SourceSelector(), builder=builder, **engines
        )
        return ContextRuntime(
            config=config, repository=repository, assembler=assembler, sessions=sessions,
            serializer=serializer, tenancy=tenancy, validation=validation, redactor=redactor,
            id_factory=id_factory, clock=the_clock, **kwargs,
        )

    @staticmethod
    def default(
        *, id_factory: Callable[[], str] | None = None, **kwargs: Any
    ) -> ContextRuntime:
        return ContextFactory.create(
            ContextConfiguration.default(), id_factory=id_factory, **kwargs
        )


def _document_store(cfg: StoreConfig) -> DocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")


def _metadata_store(cfg: StoreConfig) -> MetadataStore:
    if cfg.kind in _LOCAL:
        return InMemoryMetadataStore()
    raise ConfigError(f"unknown metadata store kind {cfg.kind!r}")


def _tokenizer(cfg: TokenizerConfig) -> Tokenizer:
    if cfg.kind in _LOCAL or cfg.kind == "word":
        return WordTokenizer(chars_per_token=cfg.chars_per_token)
    raise ConfigError(f"unknown tokenizer kind {cfg.kind!r}")

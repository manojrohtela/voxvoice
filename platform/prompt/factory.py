"""PromptFactory — the composition root (M5.4B-1).

Assembles a complete `PromptRuntime` from a `PromptConfiguration` + injected
`Tokenizer`/`ProviderProfile`/`ModelProfile` (deterministic defaults when none supplied).
Resolves store/tokenizer kinds to local adapters, builds the temporary repository, template
registry/resolver, section generator, formatter, renderer, builder, and runtime. The *only*
place adapter kinds are named; an unknown kind fails fast with `ConfigError`. Only local
deterministic adapters + the neutral profile ship in M5.4B-1.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.errors import ConfigError
from core.providers.stores import DocumentStore, MetadataStore

from .builder import PromptBuilder
from .config import PromptConfiguration, StoreConfig, TokenizerConfig
from .formatter import PromptFormatter
from .ports import ModelProfile, ProviderProfile, Tokenizer
from .renderer import PromptRenderer
from .repositories import PromptRepository
from .runtime import PromptRuntime
from .sections import SectionGenerator
from .serializer import PromptSerializer
from .stores import (
    DefaultModelProfile,
    InMemoryDocumentStore,
    InMemoryMetadataStore,
    NeutralProviderProfile,
    PromptStore,
    WordTokenizer,
)
from .templates import TemplateRegistry, TemplateResolver

_LOCAL = frozenset({"in_memory", "local"})


class PromptFactory:
    @staticmethod
    def create(
        config: PromptConfiguration,
        *,
        tokenizer: Tokenizer | None = None,
        provider_profile: ProviderProfile | None = None,
        model_profile: ModelProfile | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> PromptRuntime:
        import time

        the_clock = clock or time.time
        serializer = PromptSerializer()
        tok = tokenizer or _tokenizer(config.tokenizer)
        provider = provider_profile or NeutralProviderProfile()
        model = model_profile or DefaultModelProfile(tokenizer=tok)

        store = PromptStore(
            document_store=_document_store(config.document_store),
            metadata_store=_metadata_store(config.metadata_store),
            tokenizer=tok,
            provider_profile=provider,
            model_profile=model,
        )
        repository = PromptRepository(store, serializer, collections=config.collections)
        registry = TemplateRegistry()
        resolver = TemplateResolver(registry)
        builder = PromptBuilder(
            generator=SectionGenerator(resolver, tok),
            formatter=PromptFormatter(),
            renderer=PromptRenderer(provider),
            model_profile=model,
        )
        return PromptRuntime(
            config=config, repository=repository, builder=builder, registry=registry,
            resolver=resolver, serializer=serializer, provider=provider.name,
            id_factory=id_factory, clock=the_clock, **kwargs,
        )

    @staticmethod
    def default(
        *, id_factory: Callable[[], str] | None = None, **kwargs: Any
    ) -> PromptRuntime:
        return PromptFactory.create(
            PromptConfiguration.default(), id_factory=id_factory, **kwargs
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

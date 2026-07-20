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

from .budget import PromptBudgetManager
from .builder import PromptBuilder
from .config import PromptConfiguration, StoreConfig, TokenizerConfig
from .formatter import PromptFormatter
from .policies import PromptPolicyEngine
from .ports import ModelProfile, ProviderProfile, Tokenizer
from .renderer import PromptRenderer
from .repositories import PromptRepository
from .runtime import PromptRuntime
from .sections import SectionGenerator
from .serializer import PromptSerializer
from .stores import (
    PROVIDER_PROFILES,
    DefaultModelProfile,
    InMemoryDocumentStore,
    InMemoryMetadataStore,
    NeutralProviderProfile,
    PromptStore,
    WordTokenizer,
    model_profiles,
)
from .templates import TemplateRegistry, TemplateResolver
from .tenancy import PromptTenancy
from .validation import PromptValidationEngine

_LOCAL = frozenset({"in_memory", "local"})


class PromptFactory:
    @staticmethod
    def create(
        config: PromptConfiguration,
        *,
        tokenizer: Tokenizer | None = None,
        provider_profile: ProviderProfile | None = None,
        model_profile: ModelProfile | None = None,
        intelligence: bool = False,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
        **kwargs: Any,
    ) -> PromptRuntime:
        import time

        the_clock = clock or time.time
        serializer = PromptSerializer()
        tok = tokenizer or _tokenizer(config.tokenizer)

        # ── provider + model registries (M5.4B-2) ─────────────────────────────
        providers: dict[str, ProviderProfile] = {"neutral": NeutralProviderProfile()}
        models: dict[str, ModelProfile] = {}
        if intelligence:
            providers.update({name: cls() for name, cls in PROVIDER_PROFILES.items()})
            models.update(model_profiles(tok))

        default_provider = (
            provider_profile
            or providers.get(config.default_provider)
            or NeutralProviderProfile()
        )
        default_model = (
            model_profile or models.get(config.default_model) or DefaultModelProfile(tokenizer=tok)
        )

        store = PromptStore(
            document_store=_document_store(config.document_store),
            metadata_store=_metadata_store(config.metadata_store),
            tokenizer=tok,
            provider_profile=default_provider,
            model_profile=default_model,
        )
        repository = PromptRepository(store, serializer, collections=config.collections)
        registry = TemplateRegistry()
        resolver = TemplateResolver(registry)
        builder = PromptBuilder(
            generator=SectionGenerator(resolver, tok),
            formatter=PromptFormatter(),
            renderer=PromptRenderer(default_provider),
            model_profile=default_model,
        )

        # ── intelligence engines (wired only when enabled) ────────────────────
        tenancy = validation = budget = None
        if intelligence:
            p = config.policies
            budget = PromptBudgetManager(policy=p.budget)
            validation = PromptValidationEngine(PromptPolicyEngine(p))
            tenancy = PromptTenancy(
                allowed_providers=p.provider.allowed_providers, clock=the_clock
            )

        return PromptRuntime(
            config=config, repository=repository, builder=builder, registry=registry,
            resolver=resolver, serializer=serializer, provider=default_provider.name,
            tenancy=tenancy, validation=validation, budget_manager=budget,
            provider_profiles=providers, model_profiles=models,
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

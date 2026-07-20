"""Prompt Runtime configuration (M5.4B-1) — frozen, validated value objects.

Declarative wiring: store kinds, tokenizer, the temporary-storage TTL, collection names, and
a naive global budget override. **No provider-specific configuration** — the provider/model
are chosen via the injected `ProviderProfile`/`ModelProfile` (per request), not here.
Validated at construction (fail fast). `default()` yields an all-in-memory development config.
Budget/validation/policy/tenancy intelligence knobs are **not** here — that is M5.4B-2.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from core.errors import ConfigError

from .policies import PromptPolicies


@dataclass(frozen=True, slots=True)
class StoreConfig:
    kind: str = "in_memory"
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TokenizerConfig:
    kind: str = "word"
    chars_per_token: float = 4.0

    def __post_init__(self) -> None:
        if self.chars_per_token <= 0:
            raise ConfigError("TokenizerConfig.chars_per_token must be > 0")


@dataclass(frozen=True, slots=True)
class CollectionsConfig:
    packages: str = "prompt_packages"
    templates: str = "prompt_templates"


@dataclass(frozen=True, slots=True)
class PromptConfiguration:
    name: str = "prompt"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    metadata_store: StoreConfig = field(default_factory=StoreConfig)
    tokenizer: TokenizerConfig = field(default_factory=TokenizerConfig)
    collections: CollectionsConfig = field(default_factory=CollectionsConfig)
    policies: PromptPolicies = field(default_factory=PromptPolicies)  # M5.4B-2 intelligence
    default_provider: str = "generic"  # provider profile chosen when intelligence is on
    default_model: str = "default"
    default_budget_tokens: int = 0  # 0 → use the ModelProfile window − completion reserve
    package_ttl_s: float | None = 900.0  # temporary storage TTL (15 min); None → no expiry

    def __post_init__(self) -> None:
        if self.default_budget_tokens < 0:
            raise ConfigError("default_budget_tokens must be >= 0")
        if self.package_ttl_s is not None and self.package_ttl_s <= 0:
            raise ConfigError("package_ttl_s must be > 0 or None")

    @classmethod
    def default(cls) -> PromptConfiguration:
        return cls()

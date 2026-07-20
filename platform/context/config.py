"""Context Runtime configuration (M5.3B-1) — frozen, validated value objects.

Declarative wiring: store kinds, tokenizer, the rolling-window size, temporary-storage
TTL, per-source hit caps, and collection names. Validated at construction (fail fast).
`default()` yields an all-in-memory development configuration. Budget/ranking/compression
knobs are **not** here — that is intelligence (M5.3B-2); the core carries only a naive
global token budget + a default source order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from core.errors import ConfigError

from .domain import ContextKind

# Deterministic default assembly order (system → developer → memory → knowledge → tools →
# conversation → user → environment). Ordering *intelligence* is M5.3B-2; this is the
# fixed insertion order the core uses.
DEFAULT_SOURCE_ORDER: tuple[ContextKind, ...] = (
    ContextKind.SYSTEM_INSTRUCTION,
    ContextKind.DEVELOPER_INSTRUCTION,
    ContextKind.WORKING_MEMORY,
    ContextKind.LONG_TERM_MEMORY,
    ContextKind.KNOWLEDGE_PASSAGE,
    ContextKind.TOOL_RESULT,
    ContextKind.WORKFLOW_RESULT,
    ContextKind.AGENT_OUTPUT,
    ContextKind.CONVERSATION_HISTORY,
    ContextKind.USER_INSTRUCTION,
    ContextKind.ENVIRONMENT,
)


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
class WindowConfig:
    """Rolling conversation-window bound."""

    max_items: int = 20

    def __post_init__(self) -> None:
        if self.max_items < 0:
            raise ConfigError("WindowConfig.max_items must be >= 0")


@dataclass(frozen=True, slots=True)
class CollectionsConfig:
    packages: str = "context_packages"
    sessions: str = "context_sessions"


@dataclass(frozen=True, slots=True)
class ContextConfiguration:
    name: str = "context"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    metadata_store: StoreConfig = field(default_factory=StoreConfig)
    tokenizer: TokenizerConfig = field(default_factory=TokenizerConfig)
    window: WindowConfig = field(default_factory=WindowConfig)
    collections: CollectionsConfig = field(default_factory=CollectionsConfig)
    default_budget_tokens: int = 0  # 0 → unbounded (naive truncation only when > 0)
    per_source_limit: int = 10  # per-source hit cap passed to each ContextSource
    package_ttl_s: float | None = 900.0  # temporary storage TTL (15 min); None → no expiry

    def __post_init__(self) -> None:
        if self.default_budget_tokens < 0:
            raise ConfigError("default_budget_tokens must be >= 0")
        if self.per_source_limit <= 0:
            raise ConfigError("per_source_limit must be > 0")
        if self.package_ttl_s is not None and self.package_ttl_s <= 0:
            raise ConfigError("package_ttl_s must be > 0 or None")

    @classmethod
    def default(cls) -> ContextConfiguration:
        return cls()

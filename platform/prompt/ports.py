"""Prompt runtime-internal seams (M5.4B-1) — the provider-neutral ports.

Vendor/model concerns live behind these; the runtime never hard-codes a tokenizer, a
provider message convention, or a model window.

- `Tokenizer` — text → token estimate (deterministic local default in `stores/`).
- `ProviderProfile` — maps role-tagged `PromptSection`s into provider-neutral
  `PromptMessage`s (the provider's message convention). Anthropic/OpenAI/Gemini/local land
  as adapters later; a neutral default ships now. **No SDK, no HTTP, no LLM.**
- `ModelProfile` — the target model's context window + token counter + completion reserve
  (model-aware budgeting is M5.4B-2, but the seam is here so the estimate is provider-neutral
  now).

Interfaces + a couple of neutral shapes only. Deterministic defaults are in `stores/`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from .domain import PromptMessage, PromptSection


class Tokenizer(ABC):
    """Text → token estimate. Deterministic for a given input."""

    name: str

    @abstractmethod
    def count(self, text: str) -> int: ...


class ProviderProfile(ABC):
    """Renders role-tagged sections into provider-neutral messages (the provider's message
    convention). Deterministic; returns neutral data — never a network call or SDK object."""

    name: str

    @abstractmethod
    def render(self, sections: Sequence[PromptSection]) -> Sequence[PromptMessage]: ...


class ModelProfile(ABC):
    """The target model's context window + token counter + completion reserve."""

    name: str

    @abstractmethod
    def context_window(self) -> int: ...

    @abstractmethod
    def completion_reserve(self) -> int: ...

    @abstractmethod
    def count(self, text: str) -> int: ...

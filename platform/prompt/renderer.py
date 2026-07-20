"""Prompt renderer (M5.4B-1) — provider-neutral rendering to messages.

Turns the ordered, formatted `PromptSection`s into provider-neutral `PromptMessage`s by
delegating to the injected `ProviderProfile` (the provider's message convention). The
renderer itself owns *only* the delegation + determinism guarantee; **it never touches an
SDK, HTTP, a provider, or an LLM** — the profile returns neutral data.

The default `NeutralProviderProfile` emits one message per section in order. Provider
adapters (Anthropic/OpenAI/Gemini/local) reshape via their own `ProviderProfile` — with no
change to this renderer.
"""

from __future__ import annotations

from collections.abc import Sequence

from .domain import PromptMessage, PromptSection
from .ports import ProviderProfile


class PromptRenderer:
    def __init__(self, provider_profile: ProviderProfile) -> None:
        self._profile = provider_profile

    @property
    def provider(self) -> str:
        return self._profile.name

    def render(self, sections: Sequence[PromptSection]) -> tuple[PromptMessage, ...]:
        return tuple(self._profile.render(sections))

"""Default provider + model profiles (M5.4B-1, deterministic/provider-neutral).

The **default** `ProviderProfile`/`ModelProfile` the factory injects — deterministic, no
vendor, no SDK, no network, no LLM. Real provider adapters (Anthropic/OpenAI/Gemini/local)
and model-exact windows land later as additive implementations of these ports; they change
nothing in the Prompt Runtime architecture.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain import PromptMessage, PromptSection
from ..ports import ModelProfile, ProviderProfile, Tokenizer
from .tokenizer import WordTokenizer


class NeutralProviderProfile(ProviderProfile):
    """One message per section, in section order, preserving the section's role. The
    provider-neutral baseline; adapters reshape (e.g. merge system, group turns)."""

    name = "neutral"

    def render(self, sections: Sequence[PromptSection]) -> Sequence[PromptMessage]:
        return [
            PromptMessage(
                role=s.role,
                content=s.content,
                metadata={"kind": s.kind.value, "section_id": s.id},
            )
            for s in sections
            if s.content
        ]


class DefaultModelProfile(ModelProfile):
    """A fixed context window + completion reserve, counting via an injected tokenizer.
    Deterministic; model-exact windows are a future additive adapter."""

    def __init__(
        self,
        *,
        name: str = "default",
        window: int = 8192,
        completion_reserve: int = 1024,
        tokenizer: Tokenizer | None = None,
    ) -> None:
        if window <= 0 or completion_reserve < 0 or completion_reserve >= window:
            raise ValueError("invalid model profile window/reserve")
        self.name = name
        self._window = window
        self._reserve = completion_reserve
        self._tok = tokenizer or WordTokenizer()

    def context_window(self) -> int:
        return self._window

    def completion_reserve(self) -> int:
        return self._reserve

    def count(self, text: str) -> int:
        return self._tok.count(text)

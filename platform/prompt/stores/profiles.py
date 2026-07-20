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
    Deterministic; model-exact windows are a future additive adapter. `capabilities` +
    `metadata` are plain descriptors (not on the frozen `ModelProfile` ABC — additive)."""

    def __init__(
        self,
        *,
        name: str = "default",
        window: int = 8192,
        completion_reserve: int = 1024,
        tokenizer: Tokenizer | None = None,
        capabilities: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        if window <= 0 or completion_reserve < 0 or completion_reserve >= window:
            raise ValueError("invalid model profile window/reserve")
        self.name = name
        self._window = window
        self._reserve = completion_reserve
        self._tok = tokenizer or WordTokenizer()
        self.capabilities = capabilities or {"multimodal": "false", "streaming": "true"}
        self.metadata = metadata or {}

    def context_window(self) -> int:
        return self._window

    def completion_reserve(self) -> int:
        return self._reserve

    def count(self, text: str) -> int:
        return self._tok.count(text)


#: name → default ModelProfile factory (deterministic; windows are illustrative, not vendor
#: contracts — real model-exact profiles are additive adapters).
def model_profiles(tokenizer: Tokenizer | None = None) -> dict[str, DefaultModelProfile]:
    tok = tokenizer or WordTokenizer()
    return {
        "default": DefaultModelProfile(name="default", window=8192, completion_reserve=1024,
                                       tokenizer=tok),
        "small": DefaultModelProfile(name="small", window=4096, completion_reserve=512,
                                     tokenizer=tok),
        "large": DefaultModelProfile(name="large", window=32768, completion_reserve=4096,
                                     tokenizer=tok),
    }

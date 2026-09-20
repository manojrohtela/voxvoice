"""Streaming Text-to-Speech capability contract (Constitution §A6).

M2C.2: refined to an execution-ready shape — `synthesize` turns one utterance of
text into a stream of audio frames.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from core.audio import AudioFrame


@dataclass(frozen=True, slots=True)
class TTSCapabilities:
    streaming: bool
    markup: bool = False  # SSML-style markup support
    voices: tuple[str, ...] = field(default_factory=tuple)
    sample_rates: tuple[int, ...] = field(default_factory=tuple)
    #: Whether the provider can accept text **as it is produced**, rather than one whole
    #: utterance per call (M11.10, ADR-0045 SD6). Defaults `False`, so every provider written
    #: before this flag existed keeps its exact meaning, and the runtime adapts to `False` by
    #: synthesizing clause by clause and **recording the degradation** (AR-24, ADR-0048 PB3) —
    #: never by silently serializing the response.
    streaming_input: bool = False


class TTSProvider(ABC):
    name: str
    capabilities: TTSCapabilities

    @abstractmethod
    def synthesize(
        self, text: str, *, voice: str | None = None, language: str = "en"
    ) -> AsyncIterator[AudioFrame]:
        """Synthesize one utterance; yield audio frames as they are produced."""

    def synthesize_stream(
        self,
        chunks: AsyncIterator[str],
        *,
        voice: str | None = None,
        language: str = "en",
    ) -> AsyncIterator[AudioFrame]:
        """Synthesize from text that is still arriving (M11.10, ADR-0045 SD5).

        **Additive and optional.** `synthesize()` is unchanged and remains the abstract method
        every provider implements, so no existing provider or caller is affected. Only a provider
        declaring `capabilities.streaming_input=True` need override this, and only the runtime's
        streaming-input branch calls it — a provider that declares `False` is never asked.

        The default raises rather than quietly falling back to `synthesize()`: a silent fallback
        would make the degradation invisible, and AR-24 requires the runtime to *record* that it
        adapted. Deciding to adapt is the runtime's, not the contract's."""
        raise NotImplementedError(
            f"{type(self).__name__} does not accept streaming text input "
            "(capabilities.streaming_input is False)"
        )

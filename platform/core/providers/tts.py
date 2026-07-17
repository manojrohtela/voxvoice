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


class TTSProvider(ABC):
    name: str
    capabilities: TTSCapabilities

    @abstractmethod
    def synthesize(
        self, text: str, *, voice: str | None = None, language: str = "en"
    ) -> AsyncIterator[AudioFrame]:
        """Synthesize one utterance; yield audio frames as they are produced."""

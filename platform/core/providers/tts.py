"""Streaming Text-to-Speech capability contract (Constitution §A6). Interface only."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


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
    async def open_stream(self, *, voice: str, language: str) -> None:
        """Open a synthesis stream for a session."""

    @abstractmethod
    async def say(self, text_chunk: str) -> AsyncIterator[bytes]:
        """Accept a text chunk; yield audio frames as they synthesize."""

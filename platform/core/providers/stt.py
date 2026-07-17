"""Streaming Speech-to-Text capability contract (Constitution §A6, ADR-0005).

First-class STT MUST stream partial hypotheses. Batch-only providers are allowed
only in *declared* degraded mode (`streaming=False`) — the runtime adapts and
records the degradation (AR-24).

M2C.2: refined to an execution-ready shape — `transcribe` consumes a stream of
audio frames and yields Partials during speech + a Final at end-of-utterance.
Turn/endpoint detection is a provider/Turn-Intelligence concern (M3): a Final
marks a completed user utterance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from core.audio import AudioFrame


@dataclass(frozen=True, slots=True)
class STTCapabilities:
    streaming: bool
    partials: bool
    word_timestamps: bool = False
    languages: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Partial:
    text: str
    stability: float  # 0..1


@dataclass(frozen=True, slots=True)
class Final:
    text: str
    words: tuple[tuple[str, float, float], ...] = field(default_factory=tuple)  # (word, start, end)


class STTProvider(ABC):
    name: str
    capabilities: STTCapabilities

    @abstractmethod
    def transcribe(
        self, frames: AsyncIterator[AudioFrame], *, language: str, sample_rate: int
    ) -> AsyncIterator[Partial | Final]:
        """Consume caller audio frames; yield Partials during speech and a Final
        at each end-of-utterance."""

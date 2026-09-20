"""Deterministic provider stand-ins with configurable delays (M11.13, Profile B).

These exist because Profile B asks a question the hermetic fakes cannot answer: *does an injected
delay appear in the stage the runtime attributes it to?* Answering it needs a provider that waits
a **known** amount before producing its first output — which is exactly what no test fake does,
because every other milestone was at pains to avoid sleeping.

So the sleeps here are deliberate and are the measurement instrument, not a convenience. They are
also the reason Profile B is **not** a provider measurement: the delays are the harness's own, and
the only real quantity in the result is the runtime's overhead *around* them.

Each stand-in implements the same public contract the real provider does, and the runtime cannot
tell the difference — which is the point. Nothing here is importable by production code.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from core.audio import AudioEncoding, AudioFrame
from core.providers.llm import LLMCapabilities, LLMProvider, TokenDelta, ToolCallDelta
from core.providers.stt import Final, Partial, STTCapabilities, STTProvider
from core.providers.tts import TTSCapabilities, TTSProvider

SAMPLE_RATE = 16000
FRAME_BYTES = 640  # 20 ms of PCM16 mono at 16 kHz


@dataclass(frozen=True, slots=True)
class DelayProfile:
    """The delays Profile B injects, in milliseconds. All default to zero (Profile A).

    Named per **stage boundary** rather than per provider call so the expectation is legible: set
    `llm_first_token_ms` and the `llm` stage of `metric.turn` should carry it."""

    #: Before the STT provider emits its `Final` for an utterance.
    stt_final_ms: float = 0.0
    #: Before the LLM yields its first token.
    llm_first_token_ms: float = 0.0
    #: Before the TTS provider yields its first audio frame.
    tts_first_audio_ms: float = 0.0
    #: Before the transport accepts each outbound frame.
    transport_send_ms: float = 0.0

    @property
    def is_zero(self) -> bool:
        return not any(
            (
                self.stt_final_ms,
                self.llm_first_token_ms,
                self.tts_first_audio_ms,
                self.transport_send_ms,
            )
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "stt_final_ms": self.stt_final_ms,
            "llm_first_token_ms": self.llm_first_token_ms,
            "tts_first_audio_ms": self.tts_first_audio_ms,
            "transport_send_ms": self.transport_send_ms,
        }


async def _wait(milliseconds: float) -> None:
    """Sleep a configured duration, or yield once when the duration is zero.

    The zero case still yields, so Profile A exercises the same await points as Profile B and the
    difference between them is the delay rather than the control flow."""
    if milliseconds > 0:
        await asyncio.sleep(milliseconds / 1000.0)
    else:
        await asyncio.sleep(0)


class EnergySpeechModel:
    """A deterministic `SpeechModel`: speech probability = peak amplitude / 32767.

    Inlined here rather than imported from `tests/` so the benchmark package depends on nothing
    outside `platform/`'s production layers. It is not a VAD — it is a transparent stand-in whose
    verdict the harness can predict, which is what keeps a latency run from also being an
    accidental accuracy claim (R-47 stays open)."""

    sample_rate = SAMPLE_RATE
    window_samples = 512

    def probability(self, window: bytes) -> float:
        from array import array

        peak = max((abs(s) for s in array("h", window)), default=0)
        return min(1.0, peak / 32767)


def loud_frame(seq: int, *, amplitude: int = 30000) -> AudioFrame:
    """One 20 ms inbound frame whose energy clears the VAD threshold."""
    body = int(amplitude).to_bytes(2, "little", signed=True) * (FRAME_BYTES // 2)
    return AudioFrame(
        data=body, sample_rate=SAMPLE_RATE, encoding=AudioEncoding.PCM16, channels=1, seq=seq
    )


def silent_frame(seq: int) -> AudioFrame:
    return loud_frame(seq, amplitude=0)


class BenchmarkSTT(STTProvider):
    """Emits one `Partial` per loud frame and a `Final` when the caller stops, after a delay."""

    name = "bench-stt"

    def __init__(self, transcript: str, delays: DelayProfile) -> None:
        self.capabilities = STTCapabilities(
            streaming=True, partials=True, word_timestamps=False, languages=("en",)
        )
        self._transcript = transcript
        self._delays = delays

    async def transcribe(
        self, frames: AsyncIterator[AudioFrame], *, language: str, sample_rate: int
    ) -> AsyncIterator[Partial | Final]:
        speaking = False
        async for frame in frames:
            loud = bool(frame.data) and abs(int.from_bytes(frame.data[:2], "little", signed=True))
            if loud > 1000:
                speaking = True
                yield Partial(text=self._transcript, stability=0.0)
            elif speaking:
                speaking = False
                # The recognizer's own finalization cost — the `stt` stage of `metric.turn`.
                await _wait(self._delays.stt_final_ms)
                yield Final(text=self._transcript, words=())


class BenchmarkLLM(LLMProvider):
    """Streams a fixed reply, after a configured time-to-first-token."""

    name = "bench-llm"
    capabilities = LLMCapabilities(tools=False, structured_output=False, context_window=8192)

    def __init__(self, clauses: list[str], delays: DelayProfile) -> None:
        self._clauses = clauses
        self._delays = delays

    async def stream(
        self, *, context, tools=None, model=None
    ) -> AsyncIterator[TokenDelta | ToolCallDelta]:
        await _wait(self._delays.llm_first_token_ms)
        for clause in self._clauses:
            yield TokenDelta(text=clause)


class BenchmarkTTS(TTSProvider):
    """Yields a fixed number of frames per utterance, after a configured first-audio delay."""

    name = "bench-tts"

    def __init__(self, frames_per_clause: int, delays: DelayProfile) -> None:
        self.capabilities = TTSCapabilities(
            streaming=True, sample_rates=(SAMPLE_RATE,), streaming_input=False
        )
        self._frames = frames_per_clause
        self._delays = delays

    async def synthesize(
        self, text: str, *, voice: str | None = None, language: str = "en"
    ) -> AsyncIterator[AudioFrame]:
        await _wait(self._delays.tts_first_audio_ms)
        for index in range(self._frames):
            yield AudioFrame(
                data=bytes([index % 250 + 1, 0]) * (FRAME_BYTES // 2),
                sample_rate=SAMPLE_RATE,
                encoding=AudioEncoding.PCM16,
                channels=1,
            )


class BenchmarkEndpoint:
    """A `MediaEndpoint` the harness speaks into, with a configurable outbound accept delay."""

    def __init__(self, delays: DelayProfile) -> None:
        self._delays = delays
        self.written: list[AudioFrame] = []
        self.connected: list[str] = []
        self.closed: list[str] = []
        self._inbound: asyncio.Queue[AudioFrame | None] = asyncio.Queue()

    async def push(self, frame: AudioFrame) -> None:
        self._inbound.put_nowait(frame)
        await asyncio.sleep(0)

    async def hangup(self) -> None:
        self._inbound.put_nowait(None)
        await asyncio.sleep(0)

    async def connect(self, session_id: str) -> None:
        self.connected.append(session_id)

    async def close(self, session_id: str) -> None:
        self.closed.append(session_id)

    async def inbound(self, session_id: str) -> AsyncIterator[AudioFrame]:
        while True:
            frame = await self._inbound.get()
            if frame is None:
                return
            yield frame

    async def send(self, session_id: str, frame: AudioFrame) -> None:
        # The egress cost — what separates `tts_first_audio` from `first_audio_out`.
        await _wait(self._delays.transport_send_ms)
        self.written.append(frame)


__all__ = [
    "FRAME_BYTES",
    "EnergySpeechModel",
    "SAMPLE_RATE",
    "BenchmarkEndpoint",
    "BenchmarkLLM",
    "BenchmarkSTT",
    "BenchmarkTTS",
    "DelayProfile",
    "loud_frame",
    "silent_frame",
]

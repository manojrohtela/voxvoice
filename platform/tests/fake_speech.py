"""Deterministic TTS vendors and transports for hermetic playback tests (M11.10).

The whole audio path under test control: what the vendor produces, where its chunk boundaries
fall, whether it fails and when, and how fast the transport drains. Nothing here sleeps for a
duration — every ordering and back-pressure question is settled with `asyncio.Event`, because a
test that proves back-pressure by sleeping proves only that the machine was slow enough.

Shared by the Cartesia provider tests, the playback/scheduler tests and the bootstrap tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from core.audio import AudioEncoding, AudioFrame
from core.providers.tts import TTSCapabilities, TTSProvider
from providers.tts.speech_stream import (
    FailureKind,
    SpeechChunk,
    SpeechClient,
    SpeechSpec,
    SpeechStream,
    SpeechStreamError,
)

SAMPLE_RATE = 16000


def pcm(nbytes: int, *, fill: int = 1) -> bytes:
    """`nbytes` of non-silent PCM16, distinguishable from any other call by `fill`."""
    return bytes([fill, 0]) * (nbytes // 2)


def frame(data: bytes, *, sample_rate: int = SAMPLE_RATE) -> AudioFrame:
    return AudioFrame(
        data=data, sample_rate=sample_rate, encoding=AudioEncoding.PCM16, channels=1
    )


async def settle(until, *, spins: int = 500) -> bool:
    """Let the event loop run until `until()` holds, or until it goes quiet. Never blocks.

    The alternative — `await some_event.wait()` — turns a defect that stops the event from ever
    being set into a **hang** rather than a failure, and a hang is not evidence. This yields with
    `asyncio.sleep(0)`, which waits for no duration at all, a bounded number of times."""
    for _ in range(spins):
        if until():
            return True
        await asyncio.sleep(0)
    return until()


async def spin(times: int = 50) -> None:
    """Let the event loop run `times` times and return.

    For asserting that something did **not** happen: give every other task ample opportunity to
    run, then assert the absence. `asyncio.sleep(0)` waits for no duration, so this adds no time
    to the suite and depends on no machine speed."""
    for _ in range(times):
        await asyncio.sleep(0)


# ── the vendor seam ──────────────────────────────────────────────────────────


class FakeSpeechStream(SpeechStream):
    """Yields scripted audio chunks, then ends — or fails where the script says to."""

    def __init__(
        self,
        chunks: list[bytes],
        *,
        fail_at: int | None = None,
        kind: FailureKind = FailureKind.UNAVAILABLE,
        gate: asyncio.Event | None = None,
        started: asyncio.Event | None = None,
    ) -> None:
        self._chunks = chunks
        self._fail_at = fail_at
        self._kind = kind
        self._gate = gate
        self._started = started
        self.yielded = 0
        self.closed = 0

    async def __aiter__(self) -> AsyncIterator[SpeechChunk]:
        for index, audio in enumerate(self._chunks):
            if self._fail_at is not None and index == self._fail_at:
                raise SpeechStreamError(self._kind, "scripted failure")
            if index and self._gate is not None:
                if self._started is not None:
                    self._started.set()
                await self._gate.wait()
            self.yielded += 1
            yield SpeechChunk(audio=audio)
        if self._fail_at is not None and self._fail_at >= len(self._chunks):
            raise SpeechStreamError(self._kind, "scripted failure at end")

    async def aclose(self) -> None:
        self.closed += 1


class FakeSpeechClient(SpeechClient):
    """Opens `FakeSpeechStream`s, or fails to open at all."""

    def __init__(
        self,
        chunks: list[bytes] | None = None,
        *,
        open_error: FailureKind | None = None,
        fail_at: int | None = None,
        kind: FailureKind = FailureKind.UNAVAILABLE,
        gate: asyncio.Event | None = None,
        started: asyncio.Event | None = None,
        opened_event: asyncio.Event | None = None,
        hang_on_open: asyncio.Event | None = None,
    ) -> None:
        self._chunks = chunks if chunks is not None else [pcm(640), pcm(640, fill=2)]
        self._open_error = open_error
        self._fail_at = fail_at
        self._kind = kind
        self._gate = gate
        self._started = started
        self._opened_event = opened_event
        self._hang_on_open = hang_on_open
        self.specs: list[SpeechSpec] = []
        self.streams: list[FakeSpeechStream] = []

    async def open(self, spec: SpeechSpec) -> SpeechStream:
        self.specs.append(spec)
        if self._opened_event is not None:
            self._opened_event.set()
        if self._hang_on_open is not None:
            await self._hang_on_open.wait()
        if self._open_error is not None:
            raise SpeechStreamError(self._open_error, "scripted open failure")
        stream = FakeSpeechStream(
            list(self._chunks),
            fail_at=self._fail_at,
            kind=self._kind,
            gate=self._gate,
            started=self._started,
        )
        self.streams.append(stream)
        return stream


# ── a provider-level fake (above the vendor seam) ────────────────────────────


class ScriptedTTS(TTSProvider):
    """A `TTSProvider` whose audio per utterance the test dictates exactly.

    Used where the question is about *playback*, not about a vendor: it lets a test say "this
    clause produces these three frames" without going through a client, a spec or base64."""

    name = "scripted"

    def __init__(
        self,
        frames_per_call: int = 2,
        *,
        streaming_input: bool = False,
        error: Exception | None = None,
        error_after: int = 0,
        gate: asyncio.Event | None = None,
        started: asyncio.Event | None = None,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self.capabilities = TTSCapabilities(
            streaming=True, sample_rates=(sample_rate,), streaming_input=streaming_input
        )
        self._frames_per_call = frames_per_call
        self._error = error
        self._error_after = error_after
        self._gate = gate
        self._started = started
        self._sample_rate = sample_rate
        #: Every text this provider was asked to speak, in order.
        self.spoken: list[str] = []
        #: How many frames the consumer actually pulled — the evidence for "stopped pulling".
        self.yielded = 0
        self.stream_calls = 0

    async def synthesize(
        self, text: str, *, voice: str | None = None, language: str = "en"
    ) -> AsyncIterator[AudioFrame]:
        self.spoken.append(text)
        index = len(self.spoken)
        for n in range(self._frames_per_call):
            if self._error is not None and n >= self._error_after:
                raise self._error
            if n and self._gate is not None:
                if self._started is not None:
                    self._started.set()
                await self._gate.wait()
            # Each frame's payload identifies its utterance and position, so a reordering or a
            # duplication is visible in the assertion rather than merely probable.
            self.yielded += 1
            yield frame(pcm(640, fill=index * 10 + n), sample_rate=self._sample_rate)

    async def synthesize_stream(
        self, chunks: AsyncIterator[str], *, voice: str | None = None, language: str = "en"
    ) -> AsyncIterator[AudioFrame]:
        self.stream_calls += 1
        async for text in chunks:
            async for audio in self.synthesize(text, voice=voice, language=language):
                yield audio


class SilentTTS(TTSProvider):
    """Produces no audio at all — the "vendor said nothing" case."""

    name = "silent"
    capabilities = TTSCapabilities(streaming=True, sample_rates=(SAMPLE_RATE,))

    async def synthesize(
        self, text: str, *, voice: str | None = None, language: str = "en"
    ) -> AsyncIterator[AudioFrame]:
        return
        yield  # pragma: no cover — makes this an async generator


# ── transports ───────────────────────────────────────────────────────────────


class RecordingTransport:
    """Collects everything sent, in order, and can be made arbitrarily slow or broken."""

    def __init__(
        self,
        *,
        gate: asyncio.Event | None = None,
        error: Exception | None = None,
        error_after: int = 0,
        arrived: asyncio.Event | None = None,
    ) -> None:
        self.sent: list[AudioFrame] = []
        self._gate = gate
        self._error = error
        self._error_after = error_after
        self._arrived = arrived

    @property
    def fills(self) -> list[int]:
        """The first byte of each frame — the identity a test asserts order against."""
        return [f.data[0] if f.data else -1 for f in self.sent]

    async def send(self, frame: AudioFrame) -> None:
        if self._error is not None and len(self.sent) >= self._error_after:
            raise self._error
        if self._gate is not None:
            # A transport that accepts nothing until released: the back-pressure fixture.
            await self._gate.wait()
        self.sent.append(frame)
        if self._arrived is not None:
            self._arrived.set()


__all__ = [
    "SAMPLE_RATE",
    "FakeSpeechClient",
    "FakeSpeechStream",
    "RecordingTransport",
    "ScriptedTTS",
    "SilentTTS",
    "frame",
    "pcm",
    "settle",
    "spin",
]

"""A deterministic `TranscriptionClient` for hermetic streaming-STT tests (M11.6).

The whole vendor socket under test control: what the "vendor" answers, when it answers, when and
how it closes, and which failure it raises. No network, no credentials, no timing — a send yields
control once, exactly as a real network write would, and nothing ever sleeps for a duration.

Shared by the Deepgram provider tests, the capability conformance suite and the bootstrap tests,
so that all three judge the provider against the same fake.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from core.audio import AudioEncoding, AudioFrame
from providers.stt.transcription_session import (
    StreamSpec,
    TranscriptionResult,
    TranscriptionSessionError,
)

_END = object()

Responder = Callable[[bytes], list[TranscriptionResult]]


def echo_responder(audio: bytes) -> list[TranscriptionResult]:
    """The fake "recognizer": audio bytes carry their transcript, NUL-padded to an even length.

    Answers each chunk with an interim result and then a final one — the vendor's order."""
    text = audio.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
    return [
        TranscriptionResult(text=text, is_final=False),
        TranscriptionResult(text=text, is_final=True, words=((text, 0.0, 0.5),)),
    ]


def silent_responder(audio: bytes) -> list[TranscriptionResult]:
    return []


def pcm_frame(payload: bytes, *, sample_rate: int = 16000, seq: int | None = None) -> AudioFrame:
    """A genuine PCM16 mono frame whose bytes carry `payload` (NUL-padded to an even length)."""
    data = payload + (b"\x00" if len(payload) % 2 else b"")
    return AudioFrame(
        data=data, sample_rate=sample_rate, encoding=AudioEncoding.PCM16, channels=1, seq=seq
    )


class ScriptedSession:
    """One fake duplex stream."""

    def __init__(self, spec: StreamSpec, responder: Responder = echo_responder) -> None:
        self.spec = spec
        self.responder = responder
        self.sent: list[bytes] = []
        self.finished = False
        self.close_calls = 0
        #: Errors to raise from `send()` / `finish()`.
        self.send_error: Exception | None = None
        self.finish_error: Exception | None = None
        #: When True, `finish()` ends the result stream (the vendor's clean close after flushing).
        self.end_on_finish = True
        #: Results the vendor flushes in response to end-of-audio.
        self.flush: list[TranscriptionResult] = []
        #: Set on every successful send, so a test can await "the vendor has received audio".
        self.received = asyncio.Event()
        self._results: asyncio.Queue[object] = asyncio.Queue()

    # ── test controls ────────────────────────────────────────────────────────

    def push(self, result: TranscriptionResult) -> None:
        self._results.put_nowait(result)

    def end(self) -> None:
        """The vendor closes the stream cleanly."""
        self._results.put_nowait(_END)

    def fail(self, error: BaseException) -> None:
        """The vendor stream breaks."""
        self._results.put_nowait(error)

    @property
    def closed(self) -> bool:
        return self.close_calls > 0

    # ── TranscriptionSession ─────────────────────────────────────────────────

    async def send(self, audio: bytes) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(audio)
        for result in self.responder(audio):
            self.push(result)
        self.received.set()
        await asyncio.sleep(0)  # a network write yields control once; it never waits a duration

    async def finish(self) -> None:
        if self.finish_error is not None:
            raise self.finish_error
        self.finished = True
        for result in self.flush:
            self.push(result)
        if self.end_on_finish:
            self.end()

    async def results(self):
        while True:
            item = await self._results.get()
            if item is _END:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    async def close(self) -> None:
        self.close_calls += 1
        self._results.put_nowait(_END)


class FakeTranscriptionClient:
    """Opens `ScriptedSession`s and records every spec it was asked for."""

    def __init__(
        self,
        *,
        responder: Responder = echo_responder,
        open_error: Exception | None = None,
        configure: Callable[[ScriptedSession], None] | None = None,
    ) -> None:
        self.responder = responder
        self.open_error = open_error
        self.configure = configure
        self.specs: list[StreamSpec] = []
        self.sessions: list[ScriptedSession] = []

    async def open(self, spec: StreamSpec) -> ScriptedSession:
        if self.open_error is not None:
            raise self.open_error
        self.specs.append(spec)
        session = ScriptedSession(spec, self.responder)
        if self.configure is not None:
            self.configure(session)
        self.sessions.append(session)
        return session


__all__ = [
    "FakeTranscriptionClient",
    "ScriptedSession",
    "TranscriptionSessionError",
    "echo_responder",
    "pcm_frame",
    "silent_responder",
]

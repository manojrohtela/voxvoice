"""`DeepgramSTT` — real streaming STT behind the unchanged `STTProvider` contract (M11.6).

The provider is **first-party and library-free**. It owns the duplex, the result mapping, the
failure taxonomy, the audio-format check and the task lifecycle; the vendor socket is reached only
through the `TranscriptionClient` seam, whose one Deepgram implementation lives in
`deepgram_client.py` (AR-12).

## Genuinely incremental, genuinely duplex

`transcribe()` opens one session and then runs two things **at the same time**:

- an **upload task** that pulls caller frames one at a time, validates each, and sends it; and
- the **receive loop** (the generator itself) that yields results as the vendor produces them.

Neither waits for the other. A Partial for the first frame is yielded while the caller is still
talking — there is no buffering of the utterance and no batch emulation. When the caller's frames
end, the upload task signals end-of-audio and the vendor flushes its last results before closing.

## What is never invented

- **Partial stays Partial, Final stays Final.** `Final` is emitted only for a result the vendor
  marked final. Nothing is promoted locally — not at end-of-stream, not on a timeout, not on close.
- **Empty results are not speech.** The vendor reports empty interim results during silence. The
  pipeline treats every `Partial` as *the user is speaking* (it feeds turn-taking), so passing an
  empty one on would fabricate speech. They are dropped; non-empty text is never altered.
- **`stability` is `0.0` on every Partial.** The vendor supplies a confidence, not a stability, and
  an interim hypothesis is by definition revisable. Reporting confidence as stability would be a
  claim the vendor did not make.
- **No endpointing.** A `Final` here is the vendor's finalized *segment*; deciding when the user's
  *turn* is over is Turn/Endpoint Intelligence and out of scope (M11.7+).

## Bounded by construction

There is no queue between the caller and the vendor: the upload task holds **at most one frame**
in flight (pull → validate → send → pull). Backpressure therefore reaches the transport's own
bounded inbound buffer (M11.5, drop-oldest) instead of accumulating here. The receive side is
bounded by the websocket client's incoming buffer (32 messages of at most 1 MiB each).

## Failures

Every failure leaves as a `core.errors` type (ADR-0046, ADR-0049 CR6):

| cause | error |
|---|---|
| handshake/stream deadline | `ProviderTimeout` |
| vendor unreachable, 5xx, unexpected failure | `ProviderUnavailable` |
| rejected credential | `ProviderAuthError` |
| rate limited | `ProviderRateLimited` |
| undecodable message, rejected parameters | `ProviderProtocolError` |
| socket ended abnormally, or before the audio did | `ProviderStreamTruncated` |
| unsupported language / rate / encoding / channel layout | `CapabilityUnsupported` |

`asyncio.CancelledError` is never translated: the caller withdrawing is not the vendor failing.
Breaker accounting, deadlines between items and degradation recording belong to `ProviderGuard`,
which the pipeline already wraps around every `transcribe()` call — the provider does not duplicate
them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from core.audio import AudioEncoding, AudioFrame
from core.errors import (
    CapabilityUnsupported,
    ProviderAuthError,
    ProviderError,
    ProviderProtocolError,
    ProviderRateLimited,
    ProviderStreamTruncated,
    ProviderTimeout,
    ProviderUnavailable,
)
from core.providers.stt import Final, Partial, STTCapabilities, STTProvider

from .transcription_session import (
    FailureKind,
    StreamSpec,
    TranscriptionClient,
    TranscriptionResult,
    TranscriptionSession,
    TranscriptionSessionError,
)

log = logging.getLogger("voxvoice.providers.stt.deepgram")

#: The streaming model requested when none is configured.
DEFAULT_MODEL = "nova-3"

#: Languages declared when none are configured. Deliberately narrow: a language enters this
#: tuple when it has been *verified*, not because a vendor page lists it. Hinglish in particular
#: is **not** claimed — see DL-0180.
DEFAULT_LANGUAGES: tuple[str, ...] = ("en",)

#: Sample rates the provider will open a stream at. The runtime normalizes ingress to the session
#: rate (ADR-0044); the provider never resamples, so a session rate outside this set is refused up
#: front rather than sent and mis-transcribed.
SUPPORTED_SAMPLE_RATES: tuple[int, ...] = (8000, 16000, 24000, 32000, 44100, 48000)

#: Mono only. Multichannel transcription is a different product shape (per-channel results).
SUPPORTED_CHANNELS = 1

_TYPED: dict[FailureKind, type[ProviderError]] = {
    FailureKind.TIMEOUT: ProviderTimeout,
    FailureKind.UNAVAILABLE: ProviderUnavailable,
    FailureKind.AUTH: ProviderAuthError,
    FailureKind.RATE_LIMITED: ProviderRateLimited,
    FailureKind.PROTOCOL: ProviderProtocolError,
    FailureKind.TRUNCATED: ProviderStreamTruncated,
}


def typed_error(exc: TranscriptionSessionError) -> ProviderError:
    """Translate a neutral session failure into the platform taxonomy."""
    error_type = _TYPED.get(exc.kind, ProviderUnavailable)
    return error_type(f"deepgram stt: {exc.detail or exc.kind.value}")


class _Upload:
    """The upload half of the duplex. Owned by exactly one `transcribe()` call."""

    __slots__ = ("_frames", "_session", "_sample_rate", "error", "finishing", "sent")

    def __init__(
        self, frames: AsyncIterator[AudioFrame], session: TranscriptionSession, sample_rate: int
    ) -> None:
        self._frames = frames
        self._session = session
        self._sample_rate = sample_rate
        #: The first failure on the upload side, re-raised by the receive side.
        self.error: BaseException | None = None
        #: Set **before** end-of-audio is requested, so the receive side can tell a vendor that
        #: closed because it was asked to from one that closed on its own.
        self.finishing = False
        self.sent = 0

    async def run(self) -> None:
        try:
            async for frame in self._frames:
                audio = validated_audio(frame, self._sample_rate)
                if not audio:
                    # An empty binary message is Deepgram's legacy *close the stream* signal; an
                    # empty frame carries no audio either way, so it is skipped rather than sent.
                    continue
                await self._session.send(audio)
                self.sent += 1
            self.finishing = True
            await self._session.finish()
        except asyncio.CancelledError:
            raise
        except TranscriptionSessionError as exc:
            await self._fail(typed_error(exc))
        except Exception as exc:  # noqa: BLE001 — typed errors (e.g. a failing transport) keep
            # their own type; the receive side re-raises exactly what happened here.
            await self._fail(exc)

    async def _fail(self, error: BaseException) -> None:
        self.error = error
        # Closing the session is what unblocks the receive side: the seam guarantees `results()`
        # ends promptly after `close()`, so an upload failure can never leave the generator hung.
        await self._session.close()


def validated_audio(frame: AudioFrame, sample_rate: int) -> bytes:
    """Return the frame's PCM bytes, or raise if the frame is not what the stream was opened for.

    Checked per frame because the stream was declared to the vendor at connect time: a frame in a
    different format is not an error the vendor can report — it is silently wrong text."""
    if frame.encoding is not AudioEncoding.PCM16:
        raise CapabilityUnsupported(
            f"deepgram stt: requires pcm16 audio, got {frame.encoding.value}"
        )
    if frame.channels != SUPPORTED_CHANNELS:
        raise CapabilityUnsupported(
            f"deepgram stt: requires mono audio, got {frame.channels} channels"
        )
    if frame.sample_rate != sample_rate:
        raise CapabilityUnsupported(
            f"deepgram stt: frame sample rate {frame.sample_rate} does not match the stream's "
            f"{sample_rate}"
        )
    if len(frame.data) % 2:
        raise ProviderProtocolError("deepgram stt: pcm16 frame has an odd number of bytes")
    return frame.data


def mapped_result(result: TranscriptionResult) -> Partial | Final | None:
    """Map a vendor result onto the contract, without promoting or inventing anything."""
    text = result.text.strip()
    if not text:
        return None
    if result.is_final:
        return Final(text=text, words=result.words)
    return Partial(text=text, stability=0.0)


class DeepgramSTT(STTProvider):
    """Streaming STT over an injected `TranscriptionClient`."""

    name = "deepgram"

    def __init__(
        self,
        client: TranscriptionClient,
        *,
        model: str = DEFAULT_MODEL,
        languages: tuple[str, ...] = DEFAULT_LANGUAGES,
    ) -> None:
        if not isinstance(client, TranscriptionClient):
            raise TypeError("DeepgramSTT requires a TranscriptionClient")
        if not model:
            raise ValueError("a model is required")
        if not languages:
            raise ValueError("at least one language must be declared")
        self._client = client
        self._model = model
        self.capabilities = STTCapabilities(
            streaming=True,
            partials=True,
            word_timestamps=True,
            languages=tuple(languages),
        )

    async def transcribe(
        self, frames: AsyncIterator[AudioFrame], *, language: str, sample_rate: int
    ) -> AsyncIterator[Partial | Final]:
        self._require_supported(language, sample_rate)
        session = await self._open(
            StreamSpec(
                language=language,
                sample_rate=sample_rate,
                channels=SUPPORTED_CHANNELS,
                model=self._model,
            )
        )
        upload = _Upload(frames, session, sample_rate)
        task = asyncio.create_task(upload.run(), name="deepgram-stt-upload")
        try:
            try:
                async for result in session.results():
                    mapped = mapped_result(result)
                    if mapped is not None:
                        yield mapped
            except TranscriptionSessionError as exc:
                if upload.error is not None:
                    raise upload.error from None
                raise typed_error(exc) from None
            except Exception:  # noqa: BLE001 — a misbehaving session still leaves typed (CR6).
                # `CancelledError` and `GeneratorExit` are BaseExceptions and pass straight through.
                if upload.error is not None:
                    raise upload.error from None
                raise ProviderStreamTruncated(
                    "deepgram stt: the result stream ended unexpectedly"
                ) from None

            # The vendor ended the result stream cleanly. That is only a *finished* stream if the
            # audio ended first; otherwise the vendor stopped listening while the caller spoke.
            if upload.error is not None:
                raise upload.error
            if not upload.finishing:
                raise ProviderStreamTruncated(
                    "deepgram stt: the vendor ended the stream before the audio ended"
                )
            await asyncio.wait({task})
            if upload.error is not None:
                raise upload.error
        finally:
            await _settle(task)
            await session.close()

    def _require_supported(self, language: str, sample_rate: int) -> None:
        if language not in self.capabilities.languages:
            raise CapabilityUnsupported(
                f"deepgram stt: language {language!r} is not declared "
                f"(declared: {', '.join(self.capabilities.languages)})"
            )
        if sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise CapabilityUnsupported(
                f"deepgram stt: sample rate {sample_rate} is not supported"
            )

    async def _open(self, spec: StreamSpec) -> TranscriptionSession:
        try:
            return await self._client.open(spec)
        except asyncio.CancelledError:
            raise
        except TranscriptionSessionError as exc:
            raise typed_error(exc) from None
        except Exception:  # noqa: BLE001 — CR6 backstop
            raise ProviderUnavailable("deepgram stt: the stream could not be opened") from None


async def _settle(task: asyncio.Task[None]) -> None:
    """Cancel the upload task if it is still running and wait until it has actually stopped.

    `asyncio.wait` rather than `await task`: it never re-raises the child's cancellation, so the
    *caller's* own cancellation is the only one that propagates — and it still does."""
    if not task.done():
        task.cancel()
    await asyncio.wait({task})


__all__ = [
    "DEFAULT_LANGUAGES",
    "DEFAULT_MODEL",
    "SUPPORTED_CHANNELS",
    "SUPPORTED_SAMPLE_RATES",
    "DeepgramSTT",
    "mapped_result",
    "typed_error",
    "validated_audio",
]

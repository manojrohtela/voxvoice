"""`CartesiaTTS` — real streaming TTS behind the unchanged `TTSProvider` contract (M11.10).

The provider is **first-party and library-free**. It owns the format contract, the framing, the
failure taxonomy and the stream lifecycle; the vendor SDK is reached only through the
`SpeechClient` seam, whose one Cartesia implementation lives in `cartesia_client.py` (AR-12,
P-ADR-0002 §3 — Cartesia is the bound streaming TTS).

## Genuinely streaming output

`synthesize()` yields an `AudioFrame` for each piece of audio the vendor produces, as it produces
it. The utterance is never accumulated and replayed: playback is meant to begin on the first
frame, and a provider that gathered the whole utterance first would move the latency floor
somewhere no pipeline test could see.

## Text input is per-utterance, and says so

`capabilities.streaming_input` is **False**. Cartesia's streaming *text input* rides its WebSocket
transport, which is pinned incompatibly with the platform's existing `websockets` (see
`cartesia_client`), so this provider accepts one utterance per call. That is declared honestly
rather than emulated, and the runtime adapts by synthesizing clause by clause and **recording the
degradation** (ADR-0048 PB3, AR-24) — which is exactly what the flag exists for.

## Framing, and why the provider does it

Vendor chunk boundaries have nothing to do with sample boundaries or with a useful playback frame
size. The provider therefore re-frames into fixed-duration frames, carrying any partial sample
across chunks so no frame ever starts mid-sample. Each frame declares `PCM16`, the requested rate
and mono — a truthful declaration is only possible here, where the format that was *requested* is
known. **No resampling happens** anywhere in this plugin: the session's rate is what is asked of
the vendor, and a rate the vendor cannot produce is refused (ADR-0044 AD4/AR-41 keep resampling at
the runtime's single ingress/egress).

## Failures

Every failure leaves as a `core.errors` type (ADR-0046, ADR-0049 CR6):

| cause | error |
|---|---|
| request deadline exceeded | `ProviderTimeout` |
| vendor unreachable, 5xx | `ProviderUnavailable` |
| rejected credential | `ProviderAuthError` |
| rate limited | `ProviderRateLimited` |
| malformed payload, rejected request, undecodable audio | `ProviderProtocolError` |
| stream ended mid-utterance | `ProviderStreamTruncated` |
| unsupported rate, or streaming input requested | `CapabilityUnsupported` |

`asyncio.CancelledError` is never translated: a barge-in is not the vendor failing. Breaker
accounting, per-item deadlines and degradation recording belong to `ProviderGuard`, which the
pipeline already wraps around every synthesis — the provider does not duplicate them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from core.audio import DEFAULT_SAMPLE_RATE, AudioEncoding, AudioFrame
from core.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderProtocolError,
    ProviderRateLimited,
    ProviderStreamTruncated,
    ProviderTimeout,
    ProviderUnavailable,
)
from core.providers.tts import TTSCapabilities, TTSProvider

from .cartesia_client import SUPPORTED_SAMPLE_RATES
from .speech_stream import FailureKind, SpeechClient, SpeechSpec, SpeechStreamError

#: Bytes per PCM16 sample, mono. Named because the framing arithmetic below is otherwise a pair
#: of bare 2s whose meaning a reader has to reconstruct.
BYTES_PER_SAMPLE = 2

#: The duration of one emitted frame. 20 ms is the platform's media cadence — what WebRTC carries
#: and what the transport already sends — so playback hands the transport frames of the size it
#: already expects instead of re-chunking them downstream.
FRAME_MS = 20

#: Mono only. Multichannel speech is a different product shape.
CHANNELS = 1

_TYPED: dict[FailureKind, type[ProviderError]] = {
    FailureKind.TIMEOUT: ProviderTimeout,
    FailureKind.UNAVAILABLE: ProviderUnavailable,
    FailureKind.AUTH: ProviderAuthError,
    FailureKind.RATE_LIMITED: ProviderRateLimited,
    FailureKind.PROTOCOL: ProviderProtocolError,
    FailureKind.TRUNCATED: ProviderStreamTruncated,
}


def typed_error(exc: SpeechStreamError) -> ProviderError:
    """Translate a neutral stream failure into the platform taxonomy."""
    error_type = _TYPED.get(exc.kind, ProviderUnavailable)
    return error_type(f"cartesia tts: {exc.detail or exc.kind.value}")


def frame_bytes(sample_rate: int) -> int:
    """How many bytes make one `FRAME_MS` frame at this rate."""
    return sample_rate * FRAME_MS // 1000 * BYTES_PER_SAMPLE


class CartesiaTTS(TTSProvider):
    """Streaming TTS over an injected `SpeechClient`."""

    name = "cartesia"

    def __init__(
        self,
        client: SpeechClient,
        *,
        voice: str | None = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        sample_rates: tuple[int, ...] = SUPPORTED_SAMPLE_RATES,
    ) -> None:
        if not isinstance(client, SpeechClient):
            raise TypeError("CartesiaTTS requires a SpeechClient")
        if not sample_rates:
            raise ValueError("at least one sample rate must be declared")
        if sample_rate not in sample_rates:
            raise ValueError(f"sample rate {sample_rate} is not among the declared rates")
        self._client = client
        self._voice = voice
        # The rate this provider *produces* at. The `TTSProvider.synthesize` contract carries no
        # rate argument — deliberately, because ADR-0044 AD4 / AR-41 put sample-rate conversion at
        # the runtime's single egress point and forbid providers from resampling. So the provider
        # is configured with one rate it asks the vendor for, declares it, and the pipeline's
        # `AudioNormalizer` converts to the session's rate if they differ. Configuring this to
        # match the session's rate simply means the normalizer has nothing to do.
        self._sample_rate = sample_rate
        self.capabilities = TTSCapabilities(
            streaming=True,
            # No SSML is sent, so claiming markup support would be a promise the adapter does not
            # keep — the runtime would be entitled to pass markup that arrives as spoken angle
            # brackets.
            markup=False,
            # Empty on purpose: voices are opaque vendor ids an operator configures, and the
            # catalogue changes without a release of ours. Enumerating it here would reject valid
            # voices and assert something nothing verified.
            voices=(),
            sample_rates=tuple(sample_rates),
            # See the module docstring: declared honestly, not emulated.
            streaming_input=False,
        )

    async def synthesize(
        self, text: str, *, voice: str | None = None, language: str = "en"
    ) -> AsyncIterator[AudioFrame]:
        spoken = text.strip()
        if not spoken:
            # Nothing to say. Opening a synthesis for empty text would spend a request to be told
            # so, and some vendors answer it with a click of silence.
            return
        async for frame in self._stream(
            SpeechSpec(
                text=spoken,
                voice=voice or self._voice,
                language=language,
                sample_rate=self._sample_rate,
            )
        ):
            yield frame

    async def _stream(self, spec: SpeechSpec) -> AsyncIterator[AudioFrame]:
        """Open one synthesis and yield fixed-duration frames as audio arrives."""
        stream = await self._open(spec)
        size = frame_bytes(spec.sample_rate)
        pending = bytearray()
        produced = False
        try:
            try:
                async for chunk in stream:
                    if not chunk.audio:
                        # Keep-alive and metadata events carry no audio. Emitting a frame for one
                        # would put silence into the caller's ear that the model never produced.
                        continue
                    produced = True
                    pending.extend(chunk.audio)
                    while len(pending) >= size:
                        yield self._frame(bytes(pending[:size]), spec.sample_rate)
                        del pending[:size]
            except SpeechStreamError as exc:
                raise typed_error(exc) from None
            except Exception:  # noqa: BLE001 — a misbehaving seam still leaves typed (CR6).
                # `CancelledError` and `GeneratorExit` are BaseExceptions and pass straight through.
                raise ProviderStreamTruncated(
                    "cartesia tts: the synthesis stream ended unexpectedly"
                ) from None
            if pending:
                # The tail is shorter than a full frame. It is real audio the model produced, so
                # it is emitted rather than dropped; only a partial *sample* is impossible to
                # render and would mean the vendor truncated mid-sample.
                if len(pending) % BYTES_PER_SAMPLE:
                    raise ProviderStreamTruncated(
                        "cartesia tts: the synthesis ended mid-sample"
                    )
                yield self._frame(bytes(pending), spec.sample_rate)
            elif not produced:
                # A synthesis that returned no audio at all is not a silent utterance — it is a
                # vendor that did not do the job, and letting it pass would leave the assistant
                # mute with every subsystem reporting success.
                raise ProviderStreamTruncated("cartesia tts: the synthesis produced no audio")
        finally:
            # Runs on every path — normal end, failure, and the consumer closing the generator on
            # a barge-in — so an interrupted utterance cannot leave a vendor connection open.
            await stream.aclose()

    def _frame(self, data: bytes, sample_rate: int) -> AudioFrame:
        return AudioFrame(
            data=data,
            sample_rate=sample_rate,
            encoding=AudioEncoding.PCM16,
            channels=CHANNELS,
        )

    async def _open(self, spec: SpeechSpec):
        try:
            return await self._client.open(spec)
        except SpeechStreamError as exc:
            raise typed_error(exc) from None
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise  # cancellation during setup withdraws; it is not a vendor failure
            raise ProviderUnavailable("cartesia tts: the synthesis could not be opened") from None


__all__ = [
    "BYTES_PER_SAMPLE",
    "CHANNELS",
    "FRAME_MS",
    "CartesiaTTS",
    "frame_bytes",
    "typed_error",
]

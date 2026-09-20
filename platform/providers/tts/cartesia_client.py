"""The **only** module in the platform that imports the Cartesia SDK (M11.10, AR-12).

Everything vendor-shaped stops here: the client, its exception hierarchy, its SSE event models and
its base64 payloads. What leaves is `providers.tts.speech_stream` — raw audio bytes and a neutral
`FailureKind`, nothing else.

## What the SDK actually gives us

Verified against `cartesia` 4.2.0 by inspection of the installed package (no live API call was
made, and none is made by the test suite — R-58):

- `AsyncCartesia(api_key=..., timeout=..., max_retries=...)` — an async client over `httpx`, the
  same Stainless-generated shape as the Groq SDK M11.9 integrated.
- `client.tts.sse(...)` returns `AsyncStream[TTSSSEEvent]`: a genuine server-sent-event stream of
  audio, not a completed blob.
- Events are discriminated by `type`: `chunk` (`data` is **base64** audio, with an `audio`
  property that decodes it), `done`, `error` (carrying `message`/`title`/`request_id`),
  `timestamps` and `phoneme_timestamps`.
- `output_format` accepts `{"container": "raw", "encoding": "pcm_s16le", "sample_rate": N}` with
  N in 8000/16000/22050/24000/44100/48000 — so the session's own rate is requested directly and
  the bytes come back as exactly the PCM16 the platform already carries.
- Errors derive from `CartesiaError`; `APIStatusError` subclasses carry the HTTP status.

## Why SSE rather than the WebSocket streaming-input path

Cartesia's WebSocket transport — the one that would accept text *as it is produced* — is an
optional extra pinned to `websockets<16`. The platform already locks `websockets` 17.1 for
Deepgram (M11.6). Resolving `cartesia[websockets]` against that pin was attempted and **downgrades
Cartesia two major versions to 2.0.17** while adding aiohttp, pydub, multidict, propcache and yarl.

So this adapter uses SSE, the provider declares `streaming_input=False` truthfully, and the
runtime takes ADR-0048 PB3's documented fallback — synthesizing clause by clause and recording the
degradation. Audio output is still genuinely streamed; only text *input* is per-clause (R-59).

## Retries are switched off deliberately

The SDK retries twice by default. `ProviderGuard` already owns retry, breaker and failover policy
(AR-23, ADR-0046 FT-D3/FT-D5), and a second invisible retry layer would multiply a failing call's
latency budget and corrupt the breaker's accounting. `max_retries=0` leaves one authority.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import AsyncIterator
from typing import Any

from .speech_stream import (
    FailureKind,
    SpeechChunk,
    SpeechClient,
    SpeechSpec,
    SpeechStream,
    SpeechStreamError,
)

#: How long one synthesis request may take end to end before the SDK abandons it. The guard
#: applies its own per-item deadline on top; this stops a vendor that accepts a connection and
#: then never writes from holding a socket open underneath it.
DEFAULT_TIMEOUT_S = 30.0

#: The model requested when none is configured. Vendor vocabulary, so it is named here rather
#: than in `runtime/` (AR-21).
DEFAULT_MODEL = "sonic-2"

#: The raw PCM encoding requested. The platform's `AudioEncoding.PCM16` is signed 16-bit
#: little-endian, which is exactly this — so no transcoding happens anywhere.
RAW_ENCODING = "pcm_s16le"

#: Sample rates the vendor will return raw PCM at, from the SDK's own `RawOutputFormatParam`.
#: A session at any other rate is refused by the provider rather than silently resampled.
SUPPORTED_SAMPLE_RATES: tuple[int, ...] = (8000, 16000, 22050, 24000, 44100, 48000)

_AUTH_STATUSES = frozenset({401, 403})
_PROTOCOL_STATUSES = frozenset({400, 404, 405, 409, 415, 422})


def classify_status(status: int | None) -> FailureKind:
    """Map an HTTP status onto the neutral vocabulary."""
    if status is None:
        return FailureKind.UNAVAILABLE
    if status in _AUTH_STATUSES:
        return FailureKind.AUTH
    if status == 429:
        return FailureKind.RATE_LIMITED
    if status in _PROTOCOL_STATUSES:
        return FailureKind.PROTOCOL
    if status in (408, 504):
        return FailureKind.TIMEOUT
    if status >= 500:
        return FailureKind.UNAVAILABLE
    return FailureKind.PROTOCOL


def classify_exception(exc: BaseException) -> FailureKind:
    """Map a vendor exception onto the neutral vocabulary.

    Written against the SDK's **exception classes and HTTP statuses**, never message text: vendor
    messages are not a contract and change without notice, whereas a 401 is a 401. Import-local so
    this module stays the only one that names the SDK."""
    import cartesia

    if isinstance(exc, cartesia.APITimeoutError):
        # Checked before APIConnectionError — it is a subclass of it.
        return FailureKind.TIMEOUT
    if isinstance(exc, cartesia.APIConnectionError):
        return FailureKind.UNAVAILABLE
    if isinstance(exc, cartesia.APIStatusError):
        return classify_status(getattr(exc, "status_code", None))
    if isinstance(exc, cartesia.APIResponseValidationError):
        return FailureKind.PROTOCOL
    return FailureKind.UNAVAILABLE


def event_audio(event: Any) -> bytes:
    """The decoded audio of one vendor event, or `b""`.

    `done`, `timestamps` and `phoneme_timestamps` events carry no audio and are normal traffic, so
    they read as empty rather than raising. An `error` event is **not** silently empty — it is the
    vendor reporting failure mid-stream, and treating it as "no audio this time" would turn a
    broken utterance into a short one.

    Base64 that will not decode is a protocol failure, not something to paper over: a frame built
    from truncated bytes would be audible noise."""
    kind = getattr(event, "type", None)
    if kind == "error":
        raise SpeechStreamError(
            classify_status(getattr(event, "status_code", None)),
            "the vendor reported an error mid-synthesis",
        )
    data = getattr(event, "data", None)
    if not data:
        return b""
    decoded = getattr(event, "audio", None)
    if isinstance(decoded, bytes):
        return decoded
    if not isinstance(data, str):
        raise SpeechStreamError(FailureKind.PROTOCOL, "audio payload was not text")
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise SpeechStreamError(
            FailureKind.PROTOCOL, "audio payload was not valid base64"
        ) from None


class _CartesiaStream(SpeechStream):
    """One open Cartesia synthesis. Owns the vendor stream and closes it on every path."""

    __slots__ = ("_stream", "_closed")

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._closed = False

    async def __aiter__(self) -> AsyncIterator[SpeechChunk]:
        try:
            async for event in self._stream:
                yield SpeechChunk(audio=event_audio(event))
        except SpeechStreamError:
            raise
        except BaseException as exc:
            # `CancelledError` and `GeneratorExit` are BaseExceptions and must pass through
            # untouched: a barge-in is not the vendor failing (ADR-0046 rule 2).
            if not isinstance(exc, Exception):
                raise
            raise SpeechStreamError(
                classify_exception(exc), "the synthesis stream failed"
            ) from None

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._stream.close()
        except Exception:  # noqa: BLE001 — closing is best-effort by contract; a vendor that
            # fails to close cleanly must not turn spoken audio into a failed turn.
            pass


class CartesiaSpeechClient(SpeechClient):
    """Opens Cartesia syntheses with **one injected credential** (ADR-0049 CR4).

    The key is name-mangled and never rendered: `__repr__` is overridden because a default repr on
    a configuration object is one of the two ways credentials reach logs (the other is an exception
    message, handled by never passing vendor text through)."""

    __slots__ = ("_CartesiaSpeechClient__api_key", "_timeout_s", "_model", "_client")

    def __init__(
        self, api_key: str, *, model: str = DEFAULT_MODEL, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> None:
        if not api_key:
            raise ValueError("a cartesia api key is required")
        if not model:
            raise ValueError("a model is required")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.__api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._client: Any | None = None

    def __repr__(self) -> str:
        return f"CartesiaSpeechClient(model={self._model!r}, timeout_s={self._timeout_s})"

    async def open(self, spec: SpeechSpec) -> SpeechStream:
        client = self._ensure_client()
        try:
            stream = await client.tts.sse(
                model_id=self._model,
                transcript=spec.text,
                voice=self._voice(spec),
                language=spec.language,
                output_format={
                    "container": "raw",
                    "encoding": RAW_ENCODING,
                    "sample_rate": spec.sample_rate,
                },
            )
        except SpeechStreamError:
            raise
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise  # cancellation withdraws; it is not a vendor failure
            raise SpeechStreamError(
                classify_exception(exc), "the synthesis could not be opened"
            ) from None
        return _CartesiaStream(stream)

    def _voice(self, spec: SpeechSpec) -> dict[str, Any]:
        """The vendor's voice selector. A configured voice is an opaque vendor id (AR-21) — the
        runtime never interprets it, it only carries the operator's choice through."""
        if not spec.voice:
            raise SpeechStreamError(
                FailureKind.PROTOCOL, "a voice must be configured for synthesis"
            )
        return {"mode": "id", "id": spec.voice}

    def _ensure_client(self) -> Any:
        """Construct the vendor client on first use.

        Lazy because importing the SDK costs a package import a process that never selects this
        profile should not pay, and because construction is the first thing that can fail on a
        host where the library is absent."""
        if self._client is None:
            try:
                import cartesia
            except ImportError:
                raise SpeechStreamError(
                    FailureKind.UNAVAILABLE, "the cartesia package is not installed"
                ) from None
            self._client = cartesia.AsyncCartesia(
                api_key=self.__api_key, timeout=self._timeout_s, max_retries=0
            )
        return self._client


def cartesia_available() -> bool:
    """Whether the vendor library can be imported on this host.

    Called by the composition root so a misconfigured host fails at startup rather than when the
    first caller is waiting to be spoken to."""
    try:
        import cartesia  # noqa: F401
    except ImportError:
        return False
    return True


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_S",
    "RAW_ENCODING",
    "SUPPORTED_SAMPLE_RATES",
    "CartesiaSpeechClient",
    "cartesia_available",
    "classify_exception",
    "classify_status",
    "event_audio",
]

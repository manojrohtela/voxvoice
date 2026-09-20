"""`DeepgramTranscriptionClient` — the **only** module that imports the Deepgram SDK (M11.6, AR-12).

A `TranscriptionClient` over Deepgram's streaming listen socket. Everything vendor-shaped lives
here and nowhere else: the SDK import, the connect query parameters, the message types, the
websocket close codes and the HTTP status codes of a refused handshake. What leaves this module is
`TranscriptionResult` on success and `TranscriptionSessionError` (a neutral `FailureKind` plus a
**sanitized** detail) on failure.

## Credentials (ADR-0049)

The client is handed an already-resolved API key by the composition root (CR3) and holds only that
one credential (CR4). It never reads the environment, never resolves a secret by name, and never
places the key in a detail string, a `repr`, or a log line (CR5). Vendor exceptions are classified
and discarded — `from None` — rather than chained, because a chained vendor exception is printed
by every traceback formatter and the SDK's handshake errors carry the request headers (CR6).

## Why the SDK is imported lazily

The dependency is declared and locked (DL-0178), but importing it at module load would make
*every* process that imports the provider package pay for — and depend on — a vendor library that
only the `deepgram` STT profile uses. Deferring the import to `open()` keeps the hermetic suite and
the local profile independent of it, exactly as `AiortcMediaEndpoint` does for aiortc.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Any

from .transcription_session import (
    FailureKind,
    StreamSpec,
    TranscriptionResult,
    TranscriptionSessionError,
)

log = logging.getLogger("voxvoice.providers.stt.deepgram")

#: Deadline for the websocket handshake, in seconds. A bound (§A10), not a measured value (R-29).
DEFAULT_CONNECT_TIMEOUT_S = 10.0

#: Deepgram's wire name for signed 16-bit little-endian PCM — `core.audio.AudioEncoding.PCM16`.
_LINEAR16 = "linear16"

#: Websocket close codes that mean *the audio or the protocol was wrong*, not that the vendor broke.
_PROTOCOL_CLOSE_CODES = frozenset({1003, 1007, 1008, 1009})
#: Clean closes. Anything else that ends the socket without a clean close is truncation.
_NORMAL_CLOSE_CODES = frozenset({1000})


def classify_handshake_status(status_code: int | None) -> FailureKind:
    """Map an HTTP status from a refused handshake onto the neutral failure vocabulary."""
    if status_code in (401, 403):
        return FailureKind.AUTH
    if status_code == 429:
        return FailureKind.RATE_LIMITED
    if status_code == 408:
        return FailureKind.TIMEOUT
    if status_code == 402:
        # Insufficient credit is deliberately *not* AUTH: the credential itself is valid.
        return FailureKind.UNAVAILABLE
    if status_code is not None and 400 <= status_code < 500:
        # A malformed request — an unsupported model/language/parameter combination.
        return FailureKind.PROTOCOL
    return FailureKind.UNAVAILABLE


def classify_close(code: int | None, reason: str) -> FailureKind | None:
    """Map a websocket close onto the neutral vocabulary; `None` means *a clean close*.

    Deepgram reports "no audio received within the timeout" as `1011` with a `NET-0001` reason;
    that is a timeout, while other `1011`s are the vendor failing. The reason string is used
    **only** to classify — it is vendor text and never surfaced."""
    if code in _NORMAL_CLOSE_CODES:
        return None
    if code in _PROTOCOL_CLOSE_CODES:
        return FailureKind.PROTOCOL
    if code == 1011:
        return FailureKind.TIMEOUT if "NET-0001" in reason else FailureKind.UNAVAILABLE
    return FailureKind.TRUNCATED


def _close_details(exc: BaseException) -> tuple[int | None, str]:
    received = getattr(exc, "rcvd", None)
    if received is None:
        return None, ""
    return getattr(received, "code", None), str(getattr(received, "reason", "") or "")


def result_from_message(message: Any) -> TranscriptionResult | None:
    """Convert one Deepgram socket message into a neutral result, or `None` if it carries none.

    Only `Results` messages carry transcripts; `Metadata`, `SpeechStarted` and `UtteranceEnd` are
    not transcripts and are not turned into any. Binary frames are never expected on this socket."""
    if getattr(message, "type", None) != "Results":
        return None
    channel = getattr(message, "channel", None)
    alternatives = getattr(channel, "alternatives", None) or ()
    if not alternatives:
        return None
    best = alternatives[0]
    words = tuple(
        (str(w.word), float(w.start), float(w.end)) for w in (getattr(best, "words", None) or ())
    )
    return TranscriptionResult(
        text=str(getattr(best, "transcript", "") or ""),
        is_final=bool(getattr(message, "is_final", False)),
        words=words,
    )


class _DeepgramSession:
    """One open Deepgram listen socket, behind the neutral `TranscriptionSession` Protocol."""

    def __init__(self, socket: Any, stack: AsyncExitStack, closed_errors: tuple[type, ...]) -> None:
        self._socket = socket
        self._stack = stack
        self._closed_errors = closed_errors
        self._closed = False

    async def send(self, audio: bytes) -> None:
        try:
            await self._socket.send_media(audio)
        except asyncio.CancelledError:
            raise
        except self._closed_errors as exc:
            raise self._closed_error(exc) from None
        except Exception:  # noqa: BLE001 — classified, never chained (CR6)
            raise TranscriptionSessionError(FailureKind.UNAVAILABLE, "audio send failed") from None

    async def finish(self) -> None:
        try:
            await self._socket.send_close_stream()
        except asyncio.CancelledError:
            raise
        except self._closed_errors as exc:
            raise self._closed_error(exc) from None
        except Exception:  # noqa: BLE001
            raise TranscriptionSessionError(
                FailureKind.UNAVAILABLE, "end-of-audio signal failed"
            ) from None

    async def results(self) -> AsyncIterator[TranscriptionResult]:
        try:
            async for message in self._socket:
                result = result_from_message(message)
                if result is not None:
                    yield result
        except asyncio.CancelledError:
            raise
        except self._closed_errors as exc:
            if self._closed:
                return  # we closed it ourselves; ending is the requested outcome
            raise self._closed_error(exc) from None
        except ValueError:
            # A text frame that is not JSON. The SDK only skips *unknown* message types.
            if self._closed:
                return
            raise TranscriptionSessionError(
                FailureKind.PROTOCOL, "the vendor sent an undecodable message"
            ) from None
        except Exception:  # noqa: BLE001
            if self._closed:
                return
            raise TranscriptionSessionError(
                FailureKind.TRUNCATED, "the result stream ended unexpectedly"
            ) from None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._stack.aclose()
        except BaseException as exc:  # noqa: BLE001 — close never raises; cancellation still does
            if isinstance(exc, asyncio.CancelledError):
                raise
            log.debug("deepgram session close raised %s", type(exc).__name__)

    def _closed_error(self, exc: BaseException) -> TranscriptionSessionError:
        code, reason = _close_details(exc)
        kind = classify_close(code, reason) or FailureKind.TRUNCATED
        return TranscriptionSessionError(kind, f"socket closed (code {code})")


class DeepgramTranscriptionClient:
    """Opens Deepgram streaming sessions with one injected, already-resolved API key."""

    def __init__(self, api_key: str, *, connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("a resolved Deepgram API key is required")
        if connect_timeout_s <= 0:
            raise ValueError(f"connect_timeout_s must be positive, got {connect_timeout_s}")
        self.__api_key = api_key
        self._connect_timeout_s = connect_timeout_s

    def __repr__(self) -> str:
        return "DeepgramTranscriptionClient(api_key=***redacted***)"

    async def open(self, spec: StreamSpec) -> _DeepgramSession:
        try:
            from deepgram import AsyncDeepgramClient
            from websockets.exceptions import ConnectionClosed
        except ImportError:
            raise TranscriptionSessionError(
                FailureKind.UNAVAILABLE, "the deepgram-sdk package is not installed"
            ) from None

        stack = AsyncExitStack()
        try:
            client = AsyncDeepgramClient(api_key=self.__api_key)
            connection = client.listen.v1.connect(
                model=spec.model,
                language=spec.language,
                encoding=_LINEAR16,
                sample_rate=str(spec.sample_rate),
                channels=str(spec.channels),
                interim_results="true" if spec.interim_results else "false",
            )
            socket = await asyncio.wait_for(
                stack.enter_async_context(connection), timeout=self._connect_timeout_s
            )
        except asyncio.CancelledError:
            await stack.aclose()
            raise
        except TimeoutError:
            await _quiet_close(stack)
            raise TranscriptionSessionError(
                FailureKind.TIMEOUT, "the streaming handshake exceeded its deadline"
            ) from None
        except Exception as exc:  # noqa: BLE001 — classified by status only, never chained
            await _quiet_close(stack)
            status = getattr(exc, "status_code", None)
            kind = (
                classify_handshake_status(status)
                if status is not None
                else FailureKind.UNAVAILABLE
            )
            detail = (
                f"handshake refused (status {status})"
                if status is not None
                else "the streaming endpoint could not be reached"
            )
            raise TranscriptionSessionError(kind, detail) from None
        return _DeepgramSession(socket, stack, (ConnectionClosed,))


async def _quiet_close(stack: AsyncExitStack) -> None:
    try:
        await stack.aclose()
    except Exception:  # noqa: BLE001
        log.debug("deepgram handshake cleanup raised")


__all__ = [
    "DEFAULT_CONNECT_TIMEOUT_S",
    "DeepgramTranscriptionClient",
    "classify_close",
    "classify_handshake_status",
    "result_from_message",
]

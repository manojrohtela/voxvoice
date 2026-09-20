"""The Deepgram SDK adapter — classification, redaction and lifecycle, without the SDK (M11.6).

`DeepgramTranscriptionClient` is the one module that imports the vendor library. Its behaviour is
provable hermetically by installing **stand-in modules** for `deepgram` and `websockets.exceptions`
in `sys.modules` for the duration of a test: the adapter's lazy import then resolves to a scripted
socket, so no network, no credential and no real SDK are involved — and the suite behaves the same
whether or not the real SDK happens to be installed in the environment (TESTING_POLICY rule 1).

What these tests do **not** prove is that Deepgram's live service behaves as the adapter assumes
(close codes, message order, handshake statuses). That is recorded as an unresolved
live-verification risk (DL-0180), not implied by a green run here.
"""

from __future__ import annotations

import asyncio
import sys
import types
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from providers.stt.deepgram_client import (
    DeepgramTranscriptionClient,
    classify_close,
    classify_handshake_status,
    result_from_message,
)
from providers.stt.transcription_session import (
    FailureKind,
    StreamSpec,
    TranscriptionResult,
    TranscriptionSessionError,
)

SECRET = "dg-test-key-0123456789abcdef"
SPEC = StreamSpec(language="en", sample_rate=16000, channels=1, model="nova-3")


# ── pure classification ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status, kind",
    [
        (401, FailureKind.AUTH),
        (403, FailureKind.AUTH),
        (429, FailureKind.RATE_LIMITED),
        (408, FailureKind.TIMEOUT),
        (400, FailureKind.PROTOCOL),
        (402, FailureKind.UNAVAILABLE),
        (500, FailureKind.UNAVAILABLE),
        (503, FailureKind.UNAVAILABLE),
        (None, FailureKind.UNAVAILABLE),
    ],
)
def test_handshake_statuses_are_classified(status, kind):
    assert classify_handshake_status(status) is kind


@pytest.mark.parametrize(
    "code, reason, kind",
    [
        (1000, "", None),
        (1008, "DATA-0000", FailureKind.PROTOCOL),
        (1003, "", FailureKind.PROTOCOL),
        (1007, "", FailureKind.PROTOCOL),
        (1009, "", FailureKind.PROTOCOL),
        (1011, "NET-0001 no audio received", FailureKind.TIMEOUT),
        (1011, "internal", FailureKind.UNAVAILABLE),
        (1006, "", FailureKind.TRUNCATED),
        (1001, "", FailureKind.TRUNCATED),
        (None, "", FailureKind.TRUNCATED),
    ],
)
def test_close_codes_are_classified(code, reason, kind):
    assert classify_close(code, reason) is kind


def _results_message(transcript: str, *, is_final: bool, words=()):
    return SimpleNamespace(
        type="Results",
        is_final=is_final,
        channel=SimpleNamespace(
            alternatives=[
                SimpleNamespace(
                    transcript=transcript,
                    confidence=0.9,
                    words=[SimpleNamespace(word=w, start=s, end=e) for w, s, e in words],
                )
            ]
        ),
    )


def test_a_results_message_becomes_a_neutral_result():
    message = _results_message("hello", is_final=True, words=[("hello", 0.1, 0.4)])
    assert result_from_message(message) == TranscriptionResult(
        text="hello", is_final=True, words=(("hello", 0.1, 0.4),)
    )


def test_an_interim_results_message_is_not_final():
    assert result_from_message(_results_message("hel", is_final=False)).is_final is False


@pytest.mark.parametrize(
    "message",
    [
        SimpleNamespace(type="Metadata"),
        SimpleNamespace(type="SpeechStarted"),
        SimpleNamespace(type="UtteranceEnd"),
        b"\x00\x01",
        SimpleNamespace(type="Results", channel=SimpleNamespace(alternatives=[])),
    ],
    ids=["metadata", "speech-started", "utterance-end", "binary", "no-alternatives"],
)
def test_non_transcript_messages_produce_no_result(message):
    assert result_from_message(message) is None


# ── credential handling (ADR-0049) ──────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "   ", None, 42])
def test_the_client_requires_a_resolved_key(bad):
    with pytest.raises(ValueError, match="API key is required") as raised:
        DeepgramTranscriptionClient(bad)  # type: ignore[arg-type]
    assert SECRET not in str(raised.value)


def test_the_key_never_appears_in_repr_str_or_public_attributes():
    client = DeepgramTranscriptionClient(SECRET)
    assert SECRET not in repr(client)
    assert SECRET not in str(client)
    for name in dir(client):
        if name.startswith("_DeepgramTranscriptionClient__"):
            continue  # name-mangled private slot: the one place the key is held
        value = getattr(client, name, None)
        if isinstance(value, str):
            assert SECRET not in value, name


def test_a_non_positive_connect_timeout_is_refused():
    with pytest.raises(ValueError):
        DeepgramTranscriptionClient(SECRET, connect_timeout_s=0)


# ── a stand-in SDK ──────────────────────────────────────────────────────────


class FakeConnectionClosed(Exception):
    def __init__(self, code: int | None, reason: str = "") -> None:
        super().__init__(f"closed {code}")
        self.rcvd = None if code is None else SimpleNamespace(code=code, reason=reason)


class FakeApiError(Exception):
    """Shaped like the SDK's `ApiError`: carries the request headers — including the credential."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code} headers {{'Authorization': 'Token {SECRET}'}}")
        self.status_code = status_code
        self.headers = {"Authorization": f"Token {SECRET}"}


class FakeSocket:
    def __init__(self) -> None:
        self.media: list[bytes] = []
        self.close_streams = 0
        self.inbox: asyncio.Queue[object] = asyncio.Queue()
        self.send_error: Exception | None = None

    async def send_media(self, audio: bytes) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.media.append(audio)

    async def send_close_stream(self) -> None:
        self.close_streams += 1

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        while True:
            item = await self.inbox.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item


class FakeSDK:
    """Installs `deepgram` + `websockets.exceptions` stand-ins and records what the adapter did."""

    def __init__(self, *, connect_error: Exception | None = None, hang: bool = False) -> None:
        self.socket = FakeSocket()
        self.connect_error = connect_error
        self.hang = hang
        self.api_keys: list[str] = []
        self.connect_kwargs: list[dict] = []
        self.exited = 0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sdk = self

        class AsyncDeepgramClient:
            def __init__(self, *, api_key: str) -> None:
                sdk.api_keys.append(api_key)
                self.listen = SimpleNamespace(v1=SimpleNamespace(connect=self._connect))

            @asynccontextmanager
            async def _connect(self, **kwargs):
                sdk.connect_kwargs.append(kwargs)
                if sdk.hang:
                    await asyncio.Event().wait()
                if sdk.connect_error is not None:
                    raise sdk.connect_error
                try:
                    yield sdk.socket
                finally:
                    sdk.exited += 1
                    sdk.socket.inbox.put_nowait(None)

        deepgram = types.ModuleType("deepgram")
        deepgram.AsyncDeepgramClient = AsyncDeepgramClient
        exceptions = types.ModuleType("websockets.exceptions")
        exceptions.ConnectionClosed = FakeConnectionClosed
        websockets = types.ModuleType("websockets")
        websockets.exceptions = exceptions
        monkeypatch.setitem(sys.modules, "deepgram", deepgram)
        monkeypatch.setitem(sys.modules, "websockets", websockets)
        monkeypatch.setitem(sys.modules, "websockets.exceptions", exceptions)


# ── open ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_connects_with_the_declared_format_and_only_the_injected_key(monkeypatch):
    sdk = FakeSDK()
    sdk.install(monkeypatch)
    session = await DeepgramTranscriptionClient(SECRET).open(SPEC)
    assert sdk.api_keys == [SECRET]
    assert sdk.connect_kwargs == [
        {
            "model": "nova-3",
            "language": "en",
            "encoding": "linear16",
            "sample_rate": "16000",
            "channels": "1",
            "interim_results": "true",
        }
    ]
    await session.close()


@pytest.mark.parametrize(
    "status, kind",
    [(401, FailureKind.AUTH), (429, FailureKind.RATE_LIMITED), (503, FailureKind.UNAVAILABLE)],
)
@pytest.mark.asyncio
async def test_a_refused_handshake_is_classified_and_never_leaks_the_key(monkeypatch, status, kind):
    FakeSDK(connect_error=FakeApiError(status)).install(monkeypatch)
    with pytest.raises(TranscriptionSessionError) as raised:
        await DeepgramTranscriptionClient(SECRET).open(SPEC)
    error = raised.value
    assert error.kind is kind
    assert SECRET not in str(error) and SECRET not in error.detail
    assert error.__cause__ is None and error.__suppress_context__, "vendor error was chained"


@pytest.mark.asyncio
async def test_an_unreachable_endpoint_is_unavailable(monkeypatch):
    FakeSDK(connect_error=OSError("connection refused")).install(monkeypatch)
    with pytest.raises(TranscriptionSessionError) as raised:
        await DeepgramTranscriptionClient(SECRET).open(SPEC)
    assert raised.value.kind is FailureKind.UNAVAILABLE


@pytest.mark.asyncio
async def test_a_stalled_handshake_times_out(monkeypatch):
    FakeSDK(hang=True).install(monkeypatch)
    client = DeepgramTranscriptionClient(SECRET, connect_timeout_s=0.01)
    with pytest.raises(TranscriptionSessionError) as raised:
        await client.open(SPEC)
    assert raised.value.kind is FailureKind.TIMEOUT


@pytest.mark.asyncio
async def test_a_missing_sdk_is_a_typed_unavailability(monkeypatch):
    monkeypatch.setitem(sys.modules, "deepgram", None)  # makes `import deepgram` raise ImportError
    with pytest.raises(TranscriptionSessionError) as raised:
        await DeepgramTranscriptionClient(SECRET).open(SPEC)
    assert raised.value.kind is FailureKind.UNAVAILABLE
    assert "not installed" in raised.value.detail


# ── the open session ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_finish_and_results_round_trip(monkeypatch):
    sdk = FakeSDK()
    sdk.install(monkeypatch)
    session = await DeepgramTranscriptionClient(SECRET).open(SPEC)
    await session.send(b"\x01\x02")
    await session.finish()
    sdk.socket.inbox.put_nowait(_results_message("hi", is_final=False))
    sdk.socket.inbox.put_nowait(SimpleNamespace(type="Metadata"))
    sdk.socket.inbox.put_nowait(_results_message("hi there", is_final=True))
    sdk.socket.inbox.put_nowait(None)  # the vendor closes cleanly
    results = [r async for r in session.results()]
    assert sdk.socket.media == [b"\x01\x02"]
    assert sdk.socket.close_streams == 1
    assert results == [
        TranscriptionResult(text="hi", is_final=False),
        TranscriptionResult(text="hi there", is_final=True),
    ]
    await session.close()


@pytest.mark.parametrize(
    "code, reason, kind",
    [
        (1008, "DATA-0000", FailureKind.PROTOCOL),
        (1011, "NET-0001", FailureKind.TIMEOUT),
        (1006, "", FailureKind.TRUNCATED),
    ],
)
@pytest.mark.asyncio
async def test_an_abnormal_close_mid_stream_is_classified(monkeypatch, code, reason, kind):
    sdk = FakeSDK()
    sdk.install(monkeypatch)
    session = await DeepgramTranscriptionClient(SECRET).open(SPEC)
    sdk.socket.inbox.put_nowait(FakeConnectionClosed(code, reason))
    with pytest.raises(TranscriptionSessionError) as raised:
        [r async for r in session.results()]
    assert raised.value.kind is kind
    assert reason not in raised.value.detail or not reason, "vendor close reason was surfaced"
    await session.close()


@pytest.mark.asyncio
async def test_an_undecodable_message_is_a_protocol_error(monkeypatch):
    sdk = FakeSDK()
    sdk.install(monkeypatch)
    session = await DeepgramTranscriptionClient(SECRET).open(SPEC)
    sdk.socket.inbox.put_nowait(ValueError("Expecting value"))
    with pytest.raises(TranscriptionSessionError) as raised:
        [r async for r in session.results()]
    assert raised.value.kind is FailureKind.PROTOCOL
    await session.close()


@pytest.mark.asyncio
async def test_a_send_on_a_closed_socket_is_classified(monkeypatch):
    sdk = FakeSDK()
    sdk.install(monkeypatch)
    session = await DeepgramTranscriptionClient(SECRET).open(SPEC)
    sdk.socket.send_error = FakeConnectionClosed(1008, "DATA-0000")
    with pytest.raises(TranscriptionSessionError) as raised:
        await session.send(b"\x00\x00")
    assert raised.value.kind is FailureKind.PROTOCOL
    await session.close()


@pytest.mark.asyncio
async def test_close_is_idempotent_and_ends_results(monkeypatch):
    sdk = FakeSDK()
    sdk.install(monkeypatch)
    session = await DeepgramTranscriptionClient(SECRET).open(SPEC)
    reader = asyncio.ensure_future(_drain(session))
    await session.close()
    await session.close()
    assert await reader == []
    assert sdk.exited == 1


@pytest.mark.asyncio
async def test_an_error_after_a_local_close_is_not_reported_as_a_vendor_failure(monkeypatch):
    sdk = FakeSDK()
    sdk.install(monkeypatch)
    session = await DeepgramTranscriptionClient(SECRET).open(SPEC)
    await session.close()
    sdk.socket.inbox = asyncio.Queue()
    sdk.socket.inbox.put_nowait(FakeConnectionClosed(1006))
    assert await _drain(session) == []


async def _drain(session):
    return [r async for r in session.results()]

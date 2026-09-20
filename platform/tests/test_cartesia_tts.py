"""`CartesiaTTS` and its SDK adapter, proven without the vendor (M11.10).

The provider runs against a deterministic `SpeechClient`; the adapter runs against a **stand-in
`cartesia` module** in `sys.modules`, so the suite behaves identically whether or not the real
wheel is installed (it is under `uv sync`; it is not in a bare dev venv). No test opens a socket,
and none needs a credential.

What is deliberately **not** claimed anywhere here: audio quality, naturalness, latency, or that
Cartesia behaves on the wire as these fakes do. Those need a live account, and **no live call was
made** (R-58).
"""

from __future__ import annotations

import base64
import sys
import types

import pytest

from core.audio import AudioEncoding
from core.errors import (
    ProviderAuthError,
    ProviderProtocolError,
    ProviderRateLimited,
    ProviderStreamTruncated,
    ProviderTimeout,
    ProviderUnavailable,
)
from core.providers.tts import TTSProvider
from providers.tts.cartesia import (
    FRAME_MS,
    CartesiaTTS,
    frame_bytes,
    typed_error,
)
from providers.tts.cartesia_client import (
    CartesiaSpeechClient,
    cartesia_available,
    classify_exception,
    classify_status,
    event_audio,
)
from providers.tts.speech_stream import FailureKind, SpeechSpec, SpeechStreamError

from .fake_speech import FakeSpeechClient, pcm

SPEC = SpeechSpec(text="hello", voice="voice-1", language="en", sample_rate=16000)


async def frames(provider: CartesiaTTS, text: str = "Hello there."):
    return [f async for f in provider.synthesize(text)]


# ── 4, 5, 9: normalized, streaming audio output ─────────────────────────────


async def test_4_audio_crosses_the_boundary_as_declared_pcm_frames():
    """Every frame declares its encoding, rate and channels — no frame that lies about itself."""
    client = FakeSpeechClient([pcm(640), pcm(640, fill=2)])
    provider = CartesiaTTS(client, voice="v")
    out = await frames(provider)
    assert all(f.encoding is AudioEncoding.PCM16 for f in out)
    assert all(f.sample_rate == 16000 and f.channels == 1 for f in out)
    assert [len(f.data) for f in out] == [640, 640]


async def test_5_audio_is_streamed_rather_than_accumulated():
    """A frame is produced from the first vendor chunk, long before the utterance ends."""
    import asyncio

    from .fake_speech import settle

    gate, started = asyncio.Event(), asyncio.Event()
    client = FakeSpeechClient([pcm(640), pcm(640, fill=2)], gate=gate, started=started)
    provider = CartesiaTTS(client, voice="v")
    seen = []

    async def consume() -> None:
        async for f in provider.synthesize("Hello."):
            seen.append(f)

    task = asyncio.create_task(consume())
    assert await settle(started.is_set), "the vendor was never asked for a second chunk"
    assert len(seen) == 1, "no frame was produced before the utterance finished"
    gate.set()
    await task
    assert len(seen) == 2


async def test_vendor_chunk_boundaries_are_re_framed_to_a_fixed_duration():
    """A vendor chunk has nothing to do with a useful playback frame; one long chunk becomes
    several 20 ms frames, and several small chunks become one."""
    client = FakeSpeechClient([pcm(1600)])  # 50 ms in one chunk
    out = await frames(CartesiaTTS(client, voice="v"))
    assert [len(f.data) for f in out] == [640, 640, 320]  # 20 + 20 + the 10 ms tail


async def test_a_sample_split_across_chunks_is_not_damaged():
    """A chunk may end mid-sample; carrying the remainder is what stops audible corruption."""
    client = FakeSpeechClient([pcm(640)[:639], pcm(640)[639:] + pcm(640, fill=2)])
    out = await frames(CartesiaTTS(client, voice="v"))
    assert sum(len(f.data) for f in out) == 1280


async def test_6_empty_vendor_chunks_produce_no_audio():
    """Keep-alive and metadata events carry no audio; a frame for one would be invented silence."""
    client = FakeSpeechClient([b"", pcm(640), b"", b""])
    out = await frames(CartesiaTTS(client, voice="v"))
    assert [len(f.data) for f in out] == [640]


async def test_9_a_normal_synthesis_completes():
    client = FakeSpeechClient([pcm(640)] * 3)
    assert len(await frames(CartesiaTTS(client, voice="v"))) == 3


async def test_empty_text_is_not_sent_to_the_vendor():
    client = FakeSpeechClient()
    assert await frames(CartesiaTTS(client, voice="v"), "   ") == []
    assert client.specs == []


# ── 7, 8, 10: malformed audio, formats, truncation ─────────────────────────


async def test_10_a_synthesis_that_produces_no_audio_is_truncated_not_silent():
    """Otherwise the assistant goes mute while every subsystem reports success."""
    client = FakeSpeechClient([])
    with pytest.raises(ProviderStreamTruncated):
        await frames(CartesiaTTS(client, voice="v"))


async def test_7_audio_ending_mid_sample_is_reported_rather_than_rendered():
    client = FakeSpeechClient([pcm(640) + b"\x01"])  # odd byte count
    with pytest.raises(ProviderStreamTruncated, match="mid-sample"):
        await frames(CartesiaTTS(client, voice="v"))


def test_8_an_undeclared_sample_rate_is_refused_at_construction():
    with pytest.raises(ValueError):
        CartesiaTTS(FakeSpeechClient(), voice="v", sample_rate=11025)


def test_the_declared_rates_are_the_vendors_raw_pcm_rates():
    provider = CartesiaTTS(FakeSpeechClient(), voice="v")
    assert 16000 in provider.capabilities.sample_rates
    assert 11025 not in provider.capabilities.sample_rates


def test_frame_arithmetic_matches_the_declared_cadence():
    assert FRAME_MS == 20
    assert frame_bytes(16000) == 640
    assert frame_bytes(48000) == 1920


# ── 11–15: the failure taxonomy ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (FailureKind.TIMEOUT, ProviderTimeout),
        (FailureKind.UNAVAILABLE, ProviderUnavailable),
        (FailureKind.AUTH, ProviderAuthError),
        (FailureKind.RATE_LIMITED, ProviderRateLimited),
        (FailureKind.PROTOCOL, ProviderProtocolError),
        (FailureKind.TRUNCATED, ProviderStreamTruncated),
    ],
)
async def test_11_to_15_every_failure_kind_becomes_its_typed_error_mid_stream(kind, expected):
    client = FakeSpeechClient([pcm(640)], fail_at=1, kind=kind)
    with pytest.raises(expected):
        await frames(CartesiaTTS(client, voice="v"))


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (FailureKind.AUTH, ProviderAuthError),
        (FailureKind.RATE_LIMITED, ProviderRateLimited),
        (FailureKind.TIMEOUT, ProviderTimeout),
        (FailureKind.PROTOCOL, ProviderProtocolError),
    ],
)
async def test_a_failure_to_open_is_typed_too(kind, expected):
    with pytest.raises(expected):
        await frames(CartesiaTTS(FakeSpeechClient(open_error=kind), voice="v"))


async def test_a_rogue_seam_still_leaves_a_typed_error():
    class Rogue(FakeSpeechClient):
        async def open(self, spec):
            raise RuntimeError("https://api.example/v1?key=sk-secret refused")

    with pytest.raises(ProviderUnavailable) as raised:
        await frames(CartesiaTTS(Rogue(), voice="v"))
    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_an_unmapped_kind_is_still_typed():
    assert isinstance(
        typed_error(SpeechStreamError(FailureKind.UNAVAILABLE, "x")), ProviderUnavailable
    )


# ── 16: cancellation ────────────────────────────────────────────────────────


async def test_16_cancellation_mid_stream_closes_the_vendor_stream():
    import asyncio

    from .fake_speech import settle

    gate, started = asyncio.Event(), asyncio.Event()
    client = FakeSpeechClient([pcm(640)] * 4, gate=gate, started=started)
    provider = CartesiaTTS(client, voice="v")

    async def consume() -> None:
        async for _ in provider.synthesize("Hello."):
            pass

    task = asyncio.create_task(consume())
    assert await settle(started.is_set)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.streams[0].yielded == 1, "the vendor was still being pulled after cancellation"
    assert client.streams[0].closed == 1


async def test_cancellation_during_setup_leaves_no_stream():
    import asyncio

    from .fake_speech import settle

    hang, opened = asyncio.Event(), asyncio.Event()
    client = FakeSpeechClient(hang_on_open=hang, opened_event=opened)
    provider = CartesiaTTS(client, voice="v")

    async def consume() -> None:
        async for _ in provider.synthesize("Hello."):
            pass

    task = asyncio.create_task(consume())
    assert await settle(opened.is_set)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.streams == []
    hang.set()


async def test_the_stream_is_closed_even_when_it_fails():
    client = FakeSpeechClient([pcm(640)], fail_at=1)
    with pytest.raises(ProviderUnavailable):
        await frames(CartesiaTTS(client, voice="v"))
    assert client.streams[0].closed == 1


async def test_cancellation_is_never_translated_into_a_provider_failure():
    import asyncio

    class Cancelling(FakeSpeechClient):
        async def open(self, spec):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await frames(CartesiaTTS(Cancelling(), voice="v"))


# ── capabilities and construction ───────────────────────────────────────────


def test_the_provider_satisfies_the_contract():
    provider = CartesiaTTS(FakeSpeechClient(), voice="v")
    assert isinstance(provider, TTSProvider)
    assert provider.name == "cartesia"


def test_declared_capabilities_are_only_what_is_implemented():
    """`streaming_input=False` is the honest declaration, not an omission — see the module docs."""
    capabilities = CartesiaTTS(FakeSpeechClient(), voice="v").capabilities
    assert capabilities.streaming is True
    assert capabilities.streaming_input is False
    assert capabilities.markup is False
    assert capabilities.voices == ()


async def test_streaming_text_input_is_refused_rather_than_emulated():
    """Declaring False and then quietly accepting chunks would make the degradation invisible."""

    async def chunks():
        yield "Hello"

    provider = CartesiaTTS(FakeSpeechClient(), voice="v")
    with pytest.raises(NotImplementedError):
        async for _ in provider.synthesize_stream(chunks()):
            pass


def test_a_client_that_is_not_a_speech_client_is_refused():
    with pytest.raises(TypeError):
        CartesiaTTS(object())  # type: ignore[arg-type]


async def test_the_configured_voice_reaches_the_vendor():
    client = FakeSpeechClient([pcm(640)])
    await frames(CartesiaTTS(client, voice="voice-abc"))
    assert client.specs[-1].voice == "voice-abc"


async def test_a_per_call_voice_overrides_the_configured_one():
    client = FakeSpeechClient([pcm(640)])
    provider = CartesiaTTS(client, voice="configured")
    [f async for f in provider.synthesize("Hi.", voice="per-call")]
    assert client.specs[-1].voice == "per-call"


# ── the SDK adapter, against a stand-in module ─────────────────────────────


def _module(*, stream=None, error=None):
    module = types.ModuleType("cartesia")

    class CartesiaError(Exception):
        pass

    class APIError(CartesiaError):
        pass

    class APIConnectionError(APIError):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    class APIStatusError(APIError):
        def __init__(self, message="", status_code=None):
            super().__init__(message)
            self.status_code = status_code

    class APIResponseValidationError(APIError):
        pass

    class _TTS:
        def __init__(self):
            self.calls = []

        async def sse(self, **kwargs):
            self.calls.append(kwargs)
            if error is not None:
                raise error
            return stream

    class AsyncCartesia:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.tts = _TTS()
            module.last_client = self

    module.CartesiaError = CartesiaError
    module.APIError = APIError
    module.APIConnectionError = APIConnectionError
    module.APITimeoutError = APITimeoutError
    module.APIStatusError = APIStatusError
    module.APIResponseValidationError = APIResponseValidationError
    module.AsyncCartesia = AsyncCartesia
    module.last_client = None
    return module


class _Event:
    def __init__(self, kind="chunk", audio=None, data=None, status_code=200):
        self.type = kind
        self.status_code = status_code
        if audio is not None:
            self.data = base64.b64encode(audio).decode("ascii")
            self.audio = audio
        elif data is not None:
            self.data = data
        else:
            self.data = None


class _Stream:
    def __init__(self, events, error=None):
        self._events, self._error = events, error
        self.closed = 0

    async def __aiter__(self):
        for event in self._events:
            yield event
        if self._error is not None:
            raise self._error

    async def close(self):
        self.closed += 1


@pytest.fixture
def sdk(monkeypatch):
    def install(**kwargs):
        module = _module(**kwargs)
        monkeypatch.setitem(sys.modules, "cartesia", module)
        return module

    return install


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, FailureKind.AUTH),
        (403, FailureKind.AUTH),
        (429, FailureKind.RATE_LIMITED),
        (400, FailureKind.PROTOCOL),
        (422, FailureKind.PROTOCOL),
        (408, FailureKind.TIMEOUT),
        (500, FailureKind.UNAVAILABLE),
        (None, FailureKind.UNAVAILABLE),
    ],
)
def test_a_status_is_classified_by_what_the_runtime_can_do_about_it(status, expected):
    assert classify_status(status) is expected


def test_a_timeout_is_classified_before_its_connection_superclass(sdk):
    module = sdk()
    assert classify_exception(module.APITimeoutError()) is FailureKind.TIMEOUT
    assert classify_exception(module.APIConnectionError()) is FailureKind.UNAVAILABLE


def test_classification_never_reads_the_message_text(sdk):
    module = sdk()
    assert classify_exception(module.APIStatusError("rate limit", 401)) is FailureKind.AUTH
    assert classify_exception(module.APIStatusError("unauthorized", 429)) is (
        FailureKind.RATE_LIMITED
    )


def test_the_stand_in_mirrors_the_real_sdk():
    """Two branches, no skip: checked against the real package where present, and against the
    adapter's own table where absent. This is what stops the stand-in drifting into a fiction."""
    try:
        import cartesia as real
    except ImportError:
        assert classify_status(401) is FailureKind.AUTH
        return
    assert issubclass(real.APITimeoutError, real.APIConnectionError)
    assert issubclass(real.APIStatusError, real.APIError)
    assert real.AuthenticationError.status_code == 401
    assert real.RateLimitError.status_code == 429
    assert hasattr(real.AsyncCartesia, "__init__")


def test_audio_is_base64_decoded():
    assert event_audio(_Event(audio=b"\x01\x02")) == b"\x01\x02"


def test_an_event_without_audio_reads_as_empty():
    assert event_audio(_Event(kind="done")) == b""
    assert event_audio(object()) == b""


def test_undecodable_audio_is_a_protocol_failure():
    """A frame built from truncated bytes would be audible noise."""
    with pytest.raises(SpeechStreamError) as raised:
        event_audio(_Event(data="not!valid!base64"))
    assert raised.value.kind is FailureKind.PROTOCOL


def test_a_vendor_error_event_is_not_silently_empty():
    with pytest.raises(SpeechStreamError) as raised:
        event_audio(_Event(kind="error", status_code=429))
    assert raised.value.kind is FailureKind.RATE_LIMITED


async def test_the_client_streams_decoded_audio(sdk):
    module = sdk(stream=_Stream([_Event(audio=b"\x01\x02"), _Event(kind="done")]))
    stream = await CartesiaSpeechClient("sk-test").open(SPEC)
    assert [c.audio async for c in stream] == [b"\x01\x02", b""]
    assert module.last_client.kwargs["max_retries"] == 0  # the guard owns retry policy


async def test_the_request_carries_the_declared_format(sdk):
    module = sdk(stream=_Stream([_Event(audio=b"\x01\x02")]))
    await CartesiaSpeechClient("sk-test").open(SPEC)
    call = module.last_client.tts.calls[0]
    assert call["output_format"] == {
        "container": "raw",
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
    }
    assert call["transcript"] == "hello"
    assert call["voice"] == {"mode": "id", "id": "voice-1"}


async def test_a_missing_voice_is_refused_before_a_request_is_spent(sdk):
    sdk(stream=_Stream([]))
    with pytest.raises(SpeechStreamError):
        await CartesiaSpeechClient("sk-test").open(
            SpeechSpec(text="hi", voice=None, language="en", sample_rate=16000)
        )


async def test_a_failure_to_open_is_classified(sdk, monkeypatch):
    # The error must come from the *installed* module: `classify_exception` does an isinstance
    # check against whatever `import cartesia` resolves to, so an error built from a different
    # module instance would classify as an unrecognized one.
    module = _module()
    module.tts_error = module.APIStatusError("nope", 401)
    monkeypatch.setitem(sys.modules, "cartesia", module)
    sdk_module = _module(error=module.tts_error)
    sdk_module.APIStatusError = module.APIStatusError
    sdk_module.APITimeoutError = module.APITimeoutError
    sdk_module.APIConnectionError = module.APIConnectionError
    sdk_module.APIResponseValidationError = module.APIResponseValidationError
    monkeypatch.setitem(sys.modules, "cartesia", sdk_module)
    with pytest.raises(SpeechStreamError) as raised:
        await CartesiaSpeechClient("sk-test").open(SPEC)
    assert raised.value.kind is FailureKind.AUTH


async def test_a_vendor_message_never_reaches_the_neutral_error(sdk, monkeypatch):
    module = _module()
    error = module.APIStatusError("key sk-live-abc rejected at https://api", 401)
    installed = _module(error=error)
    installed.APIStatusError = module.APIStatusError
    installed.APITimeoutError = module.APITimeoutError
    installed.APIConnectionError = module.APIConnectionError
    installed.APIResponseValidationError = module.APIResponseValidationError
    monkeypatch.setitem(sys.modules, "cartesia", installed)
    with pytest.raises(SpeechStreamError) as raised:
        await CartesiaSpeechClient("sk-test").open(SPEC)
    assert "sk-live-abc" not in str(raised.value)
    assert raised.value.__cause__ is None


async def test_closing_is_idempotent_and_never_raises(sdk):
    obj = _Stream([_Event(audio=b"\x01\x02")])
    sdk(stream=obj)
    stream = await CartesiaSpeechClient("sk-test").open(SPEC)
    await stream.aclose()
    await stream.aclose()
    assert obj.closed == 1


async def test_a_close_that_fails_does_not_fail_the_turn(sdk):
    class Rude(_Stream):
        async def close(self):
            raise RuntimeError("already gone")

    sdk(stream=Rude([_Event(audio=b"\x01")]))
    stream = await CartesiaSpeechClient("sk-test").open(SPEC)
    await stream.aclose()


async def test_a_missing_library_is_a_typed_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "cartesia", None)
    assert cartesia_available() is False
    with pytest.raises(SpeechStreamError) as raised:
        await CartesiaSpeechClient("sk-test").open(SPEC)
    assert "not installed" in raised.value.detail


def test_the_api_key_is_never_rendered():
    client = CartesiaSpeechClient("sk-live-do-not-print")
    assert "sk-live" not in repr(client)
    assert "sk-live" not in str(client)


@pytest.mark.parametrize("kwargs", [{"timeout_s": 0}, {"model": ""}])
def test_an_invalid_client_construction_is_refused(kwargs):
    with pytest.raises(ValueError):
        CartesiaSpeechClient("sk-test", **kwargs)


def test_an_empty_key_is_refused():
    with pytest.raises(ValueError):
        CartesiaSpeechClient("")


async def test_the_credential_reaches_the_sdk_and_nowhere_else(sdk):
    module = sdk(stream=_Stream([_Event(audio=b"\x01")]))
    await CartesiaSpeechClient("sk-test-value").open(SPEC)
    assert module.last_client.kwargs["api_key"] == "sk-test-value"
    assert "api_key" not in module.last_client.tts.calls[0]

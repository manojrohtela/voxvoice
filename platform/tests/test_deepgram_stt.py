"""Real streaming STT — `DeepgramSTT` over the transcription-session seam (M11.6).

Hermetic and deterministic: the vendor socket is `tests.fake_transcription.FakeTranscriptionClient`,
so incremental streaming, every failure class, cancellation and cleanup are provable with no
network, no credential and no sleeps (TESTING_POLICY rules 1–2). Ordering is proven by awaiting the
things that actually happen — a frame being pulled, a result being yielded — never by a timer.

The property this file exists to protect is **streaming truthfulness**: a Partial for the first
frame arrives *while the caller is still talking*; Partial stays Partial and Final stays Final; and
nothing the vendor did not say is ever emitted.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib

import pytest

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
from provider.circuit_breaker import BreakerState, CircuitBreakerPolicy
from providers.stt import deepgram as deepgram_module
from providers.stt.deepgram import (
    DEFAULT_LANGUAGES,
    DEFAULT_MODEL,
    SUPPORTED_SAMPLE_RATES,
    DeepgramSTT,
    mapped_result,
)
from providers.stt.transcription_session import (
    FailureKind,
    TranscriptionClient,
    TranscriptionResult,
    TranscriptionSession,
    TranscriptionSessionError,
)
from runtime.faults.degradation import DegradationKind, DegradationRecorder
from runtime.faults.guard import GuardPolicy, ProviderGuard
from runtime.registry import CapabilityKind, ProviderRegistry
from tests.fake_transcription import (
    FakeTranscriptionClient,
    ScriptedSession,
    pcm_frame,
    silent_responder,
)

UPLOAD_TASK = "deepgram-stt-upload"


async def _frames(*payloads: bytes, sample_rate: int = 16000):
    for i, payload in enumerate(payloads):
        yield pcm_frame(payload, sample_rate=sample_rate, seq=i)


async def _collect(stream) -> list[Partial | Final]:
    return [item async for item in stream]


async def _run_until_quiet(turns: int = 200) -> None:
    """Give every ready task a fixed number of event-loop turns — no clock, no duration.

    Everything under test is in-memory, so any work that *can* happen without new input happens
    well within this budget; work still pending afterwards is genuinely blocked on input."""
    for _ in range(turns):
        await asyncio.sleep(0)


def _upload_tasks() -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name() == UPLOAD_TASK and not t.done()]


class GatedSource:
    """Caller audio under test control: frame N is released only when the test says so.

    This is what makes "a result arrived *before* the stream ended" a fact rather than a hope — the
    stream **cannot** have ended, because the test has not yet let it."""

    def __init__(self, *payloads: bytes) -> None:
        self.payloads = payloads
        self.pulled = 0
        self.released = 0
        self.ended = False
        self._gate = asyncio.Condition()

    async def release(self, count: int = 1) -> None:
        async with self._gate:
            self.released += count
            self._gate.notify_all()

    async def __aiter__(self):
        for index, payload in enumerate(self.payloads):
            async with self._gate:
                await self._gate.wait_for(lambda i=index: self.released > i)
            self.pulled += 1
            yield pcm_frame(payload, seq=index)
        self.ended = True


# ── A. contract & capability truthfulness ───────────────────────────────────


def test_a_deepgram_stt_implements_the_unchanged_contract():
    provider = DeepgramSTT(FakeTranscriptionClient())
    assert isinstance(provider, STTProvider)
    assert provider.name == "deepgram"
    signature = inspect.signature(DeepgramSTT.transcribe)
    assert list(signature.parameters) == ["self", "frames", "language", "sample_rate"]


def test_b_capabilities_are_declared_truthfully():
    capabilities = DeepgramSTT(FakeTranscriptionClient()).capabilities
    assert capabilities == STTCapabilities(
        streaming=True, partials=True, word_timestamps=True, languages=DEFAULT_LANGUAGES
    )


def test_c_hinglish_is_not_claimed_by_default():
    """DL-0180 — no empirical Hinglish evidence exists, so nothing declares it."""
    languages = DeepgramSTT(FakeTranscriptionClient()).capabilities.languages
    assert languages == ("en",)
    assert "hi" not in languages and "multi" not in languages


def test_d_it_registers_under_stt_in_the_one_registry():
    registry = ProviderRegistry()
    metadata = registry.register(
        CapabilityKind.STT, DeepgramSTT(FakeTranscriptionClient()), default=True
    )
    assert metadata.name == "deepgram"
    assert isinstance(registry.resolve(CapabilityKind.STT), DeepgramSTT)


def test_e_constructor_refuses_what_it_cannot_honour():
    with pytest.raises(TypeError):
        DeepgramSTT(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DeepgramSTT(FakeTranscriptionClient(), model="")
    with pytest.raises(ValueError):
        DeepgramSTT(FakeTranscriptionClient(), languages=())


def test_the_fakes_satisfy_the_seam_protocols():
    client = FakeTranscriptionClient()
    assert isinstance(client, TranscriptionClient)
    from providers.stt.transcription_session import StreamSpec

    spec = StreamSpec(language="en", sample_rate=16000, channels=1, model="m")
    assert isinstance(ScriptedSession(spec), TranscriptionSession)


@pytest.mark.asyncio
async def test_f_the_stream_is_opened_with_the_declared_format():
    client = FakeTranscriptionClient()
    await _collect(
        DeepgramSTT(client).transcribe(_frames(b"hi"), language="en", sample_rate=16000)
    )
    (spec,) = client.specs
    assert (spec.language, spec.sample_rate, spec.channels, spec.model) == (
        "en",
        16000,
        1,
        DEFAULT_MODEL,
    )
    assert spec.interim_results is True


# ── G–L. genuine incremental duplex ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_g_a_partial_for_frame_one_is_emitted_before_the_stream_ends():
    """**The streaming proof.** Frame 2 is held back by the test. The Partial for frame 1 must
    arrive anyway — so it cannot have come from a provider that waited for the end of audio."""
    source = GatedSource(b"hello", b"world")
    stream = DeepgramSTT(FakeTranscriptionClient()).transcribe(
        source.__aiter__(), language="en", sample_rate=16000
    )
    await source.release(1)
    pending = asyncio.ensure_future(anext(stream))
    await _run_until_quiet()
    if not pending.done():
        # Fail, don't hang: a batch-emulating provider would wait for frame 2 forever.
        pending.cancel()
        await asyncio.wait({pending})
        await stream.aclose()
        pytest.fail("no result while the caller was still talking — batch emulation")
    first = pending.result()

    assert first == Partial(text="hello", stability=0.0)
    assert source.pulled == 1, "the provider consumed audio the caller had not produced yet"
    assert source.ended is False, "the result was produced only after the stream ended"

    second = await anext(stream)
    assert second == Final(text="hello", words=(("hello", 0.0, 0.5),))
    assert source.ended is False

    await source.release(1)
    rest = await _collect(stream)
    assert rest == [
        Partial(text="world", stability=0.0),
        Final(text="world", words=(("world", 0.0, 0.5),)),
    ]
    assert source.ended is True


@pytest.mark.asyncio
async def test_h_upload_proceeds_while_the_consumer_is_not_reading():
    """Duplex, not ping-pong: audio keeps flowing to the vendor while nobody pulls a result."""
    client = FakeTranscriptionClient(responder=silent_responder)
    source = GatedSource(b"a1", b"b2", b"c3")
    stream = DeepgramSTT(client).transcribe(source.__aiter__(), language="en", sample_rate=16000)
    pending = asyncio.ensure_future(anext(stream))  # opens the stream; yields nothing (silence)
    await source.release(3)
    while not client.sessions or len(client.sessions[0].sent) < 3:
        await asyncio.sleep(0)
    assert client.sessions[0].sent == [b"a1", b"b2", b"c3"]
    assert not pending.done(), "a result was fabricated for silence"
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await stream.aclose()


@pytest.mark.asyncio
async def test_i_results_arrive_while_upload_is_blocked():
    """The receive side never waits on the upload side: a vendor result pushed while the caller is
    silent is yielded immediately."""
    client = FakeTranscriptionClient(responder=silent_responder)
    source = GatedSource(b"x")
    stream = DeepgramSTT(client).transcribe(source.__aiter__(), language="en", sample_rate=16000)
    task = asyncio.ensure_future(anext(stream))
    while not client.sessions:
        await asyncio.sleep(0)
    client.sessions[0].push(TranscriptionResult(text="late words", is_final=False))
    assert await task == Partial(text="late words", stability=0.0)
    assert source.pulled == 0
    await stream.aclose()


@pytest.mark.asyncio
async def test_j_partial_stays_partial_and_final_stays_final_in_vendor_order():
    def responder(audio):
        return [
            TranscriptionResult(text="hel", is_final=False),
            TranscriptionResult(text="hello", is_final=False),
            TranscriptionResult(text="hello there", is_final=True),
        ]

    results = await _collect(
        DeepgramSTT(FakeTranscriptionClient(responder=responder)).transcribe(
            _frames(b"x"), language="en", sample_rate=16000
        )
    )
    assert results == [
        Partial(text="hel", stability=0.0),
        Partial(text="hello", stability=0.0),
        Final(text="hello there"),
    ]


@pytest.mark.asyncio
async def test_k_no_final_is_fabricated_when_the_vendor_only_sent_interims():
    """End of audio is not end of utterance. A stream that ends on an interim ends on an interim."""
    def responder(audio):
        return [TranscriptionResult(text="unfinished", is_final=False)]

    results = await _collect(
        DeepgramSTT(FakeTranscriptionClient(responder=responder)).transcribe(
            _frames(b"x"), language="en", sample_rate=16000
        )
    )
    assert results == [Partial(text="unfinished", stability=0.0)]
    assert not any(isinstance(r, Final) for r in results)


@pytest.mark.asyncio
async def test_l_results_flushed_after_end_of_audio_are_delivered():
    def configure(session: ScriptedSession) -> None:
        session.flush = [TranscriptionResult(text="tail", is_final=True)]

    client = FakeTranscriptionClient(responder=silent_responder, configure=configure)
    results = await _collect(
        DeepgramSTT(client).transcribe(_frames(b"x"), language="en", sample_rate=16000)
    )
    assert results == [Final(text="tail")]
    assert client.sessions[0].finished is True


# ── M–N. mapping ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_m_empty_vendor_results_are_not_speech(text):
    assert mapped_result(TranscriptionResult(text=text, is_final=False)) is None
    assert mapped_result(TranscriptionResult(text=text, is_final=True)) is None


def test_n_words_are_carried_on_finals_only():
    words = (("hi", 0.1, 0.3),)
    assert mapped_result(TranscriptionResult(text="hi", is_final=True, words=words)) == Final(
        text="hi", words=words
    )
    assert mapped_result(TranscriptionResult(text="hi", is_final=False, words=words)) == Partial(
        text="hi", stability=0.0
    )


@pytest.mark.asyncio
async def test_empty_audio_frames_are_never_sent():
    """An empty binary message is Deepgram's legacy close-stream signal."""
    client = FakeTranscriptionClient(responder=silent_responder)

    async def frames():
        yield AudioFrame(data=b"", encoding=AudioEncoding.PCM16)
        yield pcm_frame(b"ok")

    await _collect(DeepgramSTT(client).transcribe(frames(), language="en", sample_rate=16000))
    assert client.sessions[0].sent == [b"ok"]


@pytest.mark.asyncio
async def test_an_empty_audio_stream_produces_nothing_and_finishes_cleanly():
    client = FakeTranscriptionClient()
    assert (
        await _collect(DeepgramSTT(client).transcribe(_frames(), language="en", sample_rate=16000))
        == []
    )
    assert client.sessions[0].finished and client.sessions[0].closed


# ── O–Q. audio format validation ────────────────────────────────────────────


@pytest.mark.parametrize(
    "frame, error",
    [
        (AudioFrame(data=b"ab", encoding=AudioEncoding.OPAQUE), CapabilityUnsupported),
        (AudioFrame(data=b"ab", encoding=AudioEncoding.PCM16, channels=2), CapabilityUnsupported),
        (
            AudioFrame(data=b"ab", encoding=AudioEncoding.PCM16, sample_rate=8000),
            CapabilityUnsupported,
        ),
        (AudioFrame(data=b"abc", encoding=AudioEncoding.PCM16), ProviderProtocolError),
    ],
    ids=["opaque-encoding", "stereo", "rate-mismatch", "odd-byte-count"],
)
@pytest.mark.asyncio
async def test_o_frames_in_the_wrong_format_are_refused_and_nothing_is_sent(frame, error):
    client = FakeTranscriptionClient()

    async def frames():
        yield frame

    with pytest.raises(error):
        await _collect(DeepgramSTT(client).transcribe(frames(), language="en", sample_rate=16000))
    assert client.sessions[0].sent == []
    assert client.sessions[0].closed
    assert _upload_tasks() == []


@pytest.mark.asyncio
async def test_p_an_undeclared_language_is_refused_before_connecting():
    client = FakeTranscriptionClient()
    with pytest.raises(CapabilityUnsupported, match="language"):
        await _collect(
            DeepgramSTT(client).transcribe(_frames(b"x"), language="hi", sample_rate=16000)
        )
    assert client.specs == [], "a stream was opened for a language nobody declared"


@pytest.mark.asyncio
async def test_q_an_unsupported_sample_rate_is_refused_before_connecting():
    client = FakeTranscriptionClient()
    assert 11025 not in SUPPORTED_SAMPLE_RATES
    with pytest.raises(CapabilityUnsupported, match="sample rate"):
        await _collect(
            DeepgramSTT(client).transcribe(_frames(b"x"), language="en", sample_rate=11025)
        )
    assert client.specs == []


@pytest.mark.asyncio
async def test_a_declared_extra_language_is_honoured():
    client = FakeTranscriptionClient()
    provider = DeepgramSTT(client, languages=("en", "hi"))
    await _collect(provider.transcribe(_frames(b"x"), language="hi", sample_rate=16000))
    assert client.specs[0].language == "hi"


# ── R–V. typed failure mapping ──────────────────────────────────────────────

_KINDS = [
    (FailureKind.TIMEOUT, ProviderTimeout),
    (FailureKind.UNAVAILABLE, ProviderUnavailable),
    (FailureKind.AUTH, ProviderAuthError),
    (FailureKind.RATE_LIMITED, ProviderRateLimited),
    (FailureKind.PROTOCOL, ProviderProtocolError),
    (FailureKind.TRUNCATED, ProviderStreamTruncated),
]


@pytest.mark.parametrize("kind, error", _KINDS, ids=[k.value for k, _ in _KINDS])
@pytest.mark.asyncio
async def test_r_open_failures_are_typed(kind, error):
    client = FakeTranscriptionClient(open_error=TranscriptionSessionError(kind, "refused"))
    with pytest.raises(error) as raised:
        await _collect(
            DeepgramSTT(client).transcribe(_frames(b"x"), language="en", sample_rate=16000)
        )
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


@pytest.mark.parametrize("kind, error", _KINDS, ids=[k.value for k, _ in _KINDS])
@pytest.mark.asyncio
async def test_s_mid_stream_failures_are_typed_after_partials_already_delivered(kind, error):
    source = GatedSource(b"hello", b"never")
    client = FakeTranscriptionClient()
    stream = DeepgramSTT(client).transcribe(source.__aiter__(), language="en", sample_rate=16000)
    await source.release(1)
    assert isinstance(await anext(stream), Partial)
    client.sessions[0].fail(TranscriptionSessionError(kind, "broke"))
    with pytest.raises(error):
        await _collect(stream)
    assert client.sessions[0].closed
    assert _upload_tasks() == []


@pytest.mark.asyncio
async def test_t_a_send_failure_is_typed_and_unblocks_the_receive_side():
    """The receive side is parked waiting for results that will never come; the upload failure
    must still surface — closing the session is what guarantees it."""
    def configure(session: ScriptedSession) -> None:
        session.send_error = TranscriptionSessionError(FailureKind.UNAVAILABLE, "send broke")

    client = FakeTranscriptionClient(configure=configure)
    with pytest.raises(ProviderUnavailable, match="send broke"):
        await _collect(
            DeepgramSTT(client).transcribe(_frames(b"x"), language="en", sample_rate=16000)
        )
    assert client.sessions[0].closed
    assert _upload_tasks() == []


@pytest.mark.asyncio
async def test_u_the_vendor_ending_before_the_audio_is_truncation_not_hangup():
    """A clean close the caller did not ask for is **not** exhaustion: the pipeline reads STT
    exhaustion as *caller hung up*, which would be the wrong real-world fact (ADR-0045 SD4)."""
    source = GatedSource(b"hello", b"more")
    client = FakeTranscriptionClient()
    stream = DeepgramSTT(client).transcribe(source.__aiter__(), language="en", sample_rate=16000)
    await source.release(1)
    await anext(stream)
    client.sessions[0].end()
    with pytest.raises(ProviderStreamTruncated, match="before the audio ended"):
        await _collect(stream)
    assert _upload_tasks() == []


@pytest.mark.asyncio
async def test_v_a_failing_audio_source_keeps_its_own_type():
    """A transport failure reaching STT through its frames stays a transport failure."""
    async def frames():
        yield pcm_frame(b"hi")
        raise ProviderUnavailable("transport/webrtc: media path failed")

    client = FakeTranscriptionClient(responder=silent_responder)
    with pytest.raises(ProviderUnavailable, match="transport/webrtc"):
        await _collect(DeepgramSTT(client).transcribe(frames(), language="en", sample_rate=16000))


@pytest.mark.asyncio
async def test_an_untyped_session_bug_still_leaves_the_plugin_typed():
    class Broken(Exception):
        pass

    client = FakeTranscriptionClient(open_error=Broken("secret-looking internals"))
    with pytest.raises(ProviderUnavailable) as raised:
        await _collect(
            DeepgramSTT(client).transcribe(_frames(b"x"), language="en", sample_rate=16000)
        )
    assert "secret-looking" not in str(raised.value)


@pytest.mark.asyncio
async def test_a_finish_failure_is_typed():
    def configure(session: ScriptedSession) -> None:
        session.finish_error = TranscriptionSessionError(FailureKind.PROTOCOL, "bad close")

    client = FakeTranscriptionClient(responder=silent_responder, configure=configure)
    with pytest.raises(ProviderProtocolError):
        await _collect(
            DeepgramSTT(client).transcribe(_frames(b"x"), language="en", sample_rate=16000)
        )


# ── W–X. cancellation and deterministic cleanup ─────────────────────────────


@pytest.mark.asyncio
async def test_w_cancellation_propagates_untranslated_and_cleans_up():
    source = GatedSource(b"hello", b"held")
    client = FakeTranscriptionClient()
    stream = DeepgramSTT(client).transcribe(source.__aiter__(), language="en", sample_rate=16000)

    async def consume():
        return [item async for item in stream]

    await source.release(1)
    consumer = asyncio.ensure_future(consume())
    await client_ready(client)
    while len(client.sessions[0].sent) < 1:
        await asyncio.sleep(0)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert client.sessions[0].closed
    assert _upload_tasks() == []


async def client_ready(client: FakeTranscriptionClient) -> None:
    while not client.sessions:
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_x_closing_the_stream_early_stops_the_upload_task_and_closes_the_session():
    source = GatedSource(b"hello", b"held forever")
    client = FakeTranscriptionClient()
    stream = DeepgramSTT(client).transcribe(source.__aiter__(), language="en", sample_rate=16000)
    await source.release(1)
    await anext(stream)
    assert len(_upload_tasks()) == 1
    await stream.aclose()
    assert _upload_tasks() == []
    assert client.sessions[0].closed
    assert source.pulled == 1


@pytest.mark.asyncio
async def test_repeated_shutdown_is_safe():
    """W — closing an already-closed stream, and a session closed by both halves, is harmless."""
    source = GatedSource(b"hello", b"held")
    client = FakeTranscriptionClient()
    stream = DeepgramSTT(client).transcribe(source.__aiter__(), language="en", sample_rate=16000)
    await source.release(1)
    await anext(stream)
    await stream.aclose()
    await stream.aclose()
    session = client.sessions[0]
    await session.close()
    assert session.closed and _upload_tasks() == []


@pytest.mark.asyncio
async def test_u_at_most_one_frame_is_in_flight_when_the_vendor_is_slow():
    """U — bounded by construction. Five frames are available, but while the vendor has not
    accepted the first, the provider pulls **no** more: backpressure reaches the caller's source
    instead of accumulating inside the provider, and nothing is dropped."""
    accepted = asyncio.Event()

    def configure(session: ScriptedSession) -> None:
        original = session.send

        async def slow_send(audio: bytes) -> None:
            await accepted.wait()
            await original(audio)

        session.send = slow_send  # type: ignore[method-assign]

    client = FakeTranscriptionClient(responder=silent_responder, configure=configure)
    source = GatedSource(b"f0", b"f1", b"f2", b"f3", b"f4")
    await source.release(5)
    stream = DeepgramSTT(client).transcribe(source.__aiter__(), language="en", sample_rate=16000)
    reader = asyncio.ensure_future(_collect(stream))
    await _run_until_quiet()

    assert source.pulled == 1, f"{source.pulled} frames pulled while the vendor accepted none"
    assert client.sessions[0].sent == []

    accepted.set()
    assert await reader == []
    assert client.sessions[0].sent == [b"f0", b"f1", b"f2", b"f3", b"f4"], "audio was lost"


def test_u_the_provider_creates_no_queue_of_its_own():
    """U, structurally — there is no STT-side buffer to bound, because there is no buffer."""
    tree = ast.parse(pathlib.Path(inspect.getfile(deepgram_module)).read_text(encoding="utf-8"))
    constructed = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert not constructed & {"Queue", "LifoQueue", "PriorityQueue", "deque"}, constructed


@pytest.mark.asyncio
async def test_no_task_survives_a_completed_stream():
    client = FakeTranscriptionClient()
    await _collect(
        DeepgramSTT(client).transcribe(_frames(b"a", b"b"), language="en", sample_rate=16000)
    )
    assert _upload_tasks() == []
    assert client.sessions[0].close_calls >= 1


# ── Y–Z. ProviderGuard reuse and AR-24 degradation ──────────────────────────


def _one_strike_guard(recorder: DegradationRecorder) -> ProviderGuard:
    """A breaker that opens on the first counted failure, so "was it counted?" is observable."""
    return ProviderGuard(
        capability="stt",
        policy=GuardPolicy(breaker=CircuitBreakerPolicy(failure_threshold=1)),
        degradations=recorder,
    )


@pytest.mark.asyncio
async def test_y_a_typed_vendor_failure_through_the_guard_degrades_visibly_and_counts():
    recorder = DegradationRecorder("ses_stt")
    guard = _one_strike_guard(recorder)
    client = FakeTranscriptionClient(
        open_error=TranscriptionSessionError(FailureKind.AUTH, "rejected")
    )
    provider = DeepgramSTT(client)
    with pytest.raises(ProviderAuthError):
        await _collect(
            guard.stream(
                provider.name,
                provider.transcribe(_frames(b"x"), language="en", sample_rate=16000),
                what="transcribe",
            )
        )
    assert [d.kind for d in recorder.history] == [DegradationKind.UNAVAILABLE]
    assert recorder.history[0].provider == "deepgram"
    assert guard.breaker_state("deepgram") is BreakerState.OPEN


@pytest.mark.asyncio
async def test_z_cancellation_through_the_guard_is_not_a_failure():
    recorder = DegradationRecorder("ses_stt")
    guard = _one_strike_guard(recorder)
    source = GatedSource(b"held")
    client = FakeTranscriptionClient()
    provider = DeepgramSTT(client)

    async def consume():
        return await _collect(
            guard.stream(
                provider.name,
                provider.transcribe(source.__aiter__(), language="en", sample_rate=16000),
            )
        )

    consumer = asyncio.ensure_future(consume())
    await client_ready(client)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert recorder.history == ()
    assert guard.breaker_state("deepgram") is BreakerState.CLOSED
    assert _upload_tasks() == []


@pytest.mark.asyncio
async def test_a_healthy_stream_through_the_guard_records_nothing():
    recorder = DegradationRecorder("ses_stt")
    guard = ProviderGuard(capability="stt", degradations=recorder)
    provider = DeepgramSTT(FakeTranscriptionClient())
    results = await _collect(
        guard.stream(
            provider.name, provider.transcribe(_frames(b"hi"), language="en", sample_rate=16000)
        )
    )
    assert [type(r) for r in results] == [Partial, Final]
    assert recorder.history == ()


# ── AA. architecture ────────────────────────────────────────────────────────

_STT_DIR = pathlib.Path(inspect.getfile(DeepgramSTT)).parent
_PLATFORM_ROOT = _STT_DIR.parent.parent


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
    return roots


def _production_files() -> list[pathlib.Path]:
    packages = ("core", "api", "runtime", "providers", "events", "observability", "billing")
    files = [
        p
        for package in packages
        for p in (_PLATFORM_ROOT / package).rglob("*.py")
        if "__pycache__" not in p.parts
    ]
    return [*files, _PLATFORM_ROOT / "bootstrap.py"]


def test_aa_the_deepgram_sdk_is_imported_only_by_the_client_module():
    """**AR-12, asserted from the AST** — prose naming the vendor is not an import."""
    offenders = [
        str(p.relative_to(_PLATFORM_ROOT))
        for p in _production_files()
        if "deepgram" in _imports(p) and p.name != "deepgram_client.py"
    ]
    assert offenders == [], f"the Deepgram SDK leaked outside its adapter: {offenders}"


def test_aa_the_client_module_does_import_the_sdk():
    """The other half — a boundary guard is only evidence if the thing it bounds exists."""
    assert "deepgram" in _imports(_STT_DIR / "deepgram_client.py")


def test_aa_the_provider_and_the_seam_are_library_free():
    for name in ("deepgram.py", "transcription_session.py"):
        roots = _imports(_STT_DIR / name)
        assert not roots & {"deepgram", "websockets", "httpx", "aiohttp"}, name


def test_aa_websockets_is_reached_only_through_the_adapter_in_the_provider_plane():
    for path in (_PLATFORM_ROOT / "providers").rglob("*.py"):
        if "__pycache__" in path.parts or path.name == "deepgram_client.py":
            continue
        assert "websockets" not in _imports(path), path.name


def test_aa_no_stt_plugin_reads_the_environment_or_resolves_secrets():
    """ADR-0049 CR4 — a plugin receives its credential; it never goes looking for one."""
    for path in _STT_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert "os" not in _imports(path), path.name
        assert "dotenv" not in _imports(path), path.name
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "environ" not in names and "getenv" not in names, path.name
        assert "CredentialProvider" not in {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
        }, path.name


def test_aa_no_vendor_type_crosses_core_or_runtime():
    """Z — neither the contracts nor the runtime can name a Deepgram or websocket type."""
    for package in ("core", "runtime"):
        for path in (_PLATFORM_ROOT / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            assert not _imports(path) & {"deepgram", "websockets"}, path.name
            source = path.read_text(encoding="utf-8")
            assert "TranscriptionSession" not in source, f"{path.name} reached into the STT seam"


def test_aa_the_provider_constructs_no_other_capability_and_no_registry():
    source = pathlib.Path(inspect.getfile(deepgram_module)).read_text(encoding="utf-8")
    forbidden_names = (
        "ProviderRegistry",
        "LocalSTT(",
        "ConversationOrchestrator",
        "RuntimePipeline",
    )
    for forbidden in forbidden_names:
        assert forbidden not in source, forbidden


def test_aa_the_orchestrator_constructs_no_stt_provider():
    from runtime.orchestrator import orchestrator as orch

    source = pathlib.Path(inspect.getfile(orch)).read_text(encoding="utf-8")
    assert "DeepgramSTT(" not in source and "LocalSTT(" not in source


def test_aa_the_provider_resamples_nothing():
    tree = ast.parse(pathlib.Path(inspect.getfile(deepgram_module)).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            assert "resampl" not in node.name.lower(), node.name


def test_typed_errors_are_all_provider_errors():
    for _, error in _KINDS:
        assert issubclass(error, ProviderError)

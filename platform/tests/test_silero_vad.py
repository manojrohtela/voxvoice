"""Real streaming VAD — `SileroVoiceActivity` over the speech-model seam (M11.7).

Hermetic and deterministic: the model is `tests.fake_speech_model`, so windowing, background
inference, the O(1) hot path, failures, cancellation and cleanup are provable without the native
library, its weights or any clock. Blocking is controlled with `threading.Event`, because inference
genuinely runs on a worker thread; nothing sleeps for a duration.

The property this file exists to protect is ADR-0045 **SD7/SD8**: `detect()` never runs the model,
never waits for it, and never does work proportional to buffered audio — and the model runs, on its
own, under a lifecycle the provider owns.

**VAD is not endpointing.** Nothing here asserts or creates turn behaviour.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
import textwrap
import threading
from array import array

import pytest

from core.audio import AudioEncoding, AudioFrame
from core.errors import (
    CapabilityUnsupported,
    ProviderError,
    ProviderProtocolError,
    ProviderTimeout,
    ProviderUnavailable,
)
from core.providers.voice_activity import (
    ReportsDroppedAudio,
    VoiceActivityCapabilities,
    VoiceActivityProvider,
    VoiceActivitySample,
)
from providers.voice_activity import silero as silero_module
from providers.voice_activity.silero import (
    INITIAL_SAMPLE,
    MAX_BUFFERED_BYTES,
    MAX_BUFFERED_MS,
    WINDOW_BYTES,
    WINDOW_MS,
    SileroVoiceActivity,
)
from providers.voice_activity.speech_model import SpeechModel, SpeechModelError
from runtime.registry import CapabilityKind, ProviderRegistry
from tests.conformance.probes import BlockingIOSentinel, CostProbe
from tests.fake_speech_model import (
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    BlockingModel,
    EnergyModel,
    FailingModel,
    WrongRateModel,
    pcm_window,
)

SESSION = "ses_vad"
INFERENCE_TASK_PREFIX = "vad-silero-inference"


def _pcm(data: bytes, *, sample_rate: int = SAMPLE_RATE, channels: int = 1) -> AudioFrame:
    return AudioFrame(
        data=data, sample_rate=sample_rate, encoding=AudioEncoding.PCM16, channels=channels
    )


def _inference_tasks() -> list[asyncio.Task]:
    return [
        t
        for t in asyncio.all_tasks()
        if t.get_name().startswith(INFERENCE_TASK_PREFIX) and not t.done()
    ]


async def _until(predicate, *, turns: int = 200_000) -> None:
    """Yield to the event loop until `predicate()` holds. No clock: a bounded number of cooperative
    turns, each of which lets the worker thread's completion callback run."""
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


async def _thread_event(event: threading.Event) -> None:
    """Await a worker-thread signal without blocking the event loop."""
    assert await asyncio.to_thread(event.wait, 30), "worker-thread signal never arrived"


def _factory(model: SpeechModel):
    return lambda: model


# ── A–B. construction and capabilities ──────────────────────────────────────


def test_a_it_implements_the_unchanged_contract():
    provider = SileroVoiceActivity(EnergyModel)
    assert isinstance(provider, VoiceActivityProvider)
    assert provider.name == "silero"
    assert inspect.iscoroutinefunction(SileroVoiceActivity.start)
    assert inspect.iscoroutinefunction(SileroVoiceActivity.stop)
    assert not inspect.iscoroutinefunction(SileroVoiceActivity.detect), "detect() must stay sync"
    assert isinstance(provider, ReportsDroppedAudio)


def test_a_construction_refuses_what_it_cannot_honour():
    with pytest.raises(TypeError):
        SileroVoiceActivity(object())  # type: ignore[arg-type]
    for threshold in (0.0, 1.0, -1, 2):
        with pytest.raises(ValueError):
            SileroVoiceActivity(EnergyModel, threshold=threshold)
    with pytest.raises(ValueError):
        SileroVoiceActivity(EnergyModel, model_load_timeout_s=0)


def test_b_capabilities_are_declared_truthfully():
    assert SileroVoiceActivity.capabilities == VoiceActivityCapabilities(
        streaming=True, emits_confidence=True, frame_ms=32, languages=()
    )


def test_j_frame_ms_is_exactly_the_analysis_window():
    """512 samples at 16 kHz is exactly 32 ms — the declaration is arithmetic, not an estimate."""
    assert WINDOW_MS * SAMPLE_RATE == WINDOW_SAMPLES * 1000
    assert SileroVoiceActivity.capabilities.frame_ms == WINDOW_MS == 32


def test_b_no_language_capability_is_claimed():
    assert SileroVoiceActivity.capabilities.languages == ()


def test_z_it_registers_in_the_one_registry():
    registry = ProviderRegistry()
    registry.register(CapabilityKind.VOICE_ACTIVITY, SileroVoiceActivity(EnergyModel), default=True)
    assert isinstance(registry.resolve(CapabilityKind.VOICE_ACTIVITY), SileroVoiceActivity)


# ── C. audio format ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "frame, error",
    [
        (AudioFrame(data=b"ab", sample_rate=SAMPLE_RATE), CapabilityUnsupported),
        (_pcm(b"abcd", channels=2), CapabilityUnsupported),
        (_pcm(b"ab", sample_rate=48000), CapabilityUnsupported),
        (_pcm(b"ab", sample_rate=8000), CapabilityUnsupported),
        (_pcm(b"abc"), ProviderProtocolError),
    ],
    ids=["opaque", "stereo", "48k", "8k", "odd-bytes"],
)
@pytest.mark.asyncio
async def test_c_unsupported_audio_is_refused_never_reinterpreted(frame, error):
    model = EnergyModel()
    provider = SileroVoiceActivity(_factory(model))
    await provider.start(session_id=SESSION)
    try:
        with pytest.raises(error):
            provider.detect(frame, session_id=SESSION)
        assert provider.buffered_ms(SESSION) == 0.0, "refused audio was buffered anyway"
    finally:
        await provider.stop(session_id=SESSION)
    assert model.windows == []


@pytest.mark.asyncio
async def test_c_supported_audio_of_any_frame_duration_is_accepted():
    provider = SileroVoiceActivity(EnergyModel)
    await provider.start(session_id=SESSION)
    try:
        for samples in (1, 80, 160, 320, 480, 960):  # sub-ms up to 60 ms frames
            sample = provider.detect(_pcm(bytes(samples * 2)), session_id=SESSION)
            assert isinstance(sample, VoiceActivitySample)
    finally:
        await provider.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_c_detect_for_an_unstarted_session_is_a_typed_error():
    with pytest.raises(ProviderProtocolError, match="not started"):
        SileroVoiceActivity(EnergyModel).detect(_pcm(bytes(2)), session_id="nope")


@pytest.mark.asyncio
async def test_d_starting_a_session_twice_is_refused():
    provider = SileroVoiceActivity(EnergyModel)
    await provider.start(session_id=SESSION)
    try:
        with pytest.raises(ProviderProtocolError, match="already started"):
            await provider.start(session_id=SESSION)
    finally:
        await provider.stop(session_id=SESSION)


# ── D–H. lifecycle ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_d_start_loads_the_model_off_the_event_loop_and_starts_one_owned_task():
    loaded_on: list[str] = []

    def factory():
        loaded_on.append(threading.current_thread().name)
        return EnergyModel()

    provider = SileroVoiceActivity(factory)
    await provider.start(session_id=SESSION)
    try:
        assert loaded_on and loaded_on[0].startswith("vad-silero"), loaded_on
        assert loaded_on[0] != threading.main_thread().name
        (task,) = _inference_tasks()
        assert task.get_name() == f"{INFERENCE_TASK_PREFIX}:{SESSION}"
    finally:
        await provider.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_e_h_stop_cancels_and_awaits_the_task_and_releases_state():
    provider = SileroVoiceActivity(EnergyModel)
    await provider.start(session_id=SESSION)
    provider.detect(_pcm(bytes(100)), session_id=SESSION)
    (task,) = _inference_tasks()
    await provider.stop(session_id=SESSION)
    assert task.done() and task.cancelled()
    assert _inference_tasks() == []
    assert provider.buffered_ms(SESSION) == 0.0
    with pytest.raises(ProviderProtocolError):
        provider.detect(_pcm(bytes(2)), session_id=SESSION)


@pytest.mark.asyncio
async def test_f_repeated_and_unknown_stop_is_safe():
    provider = SileroVoiceActivity(EnergyModel)
    await provider.start(session_id=SESSION)
    await provider.stop(session_id=SESSION)
    await provider.stop(session_id=SESSION)
    await provider.stop(session_id="never-started")
    assert _inference_tasks() == []


@pytest.mark.asyncio
async def test_g_sessions_are_independent():
    a, b = EnergyModel(), EnergyModel()
    models = iter((a, b))
    provider = SileroVoiceActivity(lambda: next(models))
    await provider.start(session_id="s-a")
    await provider.start(session_id="s-b")
    try:
        provider.detect(_pcm(pcm_window(20000)), session_id="s-a")
        await _until(lambda: provider.windows_analysed("s-a") == 1)
        assert provider.detect(_pcm(b""), session_id="s-a").speech is True
        assert provider.detect(_pcm(b""), session_id="s-b") == INITIAL_SAMPLE
        assert len(a.windows) == 1 and b.windows == []
        await provider.stop(session_id="s-a")
        assert len(_inference_tasks()) == 1
    finally:
        await provider.stop(session_id="s-b")


# ── K–M + §17. the blocking-inference proof ─────────────────────────────────


@pytest.mark.asyncio
async def test_k_l_m_detect_never_waits_for_a_blocked_model():
    """**The SD7 proof.** The model is stuck inside inference on the worker thread. `detect()` must
    keep answering — immediately, without I/O, without the model — and `stop()` must still end the
    provider-owned lifecycle deterministically."""
    model = BlockingModel()
    provider = SileroVoiceActivity(_factory(model))
    await provider.start(session_id=SESSION)
    try:
        provider.detect(_pcm(pcm_window(20000)), session_id=SESSION)
        # 1. Inference is genuinely running, on the provider's worker thread.
        await _thread_event(model.entered)
        assert model.thread_names[0].startswith("vad-silero")
        assert model.thread_names[0] != threading.current_thread().name

        # 2–4. detect() is callable while inference is blocked, returns the startup verdict, and
        # does not invoke the model itself (still exactly one call, the blocked one).
        for _ in range(50):
            with BlockingIOSentinel("detect() during blocked inference"):
                sample = provider.detect(_pcm(pcm_window(20000)), session_id=SESSION)
            assert sample == INITIAL_SAMPLE
        assert len(model.thread_names) == 1, "detect() ran the model"
        assert provider.windows_analysed(SESSION) == 0
    finally:
        # 5. stop() ends the lifecycle while the native call is still blocked.
        await provider.stop(session_id=SESSION)

    assert _inference_tasks() == []
    assert not model.returned.is_set(), "the fixture released the model before stop() returned"
    model.release.set()
    await _thread_event(model.returned)
    assert provider.windows_analysed(SESSION) == 0, "a discarded result was published"


@pytest.mark.asyncio
async def test_k_detect_cost_is_constant_while_the_buffer_is_full_and_inference_blocked():
    model = BlockingModel()
    provider = SileroVoiceActivity(_factory(model))
    await provider.start(session_id=SESSION)
    try:
        provider.detect(_pcm(pcm_window(1)), session_id=SESSION)
        await _thread_event(model.entered)
        probe = CostProbe()
        for size in (2, 1024, 65536, 1 << 20):
            frame = _pcm(bytes(size))
            probe.measure(size, lambda f=frame: provider.detect(f, session_id=SESSION))
        assert probe.is_constant(), probe.report()
        assert provider.buffered_ms(SESSION) <= MAX_BUFFERED_MS
    finally:
        await provider.stop(session_id=SESSION)
        model.release.set()


def test_m_detect_contains_no_inference_await_loop_or_model_access():
    """Structural half of SD7: `detect()` cannot run the model because it never names it, cannot
    wait because it never awaits, and cannot scan history because it has no loop."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(SileroVoiceActivity.detect)))
    for node in ast.walk(tree):
        assert not isinstance(node, ast.Await | ast.For | ast.While | ast.AsyncFor), node
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"model", "probability", "run_in_executor", "result"}, node.attr
        if isinstance(node, ast.Name):
            assert node.id not in {"time", "threading"}, node.id


# ── N–O + §18. incremental streaming and initial state ──────────────────────


@pytest.mark.asyncio
async def test_o_the_initial_state_is_no_evidence_of_speech_and_says_so():
    model = BlockingModel()
    provider = SileroVoiceActivity(_factory(model))
    await provider.start(session_id=SESSION)
    try:
        assert provider.detect(_pcm(b""), session_id=SESSION) == VoiceActivitySample(
            speech=False, confidence=0.0
        )
        assert provider.windows_analysed(SESSION) == 0
    finally:
        await provider.stop(session_id=SESSION)
        model.release.set()


@pytest.mark.asyncio
async def test_n_audio_is_analysed_incrementally_window_by_window():
    """**The streaming proof.** Window 1 produces a verdict before window 2 exists; window 2
    then updates it. No session-level accumulation, no dependence on STT."""
    model = EnergyModel()
    provider = SileroVoiceActivity(_factory(model))
    await provider.start(session_id=SESSION)
    try:
        assert provider.detect(_pcm(pcm_window(24000)), session_id=SESSION) == INITIAL_SAMPLE
        await _until(lambda: provider.windows_analysed(SESSION) == 1)
        first = provider.detect(_pcm(b""), session_id=SESSION)
        assert first.speech is True and first.confidence == pytest.approx(24000 / 32767)
        assert len(model.windows) == 1

        provider.detect(_pcm(pcm_window(0)), session_id=SESSION)
        await _until(lambda: provider.windows_analysed(SESSION) == 2)
        second = provider.detect(_pcm(b""), session_id=SESSION)
        assert second == VoiceActivitySample(speech=False, confidence=0.0)
        assert len(model.windows) == 2
    finally:
        await provider.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_j_frames_are_concatenated_into_exact_windows_without_padding():
    model = EnergyModel()
    provider = SileroVoiceActivity(_factory(model))
    await provider.start(session_id=SESSION)
    try:
        twenty_ms = pcm_window(10000)[: 320 * 2]  # 320 samples
        provider.detect(_pcm(twenty_ms), session_id=SESSION)  # 320 samples: no window yet
        for _ in range(100):
            await asyncio.sleep(0)
        assert provider.windows_analysed(SESSION) == 0, "a partial window was padded and analysed"
        provider.detect(_pcm(twenty_ms), session_id=SESSION)  # 640 samples: one window + 128
        await _until(lambda: provider.windows_analysed(SESSION) == 1)
        assert model.windows == [pcm_window(10000)]
        assert provider.buffered_ms(SESSION) == pytest.approx(128 / SAMPLE_RATE * 1000)
    finally:
        await provider.stop(session_id=SESSION)


@pytest.mark.parametrize("threshold, expected", [(0.2, True), (0.8, False)])
@pytest.mark.asyncio
async def test_n_the_threshold_decides_speech_and_confidence_is_the_raw_score(threshold, expected):
    provider = SileroVoiceActivity(EnergyModel, threshold=threshold)
    await provider.start(session_id=SESSION)
    try:
        provider.detect(_pcm(pcm_window(16384)), session_id=SESSION)  # ≈ 0.5
        await _until(lambda: provider.windows_analysed(SESSION) == 1)
        sample = provider.detect(_pcm(b""), session_id=SESSION)
        assert sample.speech is expected
        assert sample.confidence == pytest.approx(16384 / 32767)
    finally:
        await provider.stop(session_id=SESSION)


# ── I + Y. bounded state and truthful drops ─────────────────────────────────


@pytest.mark.asyncio
async def test_i_buffer_is_bounded_and_every_dropped_millisecond_is_reported():
    model = BlockingModel()
    provider = SileroVoiceActivity(_factory(model))
    await provider.start(session_id=SESSION)
    try:
        provider.detect(_pcm(pcm_window(1)), session_id=SESSION)
        await _thread_event(model.entered)  # the first window left the buffer; inference is stuck
        fed = 0
        for _ in range(200):
            provider.detect(_pcm(bytes(640)), session_id=SESSION)  # 20 ms each
            fed += 640
        assert provider.buffered_ms(SESSION) == MAX_BUFFERED_MS
        dropped = provider.drain_dropped_ms(SESSION)
        assert dropped == pytest.approx((fed - MAX_BUFFERED_BYTES) / 32)
        assert provider.drain_dropped_ms(SESSION) == 0.0, "the drain must reset"
    finally:
        await provider.stop(session_id=SESSION)
        model.release.set()


@pytest.mark.asyncio
async def test_i_no_drop_is_reported_when_inference_keeps_up():
    provider = SileroVoiceActivity(EnergyModel)
    await provider.start(session_id=SESSION)
    try:
        for i in range(5):
            provider.detect(_pcm(pcm_window(1000)), session_id=SESSION)
            await _until(lambda i=i: provider.windows_analysed(SESSION) == i + 1)
        assert provider.drain_dropped_ms(SESSION) == 0.0
    finally:
        await provider.stop(session_id=SESSION)


def test_y_the_provider_creates_no_queue():
    tree = ast.parse(pathlib.Path(inspect.getfile(silero_module)).read_text(encoding="utf-8"))
    called = {
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert not called & {"Queue", "LifoQueue", "PriorityQueue", "SimpleQueue", "deque"}, called
    assert MAX_BUFFERED_BYTES == 10 * WINDOW_BYTES and MAX_BUFFERED_MS == 320


# ── P–Q. failures ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p_background_inference_failure_is_sticky_and_typed():
    model = FailingModel(fail_after=1)
    provider = SileroVoiceActivity(_factory(model))
    await provider.start(session_id=SESSION)
    try:
        provider.detect(_pcm(pcm_window(20000, windows=2)), session_id=SESSION)
        (task,) = _inference_tasks()
        await asyncio.wait({task})
        assert provider.windows_analysed(SESSION) == 1
        for _ in range(3):
            with pytest.raises(ProviderUnavailable, match="inference exploded"):
                provider.detect(_pcm(b""), session_id=SESSION)
    finally:
        await provider.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_q_an_untyped_library_error_leaves_typed_and_sanitized():
    model = FailingModel(error=RuntimeError("native crash at 0xdeadbeef /secret/path"))
    provider = SileroVoiceActivity(_factory(model))
    await provider.start(session_id=SESSION)
    try:
        provider.detect(_pcm(pcm_window(1)), session_id=SESSION)
        (task,) = _inference_tasks()
        await asyncio.wait({task})
        with pytest.raises(ProviderUnavailable) as raised:
            provider.detect(_pcm(b""), session_id=SESSION)
        assert "deadbeef" not in str(raised.value) and "secret" not in str(raised.value)
    finally:
        await provider.stop(session_id=SESSION)


@pytest.mark.parametrize(
    "factory_error, expected",
    [
        (SpeechModelError("weights missing"), ProviderUnavailable),
        (RuntimeError("ggml init failed at /opt/secret"), ProviderUnavailable),
    ],
)
@pytest.mark.asyncio
async def test_q_model_load_failures_are_typed_and_leave_no_session(factory_error, expected):
    def factory():
        raise factory_error

    provider = SileroVoiceActivity(factory)
    with pytest.raises(expected) as raised:
        await provider.start(session_id=SESSION)
    assert "/opt/secret" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert _inference_tasks() == []
    with pytest.raises(ProviderProtocolError):
        provider.detect(_pcm(b""), session_id=SESSION)


@pytest.mark.asyncio
async def test_q_a_model_with_the_wrong_format_is_a_capability_mismatch():
    provider = SileroVoiceActivity(WrongRateModel)
    with pytest.raises(CapabilityUnsupported, match="8000 Hz"):
        await provider.start(session_id=SESSION)
    assert _inference_tasks() == []


@pytest.mark.asyncio
async def test_q_a_stalled_model_load_times_out():
    release = threading.Event()

    def factory():
        release.wait()
        return EnergyModel()

    provider = SileroVoiceActivity(factory, model_load_timeout_s=0.05)
    try:
        with pytest.raises(ProviderTimeout, match="model load"):
            await provider.start(session_id=SESSION)
    finally:
        release.set()
    assert _inference_tasks() == []


def test_q_every_raised_type_is_a_provider_error():
    raised = (CapabilityUnsupported, ProviderProtocolError, ProviderTimeout, ProviderUnavailable)
    assert all(issubclass(error, ProviderError) for error in raised)


# ── R–S. cancellation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_r_cancelling_start_propagates_and_registers_nothing():
    release = threading.Event()
    entered = threading.Event()

    def factory():
        entered.set()
        release.wait()
        return EnergyModel()

    provider = SileroVoiceActivity(factory)
    starting = asyncio.ensure_future(provider.start(session_id=SESSION))
    await _thread_event(entered)
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting
    release.set()
    assert _inference_tasks() == []
    with pytest.raises(ProviderProtocolError):
        provider.detect(_pcm(b""), session_id=SESSION)


@pytest.mark.asyncio
async def test_r_stopping_mid_inference_is_cancellation_not_failure():
    model = BlockingModel()
    provider = SileroVoiceActivity(_factory(model))
    await provider.start(session_id=SESSION)
    provider.detect(_pcm(pcm_window(1)), session_id=SESSION)
    await _thread_event(model.entered)
    (task,) = _inference_tasks()
    await provider.stop(session_id=SESSION)
    model.release.set()
    assert task.cancelled(), "cancellation was converted into an ordinary completion"
    # A restarted session is healthy: the cancelled one left no sticky failure behind.
    await provider.start(session_id=SESSION)
    try:
        assert provider.detect(_pcm(b""), session_id=SESSION) == INITIAL_SAMPLE
    finally:
        await provider.stop(session_id=SESSION)


@pytest.mark.asyncio
async def test_s_cancelled_vad_start_in_the_pipeline_records_nothing_and_trips_no_breaker():
    from provider.circuit_breaker import BreakerState, CircuitBreakerPolicy
    from providers.transport.local import LocalTransport
    from runtime.faults.degradation import DegradationRecorder
    from runtime.faults.guard import GuardPolicy, ProviderGuard
    from runtime.pipeline.pipeline import RuntimePipeline
    from runtime.voice_activity import VoiceActivityCoordinator, VoiceActivityEngine

    release, entered = threading.Event(), threading.Event()

    def factory():
        entered.set()
        release.wait()
        return EnergyModel()

    recorder = DegradationRecorder("ses_cancel")
    guard = ProviderGuard(
        capability="voice_activity",
        policy=GuardPolicy(breaker=CircuitBreakerPolicy(failure_threshold=1)),
        degradations=recorder,
    )
    provider = SileroVoiceActivity(factory)
    pipe = RuntimePipeline(
        transport=LocalTransport(),
        stt=type("S", (), {"name": "s"})(),
        llm=type("L", (), {"name": "l"})(),
        tts=type("T", (), {"name": "t"})(),
        session_id="ses_cancel",
        system_prompt="p",
        sample_rate=SAMPLE_RATE,
        voice_activity=VoiceActivityCoordinator(
            provider=provider, engine=VoiceActivityEngine("ses_cancel"), session_id="ses_cancel"
        ),
        guards={"voice_activity": guard},
    )
    starting = asyncio.ensure_future(pipe.start())
    await _thread_event(entered)
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting
    release.set()
    assert recorder.history == ()
    assert guard.breaker_state("silero") is BreakerState.CLOSED
    assert _inference_tasks() == []


# ── T. degradation through the runtime ──────────────────────────────────────


async def _run_session(model_factory, frames: list[AudioFrame], session_id: str):
    import bootstrap
    from core.session_control import StartSessionRequest
    from runtime.service import RuntimeSessionService

    registry = bootstrap.build_registry(
        voice_activity=bootstrap.VOICE_ACTIVITY_SILERO, speech_model_factory=model_factory
    )
    service = RuntimeSessionService(registry)
    await service.start(
        StartSessionRequest(
            session_id=session_id, org_id="o", agent_id="a", channel="web",
            system_prompt="p", sample_rate=SAMPLE_RATE,
        )
    )
    worker = service._workers[session_id]  # noqa: SLF001
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    return service, worker, transport


def _dropped(worker) -> list:
    return [
        d for d in worker.orchestrator.degradations.history if d.kind.value == "audio_dropped"
    ]


@pytest.mark.asyncio
async def test_t_buffer_overflow_is_recorded_as_audio_dropped():
    model = BlockingModel()
    service, worker, transport = await _run_session(_factory(model), [], "ses_drop")
    try:
        await transport.feed("ses_drop", _pcm(pcm_window(1)))
        await _thread_event(model.entered)
        for _ in range(40):
            await transport.feed("ses_drop", _pcm(bytes(640)))
        await _until(lambda: bool(_dropped(worker)))
    finally:
        model.release.set()
        await transport.hangup("ses_drop")
        await worker.wait_closed()
        await service.end("ses_drop")
    dropped = _dropped(worker)
    assert dropped[0].capability == "voice_activity" and dropped[0].provider == "silero"
    assert "ms of audio discarded" in dropped[0].detail
    assert _inference_tasks() == []


@pytest.mark.asyncio
async def test_t_inference_failure_is_recorded_and_the_call_continues():
    service, worker, transport = await _run_session(
        lambda: FailingModel(fail_after=0), [], "ses_vadfail"
    )
    await transport.feed("ses_vadfail", _pcm(pcm_window(1)))
    await _until(lambda: not _inference_tasks())
    await transport.feed("ses_vadfail", _pcm(pcm_window(1)))
    await transport.hangup("ses_vadfail")
    await worker.wait_closed()
    history = worker.orchestrator.degradations.history
    await service.end("ses_vadfail")
    failures = [d for d in history if d.kind.value == "auxiliary_disabled"]
    assert failures, f"inference failure was not recorded: {history}"
    assert "inference exploded" in failures[0].detail and failures[0].fallback


@pytest.mark.asyncio
async def test_t_model_load_failure_is_recorded_as_vad_disabled():
    def factory():
        raise SpeechModelError("weights missing")

    service, worker, transport = await _run_session(factory, [], "ses_loadfail")
    await transport.hangup("ses_loadfail")
    await worker.wait_closed()
    history = worker.orchestrator.degradations.history
    await service.end("ses_loadfail")
    assert history and history[0].kind.value == "auxiliary_disabled" and history[0].fallback


# ── X, Z–AD. architecture ───────────────────────────────────────────────────

_VAD_DIR = pathlib.Path(inspect.getfile(SileroVoiceActivity)).parent
_PLATFORM_ROOT = _VAD_DIR.parent.parent


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


_MODEL_LIBRARIES = {"pysilero_vad", "silero_vad", "onnxruntime", "torch", "webrtcvad", "ggml"}


def test_x_the_model_library_is_imported_only_by_its_adapter():
    offenders = [
        str(p.relative_to(_PLATFORM_ROOT))
        for p in _production_files()
        if _imports(p) & _MODEL_LIBRARIES and p.name != "silero_model.py"
    ]
    assert offenders == [], f"a VAD model library leaked outside its adapter: {offenders}"


def test_x_the_adapter_does_import_the_library():
    assert "pysilero_vad" in _imports(_VAD_DIR / "silero_model.py")


def test_x_the_provider_and_seam_are_library_free():
    for name in ("silero.py", "speech_model.py"):
        assert not _imports(_VAD_DIR / name) & (_MODEL_LIBRARIES | {"numpy"}), name


def test_x_no_model_type_crosses_core_or_runtime():
    for package in ("core", "runtime"):
        for path in (_PLATFORM_ROOT / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            assert not _imports(path) & _MODEL_LIBRARIES, path.name
            source = path.read_text(encoding="utf-8")
            assert "SpeechModel" not in source and "SileroVoiceActivity" not in source, path.name


def _called_and_imported_names(path: pathlib.Path) -> set[str]:
    """Names a module *calls* or *imports* — read from the AST, so prose that names a class to
    state a boundary is never mistaken for a use of it."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
        elif isinstance(node, ast.ImportFrom):
            names.update(a.name for a in node.names)
    return names


def test_z_the_vad_plugin_constructs_no_registry_and_no_other_capability():
    forbidden = {"ProviderRegistry", "LocalVoiceActivity", "ConversationOrchestrator"}
    for path in _VAD_DIR.glob("*.py"):
        assert not _called_and_imported_names(path) & forbidden, path.name


def test_z_the_orchestrator_constructs_no_vad_provider():
    from runtime.orchestrator import orchestrator as orch

    source = pathlib.Path(inspect.getfile(orch)).read_text(encoding="utf-8")
    assert "SileroVoiceActivity(" not in source and "LocalVoiceActivity(" not in source


def test_aa_ab_the_vad_plugin_neither_resamples_nor_normalizes():
    for path in _VAD_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                assert "resampl" not in node.name.lower(), (path.name, node.name)
                assert "normaliz" not in node.name.lower(), (path.name, node.name)
        assert not _imports(path) & {"audioop", "scipy", "numpy", "soxr", "samplerate"}, path.name
        assert "AudioNormalizer" not in _called_and_imported_names(path), path.name


def test_ac_ad_no_pipecat_and_no_durable_spine_reach():
    for path in _VAD_DIR.glob("*.py"):
        roots = _imports(path)
        assert "pipecat" not in roots, path.name
        assert not roots & {"events", "runtime", "api", "billing", "observability", "provider"}, (
            path.name,
            roots,
        )


def test_x_providers_reach_only_core_and_themselves():
    for path in _VAD_DIR.glob("*.py"):
        stdlib_or_allowed = {
            "__future__", "asyncio", "logging", "concurrent", "collections", "typing", "core",
            "pysilero_vad", "array", "threading", "pathlib",
        }
        assert _imports(path) <= stdlib_or_allowed, (path.name, _imports(path) - stdlib_or_allowed)


def test_the_fake_models_satisfy_the_seam():
    assert isinstance(EnergyModel(), SpeechModel)
    assert array("h", pcm_window(7)).tolist() == [7] * WINDOW_SAMPLES

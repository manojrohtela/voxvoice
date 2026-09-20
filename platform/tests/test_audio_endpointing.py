"""Audio-driven endpointing and the `metric.turn` remap (M11.8, ADR-0047 ME1).

**VAD is speech detection; endpointing decides when a speech turn ends.** These tests drive the
decision from *audio duration* only: every frame carries an exact, declared number of milliseconds,
so every threshold is crossed by arithmetic rather than by a clock, a sleep, or scheduler luck.

Nothing here needs a model, a vendor or a network. The voice-activity provider is a scripted fake;
the latency clock is a deterministic stepper; the LLM/TTS are stubs whose invocation is *asserted
against* (endpointing must never trigger them).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib

import pytest

from core.audio import AudioEncoding, AudioFrame
from core.providers.voice_activity import (
    VoiceActivityCapabilities,
    VoiceActivityProvider,
    VoiceActivitySample,
)
from observability.turn_latency import (
    ADDITIVE_INTERVALS,
    AUDIO_BOUNDARIES,
    KNOWN_BOUNDARIES,
    STAGE_BOUNDARIES,
    STAGE_INTERVALS,
    TurnLatencyTap,
)
from runtime.endpoint import (
    AUDIO_ENDPOINT_TRANSITIONS,
    AudioEndpointDetector,
    AudioEndpointKind,
    AudioEndpointPolicy,
    AudioEndpointState,
    EndpointCoordinator,
    EndpointDecision,
    EndpointEngine,
    EndpointPolicy,
    frame_duration_ms,
)
from runtime.endpoint import audio as audio_module
from runtime.turn import TurnCoordinator, TurnEngine, TurnPhase
from runtime.voice_activity import VoiceActivityCoordinator, VoiceActivityEngine

SESSION = "ses_ep"
RATE = 16000
FRAME_MS = 20.0
#: 20 ms of 16 kHz mono PCM16.
FRAME_BYTES = int(RATE * 2 * FRAME_MS / 1000)


# ── fixtures: audio, providers, clocks ──────────────────────────────────────


def speech_frame(payload: bytes = b"hello", *, ms: float = FRAME_MS) -> AudioFrame:
    """A frame the fake VAD calls speech: non-zero PCM. Its bytes double as `LocalSTT`'s text."""
    size = int(RATE * 2 * ms / 1000)
    return AudioFrame(
        data=payload.ljust(size, b"\x20"), sample_rate=RATE, encoding=AudioEncoding.PCM16
    )


def silence_frame(*, ms: float = FRAME_MS) -> AudioFrame:
    """Digital silence: all-zero PCM, which the fake VAD calls non-speech."""
    return AudioFrame(
        data=bytes(int(RATE * 2 * ms / 1000)), sample_rate=RATE, encoding=AudioEncoding.PCM16
    )


class ScriptedVAD(VoiceActivityProvider):
    """Speech iff the frame contains a non-zero sample. Deterministic, no model, no state."""

    name = "scripted-vad"
    capabilities = VoiceActivityCapabilities(streaming=True, emits_confidence=False, frame_ms=20)

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.seen = 0
        self.fail_after = fail_after

    async def start(self, *, session_id: str) -> None: ...

    async def stop(self, *, session_id: str) -> None: ...

    def detect(self, frame: AudioFrame, *, session_id: str) -> VoiceActivitySample:
        self.seen += 1
        if self.fail_after is not None and self.seen > self.fail_after:
            raise RuntimeError("vad exploded")
        return VoiceActivitySample(speech=any(frame.data), confidence=1.0)


class StepClock:
    """A monotonic clock that advances a fixed step per read — no wall time anywhere."""

    def __init__(self, *, start: float = 0.0, step: float = 0.010) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


class Publisher:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    def publish(self, type, data=None, *, turn=None, correlation_id=None, metadata=None):
        self.published.append((type, data, turn))
        return None


def feed(detector: AudioEndpointDetector, pattern: str, *, ms: float = FRAME_MS) -> list:
    """Drive the detector with a pattern: `S` = speech frame, `.` = silence frame."""
    signals = []
    for mark in pattern:
        signal = detector.observe(speech=(mark == "S"), duration_ms=ms)
        if signal:
            signals.append(signal)
    return signals


def kinds(signals) -> list[AudioEndpointKind]:
    return [s.kind for s in signals]


#: With the defaults, 120 ms of speech starts an utterance and 240 ms of silence ends it.
SPEECH_FRAMES_TO_START = 6  # 6 × 20 ms = 120 ms
SILENCE_FRAMES_TO_END = 12  # 12 × 20 ms = 240 ms


# ── 1-11, 14-15. the detector ───────────────────────────────────────────────


def test_policy_defaults_are_explicit_and_documented():
    policy = AudioEndpointPolicy()
    assert (policy.min_speech_ms, policy.trailing_silence_ms) == (120.0, 240.0)
    assert policy.max_utterance_ms == 30_000.0
    assert SPEECH_FRAMES_TO_START * FRAME_MS == policy.min_speech_ms
    assert SILENCE_FRAMES_TO_END * FRAME_MS == policy.trailing_silence_ms


@pytest.mark.parametrize(
    "field", ["min_speech_ms", "trailing_silence_ms", "max_utterance_ms"]
)
def test_an_impossible_threshold_is_refused(field):
    with pytest.raises(ValueError):
        AudioEndpointPolicy(**{field: -1.0})


def test_1_silence_never_creates_a_speech_start():
    detector = AudioEndpointDetector()
    assert feed(detector, "." * 200) == []
    assert detector.state is AudioEndpointState.IDLE
    assert detector.utterances == 0


def test_2_the_first_valid_speech_creates_exactly_one_speech_start():
    detector = AudioEndpointDetector()
    signals = feed(detector, "S" * SPEECH_FRAMES_TO_START)
    assert kinds(signals) == [AudioEndpointKind.SPEECH_START]
    assert detector.state is AudioEndpointState.SPEAKING


def test_3_continued_speech_does_not_duplicate_the_speech_start():
    detector = AudioEndpointDetector()
    signals = feed(detector, "S" * (SPEECH_FRAMES_TO_START + 50))
    assert kinds(signals) == [AudioEndpointKind.SPEECH_START]


def test_9_one_frame_before_the_threshold_does_not_start():
    detector = AudioEndpointDetector()
    assert feed(detector, "S" * (SPEECH_FRAMES_TO_START - 1)) == []
    assert detector.state is AudioEndpointState.SPEECH_PENDING


def test_8_the_start_threshold_boundary_is_exact():
    detector = AudioEndpointDetector()
    feed(detector, "S" * (SPEECH_FRAMES_TO_START - 1))
    assert kinds(feed(detector, "S")) == [AudioEndpointKind.SPEECH_START]


def test_brief_speech_below_the_threshold_is_forgotten():
    """A click is not an utterance: accrued evidence is dropped when it stops."""
    detector = AudioEndpointDetector()
    feed(detector, "SS.")
    assert detector.state is AudioEndpointState.IDLE
    assert detector.speech_ms == 0.0
    assert feed(detector, "S" * (SPEECH_FRAMES_TO_START - 1)) == []  # must start over


def test_4_one_short_silence_gap_does_not_create_a_speech_end():
    detector = AudioEndpointDetector()
    feed(detector, "S" * SPEECH_FRAMES_TO_START)
    assert feed(detector, ".") == []
    assert detector.state is AudioEndpointState.TRAILING_SILENCE
    assert detector.in_speech is True


def test_9_one_frame_before_the_end_threshold_does_not_endpoint():
    detector = AudioEndpointDetector()
    feed(detector, "S" * SPEECH_FRAMES_TO_START)
    assert feed(detector, "." * (SILENCE_FRAMES_TO_END - 1)) == []
    assert detector.in_speech is True


def test_5_10_sustained_silence_creates_exactly_one_speech_end():
    detector = AudioEndpointDetector()
    feed(detector, "S" * SPEECH_FRAMES_TO_START)
    signals = feed(detector, "." * (SILENCE_FRAMES_TO_END + 40))
    assert kinds(signals) == [AudioEndpointKind.SPEECH_END]
    assert detector.state is AudioEndpointState.IDLE
    assert detector.utterances == 1


def test_6_speech_resumes_after_a_short_gap_without_a_second_turn():
    detector = AudioEndpointDetector()
    signals = feed(detector, "S" * SPEECH_FRAMES_TO_START)
    signals += feed(detector, "." * (SILENCE_FRAMES_TO_END - 1))  # gap, just under the hold
    signals += feed(detector, "S" * 10)  # speech resumes: same utterance
    signals += feed(detector, "." * SILENCE_FRAMES_TO_END)
    assert kinds(signals) == [AudioEndpointKind.SPEECH_START, AudioEndpointKind.SPEECH_END]
    assert detector.utterances == 1


def test_the_silence_hold_restarts_after_a_gap_is_interrupted():
    """The hold counts *contiguous* silence: an interrupting speech frame resets it."""
    detector = AudioEndpointDetector()
    feed(detector, "S" * SPEECH_FRAMES_TO_START)
    feed(detector, "." * (SILENCE_FRAMES_TO_END - 1))
    feed(detector, "S")
    assert feed(detector, "." * (SILENCE_FRAMES_TO_END - 1)) == [], "the hold did not restart"
    assert kinds(feed(detector, ".")) == [AudioEndpointKind.SPEECH_END]


def test_7_a_second_utterance_after_a_completed_endpoint_starts_a_new_turn():
    detector = AudioEndpointDetector()
    first = feed(detector, "S" * SPEECH_FRAMES_TO_START + "." * SILENCE_FRAMES_TO_END)
    second = feed(detector, "S" * SPEECH_FRAMES_TO_START + "." * SILENCE_FRAMES_TO_END)
    assert kinds(first) == kinds(second) == [
        AudioEndpointKind.SPEECH_START,
        AudioEndpointKind.SPEECH_END,
    ]
    assert detector.utterances == 2


def test_boundaries_are_backdated_to_the_audio_that_produced_them():
    """The policy recognises a boundary late by construction; the signal says how late."""
    detector = AudioEndpointDetector()
    (start,) = feed(detector, "S" * SPEECH_FRAMES_TO_START)
    assert start.backdate_ms == pytest.approx(120.0)  # the whole accrued speech run
    (end,) = feed(detector, "." * SILENCE_FRAMES_TO_END)
    assert end.backdate_ms == pytest.approx(240.0)  # the whole silence hold


def test_the_max_utterance_guard_closes_a_turn_that_never_ends():
    detector = AudioEndpointDetector(AudioEndpointPolicy(max_utterance_ms=400.0))
    signals = feed(detector, "S" * 20)  # 400 ms of unbroken speech
    assert kinds(signals) == [AudioEndpointKind.SPEECH_START, AudioEndpointKind.SPEECH_END]
    assert signals[1].backdate_ms == 0.0, "the guard fires now; no silence established an end"


def test_the_max_utterance_guard_can_be_disabled():
    detector = AudioEndpointDetector(AudioEndpointPolicy(max_utterance_ms=0.0))
    assert kinds(feed(detector, "S" * 5000)) == [AudioEndpointKind.SPEECH_START]


def test_11_frames_of_zero_duration_never_cross_a_threshold():
    detector = AudioEndpointDetector()
    assert feed(detector, "S" * 100, ms=0.0) == []
    assert detector.state is AudioEndpointState.SPEECH_PENDING


def test_11_a_negative_duration_is_refused():
    with pytest.raises(ValueError):
        AudioEndpointDetector().observe(speech=True, duration_ms=-1.0)


def test_11_odd_and_opaque_frames_fall_back_to_the_declared_frame_duration():
    """An `OPAQUE` frame declares no format, so its duration is not derivable from its bytes."""
    assert frame_duration_ms(AudioFrame(data=b"hello"), fallback_ms=20.0) == 20.0
    assert frame_duration_ms(AudioFrame(data=b""), fallback_ms=32.0) == 32.0
    pcm = AudioFrame(data=bytes(640), sample_rate=RATE, encoding=AudioEncoding.PCM16)
    assert frame_duration_ms(pcm, fallback_ms=999.0) == pytest.approx(20.0)
    odd = AudioFrame(data=bytes(641), sample_rate=RATE, encoding=AudioEncoding.PCM16)
    assert frame_duration_ms(odd, fallback_ms=999.0) == pytest.approx(20.03125)
    stereo = AudioFrame(
        data=bytes(1280), sample_rate=RATE, encoding=AudioEncoding.PCM16, channels=2
    )
    assert frame_duration_ms(stereo, fallback_ms=999.0) == pytest.approx(20.0)


def test_15_the_detector_state_machine_is_deterministic_and_total():
    """Every reachable state has declared exits, and the model only ever proposes legal ones."""
    assert set(AUDIO_ENDPOINT_TRANSITIONS) == set(AudioEndpointState)
    detector = AudioEndpointDetector()
    seen = {detector.state}
    for pattern in ("SS", "..", "S" * 6, ".", "S", "." * 12, "S" * 6, "." * 12):
        feed(detector, pattern)
        seen.add(detector.state)
    assert seen == {
        AudioEndpointState.IDLE,
        AudioEndpointState.SPEECH_PENDING,
        AudioEndpointState.SPEAKING,
        AudioEndpointState.TRAILING_SILENCE,
    }


def test_14_detector_state_is_o1_regardless_of_how_much_audio_passed():
    """K — no buffer, no queue, no growth: the detector holds counters, not audio."""
    detector = AudioEndpointDetector()
    feed(detector, ("S" * 6 + "." * 12) * 200)
    assert detector.utterances == 200
    for slot in AudioEndpointDetector.__slots__:
        value = getattr(detector, slot)
        assert not isinstance(value, (list, dict, set, bytearray, tuple)), slot


def test_reset_discards_an_utterance_without_emitting_a_boundary():
    detector = AudioEndpointDetector()
    feed(detector, "S" * SPEECH_FRAMES_TO_START)
    detector.reset()
    assert detector.state is AudioEndpointState.IDLE
    assert detector.utterances == 0, "reset fabricated an end-of-speech"


def test_flush_closes_an_open_utterance_exactly_once():
    detector = AudioEndpointDetector()
    feed(detector, "S" * SPEECH_FRAMES_TO_START + "..")
    signal = detector.flush()
    assert signal.kind is AudioEndpointKind.SPEECH_END
    assert signal.backdate_ms == pytest.approx(40.0), "accrued silence must still be backdated"
    assert detector.flush().kind is AudioEndpointKind.NONE
    assert detector.utterances == 1


def test_flush_on_a_silent_stream_emits_nothing():
    detector = AudioEndpointDetector()
    feed(detector, "." * 50)
    assert detector.flush().kind is AudioEndpointKind.NONE


# ── 12-13. degraded voice activity ──────────────────────────────────────────


def test_12_a_provider_failure_is_not_silence():
    """ME8 — a frame that was never classified may neither end a turn nor start one."""
    detector = AudioEndpointDetector()
    feed(detector, "S" * SPEECH_FRAMES_TO_START)
    for _ in range(100):
        assert detector.note_unavailable().kind is AudioEndpointKind.NONE
    assert detector.in_speech is True, "an outage ended the turn"
    assert detector.silence_ms == 0.0, "an outage advanced the silence hold"
    assert detector.utterances == 0


def test_13_recovery_after_a_failure_resumes_from_the_held_state():
    detector = AudioEndpointDetector()
    feed(detector, "S" * SPEECH_FRAMES_TO_START)
    feed(detector, "." * 4)  # 80 ms of real silence
    for _ in range(50):
        detector.note_unavailable()
    # The hold resumes where it was: 160 ms more silence completes the 240 ms.
    assert feed(detector, "." * 7) == []
    assert kinds(feed(detector, ".")) == [AudioEndpointKind.SPEECH_END]


def test_12_an_outage_before_any_speech_starts_nothing():
    detector = AudioEndpointDetector()
    for _ in range(100):
        detector.note_unavailable()
    assert detector.state is AudioEndpointState.IDLE
    assert detector.utterances == 0


# ── 16-18. Turn Engine integration ──────────────────────────────────────────


def build_endpointing(*, policy: AudioEndpointPolicy | None = None):
    """The runtime trio as the Orchestrator wires it: Turn Engine ← endpoint ← detector."""
    turns = TurnCoordinator(TurnEngine(SESSION))
    endpoint = EndpointCoordinator(
        engine=EndpointEngine(SESSION, policy=EndpointPolicy(audio_driven=True)),
        session_id=SESSION,
        turns=turns,
    )
    return turns, endpoint, AudioEndpointDetector(policy)


def test_16_18_the_turn_engine_receives_the_endpoint_events_in_order():
    turns, endpoint, detector = build_endpointing()
    phases = [turns.engine.current.phase if turns.engine.current else None]

    for signal in feed(detector, "S" * SPEECH_FRAMES_TO_START):
        assert signal.kind is AudioEndpointKind.SPEECH_START
        turns.on_user_speech()
    phases.append(turns.engine.current.phase)

    for signal in feed(detector, "." * SILENCE_FRAMES_TO_END):
        assert signal.kind is AudioEndpointKind.SPEECH_END
        assert endpoint.on_speech_end() is EndpointDecision.ENDPOINT_CONFIRMED
    phases.append(turns.engine.current.phase)

    assert phases == [None, TurnPhase.USER_SPEAKING, TurnPhase.USER_FINISHED]


def test_18_the_idle_listening_speaking_finished_chain_stays_valid():
    turns = TurnCoordinator(TurnEngine(SESSION))
    context = turns.engine.begin_turn()
    assert context.phase is TurnPhase.LISTENING
    turns.on_user_speech()
    assert turns.engine.current.phase is TurnPhase.USER_SPEAKING
    turns.on_user_final()
    assert turns.engine.current.phase is TurnPhase.USER_FINISHED


def test_17_a_second_speech_end_cannot_produce_a_duplicate_user_finished():
    """The detector emits one end per utterance, which is what keeps the Turn Engine legal."""
    turns, endpoint, detector = build_endpointing()
    feed(detector, "S" * SPEECH_FRAMES_TO_START)
    turns.on_user_speech()
    feed(detector, "." * SILENCE_FRAMES_TO_END)
    endpoint.on_speech_end()
    assert turns.engine.current.phase is TurnPhase.USER_FINISHED
    # More silence produces no further boundary, so `on_user_final` is never called twice.
    assert feed(detector, "." * 100) == []


def test_a_transcript_segment_cannot_end_an_audio_driven_turn():
    """ME1 — with audio endpointing the Final is evidence, not a decision. Two deciders would
    produce two `USER_FINISHED` transitions for one utterance."""
    turns, endpoint, _ = build_endpointing()
    turns.on_user_speech()  # the turn the pipeline opens at speech_start
    assert endpoint.on_segment() is None
    assert endpoint.engine.decision is EndpointDecision.CONTINUE_LISTENING
    assert endpoint.on_speech_end() is EndpointDecision.ENDPOINT_CONFIRMED
    assert turns.engine.current.phase is TurnPhase.USER_FINISHED


def test_the_segment_driven_default_is_untouched():
    """Every existing profile keeps M3.3 behaviour: a segment still confirms immediately."""
    engine = EndpointEngine(SESSION)
    assert engine.policy.audio_driven is False
    assert engine.policy.confirms_on_segment is True
    assert engine.note_segment() is EndpointDecision.ENDPOINT_CONFIRMED


def test_the_guard_close_out_is_a_declared_transition_not_an_accident():
    """SPEAKING → IDLE exists solely for the `max_utterance_ms` guard, and is declared."""
    assert AudioEndpointState.IDLE in AUDIO_ENDPOINT_TRANSITIONS[AudioEndpointState.SPEAKING]


def test_a_pending_silence_hold_is_still_confirmed_by_audio_end_of_speech():
    engine = EndpointEngine(SESSION, policy=EndpointPolicy(silence_hold_frames=3))
    engine.note_segment()
    assert engine.decision is EndpointDecision.POTENTIAL_ENDPOINT
    assert engine.note_speech_end() is EndpointDecision.ENDPOINT_CONFIRMED


# ── pipeline integration: 16-20, 24-30 ──────────────────────────────────────


class EndOfSpeechSTT:
    """A streaming-shaped STT: it finalizes **after** the caller stops, like a real one.

    `LocalSTT` finalizes on every frame, which makes it useless for an ordering proof — it would
    put `stt_final` before the caller had even finished speaking. This stub emits partials while
    audio is loud and one Final once it has seen sustained silence, which is the behaviour §A8's
    ordering assumes."""

    name = "eos-stt"

    def __init__(self, *, silence_frames: int = SILENCE_FRAMES_TO_END) -> None:
        self.silence_frames = silence_frames

    async def transcribe(self, frames, *, language, sample_rate):
        from core.providers.stt import Final, Partial

        silence = 0
        spoke = False
        async for frame in frames:
            if any(frame.data):
                spoke, silence = True, 0
                yield Partial(text="hel", stability=0.0)
            elif spoke:
                silence += 1
                if silence == self.silence_frames:
                    yield Final(text="hello")
                    spoke = False


class SilentSTT:
    """Transcribes nothing — audio arrives, no transcript is ever produced."""

    name = "silent-stt"

    async def transcribe(self, frames, *, language, sample_rate):
        async for _ in frames:
            pass
        return
        yield  # pragma: no cover — empty async generator


class StubLLM:
    name = "stub-llm"
    calls = 0

    async def stream(self, *, context, tools=None, model=None):
        StubLLM.calls += 1
        from core.providers.llm import TokenDelta

        yield TokenDelta(text="ok")


class StubTTS:
    name = "stub-tts"
    calls = 0

    async def synthesize(self, text, *, voice=None, language=None):
        StubTTS.calls += 1
        yield AudioFrame(data=b"\x00\x00", sample_rate=RATE, encoding=AudioEncoding.PCM16)


def build_pipeline(
    frames: list[AudioFrame],
    *,
    latency=None,
    vad: VoiceActivityProvider | None = None,
    allow_interrupt: bool = False,
    policy: AudioEndpointPolicy | None = None,
    stt=None,
):
    """A `RuntimePipeline` wired exactly as the Orchestrator wires audio endpointing."""
    from providers.transport.local import LocalTransport
    from runtime.interrupt import InterruptCoordinator, InterruptEngine
    from runtime.pipeline.pipeline import RuntimePipeline

    transport = LocalTransport()
    turns = TurnCoordinator(TurnEngine(SESSION))
    endpoint = EndpointCoordinator(
        engine=EndpointEngine(SESSION, policy=EndpointPolicy(audio_driven=True)),
        session_id=SESSION,
        turns=turns,
    )
    interrupt = None
    if allow_interrupt:
        interrupt = InterruptCoordinator(
            engine=InterruptEngine(SESSION), session_id=SESSION, turns=turns
        )
    provider = vad if vad is not None else ScriptedVAD()
    voice_activity = VoiceActivityCoordinator(
        provider=provider,
        engine=VoiceActivityEngine(SESSION),
        session_id=SESSION,
        turns=turns,
        endpoint=endpoint,
        interrupt=interrupt,
    )
    pipeline = RuntimePipeline(
        transport=transport,
        stt=stt if stt is not None else EndOfSpeechSTT(),
        llm=StubLLM(),
        tts=StubTTS(),
        session_id=SESSION,
        system_prompt="p",
        sample_rate=RATE,
        turns=turns,
        voice_activity=voice_activity,
        endpoint=endpoint,
        interrupt=interrupt,
        latency=latency,
        audio_endpoint=AudioEndpointDetector(policy),
        vad_frame_ms=provider.capabilities.frame_ms,
    )
    return pipeline, transport, turns, endpoint


async def run_pipeline(pipeline, transport, frames: list[AudioFrame]) -> None:
    await pipeline.start()
    for frame in frames:
        await transport.feed(SESSION, frame)
    await transport.hangup(SESSION)
    await pipeline.run()
    await pipeline.stop()


def utterance(*, speech: int = SPEECH_FRAMES_TO_START, silence: int = SILENCE_FRAMES_TO_END):
    return [speech_frame() for _ in range(speech)] + [silence_frame() for _ in range(silence)]


@pytest.mark.asyncio
async def test_16_the_pipeline_records_the_boundaries_in_the_a8_order():
    """§A8 / ME1: speech_start → speech_end → endpoint_decided → stt_final, from real audio."""
    recorder = BoundaryRecorder()
    pipeline, transport, turns, _ = build_pipeline([], latency=recorder)
    await run_pipeline(pipeline, transport, utterance())

    order = [b for b in recorder.boundaries if b in ("speech_start", "speech_end",
                                                     "endpoint_decided", "stt_final")]
    assert order[:3] == ["speech_start", "speech_end", "endpoint_decided"]
    assert "stt_final" in order
    assert order.index("endpoint_decided") < order.index("stt_final")


class BoundaryRecorder:
    """A `LatencyObserver` double that records the call sequence."""

    def __init__(self) -> None:
        self.boundaries: list[str] = []
        self.terminals: list[str] = []
        self.backdates: list[tuple[str, float]] = []

    def record(self, session_id, turn_index, boundary, *, backdate_ms=0.0):
        self.boundaries.append(boundary)
        self.backdates.append((boundary, backdate_ms))

    def complete(self, session_id, turn_index, status):
        self.terminals.append(status)


@pytest.mark.asyncio
async def test_16_each_boundary_is_recorded_exactly_once_per_utterance():
    recorder = BoundaryRecorder()
    pipeline, transport, _, _ = build_pipeline([], latency=recorder)
    await run_pipeline(pipeline, transport, utterance())

    for boundary in ("speech_start", "speech_end", "endpoint_decided"):
        assert recorder.boundaries.count(boundary) == 1, boundary


@pytest.mark.asyncio
async def test_16_the_audio_boundaries_are_backdated_by_the_accrued_audio():
    recorder = BoundaryRecorder()
    pipeline, transport, _, _ = build_pipeline([], latency=recorder)
    await run_pipeline(pipeline, transport, utterance())

    backdates = dict(recorder.backdates)
    assert backdates["speech_start"] == pytest.approx(120.0)
    assert backdates["speech_end"] == pytest.approx(240.0)
    assert backdates["endpoint_decided"] == 0.0, "a decision happens when it happens"


@pytest.mark.asyncio
async def test_18_the_turn_reaches_user_finished_through_the_existing_phases():
    pipeline, transport, turns, _ = build_pipeline([])
    await run_pipeline(pipeline, transport, utterance())

    timing = turns.engine.history[0].timing.entered_at
    for phase in (TurnPhase.LISTENING, TurnPhase.USER_SPEAKING, TurnPhase.USER_FINISHED):
        assert phase in timing, f"{phase} was never entered"
    assert timing[TurnPhase.LISTENING] <= timing[TurnPhase.USER_SPEAKING]
    assert timing[TurnPhase.USER_SPEAKING] <= timing[TurnPhase.USER_FINISHED]


@pytest.mark.asyncio
async def test_17_27_two_utterances_produce_two_turns_and_no_duplicate_user_finished():
    recorder = BoundaryRecorder()
    pipeline, transport, turns, _ = build_pipeline([], latency=recorder)
    await run_pipeline(pipeline, transport, utterance() + utterance())

    assert recorder.boundaries.count("speech_end") == 2
    assert turns.engine.turn_count == 2
    # `TurnTiming` records only the **first** entry into a phase, and the Turn Engine refuses a
    # duplicate transition outright — so two `USER_FINISHED` advances would have raised, not
    # silently doubled. Each completed turn having entered it at most once is the observable half.
    for context in turns.engine.history:
        assert TurnPhase.USER_FINISHED in context.timing.entered_at


@pytest.mark.asyncio
async def test_5_a_short_gap_inside_one_utterance_does_not_split_the_turn():
    recorder = BoundaryRecorder()
    pipeline, transport, turns, _ = build_pipeline([], latency=recorder)
    frames = (
        [speech_frame() for _ in range(SPEECH_FRAMES_TO_START)]
        + [silence_frame() for _ in range(SILENCE_FRAMES_TO_END - 1)]  # gap under the hold
        + [speech_frame() for _ in range(3)]
        + [silence_frame() for _ in range(SILENCE_FRAMES_TO_END)]
    )
    await run_pipeline(pipeline, transport, frames)

    assert recorder.boundaries.count("speech_start") == 1
    assert recorder.boundaries.count("speech_end") == 1


@pytest.mark.asyncio
async def test_28_endpointing_alone_never_invokes_the_llm_or_tts():
    """C — the endpoint closes the *user* half of the turn and nothing else."""
    StubLLM.calls = StubTTS.calls = 0
    recorder = BoundaryRecorder()
    pipeline, transport, _, _ = build_pipeline([], latency=recorder, stt=SilentSTT())
    await run_pipeline(pipeline, transport, utterance())

    # The audio boundaries were reached…
    assert recorder.boundaries.count("speech_end") == 1
    assert "endpoint_decided" in recorder.boundaries
    # …and nothing downstream of the user turn ran, because no transcript was ever produced.
    assert StubLLM.calls == 0 and StubTTS.calls == 0


@pytest.mark.asyncio
async def test_a_turn_closed_by_audio_with_no_transcript_is_retired_not_left_open():
    """An utterance nobody could transcribe must not wedge the Turn Engine: the next utterance
    retires it as `TURN_CANCELLED` and starts a fresh turn."""
    recorder = BoundaryRecorder()
    pipeline, transport, turns, _ = build_pipeline([], latency=recorder)
    pipeline, transport, turns, _ = build_pipeline([], latency=recorder, stt=SilentSTT())
    await run_pipeline(pipeline, transport, utterance() + utterance())

    assert "cancelled" in recorder.terminals
    assert turns.engine.turn_count == 2


@pytest.mark.asyncio
async def test_29_30_no_task_and_no_durable_write_survive_the_session():
    """K/N + R — endpointing starts no task, and writes nothing to the Epic 10 spine."""
    before = len(asyncio.all_tasks())
    pipeline, transport, _, _ = build_pipeline([])
    await run_pipeline(pipeline, transport, utterance())
    assert len(asyncio.all_tasks()) <= before


@pytest.mark.asyncio
async def test_12_a_failing_vad_provider_does_not_end_the_turn():
    """J — a provider outage is neither silence nor an endpoint; audio still reaches STT."""
    recorder = BoundaryRecorder()
    vad = ScriptedVAD(fail_after=SPEECH_FRAMES_TO_START)
    pipeline, transport, turns, _ = build_pipeline([], latency=recorder, vad=vad)
    await run_pipeline(pipeline, transport, utterance(silence=SILENCE_FRAMES_TO_END * 3))

    assert recorder.boundaries.count("speech_start") == 1
    # The turn *is* closed — but by the hangup, not by the outage. The two are distinguishable:
    # a hold-based end backdates the full 240 ms of silence it counted, while this one backdates
    # nothing, because an unclassified frame advanced no counter (ME8).
    ends = [ms for boundary, ms in recorder.backdates if boundary == "speech_end"]
    assert ends == [0.0], f"an outage was counted as silence: {ends}"


@pytest.mark.asyncio
async def test_19_with_interruptions_disabled_speech_never_interrupts():
    StubLLM.calls = StubTTS.calls = 0
    pipeline, transport, turns, _ = build_pipeline([], allow_interrupt=False)
    await run_pipeline(pipeline, transport, utterance() + utterance())

    assert pipeline._interrupt is None  # noqa: SLF001 — the sequential path, unchanged
    # Ordinary speech produced ordinary turns: nothing was interrupted, because there is no
    # interrupt framework wired at all.
    assert turns.engine.turn_count == 2
    for context in turns.engine.history:
        assert TurnPhase.TURN_CANCELLED not in context.timing.entered_at


@pytest.mark.asyncio
async def test_20_with_interruptions_enabled_the_existing_path_is_reachable_from_real_speech():
    """F — M11.8 connects real speech evidence to the *existing* interrupt framework; it does not
    redesign it. The proof is that the VAF fed the interrupt engine real per-frame verdicts."""
    pipeline, transport, _, _ = build_pipeline([], allow_interrupt=True)
    await run_pipeline(pipeline, transport, utterance())

    interrupt = pipeline._interrupt  # noqa: SLF001
    assert interrupt is not None
    assert interrupt.engine.context.session_id == SESSION


# ── 24-27. the metric.turn remap (ADR-0047 ME1-ME8) ─────────────────────────


def tap(clock=None):
    publisher = Publisher()
    return TurnLatencyTap(publisher, clock=clock or StepClock()), publisher


def test_the_stage_chain_now_follows_the_a8_ordering():
    """ME1 — endpoint decision after true end-of-speech, STT final after the endpoint."""
    assert STAGE_BOUNDARIES[:3] == ("speech_end", "endpoint_decided", "stt_final")
    assert STAGE_INTERVALS[0] == ("endpoint", "speech_end", "endpoint_decided")
    assert STAGE_INTERVALS[1] == ("stt", "endpoint_decided", "stt_final")


def test_the_stage_key_names_are_preserved_exactly():
    """ME2/ME4 — additive only: renaming a key is the silent mutation AR-32 forbids."""
    assert [name for name, _, _ in STAGE_INTERVALS] == ["endpoint", "stt", "llm", "tts", "egress"]


def test_speech_start_is_an_additive_boundary_outside_the_chain():
    assert AUDIO_BOUNDARIES == ("speech_start",)
    assert "speech_start" in KNOWN_BOUNDARIES
    assert "speech_start" not in STAGE_BOUNDARIES, "the chain would no longer sum to v2v"
    assert ADDITIVE_INTERVALS == (("user_speech", "speech_start", "speech_end"),)


def test_24_25_the_audio_boundaries_are_stamped_where_the_audio_says():
    """24/25 — a backdated boundary lands earlier than the clock read that recorded it."""
    t, publisher = tap(StepClock(start=10.0, step=0.0))  # a frozen clock isolates the backdate
    t.record("s-1", 1, "speech_start", backdate_ms=120.0)
    t.record("s-1", 1, "speech_end", backdate_ms=240.0)
    t.complete("s-1", 1, "completed")

    at = publisher.published[0][1]["at"]
    assert at["speech_start"] == pytest.approx(9.88)
    assert at["speech_end"] == pytest.approx(9.76)


def test_a_negative_backdate_is_refused():
    t, _ = tap()
    with pytest.raises(ValueError):
        t.record("s-1", 1, "speech_end", backdate_ms=-1.0)


def test_26_voice_to_voice_is_measured_from_true_end_of_speech():
    """ME4 — the name, the unit and the meaning: speech_end → first_audio_out, in ms."""
    t, publisher = tap(StepClock(start=0.0, step=0.0))
    t.record("s-1", 1, "speech_end", backdate_ms=240.0)  # true end of speech: -0.240
    t.record("s-1", 1, "first_audio_out")  # 0.000
    t.complete("s-1", 1, "completed")

    payload = publisher.published[0][1]
    assert payload["voice_to_voice_ms"] == 240
    assert "voice_to_voice_ms" in payload, "the metering contract key must not be renamed"


def test_26_a_late_stamped_speech_end_would_understate_voice_to_voice():
    """Why the backdate exists: the same turn measured without it is 240 ms too optimistic."""
    t, publisher = tap(StepClock(start=0.0, step=0.0))
    t.record("s-1", 1, "speech_end")  # recognised late, stamped now
    t.record("s-1", 1, "first_audio_out")
    t.complete("s-1", 1, "completed")

    assert publisher.published[0][1]["voice_to_voice_ms"] == 0


def test_the_additive_user_speech_stage_is_derived():
    t, publisher = tap(StepClock(start=0.0, step=0.0))
    t.record("s-1", 1, "speech_start", backdate_ms=1000.0)
    t.record("s-1", 1, "speech_end", backdate_ms=240.0)
    t.complete("s-1", 1, "completed")

    assert publisher.published[0][1]["ms"]["user_speech"] == 760


def test_user_speech_is_absent_when_the_start_was_never_recorded():
    """ME8 — an unreached boundary is an absent key, never a fabricated 0."""
    t, publisher = tap()
    t.record("s-1", 1, "speech_end")
    t.complete("s-1", 1, "completed")

    payload = publisher.published[0][1]
    assert "user_speech" not in payload["ms"]
    assert "speech_start" not in payload["at"]


def test_27_multiple_turns_produce_separate_metric_records():
    t, publisher = tap()
    for turn in (1, 2, 3):
        t.record("s-1", turn, "speech_start")
        t.record("s-1", turn, "speech_end")
        t.complete("s-1", turn, "completed")

    assert [turn for _, _, turn in publisher.published] == [1, 2, 3]
    assert t.in_flight == 0


@pytest.mark.asyncio
async def test_24_27_a_real_session_emits_one_metric_turn_carrying_the_audio_boundaries():
    # The clock must advance at least as fast as the audio it stamps; a 1 s step per read stands
    # in for real time passing between taps while 20 ms frames are fed in a burst.
    t, publisher = tap(StepClock(step=1.0))
    pipeline, transport, _, _ = build_pipeline([], latency=t)
    await run_pipeline(pipeline, transport, utterance())

    assert len(publisher.published) >= 1
    payload = publisher.published[0][1]
    assert {"speech_start", "speech_end", "endpoint_decided"} <= set(payload["at"])
    assert payload["at"]["speech_start"] <= payload["at"]["speech_end"]
    assert "user_speech" in payload["ms"]
    assert "endpoint" in payload["ms"]


@pytest.mark.asyncio
async def test_no_negative_duration_is_emitted_when_backdating_outruns_the_clock():
    """ME6 — burst-fed audio can backdate a boundary past the previous one. The interval is then
    **dropped with a warning, never emitted negative**; the raw instants stay in `at{}` (ME5)."""
    t, publisher = tap(StepClock(step=0.0))  # a frozen clock: only the backdates move instants
    pipeline, transport, _, _ = build_pipeline([], latency=t)
    await run_pipeline(pipeline, transport, utterance())

    payload = publisher.published[0][1]
    assert payload["at"]["speech_end"] < payload["at"]["speech_start"]
    assert "user_speech" not in payload["ms"], "a negative stage was emitted"
    assert all(value >= 0 for value in payload["ms"].values())


@pytest.mark.asyncio
async def test_29_the_metric_is_an_in_memory_event_and_writes_no_durable_spine():
    """G/R — M11.12 owns spine wiring; M11.8 publishes through the same in-process publisher."""
    t, publisher = tap()
    pipeline, transport, _, _ = build_pipeline([], latency=t)
    await run_pipeline(pipeline, transport, utterance())

    from core.events import EventType

    assert all(kind is EventType.METRIC_TURN for kind, _, _ in publisher.published)


# ── 21-23. provider neutrality ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_21_local_voice_activity_drives_endpointing_unchanged():
    """`LocalVoiceActivity` reads text payloads and declares `frame_ms = 0`, so the composition
    root's fallback duration is what the detector counts. Endpointing still works end to end."""
    from providers.voice_activity.local import LocalVoiceActivity

    recorder = BoundaryRecorder()
    provider = LocalVoiceActivity()
    assert provider.capabilities.frame_ms == 0
    pipeline, transport, _, _ = build_pipeline([], latency=recorder, vad=provider)
    pipeline._vad_frame_ms = 20.0  # noqa: SLF001 — what the Orchestrator supplies for frame_ms=0
    text = [AudioFrame(data=b"hello") for _ in range(SPEECH_FRAMES_TO_START)]
    quiet = [AudioFrame(data=b"   ") for _ in range(SILENCE_FRAMES_TO_END)]
    await run_pipeline(pipeline, transport, text + quiet)

    assert recorder.boundaries.count("speech_start") == 1
    assert recorder.boundaries.count("speech_end") == 1


def test_22_any_fake_provider_satisfies_the_detector_because_it_takes_booleans():
    """I — the detector's whole input surface is `speech: bool` + `duration_ms: float`."""
    signature = inspect.signature(AudioEndpointDetector.observe)
    assert list(signature.parameters) == ["self", "speech", "duration_ms"]
    assert signature.parameters["speech"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.asyncio
async def test_23_real_silero_drives_endpointing_on_synthesized_audio():
    """A smoke test over the real M11.7 provider: real verdicts, real boundaries — and **no
    accuracy claim**. It asserts only that the two subsystems compose."""
    from providers.voice_activity.silero import SileroVoiceActivity
    from tests.fake_speech_model import EnergyModel, pcm_window

    provider = SileroVoiceActivity(EnergyModel)  # the real M11.7 provider, deterministic model
    detector = AudioEndpointDetector()
    loud = AudioFrame(data=pcm_window(20000), sample_rate=RATE, encoding=AudioEncoding.PCM16)
    quiet = AudioFrame(data=pcm_window(0), sample_rate=RATE, encoding=AudioEncoding.PCM16)
    window_ms = float(provider.capabilities.frame_ms)

    await provider.start(session_id=SESSION)
    signals = []
    try:
        for index, frame in enumerate([loud] * 10 + [quiet] * 12):
            provider.detect(frame, session_id=SESSION)
            # Inference is asynchronous by contract (M11.7 SD7), so let the window this frame
            # completed actually finish before reading the verdict the next frame returns.
            for _ in range(200_000):
                if provider.windows_analysed(SESSION) > index:
                    break
                await asyncio.sleep(0)
            # Reading the latest verdict with an empty frame adds no audio of its own.
            verdict = provider.detect(
                AudioFrame(data=b"", sample_rate=RATE, encoding=AudioEncoding.PCM16),
                session_id=SESSION,
            )
            signal = detector.observe(speech=verdict.speech, duration_ms=window_ms)
            if signal:
                signals.append(signal)
    finally:
        await provider.stop(session_id=SESSION)

    # The two subsystems compose: real verdicts produced one start and one end. Nothing here
    # asserts how accurately the model classified anything.
    assert kinds(signals) == [AudioEndpointKind.SPEECH_START, AudioEndpointKind.SPEECH_END]


# ── configuration activation (AR-25) ────────────────────────────────────────


def _registry(*, with_vad: bool):
    import bootstrap

    return bootstrap.build_local_registry(include_voice_activity=with_vad)


def _spec(**kwargs):
    from runtime.contracts import AgentSpec

    return AgentSpec(system_prompt="p", sample_rate=RATE, **kwargs)


def test_audio_endpointing_without_a_vad_provider_is_refused_at_start():
    """AR-25 — accepted-but-inert configuration is a session that should never start, the same
    stance `allow_interrupt` already takes."""
    from core.errors import ConfigError
    from runtime.faults import require_valid_agent_spec

    with pytest.raises(ConfigError, match="audio_endpointing"):
        require_valid_agent_spec(_spec(audio_endpointing=True), _registry(with_vad=False))


def test_audio_endpointing_with_a_vad_provider_validates():
    from runtime.faults import validate_agent_spec

    issues = validate_agent_spec(_spec(audio_endpointing=True), _registry(with_vad=True))
    assert [i for i in issues if i.field == "audio_endpointing"] == []


def test_the_default_session_does_not_enable_audio_endpointing():
    from runtime.faults import validate_agent_spec

    assert _spec().audio_endpointing is False
    assert validate_agent_spec(_spec(), _registry(with_vad=False)) == ()


def test_the_request_field_reaches_the_runtime_spec():
    from core.session_control import StartSessionRequest

    request = StartSessionRequest(
        session_id=SESSION, org_id="o", agent_id="a", channel="web",
        system_prompt="p", audio_endpointing=True,
    )
    assert request.audio_endpointing is True


@pytest.mark.asyncio
async def test_a_configured_session_wires_the_detector_and_the_audio_driven_policy():
    import bootstrap
    from core.session_control import StartSessionRequest
    from runtime.registry import CapabilityKind
    from runtime.service import RuntimeSessionService

    registry = bootstrap.build_local_registry(include_voice_activity=True)
    registry.unregister(CapabilityKind.VOICE_ACTIVITY, "local")
    registry.register(CapabilityKind.VOICE_ACTIVITY, ScriptedVAD(), default=True)
    service = RuntimeSessionService(registry)
    await service.start(
        StartSessionRequest(
            session_id="ses_cfg", org_id="o", agent_id="a", channel="web",
            system_prompt="p", sample_rate=RATE, audio_endpointing=True,
        )
    )
    worker = service._workers["ses_cfg"]  # noqa: SLF001
    pipeline = worker.orchestrator._pipeline  # noqa: SLF001
    try:
        assert isinstance(pipeline._audio_endpoint, AudioEndpointDetector)  # noqa: SLF001
        assert pipeline._vad_frame_ms == 20  # noqa: SLF001 — the provider's declared frame_ms
        assert worker.orchestrator._endpoint_engine.policy.audio_driven is True  # noqa: SLF001
    finally:
        await service.end("ses_cfg")


@pytest.mark.asyncio
async def test_an_unconfigured_session_keeps_the_segment_driven_behaviour():
    import bootstrap
    from core.session_control import StartSessionRequest
    from runtime.service import RuntimeSessionService

    service = RuntimeSessionService(bootstrap.build_local_registry(include_voice_activity=True))
    await service.start(
        StartSessionRequest(
            session_id="ses_seg", org_id="o", agent_id="a", channel="web",
            system_prompt="p", sample_rate=RATE,
        )
    )
    worker = service._workers["ses_seg"]  # noqa: SLF001
    try:
        assert worker.orchestrator._pipeline._audio_endpoint is None  # noqa: SLF001
        assert worker.orchestrator._endpoint_engine.policy.audio_driven is False  # noqa: SLF001
    finally:
        await service.end("ses_seg")


# ── R. architecture ─────────────────────────────────────────────────────────

_ENDPOINT_DIR = pathlib.Path(inspect.getfile(audio_module)).parent
_PLATFORM_ROOT = _ENDPOINT_DIR.parent.parent


def _imports(path: pathlib.Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
    return roots


_VENDOR = {"pysilero_vad", "silero_vad", "deepgram", "aiortc", "onnxruntime", "torch", "pipecat"}


def test_endpointing_is_provider_neutral_and_vendor_free():
    """I/R — no model, no vendor, no provider type reaches the endpointing layer."""
    for path in _ENDPOINT_DIR.glob("*.py"):
        roots = _imports(path)
        assert not roots & _VENDOR, (path.name, roots & _VENDOR)
        # Structural, not textual: the module docstring legitimately *mentions* a provider to say
        # how the defaults relate to its window size, and a guard that cannot tell prose from code
        # fails on its own documentation — a trap this repository has sprung before.
        identifiers = {
            n.id if isinstance(n, ast.Name) else n.attr
            for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(n, ast.Name | ast.Attribute)
        }
        leaked = {
            name
            for name in identifiers
            if any(vendor in name.lower() for vendor in ("silero", "deepgram", "aiortc"))
        }
        assert leaked == set(), (path.name, leaked)
        assert "probability" not in identifiers, path.name


def test_the_endpoint_layer_reaches_only_core_and_its_own_package():
    for path in _ENDPOINT_DIR.glob("*.py"):
        forbidden = {"providers", "api", "billing", "observability", "events", "provider"}
        assert not _imports(path) & forbidden, path.name


def test_the_detector_starts_no_task_and_holds_no_queue():
    """K — bounded by construction: no queue, no buffer, no task."""
    tree = ast.parse(pathlib.Path(inspect.getfile(audio_module)).read_text(encoding="utf-8"))
    called = {
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert not called & {"Queue", "deque", "create_task", "ensure_future", "run_in_executor"}
    assert not any(isinstance(n, ast.Await) for n in ast.walk(tree)), "the detector awaits"


def test_the_detector_invokes_no_llm_tts_or_transport():
    tree = ast.parse(pathlib.Path(inspect.getfile(audio_module)).read_text(encoding="utf-8"))
    called = {
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert not called & {"stream", "synthesize", "transcribe", "send", "publish", "record"}


def test_there_is_exactly_one_turn_state_machine():
    """E — the detector has its own *utterance* states; turn phases stay the Turn Engine's."""
    from runtime.turn.phase import TURN_TRANSITIONS

    owners = []
    for package in ("runtime", "core"):
        for path in (_PLATFORM_ROOT / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if "TURN_TRANSITIONS" in path.read_text(encoding="utf-8") and path.name == "phase.py":
                owners.append(path)
    assert len(owners) == 1, owners
    assert set(TURN_TRANSITIONS) and "TurnPhase" not in _imports(_ENDPOINT_DIR / "audio.py")


def test_the_endpoint_layer_writes_nothing_to_the_epic10_spine():
    """R — no durable event write: the endpointing layer imports no `events` package at all."""
    for path in _ENDPOINT_DIR.glob("*.py"):
        assert "events" not in _imports(path) or path.name == "events.py", path.name


# ── ME9. a VAD-off profile yields no measurement, never a wrong one ──────────


@pytest.mark.asyncio
async def test_me9_a_vad_off_session_produces_no_voice_to_voice_measurement():
    """ADR-0047 ME9 — `speech_end` exists only when voice activity participates, so a VAD-off
    profile yields an **absent measurement**, which is the honest outcome (ME8), not a zero."""
    from providers.stt.local import LocalSTT
    from providers.transport.local import LocalTransport
    from runtime.pipeline.pipeline import RuntimePipeline

    t, publisher = tap()
    transport = LocalTransport()
    pipeline = RuntimePipeline(
        transport=transport,
        stt=LocalSTT(),
        llm=StubLLM(),
        tts=StubTTS(),
        session_id="ses_novad",
        system_prompt="p",
        sample_rate=RATE,
        latency=t,  # no voice_activity, no audio_endpoint: the shipped local default
    )
    await pipeline.start()
    await transport.feed("ses_novad", AudioFrame(data=b"hello"))
    await transport.hangup("ses_novad")
    await pipeline.run()
    await pipeline.stop()

    payload = publisher.published[0][1]
    assert "speech_end" not in payload["at"]
    assert payload["voice_to_voice_ms"] is None, "a measurement was invented without a VAD"


def test_me9_the_metering_buffer_counts_that_as_missing_measurement():
    """The other half of ME9: the absence is **countable**, so an operator can see that voice
    activity is not participating rather than reading a silently wrong number."""
    from api.metering.metric_buffer import MetricTurnBuffer
    from core.events import Event, EventSource, EventType

    buffer = MetricTurnBuffer()
    buffer.record(
        Event(
            id="evt-1",
            type=EventType.METRIC_TURN,
            ts=1000.0,
            seq=1,
            session_id="ses_novad",
            source=EventSource.OBSERVABILITY,
            org_id="org-1",
            data={"status": "completed", "at": {}, "ms": {}, "voice_to_voice_ms": None},
        )
    )
    assert buffer.counters.missing_measurement == 1
    assert len(buffer) == 0, "an unmeasurable turn must not become a metering signal"

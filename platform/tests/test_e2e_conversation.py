"""The first complete voice conversation, end to end (M11.11).

Every test here drives the **production runtime** — `RuntimeSessionService` → `SessionWorker` →
`ConversationOrchestrator` → `RuntimePipeline` — and replaces only what sits *outside* the
platform: the vendor's socket, its recognizer, its model and its voice. Nothing bypasses the Turn
Engine, endpointing, `ProviderGuard`, `ProviderRegistry`, the clause detector, the Playback
Scheduler or the Playback Engine; if a test passes because a subsystem was skipped, it is not
evidence of anything and the mutants in `M11.11` would not die.

```
transport ─► VAD ─► endpointing ─► STT ─► Turn Engine ─► LLM
           ─► clauses ─► TTS ─► playback ─► transport
```

Hermetic throughout: no network, no credentials, no vendor SDK. The fakes simulate **provider
behaviour**, never runtime behaviour.
"""

from __future__ import annotations

import asyncio

import pytest

import bootstrap
from core.audio import AudioEncoding, AudioFrame
from core.providers.llm import TokenDelta
from core.providers.tts import TTSCapabilities, TTSProvider
from core.session_control import StartSessionRequest
from providers.llm.groq import GroqLLM
from providers.stt.deepgram import DeepgramSTT
from providers.tts.cartesia import CartesiaTTS
from providers.voice_activity.silero import SileroVoiceActivity
from runtime.registry import CapabilityKind
from runtime.service import RuntimeSessionService
from runtime.speech import PlaybackState

from .fake_completion import FakeCompletionClient
from .fake_conversation import (
    ConversationEndpoint,
    TurnWatcher,
    pcm_tone,
    scripted_recognizer,
    vad_drained,
)
from .fake_speech import FakeSpeechClient, pcm, spin
from .fake_speech_model import EnergyModel
from .fake_transcription import FakeTranscriptionClient

#: The session id every fixture in this module uses.
SESSION_ID = "ses_e2e"
SAMPLE_RATE = 16000
FRAME_BYTES = 640  # 20 ms of PCM16 at 16 kHz


def speech(seq: int, *, loud: bool = True) -> AudioFrame:
    """One 20 ms inbound frame.

    A loud frame's peak must clear the VAD's 0.5 threshold once the energy model scales it by
    32767 — 30000/32767 = 0.92, comfortably speech; silence is exactly zero."""
    return pcm_tone(30000 if loud else 0, nbytes=FRAME_BYTES, seq=seq, rate=SAMPLE_RATE)


def real_stack(
    *,
    words: list[str],
    reply: list[str],
    tts_chunks: list[bytes] | None = None,
    frames: list[AudioFrame] | None = None,
    allow_interruptions: bool = False,
    audio_endpointing: bool = True,
    first_message: str | None = None,
    transcripts: list | None = None,
):
    """Assemble the **real** provider classes over deterministic vendor seams.

    Deliberately not `build_real_session_controller`: that one resolves credentials and opens
    sockets. This builds the same provider *classes* — `DeepgramSTT`, `SileroVoiceActivity`,
    `GroqLLM`, `CartesiaTTS`, the WebRTC transport — over fakes standing in for the vendors, so
    the runtime under test is the production one in every respect that matters."""
    endpoint = ConversationEndpoint()
    stt_client = FakeTranscriptionClient(responder=scripted_recognizer(words))
    registry = bootstrap.build_registry(
        transport=bootstrap.TRANSPORT_WEBRTC,
        media_endpoint=endpoint,
        stt=bootstrap.STT_DEEPGRAM,
        stt_client=stt_client,
        voice_activity=bootstrap.VOICE_ACTIVITY_SILERO,
        speech_model_factory=EnergyModel,
        llm=bootstrap.LLM_GROQ,
        llm_client=FakeCompletionClient(reply),
        tts=bootstrap.TTS_CARTESIA,
        tts_client=FakeSpeechClient(tts_chunks or [pcm(FRAME_BYTES), pcm(FRAME_BYTES, fill=2)]),
        tts_voice="voice-1",
    )
    sink = None
    if transcripts is not None:

        def sink(session_id, role, text, final):  # noqa: ANN001
            transcripts.append((role, text, final))

    service = RuntimeSessionService(registry, on_transcript=sink, streaming_playback=True)
    request = StartSessionRequest(
        session_id="ses_e2e",
        org_id="o",
        agent_id="a",
        channel="web",
        system_prompt="be helpful",
        language="en",
        sample_rate=SAMPLE_RATE,
        allow_interruptions=allow_interruptions,
        audio_endpointing=audio_endpointing,
        first_message=first_message,
    )
    return service, request, endpoint, registry


#: Frames per utterance. The thresholds these clear are M11.8's real ones — 120 ms of speech to
#: open an utterance, 240 ms of trailing silence to close it — and the VAD analyses 32 ms windows
#: that lag the frames feeding them, so both counts are generous rather than exact. Spending more
#: audio than strictly necessary keeps the harness a test of the runtime rather than of how
#: finely it was tuned.
SPEECH_FRAMES = 12  # 240 ms
SILENCE_FRAMES = 22  # 440 ms


def turn_signals(service, request=None):
    """The watcher and VAD handle for a started session, created once and reused.

    Cached on the service so a test that speaks twice does not subscribe twice: each `TurnWatcher`
    is a distinct handler, so uncached use would accumulate subscriptions on the session's bus for
    no benefit."""
    session_id = (request.session_id if request is not None else SESSION_ID)
    cached = getattr(service, "_turn_signals", None)
    if cached is None:
        worker = service._workers[session_id]  # noqa: SLF001
        cached = (
            TurnWatcher(worker.orchestrator.event_bus),
            service._registry.resolve(CapabilityKind.VOICE_ACTIVITY),  # noqa: SLF001
            session_id,
        )
        service._turn_signals = cached  # noqa: SLF001
    return cached


async def run_turn(
    endpoint, *, start_seq: int = 0, pipeline=None, watcher=None, vad=None,
    session_id=SESSION_ID, service=None,
) -> int:
    """Speak one complete utterance and wait until the runtime has finished answering it.

    **M11.15 (closes R-70).** This used to wait a fixed number of event-loop spins for
    `pipeline._response_task`. That is a bet on scheduling, and an unwinnable one: the real
    `SileroVoiceActivity` runs inference on a **worker thread**, so the number of loop iterations
    between pushing audio and the turn finishing depends on the OS rather than on the runtime.
    M11.14 recorded the consequence as R-70 after a metering assertion failed once in a full-suite
    run and passed 25/25 in isolation.

    The spin budget is gone. With a `watcher` the harness waits for the runtime's **own**
    `turn.completed` / `turn.cancelled` event — the condition the assertions actually care about —
    under a generous deadline whose only job is to turn a hang into a failure."""
    seq = start_seq
    if watcher is None and service is not None:
        # Derive the runtime signals rather than falling back to a spin budget. M11.16.1 found the
        # last call sites still on that fallback: under the CPU contention a full-suite run
        # produces they are a bet on scheduling, exactly as R-70 described.
        watcher, vad, session_id = turn_signals(service)
    if watcher is not None:
        watcher.arm()

    async def deliver(frame) -> None:
        """Push one frame and let voice activity catch up before the next.

        **Pacing against the VAD rather than against the clock (M11.15, R-70/R-67).** A caller
        produces audio in real time, and `SileroVoiceActivity` analyses it on a worker thread, so
        a harness that pushes frames as fast as the loop allows makes every frame land against a
        *stale* verdict — the detector then never opens or never closes an utterance. A single
        `sleep(0)` per frame appears to work only while nothing else competes for the loop; with
        two concurrent sessions each yield hands control to the other session instead of to
        inference, and the lag becomes unbounded.

        Waiting on `buffered_ms` makes the lag exactly one frame, deterministically, regardless of
        how many sessions interleave or how loaded the machine is."""
        await endpoint.push(frame)
        if vad is not None:
            await vad_drained(vad, session_id)

    for _ in range(SPEECH_FRAMES):
        await deliver(speech(seq))
        seq += 1
    for _ in range(SILENCE_FRAMES):
        await deliver(speech(seq, loud=False))
        seq += 1
    if watcher is not None:
        await watcher.wait()
        if vad is not None:
            # The turn is over; the VAD may still be a window or two behind the caller's trailing
            # silence. Returning before it has caught up would make the *next* utterance land
            # against stale verdicts (R-70/R-67).
            await vad_drained(vad, session_id)
        return seq
    # No watcher: the caller is driving a fixture with no session bus and is asserting an
    # *absence*, so there is no completion to wait for. Deliberately not a spin budget standing
    # in for one — the M11.15 guard asserts none is left in this file.
    await spin(400)
    return seq


async def run_conversation(
    service, request, endpoint, *, utterances: int = 1, turn_events: bool = True
):
    """Start the session, speak `utterances` times, hang up, and wait for a clean stop.

    `turn_events=False` is for the one situation where the runtime's turn events are **not
    available by design**: ADR-0035 **DW4** says *"no subscriber may observe an event that is not
    durable"*, so while a durable sink is failing the bus dispatches nothing at all — including
    `turn.completed`. Waiting on it there would be waiting for something the contract forbids
    from arriving. Callers that inject a failing durable sink say so explicitly rather than having
    the harness guess."""
    await service.start(request)
    worker = service._workers[request.session_id]  # noqa: SLF001
    pipeline = worker.orchestrator._pipeline  # noqa: SLF001
    watcher = TurnWatcher(worker.orchestrator.event_bus) if turn_events else None
    vad = service._registry.resolve(CapabilityKind.VOICE_ACTIVITY)  # noqa: SLF001
    seq = 0
    for _ in range(utterances):
        seq = await run_turn(
            endpoint, start_seq=seq, pipeline=pipeline, watcher=watcher, vad=vad,
            session_id=request.session_id,
        )
    await endpoint.hangup()
    await worker.wait_closed()
    await service.end(request.session_id)
    return endpoint


# ── 1, 3–16: one complete turn through every stage ──────────────────────────


async def test_1_a_complete_one_turn_voice_conversation():
    """The milestone's headline: caller audio in, assistant audio out, through the real stack.

    Asserts at both ends *and* in the middle — the transcript proves STT and the turn path ran,
    and outbound PCM frames prove clause → TTS → scheduler → engine → transport ran."""
    transcripts: list = []
    service, request, endpoint, _ = real_stack(
        words=["hello there"], reply=["Good morning. ", "How can I help?"],
        transcripts=transcripts,
    )
    await run_conversation(service, request, endpoint)

    finals = [(role, text) for role, text, final in transcripts if final]
    assert ("user", "hello there") in finals
    assert ("agent", "Good morning. How can I help?") in finals
    outbound = endpoint.written
    assert outbound, "no assistant audio reached the transport"
    assert all(f.encoding is AudioEncoding.PCM16 for f in outbound)
    assert all(f.sample_rate == SAMPLE_RATE and f.channels == 1 for f in outbound)


async def test_2_a_complete_two_turn_conversation():
    """M11.11 is not complete if only one response can ever happen."""
    transcripts: list = []
    service, request, endpoint, _ = real_stack(
        words=["first question", "second question"],
        reply=["An answer. "],
        transcripts=transcripts,
    )
    await run_conversation(service, request, endpoint, utterances=2)

    user_finals = [t for r, t, f in transcripts if f and r == "user"]
    agent_finals = [t for r, t, f in transcripts if f and r == "agent"]
    assert user_finals == ["first question", "second question"]
    assert len(agent_finals) == 2, "the second user turn produced no assistant response"


async def test_3_to_5_inbound_audio_drives_vad_and_the_endpoint_lifecycle():
    """Transport → VAD → endpointing, observed through the real engines' own state."""
    service, request, endpoint, _ = real_stack(words=["hi"], reply=["Ok. "])
    await service.start(request)
    worker = service._workers["ses_e2e"]  # noqa: SLF001
    orchestrator = worker.orchestrator
    detector = orchestrator._pipeline._audio_endpoint  # noqa: SLF001
    assert detector is not None, "audio endpointing was not wired"

    # M11.15/R-70 applies here too, and this was the last place in the file still relying on a
    # spin budget: the test drives the detector directly rather than through `run_turn`, so the
    # pacing that fix introduced never reached it. Unpaced pushes make every frame land against a
    # stale VAD verdict, and the only thing hiding that was an idle machine — a full-suite run
    # with any concurrent CPU load is enough to break it, which is exactly how it surfaced.
    vad = service._registry.resolve(CapabilityKind.VOICE_ACTIVITY)  # noqa: SLF001
    seq = 0
    for _ in range(SPEECH_FRAMES):
        await endpoint.push(speech(seq))
        await vad_drained(vad, SESSION_ID)
        seq += 1
    assert detector.in_speech, "sustained speech did not open an utterance"

    for _ in range(SILENCE_FRAMES):
        await endpoint.push(speech(seq, loud=False))
        await vad_drained(vad, SESSION_ID)
        seq += 1
    assert detector.utterances >= 1, "trailing silence did not close the utterance"
    await endpoint.hangup()
    await worker.wait_closed()
    await service.end("ses_e2e")


# ── 6–13: each hand-off is observable ───────────────────────────────────────


async def test_6_to_9_the_endpoint_drives_llm_clauses_in_order():
    """STT final → LLM request → clause detection → TTS, with order preserved."""
    spoken: list[str] = []

    service, request, endpoint, registry = real_stack(
        words=["tell me"], reply=["First one. ", "Second one. ", "Third one."]
    )
    tts = registry.resolve(CapabilityKind.TTS)
    original = tts.synthesize

    def recording(text, *, voice=None, language="en"):
        spoken.append(text)
        return original(text, voice=voice, language=language)

    tts.synthesize = recording  # observe the real provider, do not replace it
    await run_conversation(service, request, endpoint)
    assert spoken == ["First one.", "Second one.", "Third one."], (
        "clauses did not reach TTS in source order"
    )


async def test_10_to_13_tts_audio_reaches_the_transport_in_frame_order():
    """Identifiable audio per clause proves ordering survives the whole outbound path."""
    service, request, endpoint, _ = real_stack(
        words=["go"],
        reply=["One. ", "Two."],
        tts_chunks=[pcm(FRAME_BYTES, fill=7)],
    )
    await run_conversation(service, request, endpoint)
    outbound = endpoint.written
    assert outbound, "no audio reached the transport"
    assert all(f.data[0] == 7 for f in outbound), "audio was altered on the way out"


async def test_14_to_16_audio_is_neither_lost_duplicated_nor_reordered():
    """Two clauses × two vendor chunks = four 20 ms frames, in exactly this order."""
    service, request, endpoint, _ = real_stack(
        words=["go"],
        reply=["One. ", "Two."],
        tts_chunks=[pcm(FRAME_BYTES, fill=1), pcm(FRAME_BYTES, fill=2)],
    )
    await run_conversation(service, request, endpoint)
    assert [f.data[0] for f in endpoint.written] == [1, 2, 1, 2]


async def test_one_utterance_produces_exactly_one_response_attempt():
    """§12's invariant, counted at the vendor: one completed user turn, one LLM request, one
    TTS request per clause, one playback of each frame.

    Counted at the provider seams rather than inferred from the transcript, because a duplicated
    request that produced identical text would be invisible in the transcript and audible to the
    caller as the assistant saying everything twice."""
    llm_client = FakeCompletionClient(["One answer. "])
    endpoint = ConversationEndpoint()
    registry = bootstrap.build_registry(
        transport=bootstrap.TRANSPORT_WEBRTC,
        media_endpoint=endpoint,
        stt=bootstrap.STT_DEEPGRAM,
        stt_client=FakeTranscriptionClient(responder=scripted_recognizer(["hello"])),
        voice_activity=bootstrap.VOICE_ACTIVITY_SILERO,
        speech_model_factory=EnergyModel,
        llm=bootstrap.LLM_GROQ,
        llm_client=llm_client,
        tts=bootstrap.TTS_CARTESIA,
        tts_client=FakeSpeechClient([pcm(FRAME_BYTES, fill=3)]),
        tts_voice="voice-1",
    )
    service = RuntimeSessionService(registry, streaming_playback=True)
    request = StartSessionRequest(
        session_id="ses_e2e", org_id="o", agent_id="a", channel="web",
        system_prompt="be helpful", language="en", sample_rate=SAMPLE_RATE,
        audio_endpointing=True,
    )
    await run_conversation(service, request, endpoint)
    assert len(llm_client.specs) == 1, (
        f"one utterance produced {len(llm_client.specs)} LLM requests"
    )
    tts_client = registry.resolve(CapabilityKind.TTS)._client  # noqa: SLF001
    assert len(tts_client.specs) == 1, (
        f"one clause produced {len(tts_client.specs)} synthesis requests"
    )
    assert len(endpoint.written) == 1, "the clause was played more than once"


async def test_two_utterances_produce_exactly_two_response_attempts():
    llm_client = FakeCompletionClient(["A reply. "])
    endpoint = ConversationEndpoint()
    registry = bootstrap.build_registry(
        transport=bootstrap.TRANSPORT_WEBRTC,
        media_endpoint=endpoint,
        stt=bootstrap.STT_DEEPGRAM,
        stt_client=FakeTranscriptionClient(responder=scripted_recognizer(["one", "two"])),
        voice_activity=bootstrap.VOICE_ACTIVITY_SILERO,
        speech_model_factory=EnergyModel,
        llm=bootstrap.LLM_GROQ,
        llm_client=llm_client,
        tts=bootstrap.TTS_CARTESIA,
        tts_client=FakeSpeechClient([pcm(FRAME_BYTES, fill=3)]),
        tts_voice="voice-1",
    )
    service = RuntimeSessionService(registry, streaming_playback=True)
    request = StartSessionRequest(
        session_id="ses_e2e", org_id="o", agent_id="a", channel="web",
        system_prompt="be helpful", language="en", sample_rate=SAMPLE_RATE,
        audio_endpointing=True,
    )
    await run_conversation(service, request, endpoint, utterances=2)
    assert len(llm_client.specs) == 2, (
        f"two utterances produced {len(llm_client.specs)} LLM requests"
    )


# ── 27–30: the metric boundaries, per response ──────────────────────────────


class Recorder:
    """A `LatencyObserver` recording boundaries per turn."""

    def __init__(self) -> None:
        self.by_turn: dict[int, list[str]] = {}
        self.completed: list[tuple[int, str]] = []

    def record(self, session_id, turn_index, boundary, *, backdate_ms: float = 0.0) -> None:
        self.by_turn.setdefault(turn_index, []).append(boundary)

    def complete(self, session_id, turn_index, status) -> None:
        self.completed.append((turn_index, status))


async def test_20_the_metric_chain_is_produced_by_the_real_path():
    """Every §A8 boundary is stamped, in the only order physically possible."""
    latency = Recorder()
    service, request, endpoint, _ = real_stack(words=["hello"], reply=["Hi there. "])
    await service.start(request)
    worker = service._workers["ses_e2e"]  # noqa: SLF001
    worker.orchestrator._pipeline._latency = latency  # noqa: SLF001
    await run_turn(endpoint, service=service)
    await endpoint.hangup()
    await worker.wait_closed()
    await service.end("ses_e2e")

    stamped = [b for boundaries in latency.by_turn.values() for b in boundaries]
    for boundary in ("speech_start", "speech_end", "stt_final", "llm_first_token",
                     "tts_first_audio", "first_audio_out"):
        assert boundary in stamped, f"{boundary} was never stamped"
    turn = next(b for b in latency.by_turn.values() if "tts_first_audio" in b)
    assert turn.index("tts_first_audio") < turn.index("first_audio_out")


async def test_27_metric_stamps_reset_per_response():
    """Turn 2 must stamp its own boundaries, not inherit turn 1's."""
    latency = Recorder()
    service, request, endpoint, _ = real_stack(
        words=["one", "two"], reply=["A reply. "]
    )
    await service.start(request)
    worker = service._workers["ses_e2e"]  # noqa: SLF001
    worker.orchestrator._pipeline._latency = latency  # noqa: SLF001
    seq = await run_turn(endpoint, service=service)
    await run_turn(endpoint, start_seq=seq, service=service)
    await endpoint.hangup()
    await worker.wait_closed()
    await service.end("ses_e2e")

    audible = [t for t, b in latency.by_turn.items() if "first_audio_out" in b]
    assert len(audible) >= 2, "only one turn ever stamped first_audio_out"


async def test_28_and_30_a_silent_response_stamps_no_audio_boundary():
    """No synthetic timing: a response producing no audio must stamp neither audio boundary."""

    class MuteTTS(TTSProvider):
        name = "mute"
        capabilities = TTSCapabilities(streaming=True, sample_rates=(SAMPLE_RATE,))

        async def synthesize(self, text, *, voice=None, language="en"):
            return
            yield  # pragma: no cover

    latency = Recorder()
    service, request, endpoint, registry = real_stack(words=["hello"], reply=["Hi. "])
    registry.unregister(CapabilityKind.TTS, "cartesia")
    registry.register(CapabilityKind.TTS, MuteTTS(), default=True)
    await service.start(request)
    worker = service._workers["ses_e2e"]  # noqa: SLF001
    worker.orchestrator._pipeline._latency = latency  # noqa: SLF001
    await run_turn(endpoint, service=service)
    await endpoint.hangup()
    await worker.wait_closed()
    await service.end("ses_e2e")

    stamped = [b for boundaries in latency.by_turn.values() for b in boundaries]
    assert "tts_first_audio" not in stamped
    assert "first_audio_out" not in stamped
    assert endpoint.written == []


# ── 18–21: barge-in ─────────────────────────────────────────────────────────


async def test_19_ordinary_speech_does_not_interrupt_when_interruptions_are_disabled():
    service, request, endpoint, _ = real_stack(
        words=["hello"], reply=["A long reply. "], allow_interruptions=False
    )
    await service.start(request)
    worker = service._workers["ses_e2e"]  # noqa: SLF001
    assert worker.orchestrator._pipeline._interrupt is None  # noqa: SLF001
    await run_turn(endpoint, service=service)
    await endpoint.hangup()
    await worker.wait_closed()
    await service.end("ses_e2e")
    assert endpoint.written, "the assistant was silenced without an interrupt path"


async def test_18_the_interrupt_path_is_wired_when_interruptions_are_allowed():
    service, request, endpoint, _ = real_stack(
        words=["hello"], reply=["A reply. "], allow_interruptions=True
    )
    await service.start(request)
    worker = service._workers["ses_e2e"]  # noqa: SLF001
    assert worker.orchestrator._pipeline._interrupt is not None  # noqa: SLF001
    await run_turn(endpoint, service=service)
    await endpoint.hangup()
    await worker.wait_closed()
    await service.end("ses_e2e")


async def test_20_and_21_a_barge_in_discards_stale_audio_and_the_next_turn_still_speaks():
    """Playback cancelled mid-response leaves nothing queued, and turn 2 is unaffected."""
    service, request, endpoint, _ = real_stack(
        words=["one", "two"], reply=["A reply. "], allow_interruptions=True
    )
    await service.start(request)
    worker = service._workers["ses_e2e"]  # noqa: SLF001
    playback = worker.orchestrator._pipeline._playback  # noqa: SLF001
    assert playback is not None
    seq = await run_turn(endpoint, service=service)
    await playback.abandon()
    assert playback.engine.state is PlaybackState.CANCELLED
    assert playback.engine.queued_frames == 0
    before = len(endpoint.written)
    await run_turn(endpoint, start_seq=seq, service=service)
    await endpoint.hangup()
    await worker.wait_closed()
    await service.end("ses_e2e")
    assert len(endpoint.written) > before, "the assistant never spoke again after a barge-in"


# ── 22–26: failure, cancellation, lifecycle ─────────────────────────────────


async def test_22_and_23_a_tts_failure_is_typed_and_recorded_through_the_existing_mechanism():
    from providers.tts.speech_stream import FailureKind

    service, request, endpoint, registry = real_stack(words=["hello"], reply=["Hi. "])
    tts = registry.resolve(CapabilityKind.TTS)
    tts._client = FakeSpeechClient(open_error=FailureKind.UNAVAILABLE)  # noqa: SLF001
    await service.start(request)
    worker = service._workers["ses_e2e"]  # noqa: SLF001
    await run_turn(endpoint, service=service)
    await endpoint.hangup()
    await worker.wait_closed()
    kinds = [d.kind.value for d in worker.orchestrator.degradations.history]
    assert "unavailable" in kinds, "a provider failure recorded no degradation"
    await service.end("ses_e2e")


async def test_the_streaming_input_degradation_is_recorded_once_per_response():
    """Cartesia declares `streaming_input=False`; AR-24 forbids adapting invisibly."""
    service, request, endpoint, _ = real_stack(words=["hello"], reply=["Hi. "])
    await service.start(request)
    worker = service._workers["ses_e2e"]  # noqa: SLF001
    await run_turn(endpoint, service=service)
    await endpoint.hangup()
    await worker.wait_closed()
    records = [
        d for d in worker.orchestrator.degradations.history
        if d.capability == "tts" and d.kind.value == "not_streaming"
    ]
    assert records, "the per-clause adaptation was not recorded"
    assert "cartesia" in records[0].provider
    await service.end("ses_e2e")


async def test_25_and_26_repeated_start_stop_leaks_no_tasks():
    """Three full sessions in a row must leave the event loop exactly as they found it."""
    before = len(asyncio.all_tasks())
    for _ in range(3):
        service, request, endpoint, _ = real_stack(words=["hi"], reply=["Ok. "])
        await run_conversation(service, request, endpoint)
    await spin()
    assert len(asyncio.all_tasks()) == before


async def test_24_session_stop_closes_provider_streams():
    service, request, endpoint, registry = real_stack(words=["hi"], reply=["Ok. "])
    await run_conversation(service, request, endpoint)
    tts = registry.resolve(CapabilityKind.TTS)
    for stream in tts._client.streams:  # noqa: SLF001
        assert stream.closed >= 1, "a vendor stream was left open"


# ── 31–33: the greeting ─────────────────────────────────────────────────────


async def test_31_and_32_a_greeting_plays_exactly_once_through_the_scheduler():
    service, request, endpoint, _ = real_stack(
        words=["hello"], reply=["Sure. "], first_message="Welcome to VoxVoice."
    )
    await service.start(request)
    worker = service._workers["ses_e2e"]  # noqa: SLF001
    playback = worker.orchestrator._pipeline._playback  # noqa: SLF001
    await spin()
    assert playback.greeting_played is True
    assert endpoint.written, "the greeting produced no outbound audio"
    greeted = len(endpoint.written)
    assert await playback.play_greeting() is False, "the greeting could play twice"
    await endpoint.hangup()
    await worker.wait_closed()
    await service.end("ses_e2e")
    assert len(endpoint.written) >= greeted


async def test_no_greeting_is_spoken_when_first_message_is_absent():
    service, request, endpoint, _ = real_stack(words=["hello"], reply=["Sure. "])
    await service.start(request)
    worker = service._workers["ses_e2e"]  # noqa: SLF001
    playback = worker.orchestrator._pipeline._playback  # noqa: SLF001
    await spin()
    assert playback.greeting_played is False
    assert endpoint.written == []
    await endpoint.hangup()
    await worker.wait_closed()
    await service.end("ses_e2e")


async def test_33_a_greeting_that_cannot_be_synthesized_does_not_end_the_session():
    from providers.tts.speech_stream import FailureKind

    service, request, endpoint, registry = real_stack(
        words=["hello"], reply=["Sure. "], first_message="Welcome."
    )
    registry.resolve(CapabilityKind.TTS)._client = FakeSpeechClient(  # noqa: SLF001
        open_error=FailureKind.UNAVAILABLE
    )
    await service.start(request)  # must not raise
    worker = service._workers["ses_e2e"]  # noqa: SLF001
    playback = worker.orchestrator._pipeline._playback  # noqa: SLF001
    assert playback.greeting_ready is False
    await endpoint.hangup()
    await worker.wait_closed()
    await service.end("ses_e2e")


# ── 34–36: composition honesty ──────────────────────────────────────────────


def test_34_the_real_profile_never_falls_back_to_a_local_provider():
    """Each real profile refuses without its credential — no quiet downgrade anywhere."""
    for kwargs, pattern in [
        ({"stt": bootstrap.STT_DEEPGRAM}, "refusing to fall back"),
        ({"llm": bootstrap.LLM_GROQ}, "refusing to fall back"),
        ({"tts": bootstrap.TTS_CARTESIA, "tts_voice": "v"}, "refusing to fall back"),
    ]:
        with pytest.raises(ValueError, match=pattern):
            bootstrap.build_registry(**kwargs)


def test_the_real_profile_names_every_real_provider():
    assert bootstrap.REAL_PROVIDER_PROFILE == {
        "transport": "webrtc",
        "stt": "deepgram",
        "voice_activity": "silero",
        "llm": "groq",
        "tts": "cartesia",
        "streaming_playback": True,
    }


def test_the_default_composition_remains_entirely_local():
    """M11.11 adds a real stack; it does not switch anyone onto it."""
    registry = bootstrap.build_registry()
    assert registry.resolve(CapabilityKind.STT).name == "local"
    assert registry.resolve(CapabilityKind.LLM).name == "echo"
    assert registry.resolve(CapabilityKind.TTS).name == "local"
    assert registry.resolve(CapabilityKind.TRANSPORT).name == "local"
    assert bootstrap.build_session_controller()._streaming_playback is False  # noqa: SLF001


def test_the_real_stack_uses_the_real_provider_classes():
    """The harness is not testing stand-ins: these are the production classes."""
    _, _, _, registry = real_stack(words=["x"], reply=["y. "])
    assert isinstance(registry.resolve(CapabilityKind.STT), DeepgramSTT)
    assert isinstance(registry.resolve(CapabilityKind.LLM), GroqLLM)
    assert isinstance(registry.resolve(CapabilityKind.TTS), CartesiaTTS)
    assert isinstance(registry.resolve(CapabilityKind.VOICE_ACTIVITY), SileroVoiceActivity)


def test_35_and_36_the_harness_needs_no_network_or_credential():
    """Asserted structurally over the AST, not over the file's text.

    A text scan of this module would match its own marker list, which is why the first version of
    this test failed on itself. Identifiers are the honest question anyway: does the harness
    *call* a credential resolver or open a socket?"""
    import ast
    import pathlib

    names: set[str] = set()
    for module in ("test_e2e_conversation.py", "fake_conversation.py"):
        tree = ast.parse((pathlib.Path(__file__).parent / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
    forbidden = {
        "socket", "httpx", "requests", "aiohttp", "websockets", "urllib",
        "EnvCredentialProvider", "ChainedCredentialProvider", "getenv", "environ",
        "cartesia", "groq", "deepgram", "pysilero_vad", "aiortc",
    }
    leaked = names & forbidden
    assert not leaked, f"the E2E harness reaches outside the process: {leaked}"


def test_a_token_delta_is_what_the_llm_seam_carries():
    """Guards the harness itself: if the LLM contract changed shape, these tests would be
    exercising something other than the real path."""
    assert TokenDelta(text="x").text == "x"

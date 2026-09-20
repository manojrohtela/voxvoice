"""The Playback Engine and Playback Scheduler (M11.10, ADR-0048 PB5–PB9).

Every case is a statement about audio: which frames reached the transport, in what order, how much
was held, and what happened when something was cancelled. Nothing sleeps and nothing is timed —
ordering and back-pressure are settled with events, because a test that waits a while and then
finds the right answer has not established why.

The frames carry identifying payloads (utterance index and position), so a reordering or a
duplication shows up in the assertion rather than merely being likely to.
"""

from __future__ import annotations

import asyncio

import pytest

from runtime.speech import (
    DEFAULT_CAPACITY_MS,
    Clause,
    ClauseBoundary,
    GreetingUnavailable,
    PlaybackEngine,
    PlaybackFailed,
    PlaybackScheduler,
    PlaybackState,
    frame_duration_ms,
)

from .fake_speech import (
    RecordingTransport,
    ScriptedTTS,
    SilentTTS,
    frame,
    pcm,
    settle,
    spin,
)


def clause(text: str, index: int = 0) -> Clause:
    return Clause(text=text, boundary=ClauseBoundary.PUNCTUATION, index=index)


async def engine_for(transport: RecordingTransport, **kwargs) -> PlaybackEngine:
    engine = PlaybackEngine(transport.send, **kwargs)
    await engine.start()
    return engine


# ── 17–19, 21, 22: frames reach the transport, in order, exactly once ────────


async def test_17_a_single_frame_reaches_the_transport():
    transport = RecordingTransport()
    engine = await engine_for(transport)
    await engine.enqueue(frame(pcm(640, fill=7)))
    await engine.drain()
    assert transport.fills == [7]
    assert engine.state is PlaybackState.COMPLETED
    assert engine.frames_sent == 1


async def test_18_multiple_frames_reach_the_transport():
    transport = RecordingTransport()
    engine = await engine_for(transport)
    for n in range(5):
        await engine.enqueue(frame(pcm(640, fill=n + 1)))
    await engine.drain()
    assert transport.fills == [1, 2, 3, 4, 5]


async def test_19_frame_order_is_exactly_enqueue_order():
    """A single consumer is what guarantees this; the assertion pins it."""
    transport = RecordingTransport()
    engine = await engine_for(transport, capacity_ms=10_000)
    for n in range(40):
        await engine.enqueue(frame(pcm(64, fill=(n % 250) + 1)))
    await engine.drain()
    assert transport.fills == [(n % 250) + 1 for n in range(40)]


async def test_21_no_frame_is_duplicated():
    transport = RecordingTransport()
    engine = await engine_for(transport)
    for n in range(10):
        await engine.enqueue(frame(pcm(640, fill=n + 1)))
    await engine.drain()
    assert len(transport.sent) == 10
    assert len(set(transport.fills)) == 10


async def test_22_no_outbound_audio_is_lost_under_a_full_buffer():
    """The invariant that separates back-pressure from silent loss: producing far more audio than
    the buffer holds still delivers **every** frame."""
    transport = RecordingTransport()
    engine = await engine_for(transport, capacity_ms=40.0)  # two 20 ms frames
    for n in range(60):
        await engine.enqueue(frame(pcm(640, fill=(n % 250) + 1)))
    await engine.drain()
    assert len(transport.sent) == 60
    assert transport.fills == [(n % 250) + 1 for n in range(60)]
    assert engine.frames_discarded == 0


# ── 23, 24: the buffer is bounded, and full means wait ──────────────────────


def test_frame_duration_is_computed_from_the_declared_format():
    assert frame_duration_ms(frame(pcm(640))) == pytest.approx(20.0)
    assert frame_duration_ms(frame(pcm(320), sample_rate=8000)) == pytest.approx(20.0)


def test_an_undeclared_frame_claims_no_duration():
    """An OPAQUE local-profile frame declares no format; inventing a duration would make the
    buffer's bound a fiction, so it counts as zero and the frame ceiling bounds it instead."""
    from core.audio import AudioFrame

    assert frame_duration_ms(AudioFrame(data=b"hello")) == 0.0


async def test_23_the_playback_buffer_is_bounded_in_milliseconds():
    """Held audio never exceeds the capacity, whatever the producer does."""
    gate = asyncio.Event()
    transport = RecordingTransport(gate=gate)
    engine = await engine_for(transport, capacity_ms=60.0)  # three 20 ms frames

    async def produce() -> None:
        for n in range(20):
            await engine.enqueue(frame(pcm(640, fill=n + 1)))

    task = asyncio.create_task(produce())
    assert await settle(lambda: engine.buffered_ms >= 60.0 or task.done())
    assert engine.buffered_ms <= 60.0
    assert not task.done(), "the producer was not back-pressured by a full buffer"
    gate.set()
    await task
    await engine.drain()
    assert len(transport.sent) == 20


async def test_24_a_slow_transport_back_pressures_the_producer():
    """A transport that accepts nothing must stop synthesis, not accumulate audio."""
    gate = asyncio.Event()
    transport = RecordingTransport(gate=gate)
    engine = await engine_for(transport, capacity_ms=40.0)
    produced = 0

    async def produce() -> None:
        nonlocal produced
        for n in range(30):
            await engine.enqueue(frame(pcm(640, fill=n + 1)))
            produced += 1

    task = asyncio.create_task(produce())
    # Let it fill: the buffer holds two 20 ms frames and the sender holds one more in flight,
    # so the producer legitimately gets a few ahead before it must wait.
    await spin()
    stalled = produced
    await spin()
    assert produced == stalled, "the producer ran ahead of a transport accepting nothing"
    assert not task.done()
    assert engine.buffered_ms <= 40.0
    gate.set()
    await task
    await engine.drain()
    assert len(transport.sent) == 30


async def test_a_frame_larger_than_the_whole_capacity_is_still_sent():
    """Refusing to speak would be worse than briefly exceeding the bound by one frame."""
    transport = RecordingTransport()
    engine = await engine_for(transport, capacity_ms=5.0)
    await engine.enqueue(frame(pcm(6400, fill=9)))  # 200 ms
    await engine.drain()
    assert transport.fills == [9]


async def test_the_frame_ceiling_bounds_audio_that_declares_no_duration():
    from core.audio import AudioFrame

    gate = asyncio.Event()
    transport = RecordingTransport(gate=gate)
    engine = await engine_for(transport, max_frames=4)

    async def produce() -> None:
        for _ in range(20):
            await engine.enqueue(AudioFrame(data=b"opaque"))

    task = asyncio.create_task(produce())
    assert await settle(lambda: engine.queued_frames >= 4 or task.done())
    assert engine.queued_frames <= 4
    assert not task.done()
    gate.set()
    await task
    await engine.drain()
    assert len(transport.sent) == 20


# ── 25–29: failure, cancellation, shutdown ──────────────────────────────────


async def test_25_a_transport_failure_surfaces_rather_than_being_swallowed():
    transport = RecordingTransport(error=RuntimeError("socket gone"), error_after=2)
    engine = await engine_for(transport)
    for n in range(5):
        await engine.enqueue(frame(pcm(640, fill=n + 1)))
    with pytest.raises(PlaybackFailed):
        await engine.drain()
    assert engine.state is PlaybackState.FAILED
    assert isinstance(engine.error, RuntimeError)


async def test_a_producer_waiting_on_a_failed_engine_is_released():
    """Otherwise a transport failure would hang synthesis forever on a sender that is gone."""
    transport = RecordingTransport(error=RuntimeError("socket gone"), error_after=0)
    engine = await engine_for(transport, capacity_ms=20.0)
    await engine.enqueue(frame(pcm(640, fill=1)))
    assert await settle(lambda: engine.state is PlaybackState.FAILED)
    with pytest.raises(PlaybackFailed):
        await engine.enqueue(frame(pcm(640, fill=2)))


async def test_26_cancellation_discards_queued_audio():
    gate = asyncio.Event()
    transport = RecordingTransport(gate=gate)
    engine = await engine_for(transport, capacity_ms=10_000)
    for n in range(8):
        await engine.enqueue(frame(pcm(640, fill=n + 1)))
    await engine.cancel()
    assert engine.state is PlaybackState.CANCELLED
    assert engine.frames_discarded == 8
    assert engine.queued_frames == 0
    gate.set()
    await spin()
    assert transport.sent == [], "queued audio was sent after cancellation"


async def test_27_cancellation_is_idempotent():
    """A second barge-in during the first must not raise."""
    engine = await engine_for(RecordingTransport())
    await engine.enqueue(frame(pcm(640)))
    await engine.cancel()
    await engine.cancel()
    assert engine.state is PlaybackState.CANCELLED


async def test_a_cancelled_engine_accepts_no_further_audio():
    transport = RecordingTransport()
    engine = await engine_for(transport)
    await engine.cancel()
    await engine.enqueue(frame(pcm(640, fill=5)))
    await spin()
    assert transport.sent == []


async def test_28_shutdown_leaves_nothing_running():
    engine = await engine_for(RecordingTransport())
    await engine.enqueue(frame(pcm(640)))
    await engine.stop()
    await engine.stop()
    assert engine.state is PlaybackState.CANCELLED


async def test_29_repeated_start_stop_leaks_no_task():
    """`start → stop` many times must not accumulate tasks (W: resource management)."""
    before = len(asyncio.all_tasks())
    transport = RecordingTransport()
    engine = PlaybackEngine(transport.send)
    for _ in range(10):
        await engine.start()
        await engine.enqueue(frame(pcm(640, fill=1)))
        await engine.stop()
    assert len(asyncio.all_tasks()) == before


async def test_draining_an_engine_that_never_started_completes():
    engine = PlaybackEngine(RecordingTransport().send)
    await engine.drain()
    assert engine.state is PlaybackState.COMPLETED


async def test_an_engine_without_a_route_refuses_to_start():
    with pytest.raises(RuntimeError, match="no send route"):
        await PlaybackEngine().start()


@pytest.mark.parametrize("kwargs", [{"capacity_ms": 0}, {"capacity_ms": -1}, {"max_frames": 0}])
def test_an_impossible_capacity_is_refused(kwargs):
    with pytest.raises(ValueError):
        PlaybackEngine(RecordingTransport().send, **kwargs)


def test_the_default_capacity_is_the_documented_one():
    assert DEFAULT_CAPACITY_MS == 400.0


def test_the_playback_states_are_the_documented_set():
    assert {s.value for s in PlaybackState} == {
        "idle",
        "playing",
        "draining",
        "completed",
        "cancelled",
        "failed",
    }


# ── the Scheduler: 30–38 ────────────────────────────────────────────────────


async def scheduler_for(tts, transport, **kwargs) -> PlaybackScheduler:
    engine = PlaybackEngine(transport.send, capacity_ms=kwargs.pop("capacity_ms", 10_000))
    scheduler = PlaybackScheduler(tts, engine, **kwargs)
    await engine.start()
    return scheduler


async def test_30_a_single_clause_becomes_audio():
    tts, transport = ScriptedTTS(frames_per_call=3), RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    await scheduler.clause(clause("Hello."))
    await scheduler.finish()
    assert tts.spoken == ["Hello."]
    assert len(transport.sent) == 3


async def test_31_multiple_clauses_become_audio():
    tts, transport = ScriptedTTS(frames_per_call=2), RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    for i, text in enumerate(["One.", "Two.", "Three."]):
        await scheduler.clause(clause(text, i))
    await scheduler.finish()
    assert tts.spoken == ["One.", "Two.", "Three."]
    assert len(transport.sent) == 6


async def test_32_and_34_clause_audio_never_overtakes_the_clause_before_it():
    """The milestone's ordering invariant, asserted on the frames themselves.

    `ScriptedTTS` stamps each frame with `utterance * 10 + position`, so clause 1's frames are
    11,12 and clause 2's are 21,22. Any overtaking or interleaving changes this list."""
    tts, transport = ScriptedTTS(frames_per_call=2), RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    for i, text in enumerate(["A.", "B.", "C."]):
        await scheduler.clause(clause(text, i))
    await scheduler.finish()
    assert transport.fills == [10, 11, 20, 21, 30, 31]


async def test_33_a_slow_first_clause_delays_the_second_rather_than_being_overtaken():
    """Clause 2 is not even synthesized until clause 1's audio is fully handed over."""
    gate, started = asyncio.Event(), asyncio.Event()
    tts = ScriptedTTS(frames_per_call=3, gate=gate, started=started)
    transport = RecordingTransport()
    scheduler = await scheduler_for(tts, transport)

    async def speak() -> None:
        await scheduler.clause(clause("First.", 0))
        await scheduler.clause(clause("Second.", 1))

    task = asyncio.create_task(speak())
    assert await settle(started.is_set)
    assert tts.spoken == ["First."], "the second clause was synthesized before the first finished"
    gate.set()
    await task
    await scheduler.finish()
    assert tts.spoken == ["First.", "Second."]
    assert transport.fills == [10, 11, 12, 20, 21, 22]


async def test_35_an_empty_clause_produces_no_audio_and_no_request():
    tts, transport = ScriptedTTS(), RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    await scheduler.clause(clause("   "))
    await scheduler.finish()
    assert tts.spoken == []
    assert transport.sent == []


async def test_36_a_clause_failure_surfaces_rather_than_being_swallowed():
    tts = ScriptedTTS(error=RuntimeError("tts exploded"), error_after=1)
    transport = RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    with pytest.raises(RuntimeError, match="tts exploded"):
        await scheduler.clause(clause("Boom."))


async def test_a_partly_spoken_clause_is_not_replayed_after_a_failure():
    """L: audio already played must never be repeated by a later failure."""
    tts = ScriptedTTS(frames_per_call=4, error=RuntimeError("cut off"), error_after=2)
    transport = RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    with pytest.raises(RuntimeError):
        await scheduler.clause(clause("Half."))
    await scheduler.engine.drain()
    assert transport.fills == [10, 11], "audio was replayed or duplicated after a failure"


async def test_37_cancellation_between_clauses_stops_the_rest():
    tts, transport = ScriptedTTS(frames_per_call=2), RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    await scheduler.clause(clause("Spoken.", 0))
    await scheduler.abandon()
    await scheduler.clause(clause("Never.", 1))
    assert tts.spoken == ["Spoken."]
    assert scheduler.clauses_spoken == 1


async def test_a_new_response_resets_a_cancelled_scheduler():
    """Without this the assistant would go permanently mute after its first barge-in."""
    tts, transport = ScriptedTTS(frames_per_call=1), RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    await scheduler.abandon()
    scheduler.begin_response()
    await scheduler.engine.start()
    await scheduler.clause(clause("After.", 0))
    await scheduler.finish()
    assert tts.spoken == ["After."]


async def test_38_the_scheduler_inherits_the_engines_bounded_queue():
    """There is no second queue in the Scheduler: clauses are spoken one at a time, so the only
    bound that can matter is the Engine's — asserted structurally, by there being no other."""
    gate = asyncio.Event()
    transport = RecordingTransport(gate=gate)
    tts = ScriptedTTS(frames_per_call=10)
    scheduler = await scheduler_for(tts, transport, capacity_ms=40.0)
    task = asyncio.create_task(scheduler.clause(clause("Long.")))
    assert await settle(lambda: scheduler.engine.buffered_ms >= 40.0 or task.done())
    assert scheduler.engine.buffered_ms <= 40.0
    gate.set()
    await task
    await scheduler.finish()
    assert len(transport.sent) == 10


async def test_a_silent_provider_produces_no_frames_and_no_first_audio():
    fired = []
    tts, transport = SilentTTS(), RecordingTransport()
    scheduler = await scheduler_for(tts, transport, on_first_audio=lambda: fired.append(1))
    await scheduler.clause(clause("Nothing."))
    await scheduler.finish()
    assert transport.sent == []
    assert fired == []


# ── 44, 45: no stale audio after an interrupt ───────────────────────────────


async def test_44_no_stale_audio_reaches_the_transport_after_an_interrupt():
    gate = asyncio.Event()
    transport = RecordingTransport(gate=gate)
    tts = ScriptedTTS(frames_per_call=6)
    scheduler = await scheduler_for(tts, transport, capacity_ms=10_000)
    await scheduler.clause(clause("Interrupted."))
    await scheduler.abandon()
    gate.set()
    await spin()
    assert transport.sent == [], "audio queued before the barge-in was still played"


async def test_a_barge_in_stops_the_scheduler_pulling_further_tts_audio():
    """Cancelling must stop *generation*, not merely stop delivery.

    A scheduler that kept draining the provider after a barge-in would pay for audio nobody will
    hear and hold the vendor stream open — invisible to any assertion about the transport, which
    is why this one asserts on what the provider was asked for."""
    gate, started = asyncio.Event(), asyncio.Event()
    tts = ScriptedTTS(frames_per_call=8, gate=gate, started=started)
    transport = RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    task = asyncio.create_task(scheduler.clause(clause("A long answer.")))
    assert await settle(started.is_set)
    await scheduler.abandon()
    gate.set()
    await task
    assert tts.yielded < 8, "the provider was drained to the end after a barge-in"
    assert transport.sent == []


async def test_45_a_cancelled_response_is_not_played_twice():
    tts, transport = ScriptedTTS(frames_per_call=2), RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    await scheduler.abandon()
    await scheduler.abandon()
    await scheduler.finish()
    assert transport.sent == []


# ── 46–52: the greeting ─────────────────────────────────────────────────────


async def test_46_an_absent_greeting_caches_nothing():
    tts, transport = ScriptedTTS(), RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    await scheduler.precache_greeting("")
    assert scheduler.greeting_ready is False
    assert await scheduler.play_greeting() is False
    assert tts.spoken == []


async def test_47_and_48_a_present_greeting_is_pre_cached_before_it_is_played():
    """The pre-cache is the point: synthesis happens *before* anyone is waiting to hear it."""
    tts, transport = ScriptedTTS(frames_per_call=3), RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    await scheduler.precache_greeting("Hello there.")
    assert scheduler.greeting_ready is True
    assert tts.spoken == ["Hello there."]
    assert transport.sent == [], "the greeting was played during pre-cache"
    assert await scheduler.play_greeting() is True
    await scheduler.finish()
    assert len(transport.sent) == 3


async def test_49_a_pre_cache_can_be_cancelled():
    gate, started = asyncio.Event(), asyncio.Event()
    tts = ScriptedTTS(frames_per_call=4, gate=gate, started=started)
    scheduler = await scheduler_for(tts, RecordingTransport())
    task = asyncio.create_task(scheduler.precache_greeting("Hello."))
    assert await settle(started.is_set)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert scheduler.greeting_ready is False


async def test_50_a_failed_pre_cache_is_typed_and_does_not_end_the_session():
    tts = ScriptedTTS(error=RuntimeError("vendor down"), error_after=0)
    scheduler = await scheduler_for(tts, RecordingTransport())
    with pytest.raises(GreetingUnavailable):
        await scheduler.precache_greeting("Hello.")
    assert scheduler.greeting_ready is False
    assert await scheduler.play_greeting() is False


async def test_a_greeting_that_produces_no_audio_is_unavailable_rather_than_silent():
    scheduler = await scheduler_for(SilentTTS(), RecordingTransport())
    with pytest.raises(GreetingUnavailable):
        await scheduler.precache_greeting("Hello.")


async def test_a_greeting_larger_than_its_capacity_is_refused():
    """Bounded: a misconfigured greeting cannot be held without limit."""
    tts = ScriptedTTS(frames_per_call=50)
    scheduler = await scheduler_for(tts, RecordingTransport(), greeting_capacity_ms=60.0)
    with pytest.raises(GreetingUnavailable, match="capacity"):
        await scheduler.precache_greeting("A very long greeting.")
    assert scheduler.greeting_ready is False


async def test_51_a_greeting_plays_exactly_once():
    tts, transport = ScriptedTTS(frames_per_call=2), RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    await scheduler.precache_greeting("Hi.")
    assert await scheduler.play_greeting() is True
    assert await scheduler.play_greeting() is False
    await scheduler.finish()
    assert len(transport.sent) == 2
    assert scheduler.greeting_played is True


async def test_52_no_greeting_is_played_unless_the_session_asks():
    """Pre-caching readies audio; it never starts playback on its own."""
    tts, transport = ScriptedTTS(frames_per_call=2), RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    await scheduler.precache_greeting("Hi.")
    await spin()
    assert transport.sent == [], "the greeting played without the session asking"


async def test_the_greeting_is_released_once_spoken():
    """Holding a session's greeting audio forever is the accumulation boundedness forbids."""
    tts, transport = ScriptedTTS(frames_per_call=2), RecordingTransport()
    scheduler = await scheduler_for(tts, transport)
    await scheduler.precache_greeting("Hi.")
    await scheduler.play_greeting()
    assert scheduler.greeting_ready is False


# ── the streaming-input degradation (ADR-0045 SD6 / ADR-0048 PB3) ───────────


async def test_a_provider_without_streaming_input_records_the_degradation():
    recorded: list[str] = []
    tts = ScriptedTTS(streaming_input=False)
    scheduler = await scheduler_for(
        tts, RecordingTransport(), on_degraded=recorded.append
    )
    scheduler.note_input_mode()
    assert recorded == [tts.name]


async def test_a_provider_with_streaming_input_records_nothing():
    recorded: list[str] = []
    tts = ScriptedTTS(streaming_input=True)
    scheduler = await scheduler_for(
        tts, RecordingTransport(), on_degraded=recorded.append
    )
    scheduler.note_input_mode()
    assert recorded == []

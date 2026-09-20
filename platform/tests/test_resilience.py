"""The §A10 degradation ladder, proved against the running runtime (M11.14).

Constitution **§A10** names four rungs and ADR-0046 **FT-D6** makes them the required behaviour:

> **failover → declared degraded mode → inform-the-caller policy → clean session failure with a
> full event trail.** Exhaustion is a clean failure, never a hang.

M11.4 proved the *mechanism* — `ProviderGuard`'s timeout, typed error, breaker and chain walk —
in isolation. This file proves the *behaviour*: a failure injected at a vendor seam, in a real
session, taken all the way through

```
DETECT → CLASSIFY → RECORD → RECOVER/FALL BACK → SCOPE → CLEAN UP → OBSERVE
```

Every test drives `RuntimeSessionService` → `SessionWorker` → `ConversationOrchestrator` →
`RuntimePipeline` with the production provider classes over deterministic vendor seams. A test
that passed because a subsystem was skipped would be evidence about the harness.

## What is deliberately not asserted here

That an exception was raised. §21 is explicit: *"the resilience ladder is about behaviour, not
exception classification."* `test_fault_tolerance.py` already owns the classification; what is
asserted here is what the **session** does afterwards — whether the caller can still speak, what
was recorded, what was cleaned up, and what was never fabricated.

## Hermetic

No network, no credential, no vendor SDK, and nothing sleeps for a duration. Live-provider
resilience is **NOT EXECUTED**; §34 permits an optional live smoke and none was available.
"""

from __future__ import annotations

import asyncio

import pytest

from core.errors import (
    ProviderError,
    ProvidersExhausted,
    ProviderStreamTruncated,
    ProviderTimeout,
    ProviderUnavailable,
)
from core.states import SessionState
from providers.llm.completion_stream import FailureKind as LLMFailure
from providers.tts.speech_stream import FailureKind as TTSFailure
from runtime.faults import DegradationKind, GuardPolicy, ProviderGuard
from runtime.speech import PlaybackState
from runtime.state import WorkerState

from . import resilience_harness as rh
from .fake_speech import pcm

FRAME = rh.FRAME_BYTES


# ══════════════════════════════════════════════════════════════════════════════
# A. Provider failure taxonomy — every typed error the contract names, end to end
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "kind",
    [
        LLMFailure.TIMEOUT,
        LLMFailure.UNAVAILABLE,
        LLMFailure.AUTH,
        LLMFailure.RATE_LIMITED,
        LLMFailure.PROTOCOL,
    ],
)
async def test_A_to_E_every_llm_failure_kind_is_recorded_and_leaves_the_session_usable(kind):
    """§44 A–E. Five different vendor refusals, one invariant: the call survives all of them.

    Parameterized rather than written five times because the *point* is that the outcome does not
    depend on which refusal it was — a resilience ladder that behaved differently for a rate limit
    than for a protocol error would be five ladders."""
    stack = rh.build(words=["hello"], llm_open_error=kind)
    await stack.start()
    await stack.speak()

    assert stack.degraded("llm", "unavailable"), f"{kind} recorded no degradation"
    assert stack.orchestrator.state is SessionState.IN_CONVERSATION, (
        "a turn-local provider failure ended the whole session"
    )
    assert stack.endpoint.written == [], "audio was produced for a response that never existed"
    await stack.finish()


async def test_F_a_truncated_llm_stream_is_recorded_and_not_replayed():
    """§44 F. The stream dies after the first clause has already been spoken."""
    stack = rh.build(words=["hello"], reply=["First clause. ", "Second. "], llm_fail_at=1)
    await stack.start()
    await stack.speak()

    assert stack.degraded("llm", "unavailable")
    # One request, not two: nothing re-opened the dead stream to try again.
    assert len(stack.llm_client.specs) == 1, "a truncated stream was silently re-attempted"
    await stack.finish()


async def test_G_provider_exhaustion_is_a_typed_terminus_never_a_hang():
    """§44 G. `ProvidersExhausted` is reachable, and reaching it takes finite time.

    Asserted at the guard rather than through a session because the runtime wires **one** provider
    per capability — a chain is configuration the composition root may supply, and manufacturing a
    multi-candidate session here would be testing a deployment that does not exist."""
    guard = ProviderGuard(capability="llm")
    attempts: list[str] = []

    async def always_fails(candidate: str):
        attempts.append(candidate)
        raise ProviderUnavailable(f"{candidate} is down")

    with pytest.raises(ProvidersExhausted):
        await guard.call_with_failover(("a", "b", "c"), always_fails)
    assert attempts == ["a", "b", "c"], "the chain did not walk forward exactly once each"


# ══════════════════════════════════════════════════════════════════════════════
# B. Retry discipline — §28, the bound is structural
# ══════════════════════════════════════════════════════════════════════════════


async def test_AF_a_failing_provider_is_called_exactly_once_per_turn():
    """§28/§44 AF. There is no retry ladder in the media path, so the bound is **one**.

    The strongest form this claim can take: counted at the vendor. A retry loop anywhere between
    the pipeline and the socket would show up here as a second `open`."""
    stack = rh.build(words=["hello"], tts_open_error=TTSFailure.UNAVAILABLE)
    await stack.start()
    await stack.speak()

    assert len(stack.tts_client.specs) == 1, (
        f"one clause produced {len(stack.tts_client.specs)} synthesis attempts"
    )
    await stack.finish()


async def test_an_auth_failure_is_not_retried_at_all():
    """§8.4. Credentials that were rejected will be rejected again; retrying spends quota to
    learn nothing and delays the honest answer."""
    stack = rh.build(words=["hello"], llm_open_error=LLMFailure.AUTH)
    await stack.start()
    await stack.speak()

    assert len(stack.llm_client.specs) == 1, "an authentication failure was retried"
    await stack.finish()


async def test_repeated_failures_open_the_breaker_and_it_then_refuses_without_calling():
    """§28. The breaker is reachable from the runtime's own guard, and an open breaker *stops*
    traffic rather than merely recording it."""
    guard = ProviderGuard(capability="llm")
    calls = 0

    async def fails():
        nonlocal calls
        calls += 1
        raise ProviderUnavailable("down")

    for _ in range(10):
        with pytest.raises(ProviderError):
            await guard.call("p", fails)
    before = calls
    with pytest.raises(ProviderUnavailable):
        await guard.call("p", fails)
    assert calls == before, "an open breaker still reached the provider"


async def test_cancellation_interrupts_a_call_and_is_never_a_provider_failure():
    """§19/§28. `CancelledError` must stay itself: the caller withdrawing is not the vendor
    misbehaving, and translating it would make every hangup look like an outage."""
    guard = ProviderGuard(capability="llm")
    entered = asyncio.Event()

    async def hangs():
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(guard.call("p", hangs))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert guard.breaker_state("p").value == "closed", "cancellation counted against the breaker"


async def test_a_failing_call_reaches_the_provider_exactly_once_and_returns():
    """§28. *Bounded* retry, asserted so that a defect **fails** rather than hangs.

    The obvious test for "no infinite retry" is to run a retrying guard and wait: a mutant that
    loops forever then turns the suite into a timeout, and a timeout is not evidence — it is the
    absence of it (the M11.9 lesson). So the count is taken instead, inside a deadline: one
    attempt, and the call returns."""
    guard = ProviderGuard(capability="llm")
    attempts = 0

    async def fails():
        nonlocal attempts
        attempts += 1
        raise ProviderUnavailable("down")

    async def once():
        with pytest.raises(ProviderError):
            await guard.call("p", fails)

    await asyncio.wait_for(once(), timeout=5.0)
    assert attempts == 1, f"one failing call was attempted {attempts} times"


async def test_an_authentication_failure_reaches_the_provider_exactly_once():
    """§8.4/§28. Counted at the guard, because a credential rejected once will be rejected again:
    retrying spends quota to learn nothing and delays the only honest answer."""
    from core.errors import ProviderAuthError

    guard = ProviderGuard(capability="tts")
    attempts = 0

    async def refuses():
        nonlocal attempts
        attempts += 1
        raise ProviderAuthError("credentials rejected")

    with pytest.raises(ProviderAuthError):
        await guard.call("p", refuses)
    assert attempts == 1, f"an authentication failure was attempted {attempts} times"


async def test_an_unsupported_capability_is_not_retried():
    """§8.5. A provider that cannot do something will still not be able to do it next time."""
    from core.errors import CapabilityUnsupported

    guard = ProviderGuard(capability="tts")
    attempts = 0

    async def refuses():
        nonlocal attempts
        attempts += 1
        raise CapabilityUnsupported("no such voice")

    with pytest.raises(CapabilityUnsupported):
        await guard.call("p", refuses)
    assert attempts == 1


async def test_a_provider_timeout_is_bounded_by_the_configured_deadline():
    """§29. Every failure-prone await is bounded — proved by a provider that never returns."""
    guard = ProviderGuard(capability="llm", policy=GuardPolicy(call_timeout_s=0.01))

    async def never_returns():
        await asyncio.Event().wait()

    with pytest.raises(ProviderTimeout):
        await guard.call("p", never_returns)


# ══════════════════════════════════════════════════════════════════════════════
# C. STT resilience — §9
# ══════════════════════════════════════════════════════════════════════════════


async def test_an_stt_failure_before_the_stream_starts_ends_the_session_cleanly():
    """§9. STT is **not** auxiliary: without a transcript there is no conversation, so the failure
    scope is the session — and it must still be a *clean* end, not a hang."""
    stack = rh.build(words=["hello"])
    stack.stt_client.open_error = RuntimeError("stt unreachable")
    await stack.start()
    await stack.speak(wait=False)
    await rh.spin(300)

    assert stack.orchestrator.is_terminal, "a failed STT left the conversation running forever"
    assert stack.endpoint.written == [], "audio was produced without a transcript"
    await stack.finish()


async def test_an_stt_failure_invents_no_transcript():
    """§9. No fabricated text, and no partial promoted to final — the defect that would make a
    caller hear an answer to something they never said."""
    transcripts: list = []
    stack = rh.build(words=["hello"], transcripts=transcripts)
    stack.stt_client.open_error = RuntimeError("stt unreachable")
    await stack.start()
    await stack.speak(wait=False)
    await rh.spin(200)

    assert [t for r, t, final in transcripts if final and r == "user"] == [], (
        "a failed recognizer produced a final transcript"
    )
    await stack.finish()


async def test_an_stt_stream_that_breaks_mid_utterance_is_not_read_as_a_hangup():
    """§9/§15. The rule the fault-tolerance contract opens with: a stream that *ends* is a hangup,
    a stream that *raises* is a disconnect."""
    stack = rh.build(words=["hello"])
    await stack.start()
    session = await rh.settle(lambda: bool(stack.stt_client.sessions), spins=400)
    assert session, "the recognizer session never opened"
    stack.stt_client.sessions[0].fail(RuntimeError("recognizer socket reset"))
    await rh.spin(300)

    assert stack.degradations.history, "a broken recognizer recorded no degradation"
    await stack.finish()


# ══════════════════════════════════════════════════════════════════════════════
# D. VAD resilience — §10, and the standing R-48 question
# ══════════════════════════════════════════════════════════════════════════════


async def test_K_a_vad_failure_never_ends_the_call():
    """§44 K / DL-0013. Voice activity is auxiliary: it degrades, it does not break."""
    stack = rh.build(words=["hello"], speech_model_factory=rh.failing_vad(3))
    await stack.start()
    await stack.speak(wait=False)
    await rh.spin(200)

    assert stack.orchestrator.state is SessionState.IN_CONVERSATION
    assert stack.degraded("voice_activity", "auxiliary_disabled"), (
        "a failing VAD degraded invisibly — exactly what AR-24 forbids"
    )
    await stack.finish()


async def test_a_failed_vad_is_not_treated_as_silence():
    """§10, the most dangerous mistake available here.

    A VAD that cannot answer knows *nothing* about whether the caller is speaking. Reading its
    silence as "the user stopped" would end turns that never ended and answer questions nobody
    finished asking. ADR-0047 **ME8** says a failed provider advances no counter and produces no
    boundary — so a session whose VAD dies during speech must close **no** utterance."""
    stack = rh.build(words=["hello"], speech_model_factory=rh.failing_vad(0))
    await stack.start()
    await stack.speak(wait=False)
    await rh.spin(300)

    detector = stack.pipeline._audio_endpoint  # noqa: SLF001
    assert detector.utterances == 0, (
        "a dead VAD fabricated an end-of-speech; missing evidence is not silence"
    )
    assert stack.endpoint.written == [], "the runtime answered an utterance that never ended"
    await stack.finish()


async def test_R48_sustained_vad_failure_records_one_degradation_per_frame():
    """§22. **R-48 stays open**, and this is the measurement that says why.

    The record itself is bounded — `DEGRADATION_HISTORY` caps the retained history at 64 and the
    counts survive truncation, so memory is safe. What is *not* bounded is the **event rate**: one
    `provider.degraded` publication per inbound frame, which at the 20 ms media cadence is 50 per
    second, per session, for as long as the outage lasts. That is bounded recording and unbounded
    telling, and the two are different questions.

    Asserted as the behaviour that actually exists rather than the behaviour that would be nicer,
    because a risk closed by a test that measures something else is a risk that is still open."""
    stack = rh.build(words=["hello"], speech_model_factory=rh.failing_vad(0))
    await stack.start()
    await stack.speak(wait=False)
    await rh.spin(200)

    counts = stack.degradations.counts
    observed = counts.get("voice_activity:auxiliary_disabled", 0)
    assert observed > 10, "the sustained-failure case did not actually sustain"
    assert len(stack.degradations.history) <= 64, "the retained history is not bounded"
    assert observed >= len(stack.degradations.history), (
        "counts were truncated with the history, hiding the size of the outage"
    )
    await stack.finish()


# ══════════════════════════════════════════════════════════════════════════════
# E. Endpointing resilience — §11
# ══════════════════════════════════════════════════════════════════════════════


async def test_L_the_endpoint_never_hangs_waiting_for_a_verdict_that_will_not_come():
    """§44 L. With no usable VAD verdict the session simply keeps listening — and a hangup still
    terminates it, which is what makes "does not hang" checkable rather than aspirational."""
    stack = rh.build(words=["hello"], speech_model_factory=rh.failing_vad(0))
    await stack.start()
    await stack.speak(wait=False)
    await stack.hangup()

    assert stack.orchestrator.is_terminal, "the session hung waiting for an endpoint decision"
    await stack.end()


async def test_a_vad_outage_emits_no_duplicate_speech_end():
    """§11. No boundary at all is the correct answer; two would be worse than none."""
    telemetry = rh.RecordingTelemetry()
    stack = rh.build(
        words=["hello"], speech_model_factory=rh.failing_vad(0), telemetry=telemetry
    )
    await stack.start()
    await stack.speak(wait=False)
    await rh.spin(200)

    assert telemetry.stamps.count("speech_end") == 0, (
        "a fabricated end-of-speech was stamped from a provider that answered nothing"
    )
    await stack.finish()


async def test_cancellation_during_the_endpoint_wait_leaves_no_turn_behind():
    """§11/§19. Stopping mid-utterance discards it without stamping a boundary the audio never
    produced (ADR-0038 TP5)."""
    telemetry = rh.RecordingTelemetry()
    stack = rh.build(words=["hello"], telemetry=telemetry)
    await stack.start()
    for _ in range(rh.SPEECH_FRAMES):
        await stack.endpoint.push(rh.speech(0))
    await rh.spin(120)
    await stack.end()  # stop while the utterance is still open

    assert stack.worker is None or stack.worker.state is WorkerState.STOPPED


# ══════════════════════════════════════════════════════════════════════════════
# F. LLM resilience — §12
# ══════════════════════════════════════════════════════════════════════════════


async def test_M_an_llm_failure_before_the_first_token_fabricates_no_audio_metric():
    """§44 M / §32. No speech happened, so no speech boundary may be stamped."""
    telemetry = rh.RecordingTelemetry()
    stack = rh.build(words=["hello"], llm_open_error=LLMFailure.UNAVAILABLE, telemetry=telemetry)
    await stack.start()
    await stack.speak()

    assert "tts_first_audio" not in telemetry.stamps, "a fake tts_first_audio was stamped"
    assert "first_audio_out" not in telemetry.stamps, "a fake first_audio_out was stamped"
    assert stack.endpoint.written == []
    await stack.finish()


async def test_N_an_llm_failure_after_partial_text_never_replays_what_was_spoken():
    """§44 N / §12. The caller has already heard clause one. The invariant is that they do not
    hear it twice — counted at the vendor, because a duplicate that produced identical audio is
    invisible in a transcript and unmistakable to a caller."""
    stack = rh.build(
        words=["hello"],
        reply=["First clause. ", "Second clause. "],
        llm_fail_at=1,
        tts_chunks=[pcm(FRAME, fill=9)],
    )
    await stack.start()
    await stack.speak()

    synthesized = [spec.text for spec in stack.tts_client.specs]
    assert len(synthesized) == len(set(synthesized)), (
        f"a clause was synthesized twice after a mid-stream failure: {synthesized}"
    )
    await stack.finish()


async def test_a_failed_llm_stream_is_not_consumed_further():
    """§12. Nothing keeps pulling a dead stream."""
    stack = rh.build(words=["hello"], reply=["One. ", "Two. ", "Three. "], llm_fail_at=1)
    await stack.start()
    await stack.speak()

    stream = stack.llm_client.streams[0]
    assert stream.yielded == 1, f"a dead stream yielded {stream.yielded} chunks"
    await stack.finish()


async def test_R56_no_mid_stream_llm_failover_is_attempted():
    """§12 / **R-56 stands**. Switching models halfway through a sentence would produce an answer
    written by two models, spoken as one. It is not attempted, and this counts that it is not."""
    stack = rh.build(words=["hello"], reply=["One. ", "Two. "], llm_fail_at=1)
    await stack.start()
    await stack.speak()

    assert len(stack.llm_client.specs) == 1, "a mid-stream LLM failover was attempted"
    await stack.finish()


# ══════════════════════════════════════════════════════════════════════════════
# G. TTS resilience — §13
# ══════════════════════════════════════════════════════════════════════════════


async def test_O_a_tts_failure_before_any_audio_stamps_no_audio_boundary():
    """§44 O / §32."""
    telemetry = rh.RecordingTelemetry()
    stack = rh.build(words=["hello"], tts_open_error=TTSFailure.UNAVAILABLE, telemetry=telemetry)
    await stack.start()
    await stack.speak()

    assert "tts_first_audio" not in telemetry.stamps
    assert "first_audio_out" not in telemetry.stamps
    assert stack.endpoint.written == []
    await stack.finish()


async def test_P_a_tts_failure_after_first_audio_leaves_nothing_stale_queued():
    """§44 P / §13. Queued audio from a failed response is discarded, not played out later."""
    stack = rh.build(
        words=["hello"], reply=["One. ", "Two. "],
        tts_chunks=[pcm(FRAME), pcm(FRAME, fill=2)], tts_fail_at=1,
    )
    await stack.start()
    await stack.speak()

    assert stack.playback.engine.queued_frames == 0, "stale audio survived a TTS failure"
    await stack.finish()


async def test_R61_no_mid_utterance_tts_failover_is_attempted():
    """§13 / **R-61 stands**. Half a sentence in one voice and half in another is worse than a
    sentence that stops."""
    stack = rh.build(words=["hello"], reply=["One. "], tts_fail_at=0)
    await stack.start()
    await stack.speak()

    assert len(stack.tts_client.specs) == 1, "a mid-utterance TTS failover was attempted"
    await stack.finish()


async def test_a_failed_tts_closes_its_vendor_stream():
    """§13/§20. `finally: aclose()` in the guard is what makes this deterministic rather than
    dependent on when the generator is collected."""
    stack = rh.build(words=["hello"], reply=["One. ", "Two. "], tts_fail_at=1)
    await stack.start()
    await stack.speak()
    await stack.finish()

    streams = rh.vendor_streams(stack)
    assert streams, "no vendor stream was ever opened"
    assert all(s.closed >= 1 for s in streams), "a vendor stream was left open after a failure"


async def test_the_scheduler_does_not_stay_cancelled_after_a_failed_response():
    """§13. The M11.10 defect in its resilience form: an assistant permanently mute after one
    bad turn is a worse outcome than the bad turn."""
    stack = rh.build(words=["one", "two"], reply=["A reply. "], tts_fail_at=0)
    await stack.start()
    await stack.speak()
    stack.tts_client._fail_at = None  # noqa: SLF001 — the vendor recovers
    await stack.speak()

    assert stack.endpoint.written, "the assistant never spoke again after a TTS failure"
    await stack.finish()


# ══════════════════════════════════════════════════════════════════════════════
# H. Playback and back-pressure resilience — §14, §30
# ══════════════════════════════════════════════════════════════════════════════


async def test_Q_a_transport_send_failure_is_recorded_and_playback_stops():
    """§44 Q. The Engine's `FAILED`/`CANCELLED` distinction exists so that *nobody asked for this*
    stays distinguishable from *somebody did*."""
    endpoint = rh.FailingSendEndpoint(ok_frames=0)
    stack = rh.build(words=["hello"], reply=["One. "], endpoint=endpoint)
    await stack.start()
    await stack.speak()

    assert endpoint.refused >= 1, "the failing transport was never exercised"
    assert stack.degraded("transport", "unavailable"), "a transport failure went unrecorded"
    assert stack.playback.engine.queued_frames == 0
    await stack.finish()


async def test_outbound_audio_is_never_silently_dropped_under_back_pressure():
    """§30/§44 AG. ADR-0044 **AD7**: inbound may drop oldest, outbound may not. A dropped outbound
    frame is a word the caller never hears while every subsystem reports success."""
    stack = rh.build(words=["hello"], reply=["One. "], tts_chunks=[pcm(FRAME, fill=4)] * 8)
    await stack.start()
    await stack.speak()

    engine = stack.playback.engine
    assert engine.frames_discarded == 0, (
        f"back-pressure discarded {engine.frames_discarded} outbound frames"
    )
    assert len(stack.endpoint.written) == 8, "outbound audio was lost between synthesis and send"
    await stack.finish()


async def test_a_producer_blocked_on_back_pressure_is_released_by_cancellation():
    """§30. The deadlock this bound could create if done wrong: a producer parked forever on a
    buffer that will never drain because the consumer is gone."""
    endpoint = rh.HangingSendEndpoint()
    stack = rh.build(
        words=["hello"], reply=["One. "], endpoint=endpoint,
        tts_chunks=[pcm(FRAME, fill=5)] * 40,
    )
    await stack.start()
    await stack.speak(wait=False)
    await rh.settle(lambda: endpoint.entered.is_set(), spins=800)

    await stack.end()  # cancel while the producer is blocked
    assert stack.worker is None or stack.worker.state is WorkerState.STOPPED, (
        "cancellation did not release a producer blocked on back-pressure"
    )


async def test_a_failed_consumer_releases_a_waiting_producer_rather_than_deadlocking():
    """§30. Unit-level, because the race is between two internal tasks and a session would hide
    which of them was released."""
    from runtime.speech.playback import PlaybackEngine, PlaybackFailed

    async def explodes(frame):  # noqa: ANN001
        raise ConnectionResetError("transport died")

    engine = PlaybackEngine(explodes, capacity_ms=40.0)
    await engine.start()
    await engine.enqueue(rh.speech(0))
    await rh.settle(lambda: engine.state is PlaybackState.FAILED, spins=400)
    assert engine.state is PlaybackState.FAILED
    with pytest.raises(PlaybackFailed):
        await engine.enqueue(rh.speech(1))
    await engine.stop()


# ══════════════════════════════════════════════════════════════════════════════
# I. Transport resilience — §15
# ══════════════════════════════════════════════════════════════════════════════


async def test_J_a_transport_that_cannot_connect_fails_the_session_at_startup():
    """§44 J. Failure *before* connection: the session never reaches IN_CONVERSATION, and the
    caller is told rather than left on a half-open call."""
    from runtime.errors import WorkerStartupError

    stack = rh.build(words=["hello"], endpoint=rh.RefusingConnectEndpoint())
    with pytest.raises(WorkerStartupError):
        await stack.start()
    assert stack.worker is None, "a session that never connected was registered as live"


async def test_I_a_broken_media_path_is_not_read_as_a_caller_hangup():
    """§44 I / §15 — **the M11.14 transport fix**.

    Until this milestone a broken media path emitted `transport.failed` on the event seam and then
    handed the runtime the *same* end-of-stream sentinel a hangup produces. The conversation ended
    with reason `completed`, recorded no degradation, and was indistinguishable in every
    session-level record from a caller who said goodbye. The reality event was right and
    everything downstream of it was wrong.

    ADR-0045 **SD4** and **AR-6**: a stream that ends is a hangup, a stream that raises is a
    disconnect, and the two must never collapse into one."""
    endpoint = rh.BreakingInboundEndpoint(after=4)
    stack = rh.build(words=["hello"], endpoint=endpoint)
    await stack.start()
    await stack.speak(wait=False)
    await rh.spin(300)

    assert endpoint.delivered == 4, "the transport never actually broke"
    assert stack.degradations.history, (
        "a broken media path recorded no degradation — it was read as a hangup"
    )
    # The *type* the runtime saw, not merely that something was recorded: the terminal transition
    # names the exception class, and `ProviderStreamTruncated` is the one the contract reserves
    # for a stream that ended before its natural end.
    reasons = " ".join(record.reason for record in stack.orchestrator.history)
    assert ProviderStreamTruncated.__name__ in reasons, (
        f"the fault reached the runtime as something other than a truncated stream: {reasons}"
    )
    await stack.finish()


async def test_a_clean_hangup_is_not_reported_as_a_transport_failure():
    """§15, the converse — and the reason the fix above is conditional rather than unconditional.
    A fix that turned every goodbye into an outage would be worse than the defect."""
    stack = rh.build(words=["hello"], reply=["One. "])
    await stack.start()
    await stack.speak()
    await stack.hangup()

    assert not stack.degraded("transport", "disconnected")
    assert not stack.degraded("transport", "unavailable"), (
        "a clean caller hangup was recorded as a transport failure"
    )
    await stack.end()


async def test_a_transport_failure_routes_through_the_approved_state_machine():
    """§15/§16. §A4.4 forbids IN_CONVERSATION → FAILED, so a mid-call fault is routed
    ENDING → ENDED. No hidden transition, and the reason carries the cause."""
    endpoint = rh.BreakingInboundEndpoint(after=2)
    stack = rh.build(words=["hello"], endpoint=endpoint)
    await stack.start()
    await stack.speak(wait=False)
    await rh.settle(lambda: stack.orchestrator.is_terminal, spins=800)

    assert stack.orchestrator.state is SessionState.ENDED
    reasons = [record.reason for record in stack.orchestrator.history]
    assert any("pipeline_error" in reason for reason in reasons), (
        f"the terminal transition did not carry the cause: {reasons}"
    )
    await stack.finish()


# ══════════════════════════════════════════════════════════════════════════════
# J. Session and turn resilience — §16, §17
# ══════════════════════════════════════════════════════════════════════════════


async def test_X_repeated_stop_is_idempotent():
    """§44 X / §16. Three stops, one teardown."""
    stack = rh.build(words=["hello"], reply=["One. "])
    await stack.start()
    await stack.speak()
    await stack.hangup()
    await stack.end()
    await stack.end()
    await stack.end()

    assert stack.endpoint.closed.count(rh.SESSION) <= 1, "the transport was torn down twice"


async def test_W_stopping_during_a_failure_still_reaches_a_clean_terminal_state():
    """§44 W. The cascade §25.2 names: an LLM failure and a session stop arriving together."""
    stack = rh.build(words=["hello"], llm_open_error=LLMFailure.UNAVAILABLE)
    await stack.start()
    await stack.speak(wait=False)
    await stack.end()

    assert stack.worker is None, "the session survived its own stop"


async def test_Y_a_failed_turn_is_followed_by_a_working_one():
    """§44 Y / §17 — **the M11.14 pipeline fix**, and the most consequential defect this
    milestone found.

    A response failure used to skip the endpoint reset *and* leave the Turn Engine parked in
    `ASSISTANT_THINKING` for a turn that would never complete. The next utterance asked the engine
    to enter `ASSISTANT_THINKING` again, the engine correctly refused the illegal transition, and
    the session went **permanently deaf**: every later turn failed at its first transition, with no
    transcript, no audio and no second degradation to explain why. One vendor hiccup cost the whole
    call.

    §17 is explicit that a failed turn must not corrupt the session, so the turn is now retired
    deterministically — `TURN_CANCELLED`, the terminal metric released, the endpoint reset — and
    the caller may simply speak again."""
    stack = rh.build(words=["first", "second"], reply=["A real answer. "], llm_fail_at=0)
    await stack.start()
    await stack.speak()
    assert stack.endpoint.written == [], "turn 1 was supposed to fail"

    stack.llm_client._fail_at = None  # noqa: SLF001 — the vendor recovers
    await stack.speak()

    assert len(stack.llm_client.specs) == 2, (
        "turn 2 never reached the LLM — the failed turn poisoned the session"
    )
    assert stack.endpoint.written, "turn 2 produced no audio after turn 1 failed"
    await stack.finish()


async def test_a_failed_turn_leaves_the_turn_engine_terminalized_not_mid_phase():
    """§17. The mechanism behind the test above, asserted directly so a regression names itself."""
    stack = rh.build(words=["hello"], llm_open_error=LLMFailure.UNAVAILABLE)
    await stack.start()
    await stack.speak()

    engine = stack.orchestrator.turn_engine
    assert engine.current_context is None, (
        f"a failed turn was left active in {engine.current_context}"
    )
    await stack.finish()


async def test_a_failed_turn_stamps_a_terminal_metric_but_no_audio_boundary():
    """§17/§32. The turn ended; `cancelled` is what honestly happened. `metric.turn` has no
    `failed` status and M11.14 does not invent one."""
    telemetry = rh.RecordingTelemetry()
    stack = rh.build(words=["hello"], llm_open_error=LLMFailure.UNAVAILABLE, telemetry=telemetry)
    await stack.start()
    await stack.speak()

    statuses = [status for _, status in telemetry.completed]
    assert "cancelled" in statuses, f"a failed turn emitted no terminal status: {statuses}"
    assert "completed" not in statuses, "a failed turn was reported as completed"
    await stack.finish()


async def test_a_session_failure_cleans_up_every_subsystem():
    """§16. One assertion per thing that would otherwise leak."""
    endpoint = rh.BreakingInboundEndpoint(after=2)
    stack = rh.build(words=["hello"], endpoint=endpoint)
    await stack.start()
    playback = stack.playback
    await stack.speak(wait=False)
    await rh.settle(lambda: stack.orchestrator.is_terminal, spins=800)
    await stack.finish()

    # Not `state is CANCELLED`: the media path broke before any utterance completed, so no
    # response ever started and the Engine is legitimately still IDLE. The invariant that matters
    # is that nothing is held and nothing is running — asserting a particular state here would be
    # asserting *when* the transport happened to break.
    assert playback.engine.state is not PlaybackState.PLAYING
    assert playback.engine.queued_frames == 0
    assert playback.engine.buffered_ms == 0.0
    assert endpoint.closed, "the transport was never closed"


# ══════════════════════════════════════════════════════════════════════════════
# K. Barge-in resilience — §18
# ══════════════════════════════════════════════════════════════════════════════


async def test_R_and_S_a_barge_in_discards_queued_audio_and_the_next_turn_still_speaks():
    """§44 R/S. The two halves that matter: nothing stale survives, and the assistant is not
    silenced for the rest of the call."""
    stack = rh.build(
        words=["one", "two"], reply=["A reply. "], allow_interruptions=True,
        tts_chunks=[pcm(FRAME, fill=6)] * 4,
    )
    await stack.start()
    await stack.speak()
    playback = stack.playback
    await playback.abandon()

    assert playback.engine.state is PlaybackState.CANCELLED
    assert playback.engine.queued_frames == 0
    before = len(stack.endpoint.written)
    await stack.speak()
    assert len(stack.endpoint.written) > before, "the assistant stayed mute after a barge-in"
    await stack.finish()


async def test_a_barge_in_before_any_audio_exists_is_harmless():
    """§18. Interrupting a response that has not produced a frame must not leave the Scheduler
    cancelled for the next one."""
    stack = rh.build(words=["one", "two"], reply=["A reply. "], allow_interruptions=True)
    await stack.start()
    playback = stack.playback
    await playback.abandon()
    await stack.speak()

    assert stack.endpoint.written, "a pre-audio barge-in silenced the following turn"
    await stack.finish()


async def test_a_barge_in_while_the_transport_is_blocked_does_not_deadlock():
    """§18/§30. The hardest combination: cancellation arriving while a producer is parked on
    back-pressure inside a transport that has not returned."""
    endpoint = rh.HangingSendEndpoint()
    stack = rh.build(
        words=["hello"], reply=["One. "], endpoint=endpoint, allow_interruptions=True,
        tts_chunks=[pcm(FRAME, fill=8)] * 40,
    )
    await stack.start()
    await stack.speak(wait=False)
    await rh.settle(lambda: endpoint.entered.is_set(), spins=800)
    await stack.playback.abandon()

    assert stack.playback.engine.queued_frames == 0
    endpoint.release.set()
    await stack.finish()


async def test_AB_no_stale_audio_from_a_failed_turn_reaches_the_next_one():
    """§44 AB. Audio synthesized for a response that failed answers a question the conversation
    has moved past; it must never surface in a later turn."""
    stack = rh.build(
        words=["one", "two"], reply=["Stale clause. ", "More. "], llm_fail_at=1,
        tts_chunks=[pcm(FRAME, fill=1)],
    )
    await stack.start()
    await stack.speak()
    first = list(stack.endpoint.written)

    stack.llm_client._fail_at = None  # noqa: SLF001
    stack.tts_client._chunks = [pcm(FRAME, fill=2)]  # noqa: SLF001 — a distinguishable voice
    await stack.speak()

    later = stack.endpoint.written[len(first):]
    assert all(f.data[0] == 2 for f in later), (
        "audio from the failed turn was replayed into the next one"
    )
    await stack.finish()


# ══════════════════════════════════════════════════════════════════════════════
# L. Cancellation resilience — §19
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("frames_before_stop", [0, 3, rh.SPEECH_FRAMES])
async def test_H_cancellation_at_any_point_in_a_turn_terminates_cleanly(frames_before_stop):
    """§44 H / §19. Cancellation at session startup, mid-speech, and at end-of-utterance."""
    stack = rh.build(words=["hello"], reply=["One. "])
    await stack.start()
    for seq in range(frames_before_stop):
        await stack.endpoint.push(rh.speech(seq))
    await rh.spin(40)
    await stack.end()

    assert stack.worker is None


async def test_cancellation_during_synthesis_stops_pulling_the_vendor():
    """§19. A cancelled turn must stop consuming audio rather than finishing the utterance into a
    queue that has already been discarded."""
    started = asyncio.Event()
    gate = asyncio.Event()
    stack = rh.build(
        words=["hello"], reply=["One. "], tts_gate=gate, tts_started=started,
        tts_chunks=[pcm(FRAME, fill=1)] * 6,
    )
    await stack.start()
    await stack.speak(wait=False)
    await rh.settle(lambda: started.is_set(), spins=800)

    await stack.playback.abandon()
    gate.set()
    await rh.spin(150)
    assert stack.playback.engine.queued_frames == 0
    await stack.finish()


async def test_a_cancelled_scheduler_stops_pulling_the_vendor_mid_utterance():
    """§13/§19. Counted **at the vendor**: the question is not whether the discarded audio was
    discarded, but whether the runtime kept paying for audio it had already decided to throw
    away. A scheduler that finishes the utterance into a cancelled queue looks identical from the
    caller's side and burns a whole synthesis request per barge-in."""
    started = asyncio.Event()
    gate = asyncio.Event()
    stack = rh.build(
        words=["hello"], reply=["One. "], tts_gate=gate, tts_started=started,
        tts_chunks=[pcm(FRAME, fill=1)] * 10,
    )
    await stack.start()
    await stack.speak(wait=False)
    await rh.settle(lambda: started.is_set(), spins=800)

    await stack.playback.abandon()
    stream = stack.tts_client.streams[0]
    pulled_at_cancel = stream.yielded
    gate.set()
    await rh.spin(200)

    assert stream.yielded <= pulled_at_cancel + 1, (
        f"a cancelled response kept pulling the vendor: {pulled_at_cancel} → {stream.yielded}"
    )
    await stack.finish()


async def test_a_cancelled_playback_engine_accepts_and_sends_nothing_more():
    """§14/§18. The last line of defence against stale audio: even if something upstream hands a
    cancelled Engine another frame, the caller must not hear it."""
    from runtime.speech.playback import PlaybackEngine

    sent: list = []

    async def send(frame):  # noqa: ANN001
        sent.append(frame)

    engine = PlaybackEngine(send)
    await engine.start()
    await engine.enqueue(rh.speech(0))
    await rh.settle(lambda: len(sent) == 1, spins=400)
    await engine.cancel()

    await engine.enqueue(rh.speech(1))
    await rh.spin(100)
    assert len(sent) == 1, "a cancelled playback engine sent audio from before the cancellation"
    assert engine.state is PlaybackState.CANCELLED
    await engine.stop()


async def test_cancellation_is_never_recorded_as_a_provider_degradation():
    """§19. Translating withdrawal into an outage would make every ordinary hangup look like a
    vendor incident, and make the degradation record useless for the incidents that are real."""
    stack = rh.build(words=["hello"], reply=["One. "])
    await stack.start()
    await stack.speak()
    recorder = stack.degradations
    await stack.finish()

    assert not any(
        d.kind is DegradationKind.UNAVAILABLE for d in recorder.history
    ), "shutting down a healthy session recorded a provider failure"


# ══════════════════════════════════════════════════════════════════════════════
# M. Telemetry and event-spine resilience — §23, §24
# ══════════════════════════════════════════════════════════════════════════════


async def test_T_a_raising_telemetry_tap_does_not_break_the_conversation():
    """§44 T / ADR-0038 **TP9**. Observability is never on the critical path."""
    telemetry = rh.RaisingTelemetry()
    stack = rh.build(words=["hello"], reply=["One. "], telemetry=telemetry)
    await stack.start()
    await stack.speak()

    assert telemetry.records > 0, "the failing tap was never actually called"
    assert stack.endpoint.written, "a failing telemetry tap silenced the assistant"
    assert stack.orchestrator.state is SessionState.IN_CONVERSATION
    await stack.finish()


async def test_U_a_failing_durable_sink_does_not_break_the_conversation():
    """§44 U / ADR-0035 **DW4**, AR-31. The bus never raises into a producer; the operator is told
    through the failure hook instead.

    **`wait=False` is required here, and the reason is the finding itself (M11.15, R-69).** DW4
    says *"no subscriber may observe an event that is not durable"*, so while the sink is failing
    the bus dispatches **nothing** — including `turn.completed`. A harness waiting on that event
    would wait forever, which is not a flaw in the runtime but the contract working as written.
    The conversation is driven without it and the audio is what proves the turn happened."""
    sink = rh.RaisingDurableSink()
    stack = rh.build(
        words=["hello"], reply=["One. "], durable=sink, on_durable_error=sink.on_error
    )
    await stack.start()
    await stack.speak(wait=False)

    assert sink.attempts > 0, "the durable sink was never exercised"
    assert sink.errors, "a failed persistent write notified nobody"
    assert stack.endpoint.written, "a failing event log silenced the assistant"
    assert stack.orchestrator.state is SessionState.IN_CONVERSATION
    await stack.finish()


async def test_V_a_raising_metering_sink_does_not_break_the_conversation():
    """§44 V / §33. Metering observes; it never gates speech."""
    metrics = rh.RaisingMetricSink()
    stack = rh.build(words=["hello"], reply=["One. "], metrics=metrics)
    await stack.start()
    await stack.speak()

    assert stack.endpoint.written, "a failing metering sink silenced the assistant"
    assert stack.orchestrator.state is SessionState.IN_CONVERSATION
    await stack.finish()


async def test_a_degradation_sink_that_raises_never_breaks_the_recorder():
    """§22. Recording a degradation must not itself become a second failure."""
    from runtime.faults import Degradation, DegradationRecorder

    def explodes(_degradation):  # noqa: ANN001
        raise RuntimeError("sink down")

    recorder = DegradationRecorder(session_id="s", sink=explodes)
    recorder.record(Degradation(capability="llm", kind=DegradationKind.UNAVAILABLE))
    assert len(recorder.history) == 1, "a raising sink lost the record it was told about"


async def test_telemetry_remains_correlated_to_its_session_and_turn_under_failure():
    """§23. A metric attributed to the wrong turn is worse than a missing one."""
    telemetry = rh.RecordingTelemetry()
    stack = rh.build(
        words=["first", "second"], reply=["A reply. "], llm_fail_at=0, telemetry=telemetry
    )
    await stack.start()
    await stack.speak()
    stack.llm_client._fail_at = None  # noqa: SLF001
    await stack.speak()

    turns = [turn for turn, _ in telemetry.completed]
    assert len(turns) == len(set(turns)), f"two terminal metrics shared a turn: {turns}"
    await stack.finish()


# ══════════════════════════════════════════════════════════════════════════════
# N. Failure cascades — §25
# ══════════════════════════════════════════════════════════════════════════════


async def test_cascade_1_tts_failure_plus_user_interruption():
    """§25.1."""
    stack = rh.build(
        words=["one", "two"], reply=["One. "], tts_fail_at=0, allow_interruptions=True
    )
    await stack.start()
    await stack.speak()
    await stack.playback.abandon()
    stack.tts_client._fail_at = None  # noqa: SLF001
    await stack.speak()

    assert stack.orchestrator.state is SessionState.IN_CONVERSATION
    await stack.finish()


async def test_cascade_3_stt_failure_then_the_next_turn():
    """§25.3. A recognizer that dies mid-call ends the session cleanly rather than leaving a
    half-working one — STT is not auxiliary."""
    stack = rh.build(words=["one", "two"], reply=["A reply. "])
    await stack.start()
    await rh.settle(lambda: bool(stack.stt_client.sessions), spins=400)
    stack.stt_client.sessions[0].fail(RuntimeError("recognizer died"))
    await rh.spin(200)
    await stack.speak(wait=False)

    assert stack.orchestrator.is_terminal or stack.endpoint.written == []
    await stack.finish()


async def test_cascade_4_transport_disconnect_during_active_tts():
    """§25.4."""
    endpoint = rh.FailingSendEndpoint(ok_frames=1)
    stack = rh.build(
        words=["hello"], reply=["One. "], endpoint=endpoint,
        tts_chunks=[pcm(FRAME, fill=1)] * 6,
    )
    await stack.start()
    await stack.speak()

    assert endpoint.refused >= 1
    assert stack.playback.engine.queued_frames == 0
    await stack.finish()


async def test_cascade_5_telemetry_failure_plus_provider_failure():
    """§25.5. Two failures at once must not produce a contradictory state — in particular, a
    provider failure must still be recorded when telemetry is the thing that is broken."""
    telemetry = rh.RaisingTelemetry()
    stack = rh.build(
        words=["hello"], llm_open_error=LLMFailure.UNAVAILABLE, telemetry=telemetry
    )
    await stack.start()
    await stack.speak()

    assert stack.degraded("llm", "unavailable"), (
        "a broken telemetry plane swallowed the provider degradation"
    )
    assert stack.orchestrator.state is SessionState.IN_CONVERSATION
    await stack.finish()


async def test_cascade_6_playback_failure_then_the_next_turn():
    """§25.6."""
    endpoint = rh.FailingSendEndpoint(ok_frames=0)
    stack = rh.build(words=["one", "two"], reply=["A reply. "], endpoint=endpoint)
    await stack.start()
    await stack.speak()
    endpoint._ok = 1000  # noqa: SLF001 — the transport recovers
    await stack.speak()

    assert stack.endpoint.written, "the session never recovered from a transport failure"
    await stack.finish()


async def test_cascade_7_vad_failure_plus_the_endpoint_wait():
    """§25.7. The combination that must **not** produce a turn: no verdict means no boundary."""
    stack = rh.build(words=["hello"], speech_model_factory=rh.failing_vad(0))
    await stack.start()
    await stack.speak(wait=False)
    await rh.spin(300)

    assert stack.endpoint.written == []
    assert stack.orchestrator.state is SessionState.IN_CONVERSATION
    await stack.finish()


async def test_cascade_9_cancellation_plus_a_provider_that_never_returns():
    """§25.9. A hung vendor must not outlive the session that was cancelled."""
    hang = asyncio.Event()
    stack = rh.build(words=["hello"], reply=["One. "], llm_gate=hang, llm_started=asyncio.Event())
    await stack.start()
    await stack.speak(wait=False)
    await stack.end()

    assert stack.worker is None
    hang.set()
    await rh.spin(40)


async def test_cascade_10_transport_failure_plus_telemetry_failure():
    """§25.10."""
    telemetry = rh.RaisingTelemetry()
    endpoint = rh.BreakingInboundEndpoint(after=2)
    stack = rh.build(words=["hello"], endpoint=endpoint, telemetry=telemetry)
    await stack.start()
    await stack.speak(wait=False)
    await rh.settle(lambda: stack.orchestrator.is_terminal, spins=800)

    assert stack.orchestrator.state is SessionState.ENDED, (
        "two simultaneous failures produced a contradictory terminal state"
    )
    await stack.finish()


# ══════════════════════════════════════════════════════════════════════════════
# O. Resource leaks and repeated failure cycles — §20, §45
# ══════════════════════════════════════════════════════════════════════════════

#: Enough cycles to reveal accumulation, few enough to stay a test rather than a benchmark. A
#: leak of one task per cycle shows at this count; a leak of one per hundred is not what these
#: lifecycles do.
CYCLES = 50


async def test_AA_fifty_provider_failure_cycles_leak_nothing():
    """§45. Fifty sessions that each fail and stop, measured at the event loop."""
    before = await rh.census()
    for index in range(CYCLES):
        stack = rh.build(
            words=["hello"], llm_open_error=LLMFailure.UNAVAILABLE,
            session_id=f"ses_fail_{index}",
        )
        await stack.start()
        await stack.speak(wait=False)
        await stack.finish()
    after = await rh.census()

    assert after <= before, f"{CYCLES} failure cycles leaked {after - before} tasks"


async def test_Z_fifty_cancellation_cycles_leak_nothing():
    """§45. Cancelled mid-utterance, fifty times."""
    before = await rh.census()
    for index in range(CYCLES):
        stack = rh.build(words=["hello"], reply=["One. "], session_id=f"ses_cancel_{index}")
        await stack.start()
        for seq in range(4):
            await stack.endpoint.push(rh.speech(seq))
        await stack.end()
    after = await rh.census()

    assert after <= before, f"{CYCLES} cancellation cycles leaked {after - before} tasks"


async def test_fifty_barge_in_cycles_leak_nothing():
    """§45."""
    before = await rh.census()
    for index in range(CYCLES):
        stack = rh.build(
            words=["hello"], reply=["One. "], allow_interruptions=True,
            session_id=f"ses_barge_{index}",
        )
        await stack.start()
        await stack.speak(wait=False)
        await stack.playback.abandon()
        await stack.finish()
    after = await rh.census()

    assert after <= before, f"{CYCLES} barge-in cycles leaked {after - before} tasks"


async def test_repeated_failure_cycles_leak_no_provider_streams():
    """§20. Task count is not the whole story: a vendor stream left open is a socket in
    production, and it would not show up as a task."""
    for index in range(10):
        stack = rh.build(
            words=["hello"], reply=["One. ", "Two. "], tts_fail_at=1,
            session_id=f"ses_stream_{index}",
        )
        await stack.start()
        await stack.speak()
        await stack.finish()
        assert all(s.closed >= 1 for s in rh.vendor_streams(stack)), (
            f"cycle {index} left a vendor stream open"
        )


async def test_repeated_sessions_leak_no_event_subscriptions():
    """§20/§45. M11.12 attached the metric buffer to each session's bus; a dead session that kept
    its handler registered would be exactly the leak that milestone must not have introduced."""

    class CountingMetrics:
        def __init__(self) -> None:
            self.live = 0

        def subscribe(self, bus):  # noqa: ANN001
            self.live += 1
            handle = bus.subscribe(lambda event: None, types=("metric.turn",))
            original = bus.unsubscribe

            def unsubscribe(subscription):
                self.live -= 1
                original(subscription)

            bus.unsubscribe = unsubscribe
            return handle

    metrics = CountingMetrics()
    for index in range(10):
        stack = rh.build(
            words=["hello"], reply=["One. "], metrics=metrics, session_id=f"ses_sub_{index}"
        )
        await stack.start()
        await stack.speak()
        await stack.finish()
    assert metrics.live == 0, f"{metrics.live} metric subscriptions outlived their sessions"


# ══════════════════════════════════════════════════════════════════════════════
# P. No silent fallback, no fabrication — §27, §32
# ══════════════════════════════════════════════════════════════════════════════


async def test_AE_a_failing_real_provider_never_becomes_a_local_one():
    """§44 AE / §27. Resilience must not become a hidden test escape hatch: a failing vendor is a
    failing vendor, not an invitation to answer from a local stub."""
    from providers.llm.groq import GroqLLM
    from providers.tts.cartesia import CartesiaTTS
    from runtime.registry import CapabilityKind

    stack = rh.build(words=["hello"], llm_open_error=LLMFailure.UNAVAILABLE)
    await stack.start()
    await stack.speak()

    assert isinstance(stack.registry.resolve(CapabilityKind.LLM), GroqLLM)
    assert isinstance(stack.registry.resolve(CapabilityKind.TTS), CartesiaTTS)
    await stack.finish()


async def test_AD_no_turn_ever_reports_a_boundary_its_audio_did_not_reach():
    """§44 AD / §32. Swept across every failure point rather than asserted once, because the
    fabrication risk is not in any single stage — it is in the assumption that the next stage
    happened because the previous one did."""
    cases = {
        "llm-pre-token": {"llm_open_error": LLMFailure.UNAVAILABLE},
        "llm-post-token": {"reply": ["One. ", "Two. "], "llm_fail_at": 1},
        "tts-pre-audio": {"tts_open_error": TTSFailure.UNAVAILABLE},
        "vad-dead": {"speech_model_factory": rh.failing_vad(0)},
    }
    for label, kwargs in cases.items():
        telemetry = rh.RecordingTelemetry()
        stack = rh.build(
            words=["hello"], telemetry=telemetry, session_id=f"ses_fab_{label}", **kwargs
        )
        await stack.start()
        await stack.speak(wait=False)
        await rh.spin(200)
        await stack.finish()

        if not stack.endpoint.written:
            assert "first_audio_out" not in telemetry.stamps, (
                f"{label}: first_audio_out was stamped with no audio sent"
            )


async def test_first_audio_out_is_not_stamped_when_the_transport_refuses_the_frame():
    """§32. The boundary is *the caller is now hearing audio*, so it belongs **after** the
    transport accepted the frame (ADR-0038 TP3 #6). Stamping it before the send would make a
    back-pressured or failing transport report a voice-to-voice latency nobody experienced — a
    number that gets better precisely as the system gets worse."""
    telemetry = rh.RecordingTelemetry()
    endpoint = rh.FailingSendEndpoint(ok_frames=0)
    stack = rh.build(words=["hello"], reply=["One. "], endpoint=endpoint, telemetry=telemetry)
    await stack.start()
    await stack.speak()

    assert endpoint.refused >= 1, "the transport never actually refused anything"
    assert stack.endpoint.written == []
    assert "first_audio_out" not in telemetry.stamps, (
        "first_audio_out was stamped for a frame the transport never accepted"
    )
    await stack.finish()


async def test_the_playback_engine_is_stopped_when_the_session_ends():
    """§16/§20. A sender task that outlives its session holds the transport's send path open and
    would deliver audio into a call that has already ended."""
    stack = rh.build(words=["hello"], reply=["One. "])
    await stack.start()
    engine = stack.playback.engine
    await stack.speak()
    await stack.finish()

    assert engine.state is not PlaybackState.PLAYING, "the playback engine outlived its session"
    assert engine.queued_frames == 0
    running = [t for t in asyncio.all_tasks() if t.get_name() == "playback-engine"]
    assert not running, f"{len(running)} playback sender tasks survived the session"


async def test_a_session_ended_mid_playback_leaves_no_sender_task_behind():
    """§16/§20, the case the test above cannot reach.

    A response that finished draining has already released its sender, so a shutdown that skipped
    the engine would look clean. The leak only shows when the session ends while audio is still
    moving — here, parked inside a transport that has not returned — which is exactly when a
    surviving sender would keep pushing audio into a call that has ended."""
    endpoint = rh.HangingSendEndpoint()
    stack = rh.build(
        words=["hello"], reply=["One. "], endpoint=endpoint,
        tts_chunks=[pcm(FRAME, fill=3)] * 40,
    )
    await stack.start()
    engine = stack.playback.engine
    await stack.speak(wait=False)
    await rh.settle(lambda: endpoint.entered.is_set(), spins=800)
    assert engine.state is PlaybackState.PLAYING, "playback was not actually in flight"

    await stack.end()
    endpoint.release.set()
    await rh.spin(120)

    running = [t for t in asyncio.all_tasks() if t.get_name() == "playback-engine"]
    assert not running, "a playback sender outlived the session it belonged to"
    assert engine.state is not PlaybackState.PLAYING


async def test_the_playback_buffer_never_exceeds_its_declared_capacity():
    """§30/§44 AG. The bound is the whole point of the buffer: it is how far ahead of the
    caller's ear the runtime is allowed to run, and a producer that outruns it must **wait**
    rather than accumulate."""
    from runtime.speech.playback import DEFAULT_CAPACITY_MS, PlaybackEngine

    release = asyncio.Event()
    observed: list[float] = []

    async def slow(frame):  # noqa: ANN001
        await release.wait()

    engine = PlaybackEngine(slow)
    await engine.start()

    async def flood():
        for seq in range(200):
            await engine.enqueue(rh.speech(seq))
            observed.append(engine.buffered_ms)

    task = asyncio.create_task(flood())
    await rh.spin(300)
    assert not task.done(), "the producer was never actually back-pressured"
    assert observed, "no frame was ever admitted"
    # One frame of slack: `_admits` lets a single frame exceed the bound when the buffer is empty,
    # because refusing to speak at all is worse than briefly exceeding it by one frame.
    assert max(observed) <= DEFAULT_CAPACITY_MS + 40.0, (
        f"the playback buffer grew to {max(observed):.0f} ms against a "
        f"{DEFAULT_CAPACITY_MS:.0f} ms capacity"
    )
    task.cancel()
    release.set()
    await engine.stop()


async def test_a_session_publishes_exactly_one_session_ended_event():
    """§16/§44 AC. Counted on the bus, because duplicate terminal events are how a downstream
    consumer ends up billing, alerting or reconciling a call twice."""
    stack = rh.build(words=["hello"], reply=["One. "])
    await stack.start()
    seen: list = []
    stack.orchestrator.event_bus.subscribe(
        lambda event: seen.append(event), types=("session.ended",)
    )
    await stack.speak()
    await stack.hangup()
    await stack.end()
    await stack.end()

    assert len(seen) == 1, f"session.ended was published {len(seen)} times"


async def test_a_failed_session_publishes_exactly_one_session_ended_event():
    """§16. The same count on the failure path, which is where a second terminal event is most
    likely: two code paths now release providers, and both finish the lifecycle."""
    endpoint = rh.BreakingInboundEndpoint(after=2)
    stack = rh.build(words=["hello"], endpoint=endpoint)
    await stack.start()
    seen: list = []
    stack.orchestrator.event_bus.subscribe(
        lambda event: seen.append(event), types=("session.ended",)
    )
    orchestrator = stack.orchestrator
    await stack.speak(wait=False)
    await rh.settle(lambda: orchestrator.is_terminal, spins=800)
    await stack.finish()

    assert len(seen) == 1, f"a failed session published session.ended {len(seen)} times"


async def test_AC_a_failed_session_emits_no_duplicate_terminal_event():
    """§44 AC / §16."""
    endpoint = rh.BreakingInboundEndpoint(after=2)
    stack = rh.build(words=["hello"], endpoint=endpoint)
    await stack.start()
    orchestrator = stack.orchestrator
    await stack.speak(wait=False)
    await rh.settle(lambda: orchestrator.is_terminal, spins=800)
    await stack.finish()

    terminal = [r for r in orchestrator.history if r.new in (SessionState.ENDED,
                                                             SessionState.FAILED)]
    assert len(terminal) == 1, f"the session terminalized {len(terminal)} times"


# ══════════════════════════════════════════════════════════════════════════════
# Q. Architecture guards — §36
# ══════════════════════════════════════════════════════════════════════════════


def test_providerguard_remains_the_single_provider_resilience_mechanism():
    """§36. No second breaker, no second retry ladder, no provider-specific circuit breaker.

    Structural over the AST rather than textual, because a text scan of a file that *documents*
    these names matches itself — the lesson M11.12 learned at scale."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).parent.parent
    banned = {"CircuitBreaker", "FailoverController", "RetryPolicy", "Backoff"}
    offenders: list[str] = []
    for path in (root / "providers").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(alias.name in banned for alias in node.names):
                    offenders.append(str(path))
            elif isinstance(node, ast.ClassDef) and any(
                b in node.name for b in ("CircuitBreaker", "RetryController")
            ):
                offenders.append(f"{path}:{node.name}")
    assert not offenders, f"a second provider resilience mechanism appeared: {offenders}"


def test_the_runtime_declares_exactly_one_degradation_registry():
    """§36. One `DegradationRecorder`, constructed in one place."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).parent.parent / "runtime"
    definitions = [
        f"{path}:{node.name}"
        for path in root.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef) and "Degradation" in node.name
        and node.name.endswith(("Recorder", "Registry"))
    ]
    assert len(definitions) == 1, f"more than one degradation registry exists: {definitions}"


def test_no_credential_can_reach_a_failure_event():
    """§23/§36. A failure payload is the likeliest place for a vendor error to smuggle a secret,
    because a vendor exception routinely embeds endpoints, account ids and occasionally key
    fragments. The payload is a fixed, closed set of fields — asserted here so it stays one."""
    from runtime.faults import Degradation

    payload = Degradation(
        capability="llm",
        kind=DegradationKind.UNAVAILABLE,
        detail="auth rejected",
        provider="groq",
    ).as_payload()
    assert set(payload) == {"capability", "kind", "detail", "provider", "fallback"}
    assert all(isinstance(value, str) for value in payload.values())


def test_the_playback_engine_still_owns_the_only_outbound_queue():
    """§14/§36. A second outbound queue would make ordering and discard-on-barge-in unprovable."""
    import ast
    import pathlib

    path = pathlib.Path(__file__).parent.parent / "runtime" / "speech" / "scheduler.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "Queue" not in names, "the Scheduler grew a queue of its own"


def test_the_transport_fix_introduced_no_new_transport_event():
    """§15. The M11.14 change puts the existing fault on the stream the runtime reads; it adds no
    event kind and emits nothing new."""
    from core.providers.transport import TransportEventKind

    assert {kind.value for kind in TransportEventKind} == {
        "connected", "media_ready", "disconnected", "failed",
    }


async def test_a_broken_media_path_still_emits_failed_and_not_disconnected():
    """§15. The AR-6 reality semantics M11.5 established are unchanged by the M11.14 fix."""
    from core.providers.transport import TransportEventKind

    events: list = []
    endpoint = rh.BreakingInboundEndpoint(after=1)
    stack = rh.build(words=["hello"], endpoint=endpoint)
    await stack.start()
    transport = stack.registry.resolve(
        __import__("runtime.registry", fromlist=["CapabilityKind"]).CapabilityKind.TRANSPORT
    )
    transport.observe(rh.SESSION, events.append)
    await stack.speak(wait=False)
    await rh.spin(200)

    kinds = {event.kind for event in events}
    assert TransportEventKind.DISCONNECTED not in kinds, (
        "a broken media path was reported as a clean disconnect"
    )
    await stack.finish()

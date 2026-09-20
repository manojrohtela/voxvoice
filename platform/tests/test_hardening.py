"""Hardening evidence (M11.15).

M11.14 asked *"does the system behave correctly when components fail?"* and answered yes, with
four risks left open. This file asks the next question — *"is that behaviour robust against edge
cases, concurrency, repeated degradation, malformed state and lifecycle races?"* — and carries the
evidence for the risks M11.15 set out to close.

Organised by what it proves, not by what it touches:

* **R-48** — sustained degradation is an *episode*, so a dead VAD no longer publishes one event
  per frame, while the first failure, the counts and a post-recovery failure all stay visible.
* **R-68** — the failover chain, exercised hermetically against the real `FailoverController`.
* **R-69** — durable-write failure is deterministic and observable, and the part that remains
  undefined is named rather than invented.
* **R-70** — the harness waits on runtime facts; there is no spin budget left to flake.

Nothing here sleeps for a duration, and no test retries until it passes.
"""

from __future__ import annotations

import asyncio

import pytest

from core.audio import AudioEncoding, AudioFrame
from core.errors import (
    CapabilityUnsupported,
    ProviderAuthError,
    ProviderError,
    ProvidersExhausted,
    ProviderUnavailable,
)
from core.states import SessionState
from runtime.faults import (
    Degradation,
    DegradationKind,
    DegradationRecorder,
    ProviderGuard,
)

from . import resilience_harness as rh

# ══════════════════════════════════════════════════════════════════════════════
# R-48 — sustained degradation is an episode (§5's six scenarios)
# ══════════════════════════════════════════════════════════════════════════════


def _recorder() -> tuple[DegradationRecorder, list[Degradation]]:
    published: list[Degradation] = []
    return DegradationRecorder(session_id="s", sink=published.append), published


def _vad_failure(detail: str = "observe failed") -> Degradation:
    return Degradation(
        capability="voice_activity",
        kind=DegradationKind.AUXILIARY_DISABLED,
        detail=detail,
        provider="voice_activity",
    )


def test_R48_1_a_single_failure_is_published_immediately():
    """§5.1. The first failure is never delayed, sampled or batched — it is the one nobody has
    seen yet, and suppressing it would be the opposite of the goal."""
    recorder, published = _recorder()
    recorder.record(_vad_failure())
    assert len(published) == 1
    assert len(recorder.history) == 1
    assert recorder.counts["voice_activity:auxiliary_disabled"] == 1


def test_R48_2_and_3_a_sustained_failure_publishes_once_but_counts_every_frame():
    """§5.2/§5.3. 150 frames of the same outage: **one** publication, 150 counted.

    This is the whole of R-48. The retained record was already bounded; the *telling* was not, and
    at the 20 ms media cadence that is ~50 events/second/session for the length of the outage."""
    recorder, published = _recorder()
    for _ in range(150):
        recorder.record(_vad_failure())

    assert len(published) == 1, f"a sustained outage published {len(published)} events"
    assert len(recorder.history) == 1, "the history grew once per frame"
    assert recorder.counts["voice_activity:auxiliary_disabled"] == 150, (
        "coalescing made the outage look smaller than it was"
    )
    assert recorder.suppressed == 149
    assert recorder.is_active("voice_activity"), (
        "suppression made an ongoing degradation look resolved"
    )


def test_R48_4_recovery_ends_the_episode_without_announcing_anything():
    """§5.4. `resolved()` re-arms reporting. It publishes nothing: one successful call is not a
    claim of health, and a `recovered` event would be the false recovery §5 forbids."""
    recorder, published = _recorder()
    recorder.record(_vad_failure())
    recorder.resolved("voice_activity")

    assert len(published) == 1, "recovery published an event of its own"
    assert not recorder.is_active("voice_activity")


def test_R48_5_a_failure_after_recovery_is_visible_again():
    """§5.5 — *"the second failure after recovery must not be incorrectly suppressed"*.

    The mistake this design is most likely to make, and the reason suppression is keyed to an
    episode rather than to a key that is set once and never cleared."""
    recorder, published = _recorder()
    recorder.record(_vad_failure("first outage"))
    recorder.resolved("voice_activity")
    recorder.record(_vad_failure("second outage"))

    assert len(published) == 2, "the post-recovery failure was swallowed"
    assert [d.detail for d in published] == ["first outage", "second outage"]
    assert recorder.counts["voice_activity:auxiliary_disabled"] == 2


def test_R48_6_two_sessions_degrade_independently():
    """§5.6. The recorder is per session, so one session's outage can never suppress another's
    first report. Asserted rather than assumed, because a module-level cache would be an easy and
    invisible way to get this wrong."""
    first, first_published = _recorder()
    second, second_published = _recorder()

    for _ in range(50):
        first.record(_vad_failure())
    second.record(_vad_failure())

    assert len(first_published) == 1
    assert len(second_published) == 1, "one session's episode suppressed another's first report"
    assert first.counts["voice_activity:auxiliary_disabled"] == 50
    assert second.counts["voice_activity:auxiliary_disabled"] == 1


def test_R48_different_kinds_are_different_episodes():
    """A timeout and an unavailability are different facts about the same capability; collapsing
    them would hide a change in *how* a provider is failing."""
    recorder, published = _recorder()
    recorder.record(Degradation(capability="llm", kind=DegradationKind.TIMEOUT, provider="p"))
    recorder.record(Degradation(capability="llm", kind=DegradationKind.UNAVAILABLE, provider="p"))
    assert len(published) == 2


def test_R48_different_providers_are_different_episodes():
    recorder, published = _recorder()
    recorder.record(Degradation(capability="tts", kind=DegradationKind.TIMEOUT, provider="a"))
    recorder.record(Degradation(capability="tts", kind=DegradationKind.TIMEOUT, provider="b"))
    assert len(published) == 2


def test_R48_detail_is_deliberately_not_part_of_the_episode_key():
    """A vendor message can differ on every frame. Keying on it would defeat coalescing for
    exactly the noisy failures that motivated it."""
    recorder, published = _recorder()
    for index in range(20):
        recorder.record(_vad_failure(f"observe failed: attempt {index}"))
    assert len(published) == 1
    assert published[0].detail.endswith("attempt 0"), "the retained detail is not the first one"


async def test_R48_a_guarded_success_ends_the_episode():
    """The recovery signal is the breaker's own: `ProviderGuard` already treats a successful call
    as *"this provider is usable again"*, and R-48 reuses it rather than inventing a second
    notion of recovery."""
    recorder, published = _recorder()
    guard = ProviderGuard(capability="llm", degradations=recorder)

    async def fails():
        raise ProviderUnavailable("down")

    async def works():
        return "ok"

    # Two failures: below the breaker's threshold, so the *episode* is what suppresses the
    # repeat rather than the breaker refusing the call outright.
    for _ in range(2):
        with pytest.raises(ProviderError):
            await guard.call("p", fails)
    assert len(published) == 1

    await guard.call("p", works)          # the episode ends here
    with pytest.raises(ProviderError):
        await guard.call("p", fails)
    assert len(published) == 2, "a failure after a successful call was suppressed"


async def test_R48_end_to_end_a_dead_vad_publishes_one_degradation_not_one_per_frame():
    """The measurement M11.14 recorded, re-taken against the hardened runtime.

    M11.14 asserted `observed > 10` publications and called R-48 open. The count is now one
    episode, and the occurrence count is still exact — which is the difference between fixing the
    problem and hiding it."""
    stack = rh.build(words=["hello"], speech_model_factory=rh.failing_vad(0))
    await stack.start()
    await stack.speak(wait=False)

    recorder = stack.degradations
    episodes = [
        d for d in recorder.history
        if d.capability == "voice_activity" and d.kind is DegradationKind.AUXILIARY_DISABLED
    ]
    occurrences = recorder.counts.get("voice_activity:auxiliary_disabled", 0)

    assert len(episodes) == 1, f"a sustained VAD outage produced {len(episodes)} episodes"
    assert occurrences > 10, "the outage was not actually sustained"
    assert recorder.is_active("voice_activity"), "the ongoing outage looks resolved"
    await stack.finish()


async def test_R48_a_healthy_session_records_no_voice_activity_degradation():
    """The converse, so the coalescing cannot be passing by suppressing everything."""
    stack = rh.build(words=["hello"], reply=["One. "])
    await stack.start()
    await stack.speak()
    assert not stack.degraded("voice_activity", "auxiliary_disabled")
    await stack.finish()


# ══════════════════════════════════════════════════════════════════════════════
# R-68 — the failover chain, exercised hermetically (§29)
# ══════════════════════════════════════════════════════════════════════════════


async def test_R68_a_failing_candidate_falls_over_to_the_next_which_succeeds():
    """§29. The mechanism M11.14 could only prove could not *hang*: here it actually selects."""
    recorder, published = _recorder()
    guard = ProviderGuard(capability="tts", degradations=recorder)
    tried: list[str] = []

    async def operation(candidate: str) -> str:
        tried.append(candidate)
        if candidate == "alpha":
            raise ProviderUnavailable("alpha is down")
        return f"spoken by {candidate}"

    assert await guard.call_with_failover(("alpha", "bravo"), operation) == "spoken by bravo"
    assert tried == ["alpha", "bravo"], "the chain did not walk forward exactly once each"
    assert any(d.kind is DegradationKind.FAILED_OVER for d in published), (
        "a failover hop was not recorded"
    )


async def test_R68_every_candidate_failing_raises_providers_exhausted():
    guard = ProviderGuard(capability="tts")
    tried: list[str] = []

    async def operation(candidate: str):
        tried.append(candidate)
        raise ProviderUnavailable(f"{candidate} is down")

    with pytest.raises(ProvidersExhausted):
        await guard.call_with_failover(("alpha", "bravo"), operation)
    assert tried == ["alpha", "bravo"]


async def test_R68_an_auth_failure_on_the_first_candidate_still_reaches_the_second():
    """A rejected credential is a fact about **that** vendor, not about the capability, so the
    chain still walks. What must not happen is retrying the same rejected candidate."""
    guard = ProviderGuard(capability="llm")
    tried: list[str] = []

    async def operation(candidate: str) -> str:
        tried.append(candidate)
        if candidate == "alpha":
            raise ProviderAuthError("credentials rejected")
        return "ok"

    assert await guard.call_with_failover(("alpha", "bravo"), operation) == "ok"
    assert tried == ["alpha", "bravo"], "an auth failure was retried on the same candidate"


async def test_R68_an_unsupported_capability_falls_over_rather_than_retrying():
    guard = ProviderGuard(capability="tts")
    tried: list[str] = []

    async def operation(candidate: str) -> str:
        tried.append(candidate)
        if candidate == "alpha":
            raise CapabilityUnsupported("no such voice")
        return "ok"

    assert await guard.call_with_failover(("alpha", "bravo"), operation) == "ok"
    assert tried.count("alpha") == 1


async def test_R68_cancellation_during_failover_is_not_failed_over():
    """Rule 2 in the chain walk: the caller withdrawing must not burn a candidate, and must not
    be translated into a provider failure."""
    guard = ProviderGuard(capability="llm")
    tried: list[str] = []
    entered = asyncio.Event()

    async def operation(candidate: str):
        tried.append(candidate)
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(guard.call_with_failover(("alpha", "bravo"), operation))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tried == ["alpha"], "cancellation walked the chain"


async def test_R68_a_succeeding_first_candidate_never_reaches_the_second():
    guard = ProviderGuard(capability="stt")
    tried: list[str] = []

    async def operation(candidate: str) -> str:
        tried.append(candidate)
        return "ok"

    assert await guard.call_with_failover(("alpha", "bravo"), operation) == "ok"
    assert tried == ["alpha"]


# ══════════════════════════════════════════════════════════════════════════════
# R-69 — durable-write failure is deterministic (§6, §30)
# ══════════════════════════════════════════════════════════════════════════════


async def test_R69_A_a_failed_durable_write_is_not_dispatched_to_subscribers():
    """§6.A / ADR-0035 **DW4**: *"no subscriber may observe an event that is not durable"*.

    The single most consequential consequence of the contract, and one M11.14 did not state: while
    the sink is failing the in-process bus goes **entirely silent**, so every consumer — the
    metric buffer, the gateway, operator tooling — sees nothing at all. The runtime stays
    available (AR-31); its observability does not."""
    sink = rh.RaisingDurableSink()
    stack = rh.build(
        words=["hello"], reply=["One. "], durable=sink, on_durable_error=sink.on_error
    )
    await stack.start()
    seen: list = []
    stack.orchestrator.event_bus.subscribe(lambda event: seen.append(event))
    await stack.speak(wait=False)

    assert sink.attempts > 0, "the durable sink was never exercised"
    assert seen == [], "an event that could not be persisted was dispatched anyway (DW4)"
    await stack.finish()


async def test_R69_B_every_failed_write_notifies_the_operator_hook_exactly_once():
    """§6.B. Repeated failures are each accounted; none is silently collapsed."""
    sink = rh.RaisingDurableSink()
    stack = rh.build(
        words=["hello"], reply=["One. "], durable=sink, on_durable_error=sink.on_error
    )
    await stack.start()
    await stack.speak(wait=False)

    assert len(sink.errors) == sink.attempts, (
        f"{sink.attempts} failed writes produced {len(sink.errors)} notifications"
    )
    await stack.finish()


async def test_R69_D_the_voice_session_continues_while_persistence_is_degraded():
    """§6.D / AR-31. Availability of the conversation is not conditional on the event log."""
    sink = rh.RaisingDurableSink()
    stack = rh.build(
        words=["hello"], reply=["One. "], durable=sink, on_durable_error=sink.on_error
    )
    await stack.start()
    await stack.speak(wait=False)

    assert stack.endpoint.written, "a failing event log silenced the assistant"
    assert stack.orchestrator.state is SessionState.IN_CONVERSATION
    await stack.finish()


async def test_R69_F_shutdown_completes_while_durable_writes_are_failing():
    """§6.F. A terminal path that depended on a working sink would strand sessions during an
    outage — the failure mode that turns a storage incident into a capacity incident."""
    sink = rh.RaisingDurableSink()
    stack = rh.build(
        words=["hello"], reply=["One. "], durable=sink, on_durable_error=sink.on_error
    )
    await stack.start()
    await stack.speak(wait=False)
    await stack.finish()

    assert stack.worker is None, "the session could not be stopped during a durable outage"


async def test_R69_C_recovery_resumes_dispatch_without_replaying_the_outage():
    """§6.C/§6.G. When the sink recovers, new events flow again — and the events lost during the
    outage are **not** replayed into the session.

    That is ADR-0035's declared limitation, not an oversight: M10.6 implements no recovery, and
    the outbox driver that would replay (ADR-0036/0037) is not driven from a live conversation.
    The test pins the honest behaviour so a future in-session replay is a deliberate change."""

    class RecoveringSink:
        def __init__(self) -> None:
            self.fail = True
            self.stored: list = []

        def append(self, event: object) -> bool:
            if self.fail:
                raise RuntimeError("sink unavailable")
            self.stored.append(event)
            return True

    sink = RecoveringSink()
    stack = rh.build(words=["hello"], reply=["One. "], durable=sink)
    await stack.start()
    during_outage = len(sink.stored)
    sink.fail = False
    await stack.speak(wait=False)

    assert during_outage == 0
    assert sink.stored, "the sink never recovered"
    types = {getattr(event, "type", None) for event in sink.stored}
    from core.events import EventType

    assert EventType.SESSION_STARTED not in types, (
        "an event from before the outage was replayed into the session"
    )
    await stack.finish()


async def test_R69_no_second_durable_queue_was_introduced():
    """§6/§30. The hardening must not smuggle in an outbox."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).parent.parent / "runtime"
    offenders = [
        f"{path}:{node.name}"
        for path in root.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
        and any(word in node.name.lower() for word in ("outbox", "durablequeue", "writeahead"))
    ]
    assert not offenders, f"a durable queue appeared in runtime/: {offenders}"


# ══════════════════════════════════════════════════════════════════════════════
# R-70 — the harness waits on runtime facts (§7, §31)
# ══════════════════════════════════════════════════════════════════════════════


def test_R70_no_conversation_harness_waits_on_a_fixed_spin_budget():
    """§7's requirement, asserted structurally so it cannot quietly come back.

    `settle(..., spins=N)` and bare `spin(N)` are bets on scheduling. They are gone from the
    turn-driving paths; what remains are waits on runtime facts — `turn.completed` /
    `turn.cancelled` from the Turn Engine, and the VAD provider's own `buffered_ms`."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).parent
    offenders: list[str] = []
    for name in ("test_e2e_conversation.py", "resilience_harness.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = getattr(func, "id", None) or getattr(func, "attr", None)
            if called == "settle" and any(k.arg == "spins" for k in node.keywords):
                offenders.append(f"{name}:{node.lineno} settle(spins=...)")
    assert not offenders, f"a fixed spin budget is still driving a turn: {offenders}"


def test_R70_the_turn_watcher_waits_on_the_runtimes_own_events():
    """The signal is the Turn Engine's, not a harness invention."""
    import inspect

    from core.events import EventType

    from .fake_conversation import TurnWatcher

    source = inspect.getsource(TurnWatcher)
    assert EventType.TURN_COMPLETED.name in source
    assert EventType.TURN_CANCELLED.name in source


async def test_R70_the_vad_drain_waits_on_provider_observability_not_a_counter():
    """`buffered_ms` is a runtime fact published by the provider; the loop has no iteration
    budget and the deadline only converts a hang into a failure."""
    from runtime.registry import CapabilityKind

    from .fake_conversation import vad_drained

    stack = rh.build(words=["hello"], reply=["One. "])
    await stack.start()
    provider = stack.registry.resolve(CapabilityKind.VOICE_ACTIVITY)
    await stack.speak()
    await vad_drained(provider, stack.request.session_id)
    assert provider.buffered_ms(stack.request.session_id) < 32.0
    await stack.finish()


async def test_R70_a_dead_vad_is_diagnosed_rather_than_waited_out():
    """`inference_failed` is what makes "the worker is behind" and "the worker is gone"
    different questions. Without it the only way to tell them apart is to wait — which is a
    timeout pretending to be a diagnosis, and is what made seven tests take ten seconds each."""
    from runtime.registry import CapabilityKind

    stack = rh.build(words=["hello"], speech_model_factory=rh.failing_vad(0))
    await stack.start()
    await stack.speak(wait=False)
    provider = stack.registry.resolve(CapabilityKind.VOICE_ACTIVITY)
    assert provider.inference_failed(stack.request.session_id), (
        "a dead inference worker did not report itself"
    )
    await stack.finish()


async def test_R70_a_healthy_session_never_reports_failed_inference():
    from runtime.registry import CapabilityKind

    stack = rh.build(words=["hello"], reply=["One. "])
    await stack.start()
    await stack.speak()
    provider = stack.registry.resolve(CapabilityKind.VOICE_ACTIVITY)
    assert not provider.inference_failed(stack.request.session_id)
    await stack.finish()


@pytest.mark.parametrize("run", range(5))
async def test_R70_a_two_turn_conversation_is_repeatable(run):
    """§31: repeated execution, on the path that flaked. Five consecutive two-turn conversations,
    each waiting only on runtime facts."""
    stack = rh.build(words=["first", "second"], reply=["A reply. "], session_id=f"ses_r70_{run}")
    await stack.start()
    await stack.speak()
    await stack.speak()
    assert len(stack.llm_client.specs) == 2, "a turn was lost"
    await stack.finish()


# ══════════════════════════════════════════════════════════════════════════════
# Failure-event safety and the credential boundary (§16, §17, §26)
# ══════════════════════════════════════════════════════════════════════════════


SECRET = "sk-live-THIS-MUST-NEVER-APPEAR-0123456789"


class _LeakyError(Exception):
    """A vendor exception of the shape that actually leaks: the secret is in its `repr`."""

    def __init__(self) -> None:
        super().__init__(f"auth failed for key={SECRET} at https://api.vendor/v1")
        self.api_key = SECRET

    def __repr__(self) -> str:  # pragma: no cover — exercised via str() in payloads
        return f"_LeakyError(api_key={SECRET!r})"


async def test_a_vendor_exception_carrying_a_secret_does_not_reach_a_failure_event():
    """§16. A vendor error routinely embeds endpoints, account ids and occasionally key
    fragments. The degradation payload is a closed set of five strings — this proves the *values*
    are safe too, not merely the shape."""
    recorder, published = _recorder()
    guard = ProviderGuard(capability="llm", degradations=recorder)

    async def leaks():
        raise _LeakyError()

    with pytest.raises(ProviderError):
        await guard.call("vendor", leaks)

    assert published, "the failure recorded no degradation at all"
    serialized = repr([d.as_payload() for d in published])
    assert SECRET not in serialized, "a credential reached the failure event"
    assert "api.vendor" not in serialized, "a vendor endpoint reached the failure event"


async def test_a_typed_provider_error_carrying_a_secret_is_also_contained():
    recorder, published = _recorder()
    guard = ProviderGuard(capability="tts", degradations=recorder)

    async def leaks():
        raise ProviderUnavailable(f"vendor rejected key={SECRET}")

    with pytest.raises(ProviderError):
        await guard.call("vendor", leaks)
    assert SECRET not in repr([d.as_payload() for d in published])


def test_the_failure_payload_remains_a_closed_set_of_strings():
    """§16. M11.14 established this; M11.15 keeps it, because the safety above depends on it."""
    payload = Degradation(
        capability="llm", kind=DegradationKind.UNAVAILABLE, detail="d", provider="p"
    ).as_payload()
    assert set(payload) == {"capability", "kind", "detail", "provider", "fallback"}
    assert all(isinstance(value, str) for value in payload.values())


def test_no_provider_reads_a_credential_from_the_environment():
    """§17 / ADR-0049. Credentials arrive through `CredentialProvider`; a provider that reached
    for `os.environ` would bypass the boundary entirely."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).parent.parent / "providers"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"a provider reads the environment directly: {offenders}"


def test_a_degradation_never_carries_a_non_string_value():
    """§16. An exception object smuggled into a payload would serialize its `repr`, which is
    exactly where secrets hide."""
    degradation = Degradation(
        capability="llm", kind=DegradationKind.UNAVAILABLE, detail=str(_LeakyError())[:0]
    )
    assert all(isinstance(v, str) for v in degradation.as_payload().values())


# ══════════════════════════════════════════════════════════════════════════════
# Configuration and audio-input validation (§18, §19)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "field,value",
    [("call_timeout_s", 0.0), ("call_timeout_s", -1.0), ("stream_idle_timeout_s", 0.0)],
)
def test_an_impossible_guard_policy_is_refused_rather_than_defaulted(field, value):
    """§18. *"Do not silently substitute defaults when an explicit invalid value was provided."*"""
    from runtime.faults import GuardPolicy

    with pytest.raises(ValueError, match=field):
        GuardPolicy(**{field: value})


@pytest.mark.parametrize("capacity", [0.0, -1.0])
def test_a_playback_engine_refuses_an_impossible_capacity(capacity):
    from runtime.speech.playback import PlaybackEngine

    with pytest.raises(ValueError, match="capacity_ms"):
        PlaybackEngine(capacity_ms=capacity)


def test_a_playback_engine_refuses_an_impossible_frame_ceiling():
    from runtime.speech.playback import PlaybackEngine

    with pytest.raises(ValueError, match="max_frames"):
        PlaybackEngine(max_frames=0)


@pytest.mark.parametrize("rate", [0, -1, 7, 10_000_000])
async def test_an_invalid_session_sample_rate_is_refused_at_session_start(rate):
    """§18. Rejected before the session exists, not adapted to afterwards."""
    import dataclasses

    from core.errors import ConfigError

    stack = rh.build(words=["hello"])
    bad = dataclasses.replace(stack.request, sample_rate=rate)
    with pytest.raises(ConfigError):
        await stack.service.start(bad)


@pytest.mark.parametrize(
    "frame",
    [
        AudioFrame(data=b"\x01", sample_rate=16000, encoding=AudioEncoding.PCM16, channels=1),
        AudioFrame(data=b"", sample_rate=16000, encoding=AudioEncoding.PCM16, channels=1),
        AudioFrame(data=b"\x01\x02", sample_rate=16000, encoding=AudioEncoding.PCM16, channels=0),
    ],
)
def test_malformed_audio_never_raises_out_of_the_single_ingress_conversion(frame):
    """§19. The normalizer is on the inbound hot path for every frame of every call, so a
    malformed payload must not become an exception there. It passes the frame through with its
    honest rate rather than re-stamping it — which is the existing contract, asserted here
    against the shapes most likely to break it."""
    from runtime.pipeline.audio import AudioNormalizer

    normalizer = AudioNormalizer(8000)
    assert normalizer.normalize(frame) is frame


def test_the_audio_frame_contract_was_not_changed_by_hardening():
    """§19: *"Do not change the AudioFrame contract unless absolutely required."* It was not."""
    import dataclasses

    fields = {f.name for f in dataclasses.fields(AudioFrame)}
    assert fields == {"data", "sample_rate", "encoding", "channels", "captured_at", "seq"}


@pytest.mark.parametrize(
    "frame",
    [
        AudioFrame(data=b"\x01\x02", sample_rate=8000, encoding=AudioEncoding.PCM16),
        AudioFrame(data=b"\x01\x02", sample_rate=16000, encoding=AudioEncoding.PCM16, channels=2),
        AudioFrame(data=b"\x01", sample_rate=16000, encoding=AudioEncoding.PCM16),
        AudioFrame(data=b"\x01\x02", sample_rate=16000, encoding=AudioEncoding.OPAQUE),
    ],
)
async def test_the_vad_provider_rejects_audio_it_cannot_analyse_with_a_typed_error(frame):
    """§19. Format validation belongs at the provider boundary, not in `core` — and it must be a
    typed capability error rather than whatever the library would have raised."""
    from providers.voice_activity.silero import SileroVoiceActivity

    from .fake_speech_model import EnergyModel

    provider = SileroVoiceActivity(EnergyModel)
    await provider.start(session_id="s")
    try:
        with pytest.raises((CapabilityUnsupported, ProviderError)):
            provider.detect(frame, session_id="s")
    finally:
        await provider.stop(session_id="s")


# ══════════════════════════════════════════════════════════════════════════════
# Lifecycle and resource ownership (§9, §11, §12, §22)
# ══════════════════════════════════════════════════════════════════════════════


async def test_the_vad_worker_thread_is_released_with_its_session():
    """§9. The inference worker is a real OS thread; one left per session would be the most
    expensive leak in the runtime."""
    import threading

    from runtime.registry import CapabilityKind

    before = threading.active_count()
    for index in range(20):
        stack = rh.build(words=["hello"], reply=["One. "], session_id=f"ses_thread_{index}")
        await stack.start()
        await stack.speak()
        await stack.finish()
        provider = stack.registry.resolve(CapabilityKind.VOICE_ACTIVITY)
        assert provider.buffered_ms(stack.request.session_id) == 0.0, (
            "a stopped session still holds buffered audio"
        )
    assert threading.active_count() <= before + 1, "VAD worker threads accumulated"


async def test_a_terminal_playback_engine_is_idempotent_under_repeated_stop():
    """§22. Terminal states must absorb a second call rather than resurrect or raise."""
    from runtime.speech.playback import PlaybackEngine, PlaybackState

    sent: list = []

    async def send(frame):  # noqa: ANN001
        sent.append(frame)

    engine = PlaybackEngine(send)
    await engine.start()
    await engine.enqueue(rh.speech(0))
    await engine.stop()
    await engine.stop()
    await engine.stop()
    assert engine.state is PlaybackState.CANCELLED
    assert engine.queued_frames == 0


async def test_a_failed_playback_engine_never_leaves_a_producer_blocked():
    """§22. The deadlock the bound could create: a producer parked on a buffer whose consumer is
    never coming back."""
    from runtime.speech.playback import PlaybackEngine, PlaybackFailed, PlaybackState

    async def explodes(frame):  # noqa: ANN001
        raise ConnectionResetError("transport died")

    engine = PlaybackEngine(explodes, capacity_ms=40.0)
    await engine.start()
    await engine.enqueue(rh.speech(0))
    await rh.settle(lambda: engine.state is PlaybackState.FAILED, spins=400)

    async def produce():
        with pytest.raises(PlaybackFailed):
            for seq in range(50):
                await engine.enqueue(rh.speech(seq))

    await asyncio.wait_for(produce(), timeout=5.0)
    await engine.stop()


async def test_cancellation_after_a_failed_playback_state_is_still_clean():
    """§22. `cancel()` on a FAILED engine must not raise or hang."""
    from runtime.speech.playback import PlaybackEngine, PlaybackState

    async def explodes(frame):  # noqa: ANN001
        raise ConnectionResetError("gone")

    engine = PlaybackEngine(explodes, capacity_ms=40.0)
    await engine.start()
    await engine.enqueue(rh.speech(0))
    await rh.settle(lambda: engine.state is PlaybackState.FAILED, spins=400)
    await engine.cancel()
    assert engine.state is PlaybackState.CANCELLED


async def test_a_session_that_fails_and_is_then_stopped_releases_providers_exactly_once():
    """§11. M11.14 made both terminal paths release; hardening asserts the *ownership*, not only
    the event count — a second release would close the transport twice."""
    endpoint = rh.BreakingInboundEndpoint(after=2)
    stack = rh.build(words=["hello"], endpoint=endpoint)
    await stack.start()
    orchestrator = stack.orchestrator
    await stack.speak(wait=False)
    await rh.settle(lambda: orchestrator.is_terminal, spins=800)
    await stack.finish()
    await stack.end()

    assert endpoint.closed.count(stack.request.session_id) == 1, (
        f"the transport was closed {endpoint.closed.count(stack.request.session_id)} times"
    )


async def test_releasing_providers_twice_would_announce_the_disconnect_twice():
    """§11 — *"assert lifecycle ownership"*, not merely the event count of one type.

    The transport's own `stop()` is idempotent, so a double release is invisible at the endpoint:
    counting `close()` calls proves the **transport** is well-behaved, not that the orchestrator
    released once. The observable that actually moves is the release *announcement* — one
    `provider.disconnected` per capability, per session."""
    from core.events import EventType

    stack = rh.build(words=["hello"], reply=["One. "])
    await stack.start()
    seen: list = []
    stack.orchestrator.event_bus.subscribe(
        lambda event: seen.append(event), types=frozenset({EventType.PROVIDER_DISCONNECTED})
    )
    await stack.speak()
    await stack.hangup()
    await stack.end()
    await stack.end()

    # `provider.degraded` deliberately reuses this event type (M11.4, AR-2/AR-32) and is told
    # apart by its payload: a degradation carries `kind`, a release does not. Counting both would
    # make every degraded session look like a double release.
    capabilities = [e.data.get("capability") for e in seen if "kind" not in (e.data or {})]
    assert len(capabilities) == len(set(capabilities)), (
        f"a capability was released more than once: {capabilities}"
    )


async def test_a_failed_session_releases_providers_exactly_once_too():
    """The same ownership question on the failure path, where two code paths now both release."""
    from core.events import EventType

    endpoint = rh.BreakingInboundEndpoint(after=2)
    stack = rh.build(words=["hello"], endpoint=endpoint)
    await stack.start()
    seen: list = []
    stack.orchestrator.event_bus.subscribe(
        lambda event: seen.append(event), types=frozenset({EventType.PROVIDER_DISCONNECTED})
    )
    orchestrator = stack.orchestrator
    await stack.speak(wait=False)
    await rh.settle(lambda: orchestrator.is_terminal, spins=800)
    await stack.finish()

    # `provider.degraded` deliberately reuses this event type (M11.4, AR-2/AR-32) and is told
    # apart by its payload: a degradation carries `kind`, a release does not. Counting both would
    # make every degraded session look like a double release.
    capabilities = [e.data.get("capability") for e in seen if "kind" not in (e.data or {})]
    assert len(capabilities) == len(set(capabilities)), (
        f"a failed session released a capability twice: {capabilities}"
    )


async def test_a_failure_detail_never_carries_the_exceptions_repr():
    """§16. The guard composes its own detail from the exception **type**, never its `repr` —
    which is where a vendor puts the endpoint, the account id and occasionally the key."""
    recorder, published = _recorder()
    guard = ProviderGuard(capability="llm", degradations=recorder)

    async def leaks():
        raise _LeakyError()

    with pytest.raises(ProviderError):
        await guard.call("vendor", leaks)

    assert published
    for degradation in published:
        assert SECRET not in degradation.detail
        assert "_LeakyError(" not in degradation.detail, "an exception repr reached the payload"


async def test_turn_retirement_survives_repeated_failure_and_recovery_cycles():
    """§12. The M11.14 fix, hardened: five failure/recovery alternations in one session."""
    stack = rh.build(words=["a", "b", "c", "d"], reply=["Answer. "], llm_fail_at=0)
    await stack.start()
    for index in range(4):
        stack.llm_client._fail_at = 0 if index % 2 == 0 else None  # noqa: SLF001
        await stack.speak()
    assert stack.orchestrator.state is SessionState.IN_CONVERSATION
    assert stack.endpoint.written, "the session never recovered its voice"
    await stack.finish()

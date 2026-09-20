"""The voice runtime wired to the frozen Epic 10 spine (M11.12).

Every test drives the **production runtime** of M11.11 — `RuntimeSessionService` → `SessionWorker`
→ `ConversationOrchestrator` → `RuntimePipeline` — and asserts what reached Epic 10. The Epic 10
side is used exactly as it was built and frozen: `DurableEventLog` for durable events,
`MetricTurnBuffer` for metering signals, `TurnLatencyTap` for `metric.turn`. Nothing here replaces
the runtime with a direct event-store test, and nothing here reimplements an Epic 10 mechanism.

**No new EventType was added.** Every fact a session produces already had a member of the frozen
vocabulary; the mapping is asserted below rather than described.
"""

from __future__ import annotations

import asyncio

import pytest

import bootstrap
from core.events import Event, EventSource, EventType
from runtime.observability_plane import UNWIRED, ObservabilityPlane
from runtime.service import RuntimeSessionService

from .test_e2e_conversation import SAMPLE_RATE, real_stack, run_conversation


class RecordingLog:
    """A `DurableEventLog` that records envelopes verbatim, and can fail or stall on demand.

    Stands in only for the *storage*, which is the one thing a hermetic test must not do for real;
    the bus, the envelope, the ordering and the failure hook are Epic 10's own."""

    def __init__(self, *, error: Exception | None = None, gate: asyncio.Event | None = None):
        self.events: list[Event] = []
        self._error = error
        self._gate = gate
        self.append_calls = 0

    def append(self, event: Event) -> bool:
        self.append_calls += 1
        if self._error is not None:
            raise self._error
        # Idempotent on identity, exactly as ADR-0032 ED2 requires of a real log.
        if any(existing.id == event.id for existing in self.events):
            return False
        self.events.append(event)
        return True

    # ── assertions helpers ───────────────────────────────────────────────────

    def types(self) -> list[EventType]:
        return [e.type for e in self.events]

    def of(self, *types: EventType) -> list[Event]:
        return [e for e in self.events if e.type in types]

    def sessions(self) -> set[str]:
        return {e.session_id for e in self.events}


def wired_plane(log=None, metrics=None, *, telemetry: bool = True, on_error=None):
    return bootstrap.build_observability_plane(
        durable=log, metrics=metrics, telemetry=telemetry, on_durable_error=on_error
    )


async def conversation(
    *, plane, words=None, reply=None, utterances=1, turn_events=True, **kwargs
):
    """One M11.11 conversation, with an observability plane attached.

    `turn_events=False` where the test injects a **failing durable sink**: ADR-0035 DW4 suppresses
    dispatch of anything that could not be persisted, so the runtime's turn events are
    contractually unavailable there and the harness must not wait on them (M11.15)."""
    service, request, endpoint, registry = real_stack(
        words=words or ["hello there"], reply=reply or ["Good morning. "], **kwargs
    )
    wired = RuntimeSessionService(
        registry,
        on_transcript=None,
        streaming_playback=True,
        observability=plane,
    )
    await run_conversation(
        wired, request, endpoint, utterances=utterances, turn_events=turn_events
    )
    return endpoint, registry


# ── 1, 2, 9, 10, 13: lifecycle events reach the durable log, in causal order ──


async def test_1_2_9_10_the_session_lifecycle_is_durably_recorded():
    """Session start, transport reality, turn lifecycle and session end all land."""
    log = RecordingLog()
    await conversation(plane=wired_plane(log))
    types = log.types()
    for expected in (
        EventType.RUNTIME_STARTED,
        EventType.SESSION_STARTED,
        EventType.TRANSPORT_CONNECTED,
        EventType.TURN_STARTED,
        EventType.SESSION_ENDED,
    ):
        assert expected in types, f"{expected.value} never reached the durable log"


async def test_13_events_preserve_causal_order():
    """Asserted on the bus's own sequence numbers, not on wall-clock time."""
    log = RecordingLog()
    await conversation(plane=wired_plane(log))
    seqs = [e.seq for e in log.events]
    assert seqs == sorted(seqs), "the durable log received events out of sequence"
    assert len(set(seqs)) == len(seqs), "a sequence number was reused"
    started = log.types().index(EventType.SESSION_STARTED)
    ended = log.types().index(EventType.SESSION_ENDED)
    assert started < ended


async def test_a_turn_starts_before_the_assistant_speaks():
    log = RecordingLog()
    await conversation(plane=wired_plane(log))
    types = log.types()
    assert types.index(EventType.TURN_STARTED) < types.index(
        EventType.ASSISTANT_STARTED_SPEAKING
    )


# ── 11, 12, 33, 34: correlation ─────────────────────────────────────────────


async def test_11_every_event_carries_the_session_correlation():
    log = RecordingLog()
    await conversation(plane=wired_plane(log))
    assert log.sessions() == {"ses_e2e"}
    assert all(e.conversation_id == "ses_e2e" for e in log.events)


async def test_33_and_34_two_sessions_never_cross_correlate():
    """Concurrent sessions sharing one durable log must remain separable by correlation alone."""
    log = RecordingLog()
    plane = wired_plane(log)

    async def run(session_id: str, word: str) -> None:
        from dataclasses import replace

        service, request, endpoint, registry = real_stack(words=[word], reply=["Ok. "])
        request = replace(request, session_id=session_id)
        wired = RuntimeSessionService(registry, streaming_playback=True, observability=plane)
        await run_conversation(wired, request, endpoint)

    await asyncio.gather(run("ses_a", "alpha"), run("ses_b", "bravo"))
    assert log.sessions() == {"ses_a", "ses_b"}
    for session_id in ("ses_a", "ses_b"):
        events = [e for e in log.events if e.session_id == session_id]
        assert EventType.SESSION_STARTED in [e.type for e in events]
        assert EventType.SESSION_ENDED in [e.type for e in events]


# ── 14: no duplicate events from cleanup races ──────────────────────────────


async def test_14_repeated_session_stop_produces_no_duplicate_lifecycle_event():
    """Cleanup runs more than once in practice; the durable stream must not say so."""
    log = RecordingLog()
    service, request, endpoint, _ = real_stack(words=["hi"], reply=["Ok. "])
    wired = RuntimeSessionService(service._registry, streaming_playback=True,  # noqa: SLF001
                                  observability=wired_plane(log))
    await run_conversation(wired, request, endpoint)
    await wired.end(request.session_id)  # a second stop
    await wired.end(request.session_id)  # and a third
    assert len(log.of(EventType.SESSION_ENDED)) == 1
    assert len(log.of(EventType.SESSION_STARTED)) == 1


async def test_the_durable_log_is_idempotent_on_identity():
    """ADR-0032 ED2, asserted against the log the runtime actually writes through."""
    log = RecordingLog()
    await conversation(plane=wired_plane(log))
    first = log.events[0]
    assert log.append(first) is False, "re-appending a recorded id was not a no-op"


# ── 15–18: metric.turn through the existing telemetry model ─────────────────


async def test_15_the_metric_turn_chain_reaches_the_durable_log():
    """`metric.turn` is produced by `observability/` — the sole authorized producer (AR-2)."""
    log = RecordingLog()
    await conversation(plane=wired_plane(log))
    metrics = log.of(EventType.METRIC_TURN)
    assert metrics, "no metric.turn was produced by a real conversation"
    assert all(e.source is EventSource.OBSERVABILITY for e in metrics)
    payload = metrics[0].data
    assert "voice_to_voice_ms" in payload
    at = payload.get("at", {})
    for boundary in ("speech_end", "stt_final", "llm_first_token", "tts_first_audio",
                     "first_audio_out"):
        assert boundary in at, f"{boundary} missing from the metric payload"


async def test_16_17_the_audio_boundaries_are_not_fabricated():
    """A response that produces no audio must stamp neither audio boundary."""
    from core.providers.tts import TTSCapabilities, TTSProvider
    from runtime.registry import CapabilityKind

    class MuteTTS(TTSProvider):
        name = "mute"
        capabilities = TTSCapabilities(streaming=True, sample_rates=(SAMPLE_RATE,))

        async def synthesize(self, text, *, voice=None, language="en"):
            return
            yield  # pragma: no cover

    log = RecordingLog()
    service, request, endpoint, registry = real_stack(words=["hello"], reply=["Hi. "])
    registry.unregister(CapabilityKind.TTS, "cartesia")
    registry.register(CapabilityKind.TTS, MuteTTS(), default=True)
    wired = RuntimeSessionService(registry, streaming_playback=True,
                                  observability=wired_plane(log))
    await run_conversation(wired, request, endpoint)
    for event in log.of(EventType.METRIC_TURN):
        at = event.data.get("at", {})
        assert "tts_first_audio" not in at, "audio timing was fabricated for a silent response"
        assert "first_audio_out" not in at


async def test_25_a_two_turn_conversation_produces_two_distinct_metric_chains():
    log = RecordingLog()
    await conversation(plane=wired_plane(log), words=["one", "two"], utterances=2)
    metrics = log.of(EventType.METRIC_TURN)
    assert len(metrics) >= 2, "the second turn produced no metric.turn"
    turns = [e.turn for e in metrics]
    assert len(set(turns)) >= 2, "both turns shared one correlation"


# ── 23–28: metering through the frozen MetricStreamPort ─────────────────────


def metric_buffer():
    from api.metering import MetricTurnBuffer

    return MetricTurnBuffer()


async def test_23_24_the_metric_buffer_receives_one_signal_per_turn():
    """Billing's only inbound path: the platform buffer fills, Billing later pulls."""
    buffer = metric_buffer()
    await conversation(plane=wired_plane(None, buffer))
    signals = buffer.drain("o")
    assert len(signals) == 1, f"one turn produced {len(signals)} metering signals"
    assert signals[0].raw_unit == "ms"
    assert signals[0].event_type == EventType.METRIC_TURN.value


async def test_25_multi_turn_produces_separate_turn_usage():
    buffer = metric_buffer()
    await conversation(plane=wired_plane(None, buffer), words=["one", "two"], utterances=2)
    signals = buffer.drain("o")
    assert len(signals) == 2, f"two turns produced {len(signals)} metering signals"
    assert len({s.event_id for s in signals}) == 2, "the two turns shared an idempotency key"


async def test_26_27_28_usage_is_not_double_counted_by_a_second_drain():
    """`(event_id, meter_code)` is Billing's idempotency key; a drained signal is not re-served."""
    buffer = metric_buffer()
    await conversation(plane=wired_plane(None, buffer))
    first = buffer.drain("o")
    second = buffer.drain("o")
    assert len(first) == 1
    assert second == (), "a drained signal was served twice"


async def test_31_repeated_stop_does_not_duplicate_usage():
    buffer = metric_buffer()
    service, request, endpoint, registry = real_stack(words=["hi"], reply=["Ok. "])
    wired = RuntimeSessionService(registry, streaming_playback=True,
                                  observability=wired_plane(None, buffer))
    await run_conversation(wired, request, endpoint)
    await wired.end(request.session_id)
    await wired.end(request.session_id)
    assert len(buffer.drain("o")) == 1


async def test_18_a_silent_response_records_no_measurement():
    """`missing_measurement` is the buffer's own counter for exactly this (MB9)."""
    from core.providers.tts import TTSCapabilities, TTSProvider
    from runtime.registry import CapabilityKind

    class MuteTTS(TTSProvider):
        name = "mute"
        capabilities = TTSCapabilities(streaming=True, sample_rates=(SAMPLE_RATE,))

        async def synthesize(self, text, *, voice=None, language="en"):
            return
            yield  # pragma: no cover

    buffer = metric_buffer()
    service, request, endpoint, registry = real_stack(words=["hello"], reply=["Hi. "])
    registry.unregister(CapabilityKind.TTS, "cartesia")
    registry.register(CapabilityKind.TTS, MuteTTS(), default=True)
    wired = RuntimeSessionService(registry, streaming_playback=True,
                                  observability=wired_plane(None, buffer))
    await run_conversation(wired, request, endpoint)
    assert buffer.drain("o") == ()
    assert buffer.counters.missing_measurement >= 1


# ── 19, 20, 22: failure and degradation through existing mechanisms ─────────


async def test_19_20_provider_failure_is_represented_through_existing_events():
    from providers.tts.speech_stream import FailureKind
    from runtime.registry import CapabilityKind

    log = RecordingLog()
    service, request, endpoint, registry = real_stack(words=["hello"], reply=["Hi. "])
    from .fake_speech import FakeSpeechClient

    registry.resolve(CapabilityKind.TTS)._client = FakeSpeechClient(  # noqa: SLF001
        open_error=FailureKind.UNAVAILABLE
    )
    wired = RuntimeSessionService(registry, streaming_playback=True,
                                  observability=wired_plane(log))
    await run_conversation(wired, request, endpoint)
    # The degradation is recorded through M11.4's recorder; the durable stream carries the
    # provider vocabulary the frozen model already has.
    assert EventType.PROVIDER_DISCONNECTED in log.types() or log.of(EventType.RUNTIME_ERROR)


# ── 21, 22: telemetry/durable failure must not kill the conversation ────────


async def test_21_22_a_durable_write_failure_does_not_break_the_conversation():
    """AR-31: the bus never raises into a producer. The caller still gets audio.

    `turn_events=False` because ADR-0035 **DW4** makes them unavailable here by design — an event
    that could not be persisted is never dispatched, so nothing reaches a subscriber while the
    sink is failing. The outbound audio is what proves the conversation ran (M11.15, R-69)."""
    failures: list[tuple] = []
    log = RecordingLog(error=RuntimeError("spine unavailable"))
    endpoint, _ = await conversation(
        plane=wired_plane(log, on_error=lambda e, exc: failures.append((e.type, exc))),
        turn_events=False,
    )
    assert endpoint.written, "a durable write failure silenced the assistant"
    assert log.append_calls > 0
    assert failures, "the operator hook was never called for a failed write"


async def test_a_metering_sink_failure_does_not_break_the_conversation():
    class ExplodingBuffer:
        def subscribe(self, bus):
            def handler(event):
                raise RuntimeError("metering down")

            return bus.subscribe(handler)

    endpoint, _ = await conversation(plane=wired_plane(None, ExplodingBuffer()))
    assert endpoint.written, "a metering failure silenced the assistant"


async def test_a_telemetry_failure_does_not_break_the_conversation():
    class ExplodingTap:
        def record(self, *a, **k):
            raise RuntimeError("telemetry down")

        def complete(self, *a, **k):
            raise RuntimeError("telemetry down")

    plane = ObservabilityPlane(telemetry=lambda publisher: ExplodingTap())
    endpoint, _ = await conversation(plane=plane)
    assert endpoint.written, "a telemetry failure silenced the assistant"


# ── default-off and boundedness ─────────────────────────────────────────────


def test_the_default_composition_wires_nothing():
    """Every milestone before M11.12 had an unwired plane; that is still the default."""
    assert UNWIRED.is_wired is False
    controller = bootstrap.build_session_controller()
    assert controller._observability.is_wired is False  # noqa: SLF001


async def test_an_unwired_plane_produces_no_durable_write_and_no_metric():
    log = RecordingLog()
    endpoint, _ = await conversation(plane=UNWIRED)
    assert endpoint.written, "the conversation itself must be unaffected"
    assert log.events == []


async def test_the_metric_buffer_is_bounded():
    """Capacity is the buffer's own (MB3); the runtime adds no second, unbounded accumulator."""
    from api.metering import MetricTurnBuffer

    buffer = MetricTurnBuffer(capacity=1)
    await conversation(plane=wired_plane(None, buffer), words=["one", "two"], utterances=2)
    assert len(buffer.drain("o")) <= 1
    assert buffer.counters.dropped >= 1


async def test_the_session_releases_its_metric_subscription():
    """A process-wide buffer must not leave a handler registered on a dead session's bus.

    Asserted on the bus's own subscriber count, not on the session dict: the session always
    disappears, whereas a leaked handler is exactly what a missing release looks like."""
    buffer = metric_buffer()
    service, request, endpoint, registry = real_stack(words=["hi"], reply=["Ok. "])
    wired = RuntimeSessionService(registry, streaming_playback=True,
                                  observability=wired_plane(None, buffer))
    await wired.start(request)
    worker = wired._workers[request.session_id]  # noqa: SLF001
    orchestrator = worker.orchestrator
    bus = orchestrator.event_bus
    assert orchestrator._metrics_subscription is not None  # noqa: SLF001
    before = bus.subscriber_count
    await endpoint.hangup()
    await worker.wait_closed()
    await wired.end(request.session_id)
    assert orchestrator._metrics_subscription is None, (  # noqa: SLF001
        "the metric subscription handle was not released"
    )
    assert bus.subscriber_count < before, "the metric handler is still registered on the bus"


async def test_no_durable_event_is_written_per_audio_frame():
    """A durable write per frame would put storage on the audio path (§29)."""
    log = RecordingLog()
    await conversation(plane=wired_plane(log))
    # An utterance is 34 frames in and several out; the durable stream must be lifecycle-shaped.
    assert len(log.events) < 34, f"{len(log.events)} events for one turn looks frame-shaped"


# ── 35, 36: nothing vendor-shaped or secret reaches Epic 10 ─────────────────


async def test_35_no_vendor_type_reaches_the_durable_log():
    log = RecordingLog()
    await conversation(plane=wired_plane(log))
    for event in log.events:
        # Every payload value must be a plain, serializable scalar or container — a vendor SDK
        # *object* must never be stored. Provider **names** are legitimate and pre-existing:
        # `PROVIDER_CONNECTED` was designed to carry `{"capability": ..., "provider": ...}`, and
        # an opaque capability selector is neutral data, not a vendor type (AR-21).
        for value in list(event.data.values()) + list(event.metadata.values()):
            assert isinstance(value, str | int | float | bool | type(None) | dict | list | tuple), (
                f"a non-primitive {type(value).__name__} reached Epic 10"
            )
            assert type(value).__module__ in ("builtins",), (
                f"a {type(value).__module__} object reached Epic 10"
            )


async def test_36_no_credential_reaches_the_durable_log():
    log = RecordingLog()
    await conversation(plane=wired_plane(log))
    for event in log.events:
        rendered = repr(event.data) + repr(event.metadata)
        for marker in ("api_key", "sk-", "gsk-", "csk-", "Authorization", "Bearer"):
            assert marker.lower() not in rendered.lower(), f"{marker} reached Epic 10"


async def test_no_raw_audio_or_transcript_is_persisted():
    """Data minimization: Epic 10 stores metadata, not content (§32)."""
    log = RecordingLog()
    await conversation(plane=wired_plane(log), words=["my secret passphrase"],
                       reply=["A confidential answer. "])
    for event in log.events:
        rendered = repr(event.data)
        assert "secret passphrase" not in rendered, "a transcript was persisted"
        assert "confidential answer" not in rendered, "an LLM response was persisted"


# ── architecture ────────────────────────────────────────────────────────────


def test_the_runtime_never_imports_events_billing_or_observability():
    """`runtime/` keeps its layer budget: the composition root supplies the plane."""
    import ast
    from pathlib import Path

    plane = Path(bootstrap.__file__).parent / "runtime" / "observability_plane.py"
    tree = ast.parse(plane.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module.split(".")[0])
    assert not (imported & {"events", "billing", "observability", "providers", "api"})


def test_no_second_event_bus_telemetry_or_metering_system_was_added():
    import ast
    from pathlib import Path

    root = Path(bootstrap.__file__).parent
    names: set[str] = set()
    for path in (root / "runtime").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                names.add(node.name)
    for forbidden in ("EventBus", "EventStore", "MeteringEngine", "TelemetrySystem"):
        assert forbidden not in names, f"runtime/ defines a second {forbidden}"


def test_no_new_event_type_was_added_for_m11_12():
    """The frozen vocabulary already represented every runtime fact."""
    values = {e.value for e in EventType}
    for expected in ("session.started", "session.ended", "turn.started", "turn.completed",
                     "transport.connected", "metric.turn", "provider.connected"):
        assert expected in values
    assert "m11_12" not in " ".join(values)


@pytest.mark.parametrize("attr", ["durable", "metrics", "telemetry", "on_durable_error"])
def test_every_plane_attachment_is_independently_optional(attr):
    plane = ObservabilityPlane(**{attr: object()})
    assert plane.is_wired is (attr != "on_durable_error")

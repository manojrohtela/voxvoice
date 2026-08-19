"""Per-turn latency taps and the `metric.turn` producer (M10.9) — the executable form of ADR-0038.

The first tests in this repository in which **AR-42** is actually satisfied: *"every stage
boundary in the conversation loop carries a latency tap feeding `metric.turn`."*

Two halves. The **producer** half exercises `TurnLatencyTap` against a fake publisher and a frozen
monotonic clock — no pipeline, because the derivation is a pure function of instants and a
conversation would only slow the proof down. The **integration** half drives a real
`RuntimePipeline` with stub providers to prove the boundaries are captured at the right call
sites, including the three that live inside `async for` loops and that no bus-derived design could
ever see.

Nothing here sleeps, reads a wall clock, opens a socket or starts a thread. Every duration is
asserted **by value**, which is possible only because the clock is injected (ADR-0038 TP7).
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from core.events import EventMetadata, EventSource, EventType
from events import InProcessEventBus
from observability import (
    KNOWN_BOUNDARIES,
    LIFECYCLE_MARKERS,
    STAGE_BOUNDARIES,
    STAGE_INTERVALS,
    TERMINAL_STATUSES,
    TurnLatencyTap,
)
from runtime.contracts import LatencyObserver


class StepClock:
    """Deterministic monotonic clock: every read advances by `step` seconds."""

    def __init__(self, start: float = 100.0, step: float = 0.010) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


class FrozenClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class FakePublisher:
    """Captures published (type, data, turn) triples. Structurally a `MetricPublisher`."""

    def __init__(self) -> None:
        self.published: list[tuple[EventType, dict, int | None]] = []

    def publish(self, type, data=None, *, turn=None, correlation_id=None, metadata=None):
        self.published.append((type, data or {}, turn))
        return None


def tap(clock=None) -> tuple[TurnLatencyTap, FakePublisher]:
    publisher = FakePublisher()
    return TurnLatencyTap(publisher, clock=clock or FrozenClock()), publisher


def drive(t: TurnLatencyTap, boundaries, *, session="s-1", turn=1) -> None:
    for boundary in boundaries:
        t.record(session, turn, boundary)


# ── 1-2. the enumeration itself ─────────────────────────────────────────────


def test_the_six_ar42_boundaries_are_exactly_these():
    """(1) AR-42 is a merge gate — *"a new stage without a tap doesn't merge"* — so the list it
    gates against is pinned here, as an exact tuple in real pipeline order (ADR-0038 TP3/TP3a)."""
    assert STAGE_BOUNDARIES == (
        "speech_end",
        "stt_final",
        "endpoint_decided",
        "llm_first_token",
        "tts_first_audio",
        "first_audio_out",
    )


def test_playback_start_is_a_lifecycle_marker_not_a_latency_stage():
    """(2) It is recorded, but no duration is derived across it."""
    assert LIFECYCLE_MARKERS == ("playback_start",)
    assert "playback_start" not in STAGE_BOUNDARIES
    derived = {name for name, _, _ in STAGE_INTERVALS}
    endpoints = {a for _, a, _ in STAGE_INTERVALS} | {b for _, _, b in STAGE_INTERVALS}
    assert "playback_start" not in derived
    assert "playback_start" not in endpoints


def test_the_stage_chain_is_contiguous_over_the_six_boundaries():
    """Each stage begins where the previous ended — which is what makes the five stages sum to
    voice-to-voice, and what would expose a boundary quietly dropped from the chain."""
    assert [a for _, a, _ in STAGE_INTERVALS] == list(STAGE_BOUNDARIES[:-1])
    assert [b for _, _, b in STAGE_INTERVALS] == list(STAGE_BOUNDARIES[1:])


def test_an_unknown_boundary_is_refused_loudly():
    """Drift between the pipeline and the enumeration is exactly what AR-42's list exists to
    catch, so it raises rather than being silently recorded."""
    t, _ = tap()
    with pytest.raises(ValueError, match="unknown stage boundary"):
        t.record("s-1", 1, "llm_last_token")


def test_an_unknown_terminal_status_is_refused_loudly():
    t, _ = tap()
    with pytest.raises(ValueError, match="unknown terminal status"):
        t.complete("s-1", 1, "finished")


def test_the_terminal_statuses_are_exactly_the_three_ratified_ones():
    assert TERMINAL_STATUSES == frozenset({"completed", "cancelled", "interrupted"})


# ── 3-6. clock, derivation, voice-to-voice ──────────────────────────────────


def test_instants_come_from_the_injected_clock(monkeypatch):
    """(3) No ambient clock: a frozen clock produces a frozen instant."""
    t, publisher = tap(FrozenClock(500.0))
    drive(t, ("speech_end", "first_audio_out"))
    t.complete("s-1", 1, "completed")

    at = publisher.published[0][1]["at"]
    assert at == {"speech_end": 500.0, "first_audio_out": 500.0}


def test_durations_are_derived_by_value_from_a_stepping_clock():
    """(4)(5) 10 ms per boundary, asserted by value — possible only because the clock is
    injected and nothing sleeps."""
    t, publisher = tap(StepClock(start=0.0, step=0.010))
    drive(t, STAGE_BOUNDARIES)
    t.complete("s-1", 1, "completed")

    ms = publisher.published[0][1]["ms"]
    assert ms == {"stt": 10, "endpoint": 10, "llm": 10, "tts": 10, "egress": 10}


def test_voice_to_voice_is_speech_end_to_first_audio_out():
    """(6) The primary scalar — the number invariant I1 is about."""
    t, publisher = tap(StepClock(start=0.0, step=0.010))
    drive(t, STAGE_BOUNDARIES)
    t.complete("s-1", 1, "completed")

    payload = publisher.published[0][1]
    assert payload["voice_to_voice_ms"] == 50
    assert sum(payload["ms"].values()) == payload["voice_to_voice_ms"]


def test_voice_to_voice_is_none_when_either_endpoint_is_missing():
    t, publisher = tap(StepClock(start=0.0, step=0.010))
    drive(t, ("stt_final", "endpoint_decided"))
    t.complete("s-1", 1, "completed")

    assert publisher.published[0][1]["voice_to_voice_ms"] is None


def test_a_negative_interval_is_dropped_rather_than_emitted():
    """ADR-0038 TP5 — a negative latency is evidence of a defect, not a measurement."""
    t, publisher = tap(StepClock(start=0.0, step=-0.010))  # clock runs backwards
    drive(t, ("speech_end", "stt_final"))
    t.complete("s-1", 1, "completed")

    payload = publisher.published[0][1]
    assert "stt" not in payload["ms"]
    assert payload["voice_to_voice_ms"] is None


# ── 7. missing boundary is an absent key, never zero ────────────────────────


def test_an_unreached_boundary_is_an_absent_key_not_zero():
    """(7) A fabricated 0 ms is indistinguishable from a real one."""
    t, publisher = tap(StepClock(start=0.0, step=0.010))
    drive(t, ("speech_end", "stt_final"))  # LLM/TTS never reached
    t.complete("s-1", 1, "completed")

    payload = publisher.published[0][1]
    assert set(payload["at"]) == {"speech_end", "stt_final"}
    assert set(payload["ms"]) == {"stt"}
    for absent in ("llm", "tts", "egress"):
        assert absent not in payload["ms"]


def test_the_first_record_of_a_boundary_wins():
    """A boundary is when a stage was *first* reached; a re-record must not move it."""
    t, publisher = tap(StepClock(start=0.0, step=0.010))
    t.record("s-1", 1, "speech_end")  # 0.000
    t.record("s-1", 1, "speech_end")  # ignored
    t.record("s-1", 1, "stt_final")  # 0.010
    t.complete("s-1", 1, "completed")

    assert publisher.published[0][1]["ms"]["stt"] == 10


# ── 8-11. emission semantics ────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["completed", "cancelled", "interrupted"])
def test_each_terminal_outcome_emits_exactly_once(status):
    """(8)(9)(10) Cancelled and interrupted turns emit too — a barge-in 300 ms into synthesis is
    exactly the latency an operator needs."""
    t, publisher = tap()
    drive(t, ("speech_end", "stt_final"))
    t.complete("s-1", 1, status)

    assert len(publisher.published) == 1
    assert publisher.published[0][1]["status"] == status


def test_a_turn_with_no_boundaries_emits_nothing():
    """(11) There is no metric to report, and a zero-filled one would be a fabrication."""
    t, publisher = tap()
    t.complete("s-1", 1, "completed")
    assert publisher.published == []


def test_a_second_terminal_call_does_not_re_emit():
    """State is released at emission, so a stray terminal notification cannot resurrect it."""
    t, publisher = tap()
    drive(t, ("speech_end",))
    t.complete("s-1", 1, "completed")
    t.complete("s-1", 1, "completed")
    assert len(publisher.published) == 1


def test_the_event_type_and_turn_attribution_are_correct():
    t, publisher = tap()
    drive(t, ("speech_end",), turn=7)
    t.complete("s-1", 7, "completed")

    event_type, _, turn = publisher.published[0]
    assert event_type is EventType.METRIC_TURN
    assert turn == 7


# ── 18-20. lifecycle and isolation ──────────────────────────────────────────


def test_two_sessions_with_the_same_turn_index_never_collide():
    """(18)"""
    t, publisher = tap(StepClock(start=0.0, step=0.010))
    t.record("s-A", 1, "speech_end")
    t.record("s-B", 1, "speech_end")
    assert t.in_flight == 2

    t.complete("s-A", 1, "completed")
    t.complete("s-B", 1, "cancelled")
    assert [p[1]["status"] for p in publisher.published] == ["completed", "cancelled"]


def test_state_is_released_after_terminal_emission():
    """(19)"""
    t, _ = tap()
    drive(t, ("speech_end",))
    assert t.in_flight == 1
    t.complete("s-1", 1, "completed")
    assert t.in_flight == 0


def test_state_does_not_accumulate_across_turns():
    """(20) Bounded by in-flight turns, never by session lifetime (ADR-0038 TP8)."""
    t, _ = tap()
    for turn in range(50):
        t.record("s-1", turn, "speech_end")
        t.complete("s-1", turn, "completed")
    assert t.in_flight == 0


def test_the_tap_satisfies_the_runtime_observer_contract_structurally():
    """ADR-0038 TP2 — `runtime` imports no `observability`; they meet by shape."""
    assert isinstance(TurnLatencyTap(FakePublisher()), LatencyObserver)


# ── 21-24. envelope and vocabulary ──────────────────────────────────────────


def test_the_event_is_published_with_the_observability_source():
    """(21) ADR-0038 TP4 — the event names the component that produced it."""
    bus = InProcessEventBus()
    seen = []
    bus.subscribe(seen.append, types=EventType.METRIC_TURN)
    publisher = bus.publisher(
        EventMetadata(session_id="s-1", source=EventSource.OBSERVABILITY, org_id="o-1")
    )
    t = TurnLatencyTap(publisher, clock=FrozenClock())
    drive(t, ("speech_end",))
    t.complete("s-1", 1, "completed")

    assert len(seen) == 1
    assert seen[0].source is EventSource.OBSERVABILITY
    assert seen[0].type is EventType.METRIC_TURN


def test_metric_turn_remains_the_only_metric_event_type():
    """(23) M10.9 adds no EventType — the vocabulary was M10.8a's whole job."""
    assert {m.value for m in EventType if m.value.startswith("metric.")} == {"metric.turn"}


def test_existing_event_type_values_are_unchanged():
    """(22)"""
    assert EventType.COST_UPDATED.value == "cost.updated"
    assert EventType.TURN_STARTED.value == "turn.started"
    assert EventType.TOOL_METRICS_UPDATED.value == "tool.metrics.updated"
    assert len(list(EventType)) == 104


def test_attribution_is_not_duplicated_in_the_payload():
    """(24) Identity is the envelope's (ED3); a second copy is a second thing that can disagree."""
    bus = InProcessEventBus()
    seen = []
    bus.subscribe(seen.append, types=EventType.METRIC_TURN)
    publisher = bus.publisher(
        EventMetadata(session_id="s-1", source=EventSource.OBSERVABILITY, org_id="o-1")
    )
    t = TurnLatencyTap(publisher, clock=FrozenClock())
    drive(t, ("speech_end",))
    t.complete("s-1", 1, "completed")

    event = seen[0]
    assert set(event.data) == {"status", "at", "ms", "voice_to_voice_ms"}
    assert event.session_id == "s-1" and event.org_id == "o-1"


def test_the_payload_contains_only_the_ratified_keys():
    t, publisher = tap(StepClock(start=0.0, step=0.010))
    drive(t, STAGE_BOUNDARIES + LIFECYCLE_MARKERS)
    t.complete("s-1", 1, "completed")

    payload = publisher.published[0][1]
    assert set(payload) == {"status", "at", "ms", "voice_to_voice_ms"}
    assert set(payload["at"]) <= KNOWN_BOUNDARIES
    assert set(payload["ms"]) <= {"stt", "endpoint", "llm", "tts", "egress"}
    for forbidden in ("meter_code", "org_id", "session_id", "turn_index", "trace_id"):
        assert forbidden not in payload


# ── 12-17. pipeline integration: the real call sites ────────────────────────


class Recorder:
    """A `LatencyObserver` that records the call sequence, for call-site proofs."""

    def __init__(self) -> None:
        self.boundaries: list[str] = []
        self.terminals: list[str] = []

    def record(self, session_id, turn_index, boundary):
        self.boundaries.append(boundary)

    def complete(self, session_id, turn_index, status):
        self.terminals.append(status)


def build_pipeline(observer, *, tokens=("hi",), frames=2):
    """A `RuntimePipeline` over stub providers — no vendor, no network, no audio hardware."""
    from core.audio import AudioFrame
    from core.providers.llm import TokenDelta
    from core.providers.stt import Final
    from runtime.pipeline.pipeline import RuntimePipeline

    class STT:
        name = "stub-stt"

        async def transcribe(self, frames, *, language, sample_rate):
            yield Final(text="hello")

    class LLM:
        name = "stub-llm"

        async def stream(self, *, context):
            for token in tokens:
                yield TokenDelta(text=token)

    class TTS:
        name = "stub-tts"

        async def synthesize(self, text, *, voice=None, language=None):
            for _ in range(frames):
                yield AudioFrame(data=b"\x00\x00", sample_rate=16000)

    class Transport:
        name = "stub-transport"
        sent: list = []

        async def start(self, *, session_id): ...
        async def stop(self, *, session_id): ...
        async def send(self, *, session_id, frame):
            self.sent.append(frame)

        async def inbound(self, *, session_id):
            return
            yield  # pragma: no cover — empty async generator

    return RuntimePipeline(
        transport=Transport(),
        stt=STT(),
        llm=LLM(),
        tts=TTS(),
        session_id="s-1",
        system_prompt="be brief",
        latency=observer,
    )


def test_the_streaming_boundaries_are_captured_at_the_real_call_sites():
    """(12)(13)(14) The three boundaries that live inside `async for` loops — the ones no
    bus-derived design can observe, and the reason M10.9 needed a runtime seam at all."""
    observer = Recorder()
    pipeline = build_pipeline(observer)
    asyncio.run(pipeline.run())

    for boundary in ("stt_final", "llm_first_token", "tts_first_audio", "first_audio_out"):
        assert boundary in observer.boundaries, f"{boundary} was never captured"


def test_each_streaming_boundary_is_captured_once_despite_many_items():
    """First token / first frame — not every token and every frame."""
    observer = Recorder()
    pipeline = build_pipeline(observer, tokens=("a", "b", "c", "d"), frames=5)
    asyncio.run(pipeline.run())

    assert observer.boundaries.count("llm_first_token") == 1
    assert observer.boundaries.count("tts_first_audio") == 1
    assert observer.boundaries.count("first_audio_out") == 1


def test_the_pipeline_reaches_a_terminal_outcome_exactly_once():
    """(15) One completion per turn."""
    observer = Recorder()
    pipeline = build_pipeline(observer)
    asyncio.run(pipeline.run())

    assert observer.terminals == ["completed"]


def test_no_publish_happens_at_an_intermediate_boundary():
    """(16) ADR-0038 TP10 — capture is cheap; publication happens once, off the hot path.

    Proven end-to-end: a real tap on a real bus, driven by a real pipeline, must produce exactly
    one event no matter how many boundaries were crossed."""
    bus = InProcessEventBus()
    seen = []
    bus.subscribe(seen.append, types=EventType.METRIC_TURN)
    publisher = bus.publisher(
        EventMetadata(session_id="s-1", source=EventSource.OBSERVABILITY)
    )
    pipeline = build_pipeline(TurnLatencyTap(publisher, clock=StepClock(0.0, 0.010)))
    asyncio.run(pipeline.run())

    assert len(seen) == 1, "exactly one metric.turn per turn, never one per boundary"


def test_a_raising_observer_never_breaks_the_conversation():
    """(17) DL-0013 — explicit, logged degradation; the conversation completes regardless."""

    class Exploding:
        def record(self, *a):
            raise RuntimeError("tap blew up")

        def complete(self, *a):
            raise RuntimeError("completion blew up")

    pipeline = build_pipeline(Exploding())
    asyncio.run(pipeline.run())  # must not raise


def test_without_an_observer_the_pipeline_behaves_exactly_as_before():
    """ADR-0038 TP2 / TV18 — the seam is optional, and absent means untouched."""
    pipeline = build_pipeline(None)
    asyncio.run(pipeline.run())  # must not raise


# ── 25. boundary and budget assertions ──────────────────────────────────────


def _imported_tops(path: pathlib.Path) -> set[str]:
    tops: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            tops |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            tops.add(node.module.split(".")[0])
    return tops


def test_observability_keeps_its_core_plus_stdlib_budget():
    """(25) ADR-0033 MS8 — and the reason `MetricPublisher` is a structural Protocol rather
    than an import of `events.EventPublisher`."""
    forbidden = {"runtime", "events", "api", "billing", "providers", "replay", "sqlmodel",
                 "sqlalchemy", "httpx", "fastapi", "pydantic"}
    for path in pathlib.Path("observability").rglob("*.py"):
        assert not (_imported_tops(path) & forbidden), f"{path} imports outside its budget"


def test_runtime_does_not_import_observability():
    """The inversion that makes the seam legal — `runtime` knows a Protocol, not a plane."""
    for path in pathlib.Path("runtime").rglob("*.py"):
        assert "observability" not in _imported_tops(path), f"{path} imports observability"


def test_m109_added_no_worker_thread_timer_or_persistence():
    """Vocabulary + producer only: no scheduler, buffer, retention or storage (TP10/TP11)."""
    banned = ("threading", "Thread(", "asyncio.create_task", "Timer(", "sleep(", "while True",
              "sqlmodel", "sqlalchemy", "MetricStream", "MetricSignal", "def purge",
              "def apply_retention")
    for path in pathlib.Path("observability").rglob("*.py"):
        body = path.read_text()
        for token in banned:
            assert token not in body, f"{path} contains {token}"


def test_turn_engine_clock_semantics_are_unchanged():
    """ADR-0038 TP7 / TV19 — the tap carrying its own monotonic clock is exactly what makes
    changing `runtime/turn/**` unnecessary."""
    source = pathlib.Path("runtime/turn/engine.py").read_text()
    assert "self._clock = clock or time.time" in source
    assert "monotonic" not in source
    timing = pathlib.Path("runtime/turn/context.py").read_text()
    assert "monotonic-agnostic wall clock" in timing


def test_no_migration_or_schema_entered_m109():
    from events.persistence.migrations import MIGRATIONS

    assert tuple(m.VERSION for m in MIGRATIONS) == ("0034", "0035", "0036")
    assert not list(pathlib.Path("events/persistence/migrations").glob("v0037*"))

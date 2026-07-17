"""Event Bus runtime integration (M4.1).

Verifies runtime components publish events through the bus without direct coupling:
a real conversation emits the expected lifecycle + turn events in deterministic
order, and the conversation itself is unchanged (the bus is notify-only).
"""

from __future__ import annotations

import contextlib

import pytest

from bootstrap import build_local_registry
from core.audio import AudioFrame
from core.events import Event, EventSource, EventType
from runtime.contracts import (
    AgentSpec,
    RuntimeContext,
    SessionContext,
    WorkerConfiguration,
)
from runtime.registry import CapabilityKind
from runtime.worker import SessionWorker


def _context(registry, transcripts, *, agent=None, session_id="ses_ev"):
    return RuntimeContext(
        session=SessionContext(session_id=session_id, org_id="org_1", agent_id="agt_1"),
        config=WorkerConfiguration(worker_id="wrk_ev", shutdown_timeout_s=2.0),
        registry=registry,
        agent=agent or AgentSpec(system_prompt="be helpful"),
        on_transcript=lambda sid, role, text, final: transcripts.append((role, text, final)),
    )


@pytest.fixture
async def started():
    workers: list[SessionWorker] = []
    yield workers
    for w in workers:
        with contextlib.suppress(Exception):
            await w.cancel()


async def test_conversation_publishes_lifecycle_and_turn_events(started):
    registry = build_local_registry()
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    transcripts: list[tuple[str, str, bool]] = []
    worker = SessionWorker(_context(registry, transcripts))
    started.append(worker)

    await worker.start()
    bus = worker.orchestrator.event_bus
    assert bus is not None
    events: list[Event] = []
    bus.subscribe(events.append)  # subscribe after start (RuntimeStarted already fired)

    await transport.feed("ses_ev", AudioFrame(data=b"hello"))
    await transport.feed("ses_ev", AudioFrame(data=b"world"))
    await transport.hangup("ses_ev")
    await worker.wait_closed()
    await worker.stop()

    types = [e.type for e in events]
    # Turn lifecycle for both utterances (started → completed), in order.
    assert types.count(EventType.TURN_STARTED) == 2
    assert types.count(EventType.TURN_COMPLETED) == 2
    assert EventType.ASSISTANT_STARTED_SPEAKING in types
    # Session end fired.
    assert EventType.SESSION_ENDED in types
    # Strictly ordered by monotonic seq.
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)  # exactly-once, unique seq
    # Every event names its producing subsystem + the session.
    assert all(isinstance(e.source, EventSource) for e in events)
    assert all(e.session_id == "ses_ev" for e in events)
    # A TurnStarted came from the Turn Engine.
    turn_started = next(e for e in events if e.type is EventType.TURN_STARTED)
    assert turn_started.source is EventSource.TURN_ENGINE

    # Behaviour unchanged: the conversation produced its usual transcript + audio.
    finals = [(r, t) for (r, t, f) in transcripts if f]
    assert ("agent", "You said: hello") in finals
    assert ("agent", "You said: world") in finals
    assert [f.data.decode() for f in transport.sent("ses_ev")] == [
        "You said: hello",
        "You said: world",
    ]
    # Bus was shut down on session end.
    assert bus.is_shutdown is True


async def test_bus_is_created_even_for_idle_lifecycle(started):
    """A worker with no registry/agent still gets a bus + runtime/session lifecycle
    events (no pipeline, but the platform exists)."""
    ctx = RuntimeContext(
        session=SessionContext(session_id="ses_idle", org_id="o", agent_id="a"),
        config=WorkerConfiguration(worker_id="w", shutdown_timeout_s=2.0),
        registry=None,
        agent=None,
    )
    worker = SessionWorker(ctx)
    started.append(worker)
    await worker.start()
    assert worker.orchestrator.event_bus is not None
    await worker.cancel()
    assert worker.orchestrator.event_bus.is_shutdown is True


async def test_endpoint_and_speech_events_with_vad(started):
    """With VAD enabled the bus also carries speech + endpoint events."""
    registry = build_local_registry(include_voice_activity=True)
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    transcripts: list[tuple[str, str, bool]] = []
    worker = SessionWorker(_context(registry, transcripts, session_id="ses_vad_ev"))
    started.append(worker)

    await worker.start()
    bus = worker.orchestrator.event_bus
    events: list[Event] = []
    bus.subscribe(events.append)

    await transport.feed("ses_vad_ev", AudioFrame(data=b"hello"))
    await transport.hangup("ses_vad_ev")
    await worker.wait_closed()
    await worker.stop()

    types = {e.type for e in events}
    assert EventType.SPEECH_STARTED in types  # VAD → speech
    assert EventType.ENDPOINT_DETECTED in types  # endpoint → detected
    # The endpoint event was produced by the Endpoint subsystem.
    ep = next(e for e in events if e.type is EventType.ENDPOINT_DETECTED)
    assert ep.source is EventSource.ENDPOINT

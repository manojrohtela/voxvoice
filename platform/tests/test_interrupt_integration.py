"""Interrupt Intelligence runtime integration (M3.4).

Drives real barge-in through the full stack (SessionWorker → Orchestrator →
Pipeline → Providers) with a controllable multi-frame TTS that parks mid-utterance,
so a caller can speak over the assistant deterministically. Also verifies graceful
fallback: with interruption disabled (or without a VAD provider) conversations run
exactly as M3.3.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import pytest

from bootstrap import build_local_registry
from core.audio import AudioFrame
from core.providers.tts import TTSCapabilities, TTSProvider
from core.states import SessionState
from runtime.contracts import (
    AgentSpec,
    RuntimeContext,
    SessionContext,
    WorkerConfiguration,
)
from runtime.interrupt import InterruptDecision
from runtime.registry import CapabilityKind
from runtime.turn import TurnPhase
from runtime.worker import SessionWorker

TIMEOUT = 2.0


class ControllableTTS(TTSProvider):
    """Yields a first frame immediately, then parks on a gate before the second —
    a deterministic window during which the caller can barge in."""

    name = "controllable"
    capabilities = TTSCapabilities(streaming=True, sample_rates=(16000,))

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def synthesize(
        self, text: str, *, voice: str | None = None, language: str = "en"
    ) -> AsyncIterator[AudioFrame]:
        yield AudioFrame(data=text.encode("utf-8"), sample_rate=16000)
        self.started.set()  # first frame consumed; now parked
        await self.gate.wait()
        yield AudioFrame(data=b"[cont]", sample_rate=16000)


def _context(registry, transcripts, *, allow_interrupt, session_id):
    return RuntimeContext(
        session=SessionContext(session_id=session_id, org_id="org_1", agent_id="agt_1"),
        config=WorkerConfiguration(worker_id="wrk_int", shutdown_timeout_s=2.0),
        registry=registry,
        agent=AgentSpec(system_prompt="be helpful", allow_interrupt=allow_interrupt),
        on_transcript=lambda sid, role, text, final: transcripts.append((role, text, final)),
    )


@pytest.fixture
async def started():
    workers: list[SessionWorker] = []
    yield workers
    for w in workers:
        with contextlib.suppress(Exception):
            await w.cancel()


async def test_barge_in_preempts_assistant_and_starts_new_turn(started):
    """The caller speaks while the assistant is playing → playback is pre-empted,
    the interrupted turn is cancelled, and a new user turn runs to completion."""
    registry = build_local_registry(include_voice_activity=True)
    ctts = ControllableTTS()
    registry.unregister(CapabilityKind.TTS, "local")
    registry.register(CapabilityKind.TTS, ctts, default=True)
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    transcripts: list[tuple[str, str, bool]] = []
    worker = SessionWorker(
        _context(registry, transcripts, allow_interrupt=True, session_id="ses_bi")
    )
    started.append(worker)

    await worker.start()
    interrupt = worker.orchestrator.interrupt_engine
    turns = worker.orchestrator.turn_engine
    assert interrupt is not None  # wired: allow_interrupt + VAD present

    # Turn 1: caller says "hello" → assistant begins replying and parks mid-playback.
    await transport.feed("ses_bi", AudioFrame(data=b"hello"))
    await asyncio.wait_for(ctts.started.wait(), TIMEOUT)
    ctts.started.clear()

    # Barge-in: caller speaks over the assistant.
    await transport.feed("ses_bi", AudioFrame(data=b"actually wait"))
    # The barge-in response (turn 2) also parks after its first frame.
    await asyncio.wait_for(ctts.started.wait(), TIMEOUT)

    # Exactly one interrupt was confirmed and completed.
    assert interrupt.context.interrupt_count == 1
    assert interrupt.decision in (
        InterruptDecision.ASSISTANT_SPEAKING,
        InterruptDecision.IDLE,
    )

    ctts.gate.set()  # let the barge-in response finish
    await transport.hangup("ses_bi")
    await asyncio.wait_for(worker.wait_closed(), TIMEOUT)
    await worker.stop()

    # The interrupted turn was cancelled; the barge-in turn completed. No lost turn.
    assert turns.turn_count == 2
    phases = [c.phase for c in turns.history]
    assert phases == [TurnPhase.TURN_CANCELLED, TurnPhase.TURN_COMPLETED]

    # No duplicated assistant response; the interrupted playback was truncated
    # (its second frame "[cont]" was never sent — only the barge-in reply's was).
    sent = [f.data.decode() for f in transport.sent("ses_bi")]
    assert sent == ["You said: hello", "You said: actually wait", "[cont]"]
    finals = [(r, t) for (r, t, f) in transcripts if f]
    assert finals.count(("agent", "You said: actually wait")) == 1


async def test_interrupt_disabled_runs_exactly_as_before(started):
    """allow_interrupt=False with a VAD provider present → no interrupt subsystem,
    sequential (uninterruptible) playback, behaviour identical to M3.3."""
    registry = build_local_registry(include_voice_activity=True)
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    transcripts: list[tuple[str, str, bool]] = []
    worker = SessionWorker(
        _context(registry, transcripts, allow_interrupt=False, session_id="ses_off")
    )
    started.append(worker)

    await worker.start()
    assert worker.orchestrator.interrupt_engine is None  # graceful fallback

    await transport.feed("ses_off", AudioFrame(data=b"hello"))
    await transport.hangup("ses_off")
    await asyncio.wait_for(worker.wait_closed(), TIMEOUT)
    await worker.stop()

    assert worker.conversation_state is SessionState.ENDED
    finals = [(r, t) for (r, t, f) in transcripts if f]
    assert ("agent", "You said: hello") in finals
    assert [f.data.decode() for f in transport.sent("ses_off")] == ["You said: hello"]


async def test_interrupt_requires_a_vad_provider(started):
    """allow_interrupt=True but NO VAD provider → interrupt cannot be wired (no
    speech signal); the conversation still runs (graceful fallback)."""
    registry = build_local_registry()  # no VAD
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    transcripts: list[tuple[str, str, bool]] = []
    worker = SessionWorker(
        _context(registry, transcripts, allow_interrupt=True, session_id="ses_novad")
    )
    started.append(worker)

    await worker.start()
    assert worker.orchestrator.interrupt_engine is None  # needs VAD → not wired

    await transport.feed("ses_novad", AudioFrame(data=b"hello"))
    await transport.hangup("ses_novad")
    await asyncio.wait_for(worker.wait_closed(), TIMEOUT)
    await worker.stop()

    finals = [(r, t) for (r, t, f) in transcripts if f]
    assert ("agent", "You said: hello") in finals

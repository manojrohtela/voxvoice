"""Smart Turn Intelligence runtime integration (M3.5).

Verifies the pipeline consumes Smart Turn decisions: default identity policy keeps
conversations unchanged; a registered semantic evaluator is resolved and blended
with no runtime change; and an adaptive interruption policy can IGNORE a barge-in
(the assistant keeps speaking) — all through the full worker stack.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Mapping

import pytest

from bootstrap import build_local_registry
from core.audio import AudioFrame
from core.providers.semantic_turn import (
    SemanticCapabilities,
    SemanticHint,
    SemanticTurnEvaluator,
)
from core.providers.tts import TTSCapabilities, TTSProvider
from core.states import SessionState
from runtime.contracts import (
    AgentSpec,
    RuntimeContext,
    SessionContext,
    WorkerConfiguration,
)
from runtime.registry import CapabilityKind
from runtime.smart_turn import SmartTurnPolicy
from runtime.turn import TurnPhase
from runtime.worker import SessionWorker

TIMEOUT = 2.0


class ControllableTTS(TTSProvider):
    name = "controllable"
    capabilities = TTSCapabilities(streaming=True, sample_rates=(16000,))

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def synthesize(
        self, text: str, *, voice: str | None = None, language: str = "en"
    ) -> AsyncIterator[AudioFrame]:
        yield AudioFrame(data=text.encode("utf-8"), sample_rate=16000)
        self.started.set()
        await self.gate.wait()
        yield AudioFrame(data=b"[cont]", sample_rate=16000)


class RecordingSemantic(SemanticTurnEvaluator):
    name = "recording"
    capabilities = SemanticCapabilities(end_of_turn=True)

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, features: Mapping[str, float]) -> SemanticHint:
        self.calls += 1
        return SemanticHint()


def _ctx(registry, transcripts, *, agent, session_id):
    return RuntimeContext(
        session=SessionContext(session_id=session_id, org_id="o", agent_id="a"),
        config=WorkerConfiguration(worker_id="w", shutdown_timeout_s=2.0),
        registry=registry,
        agent=agent,
        on_transcript=lambda sid, role, text, final: transcripts.append((role, text, final)),
    )


@pytest.fixture
async def started():
    workers: list[SessionWorker] = []
    yield workers
    for w in workers:
        with contextlib.suppress(Exception):
            await w.cancel()


async def test_smart_turn_default_policy_unchanged(started):
    """Smart Turn enabled with the default identity policy → the conversation runs
    exactly as before (endpoint-confirmed → finish → respond)."""
    registry = build_local_registry()
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    transcripts: list[tuple[str, str, bool]] = []
    agent = AgentSpec(system_prompt="be helpful", smart_turn=True)
    worker = SessionWorker(_ctx(registry, transcripts, agent=agent, session_id="ses_st"))
    started.append(worker)

    await worker.start()
    assert worker.orchestrator.smart_turn_engine is not None  # wired

    await transport.feed("ses_st", AudioFrame(data=b"hello"))
    await transport.feed("ses_st", AudioFrame(data=b"world"))
    await transport.hangup("ses_st")
    await asyncio.wait_for(worker.wait_closed(), TIMEOUT)
    await worker.stop()

    finals = [(r, t) for (r, t, f) in transcripts if f]
    assert ("agent", "You said: hello") in finals
    assert ("agent", "You said: world") in finals
    assert [f.data.decode() for f in transport.sent("ses_st")] == [
        "You said: hello",
        "You said: world",
    ]


async def test_registered_semantic_evaluator_is_resolved_and_used(started):
    """A registered SEMANTIC_TURN provider is resolved from the registry and blended
    by the engine — no runtime code change (the extension point works)."""
    registry = build_local_registry()
    semantic = RecordingSemantic()
    registry.register(CapabilityKind.SEMANTIC_TURN, semantic, default=True)
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    transcripts: list[tuple[str, str, bool]] = []
    agent = AgentSpec(system_prompt="be helpful", smart_turn=True)
    worker = SessionWorker(_ctx(registry, transcripts, agent=agent, session_id="ses_sem"))
    started.append(worker)

    await worker.start()
    # The engine was constructed with the registered evaluator (no runtime change).
    assert worker.orchestrator.smart_turn_engine.semantic is semantic

    await transport.feed("ses_sem", AudioFrame(data=b"hello"))
    await transport.hangup("ses_sem")
    await asyncio.wait_for(worker.wait_closed(), TIMEOUT)
    await worker.stop()

    assert semantic.calls > 0  # the evaluator was consulted during arbitration
    finals = [(r, t) for (r, t, f) in transcripts if f]
    assert ("agent", "You said: hello") in finals


async def test_smart_turn_ignores_barge_in_by_policy(started):
    """An adaptive interruption policy with a high dynamic threshold makes Smart
    Turn IGNORE a barge-in — the assistant is NOT pre-empted and finishes speaking."""
    registry = build_local_registry(include_voice_activity=True)
    ctts = ControllableTTS()
    registry.unregister(CapabilityKind.TTS, "local")
    registry.register(CapabilityKind.TTS, ctts, default=True)
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    transcripts: list[tuple[str, str, bool]] = []
    agent = AgentSpec(
        system_prompt="be helpful",
        allow_interrupt=True,
        smart_turn=True,
        # Confidence 1.0 * weight 1.0 = 1.0 < 5.0 → every barge-in is IGNORED.
        smart_turn_policy=SmartTurnPolicy(dynamic_interruption_threshold=5.0),
    )
    worker = SessionWorker(_ctx(registry, transcripts, agent=agent, session_id="ses_ign"))
    started.append(worker)

    await worker.start()
    interrupt = worker.orchestrator.interrupt_engine
    turns = worker.orchestrator.turn_engine
    assert interrupt is not None and worker.orchestrator.smart_turn_engine is not None

    await transport.feed("ses_ign", AudioFrame(data=b"hello"))
    await asyncio.wait_for(ctts.started.wait(), TIMEOUT)  # assistant parked mid-reply
    ctts.started.clear()

    # Caller speaks over the assistant, but the policy ignores it.
    await transport.feed("ses_ign", AudioFrame(data=b"noise"))
    # Give the run loop a few turns to process the ignored barge-in.
    for _ in range(5):
        await asyncio.sleep(0)

    ctts.gate.set()  # let the (uninterrupted) reply finish
    await transport.hangup("ses_ign")
    await asyncio.wait_for(worker.wait_closed(), TIMEOUT)
    await worker.stop()

    # No interrupt was ever completed (all barge-ins ignored).
    assert interrupt.context.interrupt_count == 0
    # The first turn completed normally (was not cancelled by a barge-in).
    assert TurnPhase.TURN_CANCELLED not in [c.phase for c in turns.history]
    # The assistant's first reply played fully (both frames sent).
    sent = [f.data.decode() for f in transport.sent("ses_ign")]
    assert "You said: hello" in sent and "[cont]" in sent
    assert worker.conversation_state is SessionState.ENDED

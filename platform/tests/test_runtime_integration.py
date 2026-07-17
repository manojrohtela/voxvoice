"""Full Runtime integration (M2C.2): SessionWorker → Orchestrator → Pipeline →
Provider Registry, driving a real conversation through the local provider profile.

Verifies the ownership boundaries hold end-to-end: the worker owns worker
lifecycle, the orchestrator owns conversation state and resolves providers from
the registry, the pipeline executes the turns.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from bootstrap import build_local_registry
from core.audio import AudioFrame
from core.states import SessionState
from runtime.contracts import (
    AgentSpec,
    RuntimeContext,
    RuntimeDependencies,
    SessionContext,
    WorkerConfiguration,
)
from runtime.registry import CapabilityKind
from runtime.state import WorkerState
from runtime.worker import SessionWorker


def _context(registry, transcripts, session_id="ses_int"):
    return RuntimeContext(
        session=SessionContext(session_id=session_id, org_id="org_1", agent_id="agt_1"),
        dependencies=RuntimeDependencies(),
        config=WorkerConfiguration(worker_id="wrk_int", shutdown_timeout_s=2.0),
        registry=registry,
        agent=AgentSpec(system_prompt="be helpful"),
        on_transcript=lambda sid, role, text, final: transcripts.append((role, text, final)),
    )


@pytest.fixture
async def started():
    workers: list[SessionWorker] = []
    yield workers
    for w in workers:
        with contextlib.suppress(Exception):
            await w.cancel()


async def test_worker_runs_conversation_through_registry_providers(started):
    registry = build_local_registry()
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    transcripts: list[tuple[str, str, bool]] = []
    worker = SessionWorker(_context(registry, transcripts))
    started.append(worker)

    await worker.start()
    assert worker.state is WorkerState.RUNNING
    assert worker.conversation_state is SessionState.IN_CONVERSATION

    # Act as the caller: speak one utterance, then hang up.
    await transport.feed("ses_int", AudioFrame(data=b"hello runtime"))
    await transport.hangup("ses_int")
    await worker.wait_closed()  # pipeline processes the utterance; hangup ends it naturally

    assert worker.conversation_state is SessionState.ENDED
    await worker.stop()  # finalize the worker
    assert worker.state is WorkerState.STOPPED
    finals = [(r, t) for (r, t, f) in transcripts if f]
    assert ("user", "hello runtime") in finals
    assert ("agent", "You said: hello runtime") in finals
    assert [f.data.decode() for f in transport.sent("ses_int")] == ["You said: hello runtime"]


async def test_worker_startup_fails_when_capability_missing(started):
    from runtime.errors import WorkerStartupError

    registry = build_local_registry()
    registry.unregister(CapabilityKind.STT, "local")  # remove a required capability
    worker = SessionWorker(_context(registry, []))
    started.append(worker)

    with pytest.raises(WorkerStartupError):
        await worker.start()
    assert worker.state is WorkerState.FAILED
    assert worker.conversation_state is SessionState.FAILED


async def test_cancel_ends_conversation_mid_flight(started):
    registry = build_local_registry()
    transcripts: list[tuple[str, str, bool]] = []
    worker = SessionWorker(_context(registry, transcripts, session_id="ses_cancel"))
    started.append(worker)
    await worker.start()
    await asyncio.sleep(0)
    await worker.cancel()
    assert worker.state is WorkerState.STOPPED
    assert worker.conversation_state is SessionState.ENDED


async def test_concurrent_sessions_are_isolated(started):
    """Two conversations run concurrently through the shared (multi-session)
    provider set with no cross-contamination."""
    registry = build_local_registry()
    transport = registry.resolve(CapabilityKind.TRANSPORT)
    a_scripts: list[tuple[str, str, bool]] = []
    b_scripts: list[tuple[str, str, bool]] = []
    wa = SessionWorker(_context(registry, a_scripts, session_id="ses_A"))
    wb = SessionWorker(_context(registry, b_scripts, session_id="ses_B"))
    started.extend([wa, wb])

    await asyncio.gather(wa.start(), wb.start())
    await transport.feed("ses_A", AudioFrame(data=b"alpha"))
    await transport.feed("ses_B", AudioFrame(data=b"beta"))
    await transport.hangup("ses_A")
    await transport.hangup("ses_B")
    await asyncio.gather(wa.wait_closed(), wb.wait_closed())
    await asyncio.gather(wa.stop(), wb.stop())

    a_finals = [(r, t) for (r, t, f) in a_scripts if f]
    b_finals = [(r, t) for (r, t, f) in b_scripts if f]
    assert ("agent", "You said: alpha") in a_finals
    assert ("agent", "You said: beta") in b_finals
    # No cross-contamination between sessions.
    assert all("beta" not in t for (_, t) in a_finals)
    assert all("alpha" not in t for (_, t) in b_finals)
    assert [f.data.decode() for f in transport.sent("ses_A")] == ["You said: alpha"]
    assert [f.data.decode() for f in transport.sent("ses_B")] == ["You said: beta"]

"""SessionWorker ↔ Conversation Orchestrator integration tests (M2B.2).

Verifies the ownership boundaries: the worker hosts exactly one orchestrator and
delegates conversation control to it; the worker owns worker lifecycle only and
never writes conversation state; the orchestrator is the sole conversation writer.
"""

from __future__ import annotations

import contextlib

import pytest

from core.states import SessionState as C
from runtime.contracts import (
    RuntimeContext,
    SessionContext,
    WorkerConfiguration,
)
from runtime.errors import WorkerStartupError
from runtime.orchestrator import ConversationOrchestrator
from runtime.state import WorkerState
from runtime.worker import SessionWorker


def _ctx() -> RuntimeContext:
    return RuntimeContext(
        session=SessionContext(session_id="ses_1", org_id="org_1", agent_id="agt_1"),
        config=WorkerConfiguration(worker_id="wrk_1", shutdown_timeout_s=2.0),
    )


@pytest.fixture
async def workers():
    created: list[SessionWorker] = []

    def _make() -> SessionWorker:
        w = SessionWorker(_ctx())
        created.append(w)
        return w

    yield _make
    for w in created:
        with contextlib.suppress(Exception):
            await w.cancel()


async def test_conversation_state_none_before_start(workers):
    w = workers()
    assert w.orchestrator is None
    assert w.conversation_state is None


async def test_worker_hosts_orchestrator_reaches_in_conversation(workers):
    w = workers()
    await w.start()
    assert w.orchestrator is not None
    assert w.conversation_state is C.IN_CONVERSATION
    await w.stop()
    assert w.state is WorkerState.STOPPED
    assert w.conversation_state is C.ENDED


async def test_worker_cancel_ends_conversation(workers):
    w = workers()
    await w.start()
    await w.cancel()
    assert w.state is WorkerState.STOPPED
    assert w.conversation_state is C.ENDED


async def test_worker_does_not_write_conversation_state(workers):
    w = workers()
    await w.start()
    # The worker exposes conversation state read-only; it is not a writer.
    with pytest.raises(AttributeError):
        w.conversation_state = C.ENDED
    # Exactly one writer exists: the orchestrator.
    assert isinstance(w.orchestrator, ConversationOrchestrator)
    await w.stop()


async def test_worker_startup_failure_fails_conversation(workers, monkeypatch):
    async def boom(self) -> None:
        raise RuntimeError("provider open boom")

    monkeypatch.setattr(ConversationOrchestrator, "_open_providers", boom)
    w = workers()
    with pytest.raises(WorkerStartupError):
        await w.start()
    assert w.state is WorkerState.FAILED
    # The orchestrator was created and routed the conversation to FAILED.
    assert w.conversation_state is C.FAILED


async def test_worker_health_includes_conversation_state(workers):
    w = workers()
    await w.start()
    assert w.health["conversation_state"] == "in_conversation"
    await w.stop()
    assert w.health["conversation_state"] == "ended"

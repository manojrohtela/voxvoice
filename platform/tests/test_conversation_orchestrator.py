"""Conversation Orchestrator tests (M2B.2).

Covers normal lifecycle, cancellation, startup failure, mid-conversation failure
routing, idempotency, sole-writer ownership, dependency injection with NO provider
execution, lifecycle notifications, and history. No speech/AI/provider logic.
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from core.states import SessionState as C
from runtime.contracts import (
    RuntimeContext,
    RuntimeDependencies,
    SessionContext,
    WorkerConfiguration,
)
from runtime.errors import OrchestrationError, OrchestrationStartupError
from runtime.orchestrator import ConversationOrchestrator


def _ctx(deps: RuntimeDependencies | None = None) -> RuntimeContext:
    return RuntimeContext(
        session=SessionContext(session_id="ses_1", org_id="org_1", agent_id="agt_1"),
        dependencies=deps or RuntimeDependencies(),
        config=WorkerConfiguration(worker_id="wrk_1"),
    )


async def test_normal_lifecycle():
    seen: list[C] = []
    orch = ConversationOrchestrator(_ctx(), on_transition=lambda r: seen.append(r.new))
    await orch.begin()
    assert orch.state is C.IN_CONVERSATION
    run_task = asyncio.create_task(orch.run())
    await asyncio.sleep(0)  # let run() start awaiting
    await orch.end(reason="completed")
    await run_task
    assert orch.state is C.ENDED
    assert seen == [C.CREATED, C.CONNECTED, C.IN_CONVERSATION, C.ENDING, C.ENDED]


async def test_begin_requires_created():
    orch = ConversationOrchestrator(_ctx())
    await orch.begin()
    with pytest.raises(OrchestrationError):
        await orch.begin()  # already IN_CONVERSATION
    await orch.end()


async def test_run_before_begin_raises():
    orch = ConversationOrchestrator(_ctx())
    with pytest.raises(OrchestrationError):
        await orch.run()  # conversation still CREATED


async def test_run_returns_when_already_ended():
    orch = ConversationOrchestrator(_ctx())
    await orch.begin()
    await orch.end()
    await orch.run()  # tolerated: returns promptly, no raise
    assert orch.state is C.ENDED


async def test_cancellation():
    orch = ConversationOrchestrator(_ctx())
    await orch.begin()
    await orch.cancel(reason="cancelled")
    assert orch.state is C.ENDED
    assert any(r.reason == "cancelled" for r in orch.history)


async def test_end_is_idempotent():
    orch = ConversationOrchestrator(_ctx())
    await orch.begin()
    await orch.end()
    await orch.end()  # no error
    assert orch.state is C.ENDED


async def test_startup_failure_routes_to_failed(monkeypatch):
    async def boom(self) -> None:
        raise RuntimeError("provider open boom")

    monkeypatch.setattr(ConversationOrchestrator, "_open_providers", boom)
    orch = ConversationOrchestrator(_ctx())
    with pytest.raises(OrchestrationStartupError):
        await orch.begin()
    assert orch.state is C.FAILED


async def test_midconversation_failure_routes_through_ending():
    # The state machine forbids IN_CONVERSATION -> FAILED; a mid-conversation
    # failure must end via ENDING -> ENDED (no hidden transitions).
    orch = ConversationOrchestrator(_ctx())
    await orch.begin()
    orch.fail(reason="explosion")
    assert orch.state is C.ENDED
    kinds = [r.new for r in orch.history]
    assert C.ENDING in kinds and kinds[-1] is C.ENDED


async def test_sole_writer_state_is_read_only():
    orch = ConversationOrchestrator(_ctx())
    with pytest.raises(AttributeError):
        orch.state = C.ENDED  # property has no setter
    assert not hasattr(orch, "sm")  # the state machine is private (no public writer)


async def test_dependency_injection_without_provider_execution():
    stt, tts, llm, transport = Mock(), Mock(), Mock(), Mock()
    deps = RuntimeDependencies(stt=stt, tts=tts, llm=llm, transport=transport)
    orch = ConversationOrchestrator(_ctx(deps))
    await orch.begin()
    await orch.end()
    # No provider was executed at M2B.2 (interfaces held, never called).
    for m in (stt, tts, llm, transport):
        assert m.mock_calls == []
    assert orch.dependencies.stt is stt


async def test_history_records_provenance():
    orch = ConversationOrchestrator(_ctx())
    await orch.begin()
    first = orch.history[1]  # [0] is the initial CREATED record
    assert first.previous is C.CREATED and first.new is C.CONNECTED
    assert first.source == "orchestrator" and first.ts > 0

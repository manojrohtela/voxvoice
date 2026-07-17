"""SessionWorker lifecycle tests (M2A).

Covers lifecycle transitions, startup, shutdown, cancellation, dependency
injection, invalid transitions, cleanup guarantees, and failure handling. No
conversation/provider behaviour exists to test — that is later milestones.
"""

from __future__ import annotations

import contextlib

import pytest

from runtime.contracts import (
    RuntimeContext,
    SessionContext,
    WorkerConfiguration,
)
from runtime.errors import InvalidWorkerTransition, WorkerStartupError
from runtime.state import WorkerState as S
from runtime.worker import SessionWorker


def _context(worker_id: str = "wrk_1") -> RuntimeContext:
    return RuntimeContext(
        session=SessionContext(session_id="ses_1", org_id="org_1", agent_id="agt_1"),
        config=WorkerConfiguration(worker_id=worker_id, shutdown_timeout_s=2.0),
    )


class _Recorder:
    """A lifecycle observer that records the state sequence."""

    def __init__(self) -> None:
        self.states: list[S] = []

    def __call__(self, signal) -> None:  # noqa: ANN001 — LifecycleSignal
        self.states.append(signal.state)


@pytest.fixture
async def workers():
    """Factory that guarantees every created worker is torn down."""
    created: list[SessionWorker] = []

    def _make(context: RuntimeContext, on_lifecycle=None) -> SessionWorker:  # noqa: ANN001
        w = SessionWorker(context, on_lifecycle=on_lifecycle)
        created.append(w)
        return w

    yield _make
    for w in created:
        with contextlib.suppress(Exception):
            await w.cancel()


async def test_happy_path_lifecycle(workers):
    rec = _Recorder()
    w = workers(_context(), on_lifecycle=rec)
    assert w.state is S.CREATED
    await w.start()
    assert w.state is S.RUNNING
    await w.stop()
    assert w.state is S.STOPPED
    assert rec.states == [S.CREATED, S.INITIALIZING, S.READY, S.RUNNING, S.STOPPING, S.STOPPED]


async def test_start_requires_created(workers):
    w = workers(_context())
    await w.start()
    with pytest.raises(InvalidWorkerTransition):
        await w.start()  # already RUNNING
    await w.stop()


async def test_stop_before_start_cleans_up(workers):
    rec = _Recorder()
    w = workers(_context(), on_lifecycle=rec)
    await w.stop()  # CREATED -> STOPPING -> STOPPED
    assert w.state is S.STOPPED
    assert rec.states == [S.CREATED, S.STOPPING, S.STOPPED]


async def test_stop_is_idempotent(workers):
    w = workers(_context())
    await w.start()
    await w.stop()
    await w.stop()  # no error, stays stopped
    assert w.state is S.STOPPED


async def test_cancel_during_running(workers):
    w = workers(_context())
    await w.start()
    await w.cancel()
    assert w.state is S.STOPPED
    assert w._run_task is not None and w._run_task.done()


async def test_stop_ends_run_task(workers):
    w = workers(_context())
    await w.start()
    run_task = w._run_task
    await w.stop()
    assert run_task is not None and run_task.done()


async def test_runs_without_a_registry(workers):
    # No registry/agent → the worker runs an idle conversation (lifecycle only).
    w = workers(_context())
    await w.start()
    assert w.state is S.RUNNING
    await w.stop()
    assert w.state is S.STOPPED


async def test_invalid_direct_transition_raises(workers):
    w = workers(_context())
    with pytest.raises(InvalidWorkerTransition):
        w._transition(S.RUNNING)  # CREATED -> RUNNING is illegal


async def test_startup_failure_sets_failed_and_cleans_up(workers):
    rec = _Recorder()
    w = workers(_context(), on_lifecycle=rec)

    async def boom() -> None:
        raise RuntimeError("init boom")

    w._initialize = boom  # force initialization to fail

    with pytest.raises(WorkerStartupError):
        await w.start()
    assert w.state is S.FAILED
    assert S.FAILED in rec.states
    assert w._cleaned_up is True
    assert w.health["healthy"] is False


async def test_health_and_metadata(workers):
    w = workers(_context(worker_id="wrk_meta"))
    await w.start()
    h = w.health
    assert h["state"] == "running" and h["healthy"] is True
    assert h["worker_id"] == "wrk_meta" and h["session_id"] == "ses_1"
    m = w.metadata
    assert m.state is S.RUNNING and m.started_at is not None and m.stopped_at is None
    await w.stop()
    assert w.metadata.state is S.STOPPED and w.metadata.stopped_at is not None


async def test_cleanup_is_idempotent(workers):
    w = workers(_context())
    await w.start()
    await w.stop()
    assert w._cleaned_up is True
    await w.cancel()  # already terminal -> no-op, no error
    assert w.state is S.STOPPED

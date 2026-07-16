"""SessionWorker execution-lifecycle states (M2A).

This is the **worker's** execution lifecycle — distinct from the *conversation*
lifecycle (`core.states.SessionState`, owned by the Conversation Orchestrator at
M2B+). Keeping them apart (one concept, one name) prevents conflating "is the
worker process running" with "where is the conversation". Illegal transitions
raise `runtime.errors.InvalidWorkerTransition`.
"""

from __future__ import annotations

from enum import StrEnum


class WorkerState(StrEnum):
    CREATED = "created"
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


# Legal worker transitions (source -> allowed targets). FAILED is reachable from
# every non-terminal state; STOPPING is reachable from CREATED too, so an
# unstarted worker can still be cleaned up. STOPPED and FAILED are terminal.
WORKER_TRANSITIONS: dict[WorkerState, frozenset[WorkerState]] = {
    WorkerState.CREATED: frozenset(
        {WorkerState.INITIALIZING, WorkerState.STOPPING, WorkerState.FAILED}
    ),
    WorkerState.INITIALIZING: frozenset({WorkerState.READY, WorkerState.FAILED}),
    WorkerState.READY: frozenset(
        {WorkerState.RUNNING, WorkerState.STOPPING, WorkerState.FAILED}
    ),
    WorkerState.RUNNING: frozenset({WorkerState.STOPPING, WorkerState.FAILED}),
    WorkerState.STOPPING: frozenset({WorkerState.STOPPED, WorkerState.FAILED}),
    WorkerState.STOPPED: frozenset(),
    WorkerState.FAILED: frozenset(),
}

# Terminal states — no further transitions; lifecycle calls become no-ops.
TERMINAL_WORKER_STATES: frozenset[WorkerState] = frozenset(
    {WorkerState.STOPPED, WorkerState.FAILED}
)

"""Workflow execution lifecycle (M6.4B-2) — built on `core.fsm`.

The second FSM the M6.4A design's §6.1 commits to, restoring Tool Runtime's
dual-FSM shape (Bundle 1's `workflow.lifecycle` is the descriptor FSM; this
is the distinct, runtime-execution FSM). Reuses `core.fsm.StateMachine`
unchanged — no new state-machine framework.

```
PENDING -> RUNNING -> COMPLETED
              <-> PAUSED (PAUSE / RESUME)
              -> RECOVERING (NODE_FAILED)
                    -> RUNNING (RECOVER, retry succeeds)
                    -> COMPENSATING (COMPENSATE) -> FAILED (FAIL)
                    -> FAILED (FAIL, no compensation)
any non-terminal -> CANCELLED (CANCEL)
```
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import WorkflowExecution, WorkflowExecutionState


class WorkflowExecutionTrigger(StrEnum):
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    NODE_FAILED = "node_failed"
    RECOVER = "recover"
    COMPENSATE = "compensate"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"


def _execution_def() -> StateMachineDefinition[WorkflowExecutionState, WorkflowExecutionTrigger]:
    s = WorkflowExecutionState
    t = WorkflowExecutionTrigger
    d: StateMachineDefinition[WorkflowExecutionState, WorkflowExecutionTrigger] = (
        StateMachineDefinition()
    )
    d.add(s.PENDING, t.START, s.RUNNING)
    d.add(s.RUNNING, t.PAUSE, s.PAUSED)
    d.add(s.PAUSED, t.RESUME, s.RUNNING)
    d.add(s.RUNNING, t.NODE_FAILED, s.RECOVERING)
    d.add(s.RECOVERING, t.RECOVER, s.RUNNING)
    d.add(s.RECOVERING, t.COMPENSATE, s.COMPENSATING)
    d.add(s.RECOVERING, t.FAIL, s.FAILED)
    d.add(s.COMPENSATING, t.FAIL, s.FAILED)
    d.add(s.RUNNING, t.COMPLETE, s.COMPLETED)
    for src in (s.PENDING, s.RUNNING, s.PAUSED, s.RECOVERING, s.COMPENSATING):
        d.add(src, t.CANCEL, s.CANCELLED)
    return d


WORKFLOW_EXECUTION_LIFECYCLE = _execution_def()


def advance_execution(
    execution: WorkflowExecution, trigger: WorkflowExecutionTrigger,
    *, clock: Callable[[], float] | None = None,
) -> WorkflowExecution:
    import time

    now = (clock or time.time)()
    fsm: StateMachine[WorkflowExecutionState, WorkflowExecutionTrigger] = StateMachine(
        WORKFLOW_EXECUTION_LIFECYCLE, execution.state, machine_id=execution.execution_id,
        clock=clock,
    )
    return execution.with_state(fsm.fire(trigger), now=now)

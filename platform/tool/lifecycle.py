"""Tool Runtime lifecycles (M6.1B-1) — built on `core.fsm`.

Two independent deterministic state machines, dogfooding the shared FSM framework (no
hand-written transition logic). Illegal moves raise typed `FSMError`s.

**Descriptor lifecycle** (per `ToolDescriptor` — is this tool version usable at all):
DRAFT → REGISTERED → ACTIVE → DEPRECATED → RETIRED, with direct RETIRE edges from
REGISTERED and ACTIVE (a tool may be withdrawn before or after it is ever activated,
without forcing an intermediate DEPRECATED hop).

**Execution lifecycle** (per `ToolResult` — how did this one call go):
RECEIVED → VALIDATING → EXECUTING → COMPLETED, with FAIL reachable from any active
state and CANCEL reachable from any pre-terminal state. No AUTHORIZING state yet —
authorization is the Policy Engine's job (M6.1B-2, an explicit non-goal here); no
retry loop.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import DescriptorState, ExecutionState, ToolDescriptor, ToolResult


class DescriptorTrigger(StrEnum):
    REGISTER = "register"
    ACTIVATE = "activate"
    DEPRECATE = "deprecate"
    RETIRE = "retire"


class ExecutionTrigger(StrEnum):
    VALIDATE = "validate"
    EXECUTE = "execute"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"


def _descriptor_def() -> StateMachineDefinition[DescriptorState, DescriptorTrigger]:
    d: StateMachineDefinition[DescriptorState, DescriptorTrigger] = StateMachineDefinition()
    d.add(DescriptorState.DRAFT, DescriptorTrigger.REGISTER, DescriptorState.REGISTERED)
    d.add(DescriptorState.REGISTERED, DescriptorTrigger.ACTIVATE, DescriptorState.ACTIVE)
    d.add(DescriptorState.ACTIVE, DescriptorTrigger.DEPRECATE, DescriptorState.DEPRECATED)
    d.add(DescriptorState.DEPRECATED, DescriptorTrigger.RETIRE, DescriptorState.RETIRED)
    # a tool may be withdrawn before or after activation without an intermediate hop
    d.add(DescriptorState.REGISTERED, DescriptorTrigger.RETIRE, DescriptorState.RETIRED)
    d.add(DescriptorState.ACTIVE, DescriptorTrigger.RETIRE, DescriptorState.RETIRED)
    return d


TOOL_DESCRIPTOR_LIFECYCLE = _descriptor_def()


def advance_descriptor(
    descriptor: ToolDescriptor, trigger: DescriptorTrigger,
    *, clock: Callable[[], float] | None = None,
) -> ToolDescriptor:
    """Validate `trigger` against the descriptor's state; return it in the new state."""
    fsm: StateMachine[DescriptorState, DescriptorTrigger] = StateMachine(
        TOOL_DESCRIPTOR_LIFECYCLE, descriptor.state,
        machine_id=descriptor.qualified_name, clock=clock,
    )
    return descriptor.with_state(fsm.fire(trigger))


def _execution_def() -> StateMachineDefinition[ExecutionState, ExecutionTrigger]:
    d: StateMachineDefinition[ExecutionState, ExecutionTrigger] = StateMachineDefinition()
    d.add(ExecutionState.RECEIVED, ExecutionTrigger.VALIDATE, ExecutionState.VALIDATING)
    d.add(ExecutionState.VALIDATING, ExecutionTrigger.EXECUTE, ExecutionState.EXECUTING)
    d.add(ExecutionState.EXECUTING, ExecutionTrigger.COMPLETE, ExecutionState.COMPLETED)
    for st in (ExecutionState.RECEIVED, ExecutionState.VALIDATING, ExecutionState.EXECUTING):
        d.add(st, ExecutionTrigger.FAIL, ExecutionState.FAILED)
        d.add(st, ExecutionTrigger.CANCEL, ExecutionState.CANCELLED)
    return d


TOOL_EXECUTION_LIFECYCLE = _execution_def()


def advance_execution(
    result: ToolResult, trigger: ExecutionTrigger,
    *, clock: Callable[[], float] | None = None,
) -> ToolResult:
    """Validate `trigger` against the result's state; return it in the new state."""
    fsm: StateMachine[ExecutionState, ExecutionTrigger] = StateMachine(
        TOOL_EXECUTION_LIFECYCLE, result.state, machine_id=result.request_id, clock=clock,
    )
    return result.with_state(fsm.fire(trigger))

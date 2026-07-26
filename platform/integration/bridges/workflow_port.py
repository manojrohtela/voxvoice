"""WorkflowPortBridge (M6.7B-1) — closes Deferred Decisions item 10.

Per the M6.7A design §18 ADR rec 2: `WorkflowPort` conformance is resolved via
a bridge adapter at the composition root, never inside `workflow/` — the same
pattern `bridge.ProviderModelBridge` already established for `ModelProvider`.
This adapter satisfies the **frozen M5.0** `core.ports.workflow.WorkflowPort`
(`start`/`signal`/`status`) by delegating to
`workflow.execution.WorkflowExecutionRuntime`, proving the runtime conforms to
that port without ever asserting an unverified shim inside the capability
(the honest gap Agent Runtime's own `AgentWorkflowPort` docstring named).

Status vocabulary adaptation: `WorkflowExecutionState` has eight members;
the frozen port documents four (`running|paused|completed|failed`). The
mapping here is the conformance adaptation the bridge exists to provide."""

from __future__ import annotations

from typing import Any

from core.ports.workflow import WorkflowHandle, WorkflowPort, WorkflowStart
from workflow import WorkflowRegistry
from workflow.execution import WorkflowExecutionRuntime

from ..errors import IntegrationError
from .refs import parse_workflow_ref

# WorkflowExecutionState value -> the frozen WorkflowPort's four-value vocabulary.
_PORT_STATUS = {
    "pending": "running",
    "running": "running",
    "recovering": "running",
    "compensating": "running",
    "paused": "paused",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "failed",
}


class WorkflowPortBridge(WorkflowPort):
    def __init__(
        self, execution_runtime: WorkflowExecutionRuntime, registry: WorkflowRegistry
    ) -> None:
        self._rt = execution_runtime
        self._registry = registry
        self._instances: dict[str, tuple[str, str]] = {}  # instance_id -> (workflow_id, status)

    def start(self, start: WorkflowStart) -> WorkflowHandle:
        namespace, id_, version = parse_workflow_ref(start.workflow_id)
        self._registry.get_descriptor(namespace, id_, version)  # raises if unregistered
        execution, _result = self._rt.start(
            namespace, id_, version, initial_variables=dict(start.inputs)
        )
        status = _PORT_STATUS.get(execution.state.value, "running")
        self._instances[execution.execution_id] = (start.workflow_id, status)
        return WorkflowHandle(
            instance_id=execution.execution_id, workflow_id=start.workflow_id, status=status
        )

    def signal(self, instance_id: str, name: str, payload: dict[str, Any]) -> None:
        execution, _result = self._rt.resume_after_event(
            instance_id, {"signal": name, **dict(payload)}
        )
        workflow_id = self._instances.get(instance_id, (instance_id, ""))[0]
        self._instances[instance_id] = (
            workflow_id, _PORT_STATUS.get(execution.state.value, "running")
        )

    def status(self, instance_id: str) -> WorkflowHandle:
        try:
            workflow_id, status = self._instances[instance_id]
        except KeyError:
            raise IntegrationError(f"unknown workflow instance {instance_id!r}") from None
        return WorkflowHandle(instance_id=instance_id, workflow_id=workflow_id, status=status)

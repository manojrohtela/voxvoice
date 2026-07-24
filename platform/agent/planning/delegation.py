"""Workflow delegation (M6.5B-2) — resolve/submit/status/signal, all as
direct, thin forwarding calls through the Agent-owned `AgentWorkflowPort`
seam (Bundle 1). **Delegation never becomes execution**: this module never
constructs or executes a `WorkflowGraph` — Workflow Runtime, behind the
port, owns that exclusively.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..extensions import AgentWorkflowPort
from .domain import PlanStep
from .workflow_selection import select_workflow


def delegate_step(step: PlanStep, *, workflow_port: AgentWorkflowPort) -> str:
    if step.kind != "delegate_workflow" or not step.workflow_ref:
        raise ValueError(f"step {step.step_id!r} is not a delegate_workflow step")
    handle = select_workflow(step.workflow_ref, workflow_port=workflow_port)
    return workflow_port.submit(handle, step.inputs)


def query_status(execution_id: str, *, workflow_port: AgentWorkflowPort) -> str:
    return workflow_port.status(execution_id)


def send_signal(
    execution_id: str, name: str, payload: Mapping[str, Any], *, workflow_port: AgentWorkflowPort,
) -> None:
    workflow_port.signal(execution_id, name, payload)

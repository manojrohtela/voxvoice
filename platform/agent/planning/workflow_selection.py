"""Workflow selection (M6.5B-2) — a deterministic lookup only, never a
fuzzy/AI match. The *choice* of which `workflow_ref` to use came from the
Reasoning-informed decomposition step (`decomposer.py`); this module only
resolves a namespace.id[@version] reference to a concrete, registered
workflow via the Agent-owned `AgentWorkflowPort` seam (Bundle 1, `agent.
extensions`) — never `core.ports.workflow.WorkflowPort` directly (see
`AgentWorkflowPort`'s own docstring for why).
"""

from __future__ import annotations

from ..extensions import AgentWorkflowPort


def select_workflow(workflow_ref: str, *, workflow_port: AgentWorkflowPort) -> str:
    return workflow_port.resolve(workflow_ref)

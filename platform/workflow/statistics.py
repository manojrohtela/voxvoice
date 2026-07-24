"""Workflow runtime statistics (M6.4B-1) — pure aggregation over
already-registered `WorkflowDescriptor` sequences. No I/O, no side effects.
No execution statistics exist here (Bundle 2 scope) -- only registration-time
shape counts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .domain import WorkflowDescriptor


@dataclass(frozen=True, slots=True)
class WorkflowRuntimeStatistics:
    per_namespace: dict[str, int] = field(default_factory=dict)
    per_node_kind: dict[str, int] = field(default_factory=dict)
    workflow_count: int = 0
    avg_node_count: float = 0.0


def compute_statistics(descriptors: Sequence[WorkflowDescriptor]) -> WorkflowRuntimeStatistics:
    per_namespace: dict[str, int] = {}
    per_node_kind: dict[str, int] = {}
    total_nodes = 0

    for d in descriptors:
        per_namespace[d.manifest.namespace] = per_namespace.get(d.manifest.namespace, 0) + 1
        for node in d.manifest.graph.nodes:
            per_node_kind[node.kind.value] = per_node_kind.get(node.kind.value, 0) + 1
        total_nodes += len(d.manifest.graph.nodes)

    count = len(descriptors)
    return WorkflowRuntimeStatistics(
        per_namespace=per_namespace, per_node_kind=per_node_kind, workflow_count=count,
        avg_node_count=(total_nodes / count) if count else 0.0,
    )

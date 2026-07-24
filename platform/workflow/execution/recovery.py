"""Recovery engine (M6.4B-2) — resumes a `RECOVERING` execution from its
**last checkpoint**, never from the start of the graph. A thin, named
surface over `WorkflowExecutionEngine.recover` (the shared continuation
logic lives there, alongside `resume_after_event`, to avoid duplicating the
deterministic-loop machinery) — kept as its own module so recovery has an
explicit, independently-testable entry point per this bundle's scope.
"""

from __future__ import annotations

from ..domain import WorkflowGraph
from .cancellation import WorkflowCancellationToken
from .domain import WorkflowExecution, WorkflowSnapshot
from .engine import EngineOutcome, WorkflowExecutionEngine


def recover_execution(
    engine: WorkflowExecutionEngine,
    graph: WorkflowGraph,
    execution: WorkflowExecution,
    snapshot: WorkflowSnapshot,
    *,
    cancellation: WorkflowCancellationToken | None = None,
) -> EngineOutcome:
    """`snapshot` must be the execution's own last recorded checkpoint state
    -- recovery always resumes from `snapshot.current_node`, the node whose
    failure drove the execution into `RECOVERING`, never from
    `graph.entry_node`."""
    return engine.recover(graph, execution, snapshot, cancellation=cancellation)

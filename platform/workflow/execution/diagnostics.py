"""WorkflowExecutionDiagnostics (M6.4B-2) — a read-only aggregator. Never
mutates the repository it inspects.
"""

from __future__ import annotations

from .config import RUNTIME_VERSION, WorkflowExecutionConfiguration
from .domain import WorkflowExecutionDiagnostics


def build_diagnostics(
    *,
    config: WorkflowExecutionConfiguration,
    execution_count: int = 0,
    replay_count: int = 0,
    healthy: bool = True,
) -> WorkflowExecutionDiagnostics:
    return WorkflowExecutionDiagnostics(
        runtime_version=RUNTIME_VERSION, config_fingerprint=config.fingerprint(),
        execution_stats={"executions": execution_count},
        replay_stats={"replays": replay_count},
        health_summary="healthy" if healthy else "unhealthy",
    )

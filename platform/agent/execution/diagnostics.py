"""AgentExecutionDiagnostics (M6.5B-3) — a read-only aggregator. Never
mutates the repository it inspects.
"""

from __future__ import annotations

from .config import RUNTIME_VERSION, AgentExecutionConfiguration
from .domain import AgentExecutionDiagnostics


def build_diagnostics(
    *,
    config: AgentExecutionConfiguration,
    execution_count: int = 0,
    replay_count: int = 0,
    healthy: bool = True,
) -> AgentExecutionDiagnostics:
    return AgentExecutionDiagnostics(
        runtime_version=RUNTIME_VERSION, config_fingerprint=config.fingerprint(),
        execution_stats={"count": execution_count},
        replay_stats={"count": replay_count},
        health_summary="healthy" if healthy else "unhealthy",
    )

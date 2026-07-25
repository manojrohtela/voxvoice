"""LoopDiagnostics (M6.6B-1) — a read-only builder. Unlike every other Epic 6
capability's own diagnostics (which report registry/repository stats), Loop
Engine has neither — this reports only configuration identity and, once a
caller has run a loop, the shape of that single `LoopResult`. No live
statistics aggregation ships in this bundle (that is M6.6B-2's own
`LoopStatistics` builder, over many results).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.config import RUNTIME_VERSION, LoopConfiguration
from ..core.domain import LoopResult


@dataclass(frozen=True, slots=True)
class LoopDiagnostics:
    runtime_version: str
    config_fingerprint: str
    loop_id: str | None = None
    iteration_count: int | None = None
    stop_reason: str | None = None
    converged: bool | None = None
    health_summary: str = "unknown"


def build_diagnostics(
    *,
    config: LoopConfiguration,
    result: LoopResult | None = None,
    healthy: bool = True,
) -> LoopDiagnostics:
    return LoopDiagnostics(
        runtime_version=RUNTIME_VERSION, config_fingerprint=config.fingerprint(),
        loop_id=result.loop_id if result else None,
        iteration_count=result.iteration_count if result else None,
        stop_reason=result.stop_reason if result else None,
        converged=result.converged if result else None,
        health_summary="healthy" if healthy else "unhealthy",
    )

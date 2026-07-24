"""Planning diagnostics (M6.5B-2) — a read-only aggregator. Never mutates
anything it inspects.
"""

from __future__ import annotations

from dataclasses import dataclass

RUNTIME_VERSION = "M6.5B-2"


@dataclass(frozen=True, slots=True)
class PlanningDiagnostics:
    runtime_version: str
    healthy: bool
    plans_generated: int = 0
    decisions_recorded: int = 0


def build_planning_diagnostics(
    *, plans_generated: int, decisions_recorded: int, healthy: bool = True,
) -> PlanningDiagnostics:
    return PlanningDiagnostics(
        runtime_version=RUNTIME_VERSION, healthy=healthy,
        plans_generated=plans_generated, decisions_recorded=decisions_recorded,
    )

"""AgentRuntimeDiagnostics (M6.5B-1) — a read-only aggregator. Never mutates
the registry/repository it inspects.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .config import RUNTIME_VERSION, AgentConfiguration
from .registry import AgentRegistry


@dataclass(frozen=True, slots=True)
class AgentRuntimeDiagnostics:
    runtime_version: str
    config_fingerprint: str
    registry_stats: Mapping[str, object] = field(default_factory=dict)
    repository_stats: Mapping[str, object] = field(default_factory=dict)
    health_summary: str = "unknown"


def build_diagnostics(
    *,
    config: AgentConfiguration,
    registry: AgentRegistry,
    repository_registration_count: int = 0,
    healthy: bool = True,
) -> AgentRuntimeDiagnostics:
    names = registry.names()
    return AgentRuntimeDiagnostics(
        runtime_version=RUNTIME_VERSION, config_fingerprint=config.fingerprint(),
        registry_stats={"agents": names, "count": len(names)},
        repository_stats={"registrations": repository_registration_count},
        health_summary="healthy" if healthy else "unhealthy",
    )

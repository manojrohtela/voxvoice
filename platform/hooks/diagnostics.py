"""HookRuntimeDiagnostics (M6.3B) — a read-only aggregator. Never mutates the
registry/repository it inspects.
"""

from __future__ import annotations

from .config import RUNTIME_VERSION, HookConfiguration
from .domain import HookRuntimeDiagnostics
from .registry import HookRegistry


def build_diagnostics(
    *,
    config: HookConfiguration,
    registry: HookRegistry,
    run_count: int = 0,
    replay_count: int = 0,
    healthy: bool = True,
) -> HookRuntimeDiagnostics:
    names = registry.names()
    return HookRuntimeDiagnostics(
        runtime_version=RUNTIME_VERSION, config_fingerprint=config.fingerprint(),
        registry_stats={"hooks": names, "count": len(names)},
        execution_stats={"runs": run_count},
        replay_stats={"replays": replay_count},
        health_summary="healthy" if healthy else "unhealthy",
    )

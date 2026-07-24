"""Agent runtime statistics (M6.5B-1) — pure aggregation over
already-registered `AgentDescriptor` sequences. No I/O, no side effects. No
goal/decision/delegation statistics exist here (later-bundle scope) — only
registration-time shape counts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .domain import AgentDescriptor


@dataclass(frozen=True, slots=True)
class AgentRuntimeStatistics:
    per_namespace: dict[str, int] = field(default_factory=dict)
    per_capability_kind: dict[str, int] = field(default_factory=dict)
    agent_count: int = 0
    avg_capability_count: float = 0.0


def compute_statistics(descriptors: Sequence[AgentDescriptor]) -> AgentRuntimeStatistics:
    per_namespace: dict[str, int] = {}
    per_capability_kind: dict[str, int] = {}
    total_capabilities = 0

    for d in descriptors:
        per_namespace[d.manifest.namespace] = per_namespace.get(d.manifest.namespace, 0) + 1
        for cap in d.manifest.capabilities:
            per_capability_kind[cap.kind] = per_capability_kind.get(cap.kind, 0) + 1
        total_capabilities += len(d.manifest.capabilities)

    count = len(descriptors)
    return AgentRuntimeStatistics(
        per_namespace=per_namespace, per_capability_kind=per_capability_kind, agent_count=count,
        avg_capability_count=(total_capabilities / count) if count else 0.0,
    )

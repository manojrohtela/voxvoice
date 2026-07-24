"""Capability resolution (M6.5B-2) — resolves each declared `AgentCapability`
via the injected `AgentCapabilityResolver` seam (Bundle 1, `agent.
extensions`). Mirrors `skill.extensions.CapabilityResolver`'s own precedent,
applied a second time at the Agent layer. Agent Runtime never imports a
Skill/Tool/Workflow registry directly to do this itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain import AgentCapability
from ..extensions import AgentCapabilityResolver
from .domain import ResolvedCapability


def resolve_capabilities(
    capabilities: Sequence[AgentCapability], *, resolver: AgentCapabilityResolver,
) -> tuple[ResolvedCapability, ...]:
    return tuple(resolver.resolve(cap) for cap in capabilities)

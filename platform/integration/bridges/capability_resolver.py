"""PlatformCapabilityResolver (M6.7B-2) — the concrete `AgentCapabilityResolver`.

Per the M6.7A design §8: the concrete resolver that answers an Agent's declared
`AgentCapability` against whichever runtime its `kind` implies is composition-
root code, built in `integration/`, never inside `agent/`/`skill/`. This
resolves against each owning runtime's own **published, read-only** discovery
surface (`ToolCapabilityCatalog.discover`, `SkillCapabilityCatalog.discover`,
the Workflow registry's public `names()`) — never a capability's private state,
never a registry mutation, never a redesign.

Memory/Knowledge are runtime services reached via `pipeline.RuntimePipeline`
(design §10), always present in a composed platform — a declared requirement for
them resolves as available without a separate lookup. An unknown `kind`
resolves as unavailable (the Agent planner, not this resolver, decides whether
that is fatal — resolution reports satisfiability only, per ADR-0014).
"""

from __future__ import annotations

from agent.extensions import AgentCapabilityResolver
from agent.planning import ResolvedCapability
from skill import SkillRuntime
from tool import ToolRuntime
from workflow import WorkflowRuntime

_ALWAYS_AVAILABLE = frozenset({"memory", "knowledge"})


class PlatformCapabilityResolver(AgentCapabilityResolver):
    def __init__(
        self,
        *,
        tool_runtime: ToolRuntime,
        skill_runtime: SkillRuntime,
        workflow_runtime: WorkflowRuntime,
    ) -> None:
        self._tool = tool_runtime
        self._skill = skill_runtime
        self._workflow = workflow_runtime

    def resolve(self, requirement) -> ResolvedCapability:
        kind = getattr(requirement, "kind", "")
        tag = getattr(requirement, "tag", "")

        if kind == "tool":
            refs = self._tool.catalog.discover(tag)
        elif kind == "skill":
            refs = self._skill.catalog.discover(tag)
        elif kind == "workflow":
            refs = tuple(n for n in self._workflow.registry.names() if n == tag)
        elif kind in _ALWAYS_AVAILABLE:
            return ResolvedCapability(requirement=requirement, resolved_ref=tag, available=True)
        else:
            return ResolvedCapability(requirement=requirement, resolved_ref=None, available=False)

        return ResolvedCapability(
            requirement=requirement,
            resolved_ref=refs[0] if refs else None,
            available=bool(refs),
        )

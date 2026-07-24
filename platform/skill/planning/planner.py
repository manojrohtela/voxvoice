"""SkillExecutionPlanner (M6.2B-2) — deterministic, replayable, provider- and
transport-neutral planning. Builds a leaf-first dependency execution order
from a `SkillRegistry`'s already-validated (acyclic, pre-registered)
dependency graph.

Never executes a Skill or Tool, never resolves capabilities — capability
requirements are copied onto the plan verbatim, as *declared* data, never
checked against a `CapabilityResolver` (which remains interface-only and is
never imported or instantiated here).
"""

from __future__ import annotations

from ..domain import SkillDescriptor
from ..ids import content_hash
from ..registry import SkillRegistry
from .domain import SkillPlan, SkillPlanStep
from .ids import plan_id_for

_Key = tuple[str, str, str]


def build_plan(
    registry: SkillRegistry,
    namespace: str,
    id_: str,
    version: str | None = None,
    *,
    now: float = 0.0,
    plan_version: str = "1",
    planner_version: str = "1",
) -> SkillPlan:
    root = registry.get_descriptor(namespace, id_, version)
    steps = _topological_order(registry, root)

    dependency_hash = content_hash(
        *[f"{s.qualified_name}@{s.version}" for s in steps], "dependency_order",
    )
    manifest_hash = content_hash(
        root.manifest.qualified_name, root.manifest.version, root.manifest.interface_version,
    )
    capability_requirements = tuple(
        sorted(root.manifest.required_capabilities, key=lambda r: (r.kind.value, r.tag))
    )

    return SkillPlan(
        plan_id=plan_id_for(root.qualified_name, root.version, dependency_hash),
        namespace=root.manifest.namespace, id=root.manifest.id, version=root.manifest.version,
        steps=steps, capability_requirements=capability_requirements,
        manifest_hash=manifest_hash, dependency_hash=dependency_hash,
        plan_version=plan_version, planner_version=planner_version, created_at=now,
    )


def _topological_order(
    registry: SkillRegistry, root: SkillDescriptor
) -> tuple[SkillPlanStep, ...]:
    """A deterministic, leaf-first post-order walk of the dependency graph.
    The registry has already validated this graph acyclic and every
    dependency already-registered at registration time (§7/§9 of the M6.2A
    design), so a plain post-order DFS — visiting each distinct
    `(namespace, id, version)` node exactly once, children sorted for
    determinism — yields a valid execution order with no separate cycle
    guard needed here."""
    visited: set[_Key] = set()
    order: list[SkillPlanStep] = []

    def walk(descriptor: SkillDescriptor) -> None:
        key = (descriptor.manifest.namespace, descriptor.manifest.id, descriptor.manifest.version)
        if key in visited:
            return
        visited.add(key)
        for dep in sorted(
            descriptor.manifest.dependencies, key=lambda d: (d.namespace, d.id, d.version or "")
        ):
            dep_descriptor = registry.get_descriptor(dep.namespace, dep.id, dep.version)
            walk(dep_descriptor)
        order.append(
            SkillPlanStep(
                namespace=key[0], id=key[1], version=key[2], sequence=len(order),
            )
        )

    walk(root)
    return tuple(order)


class SkillExecutionPlanner:
    """A thin, stateless facade over `build_plan` — deterministic given the
    same registry state, replayable, provider- and transport-neutral (no
    provider/transport concept appears anywhere in this module)."""

    def plan(
        self,
        registry: SkillRegistry,
        namespace: str,
        id_: str,
        version: str | None = None,
        *,
        now: float = 0.0,
    ) -> SkillPlan:
        return build_plan(registry, namespace, id_, version, now=now)

"""SkillExecutionContext builder (M6.2B-2) — a pure projection of an
already-built `SkillPlan`. Never influences the plan; never resolves any
capability requirement it copies onto the context.
"""

from __future__ import annotations

from collections.abc import Mapping

from .domain import SkillExecutionContext, SkillPlan
from .ids import context_id_for


def build_execution_context(
    plan: SkillPlan,
    *,
    org_id: str = "",
    tenant_id: str = "",
    requested_by: str = "",
    now: float = 0.0,
    metadata: Mapping[str, str] | None = None,
) -> SkillExecutionContext:
    return SkillExecutionContext(
        context_id=context_id_for(plan.plan_id, requested_by, now),
        plan_id=plan.plan_id, root=f"{plan.qualified_name}@{plan.version}",
        namespace=plan.namespace, org_id=org_id, tenant_id=tenant_id,
        requested_by=requested_by, created_at=now,
        capability_requirements=plan.capability_requirements, metadata=dict(metadata or {}),
    )

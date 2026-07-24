"""SkillPlanReplayRecord builder (M6.2B-2) — everything needed to
deterministically re-verify one planning pass later, without a live registry
reference. Mirrors `tool.observability.replay.replay_record_for`'s shape;
"replay" here is planning-replay only — there is no execution artifact to
replay, since a Skill never executes (§15 of the M6.2A design).
"""

from __future__ import annotations

from ..ids import content_hash
from .domain import SkillExecutionContext, SkillPlan, SkillPlanReplayRecord
from .ids import replay_id_for
from .serializer import PlanningSerializer


def plan_replay_record_for(
    plan: SkillPlan,
    context: SkillExecutionContext,
    *,
    now: float = 0.0,
    serializer: PlanningSerializer | None = None,
) -> SkillPlanReplayRecord:
    s = serializer or PlanningSerializer()
    plan_document = s.plan_to_dict(plan)
    context_document = s.context_to_dict(context)
    replay_hash = content_hash(plan_document, context_document, "planning_replay")
    return SkillPlanReplayRecord(
        replay_id=replay_id_for(plan.plan_id, context.context_id),
        plan_document=plan_document, context_document=context_document,
        replay_hash=replay_hash, replay_timestamp=now,
    )

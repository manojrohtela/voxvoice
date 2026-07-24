"""Planning events (M6.2B-2) — phase constants + their `EventType` mapping.
Reuses the existing `Event`/`EventBus`/`EventSource.SKILL` envelope
architecture unchanged; the only change to `core.events` is eight additive
`EventType` members plus one additive `EventSource` member (mirrors
`tool.observability.events`'s own shape).
"""

from __future__ import annotations

from enum import StrEnum

from core.events import EventType


class PlanningPhase(StrEnum):
    PLAN_CREATED = "plan_created"
    CONTEXT_CREATED = "context_created"
    POLICY_EVALUATED = "policy_evaluated"
    REPLAY_RECORDED = "replay_recorded"
    REPLAY_VALIDATED = "replay_validated"
    REPLAY_MISMATCH = "replay_mismatch"
    DIAGNOSTICS_GENERATED = "diagnostics_generated"
    PLANNING_FAILED = "planning_failed"


PHASE_EVENT_TYPE: dict[PlanningPhase, EventType] = {
    PlanningPhase.PLAN_CREATED: EventType.SKILL_PLAN_CREATED,
    PlanningPhase.CONTEXT_CREATED: EventType.SKILL_CONTEXT_CREATED,
    PlanningPhase.POLICY_EVALUATED: EventType.SKILL_POLICY_EVALUATED,
    PlanningPhase.REPLAY_RECORDED: EventType.SKILL_REPLAY_RECORDED,
    PlanningPhase.REPLAY_VALIDATED: EventType.SKILL_REPLAY_VALIDATED,
    PlanningPhase.REPLAY_MISMATCH: EventType.SKILL_REPLAY_MISMATCH,
    PlanningPhase.DIAGNOSTICS_GENERATED: EventType.SKILL_DIAGNOSTICS_GENERATED,
    PlanningPhase.PLANNING_FAILED: EventType.SKILL_PLANNING_FAILED,
}

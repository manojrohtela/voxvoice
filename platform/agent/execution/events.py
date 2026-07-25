"""Execution events (M6.5B-3) — phase constants + their `EventType` mapping.
`AGENT_ACTIVATED`/`AGENT_HANDOFF` (Phase 0 scaffolding) are unrelated to
goal execution; a goal genuinely executes over time, so it gets its own
started/completed/failed/cancelled/revised/replay/diagnostics vocabulary,
mirroring `workflow.execution.events`'s own extension.
"""

from __future__ import annotations

from enum import StrEnum

from core.events import EventType


class ExecutionPhase(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REVISED = "revised"
    REPLAY_RECORDED = "replay_recorded"
    REPLAY_VALIDATED = "replay_validated"
    REPLAY_MISMATCH = "replay_mismatch"
    DIAGNOSTICS_GENERATED = "diagnostics_generated"


PHASE_EVENT_TYPE: dict[ExecutionPhase, EventType] = {
    ExecutionPhase.STARTED: EventType.AGENT_GOAL_STARTED,
    ExecutionPhase.COMPLETED: EventType.AGENT_GOAL_COMPLETED,
    ExecutionPhase.FAILED: EventType.AGENT_GOAL_FAILED,
    ExecutionPhase.CANCELLED: EventType.AGENT_GOAL_CANCELLED,
    ExecutionPhase.REVISED: EventType.AGENT_GOAL_REVISED,
    ExecutionPhase.REPLAY_RECORDED: EventType.AGENT_REPLAY_RECORDED,
    ExecutionPhase.REPLAY_VALIDATED: EventType.AGENT_REPLAY_VALIDATED,
    ExecutionPhase.REPLAY_MISMATCH: EventType.AGENT_REPLAY_MISMATCH,
    ExecutionPhase.DIAGNOSTICS_GENERATED: EventType.AGENT_DIAGNOSTICS_GENERATED,
}

"""Execution events (M6.4B-2) — phase constants + their `EventType` mapping.
Reuses Phase 0's pre-existing `WORKFLOW_STARTED`/`WORKFLOW_STEP`/
`WORKFLOW_COMPLETED` scaffolding verbatim for the started/checkpoint/
completed cases; only failure/cancellation/replay/diagnostics needed new,
additive `EventType` members.
"""

from __future__ import annotations

from enum import StrEnum

from core.events import EventType


class ExecutionPhase(StrEnum):
    STARTED = "started"
    CHECKPOINT_CREATED = "checkpoint_created"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REPLAY_RECORDED = "replay_recorded"
    REPLAY_VALIDATED = "replay_validated"
    REPLAY_MISMATCH = "replay_mismatch"
    DIAGNOSTICS_GENERATED = "diagnostics_generated"


PHASE_EVENT_TYPE: dict[ExecutionPhase, EventType] = {
    ExecutionPhase.STARTED: EventType.WORKFLOW_STARTED,
    ExecutionPhase.CHECKPOINT_CREATED: EventType.WORKFLOW_STEP,
    ExecutionPhase.COMPLETED: EventType.WORKFLOW_COMPLETED,
    ExecutionPhase.FAILED: EventType.WORKFLOW_FAILED,
    ExecutionPhase.CANCELLED: EventType.WORKFLOW_CANCELLED,
    ExecutionPhase.REPLAY_RECORDED: EventType.WORKFLOW_REPLAY_RECORDED,
    ExecutionPhase.REPLAY_VALIDATED: EventType.WORKFLOW_REPLAY_VALIDATED,
    ExecutionPhase.REPLAY_MISMATCH: EventType.WORKFLOW_REPLAY_MISMATCH,
    ExecutionPhase.DIAGNOSTICS_GENERATED: EventType.WORKFLOW_DIAGNOSTICS_GENERATED,
}

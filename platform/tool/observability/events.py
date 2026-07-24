"""Observability events (M6.1B-3) — phase constants + their `EventType` mapping.
Reuses the existing `Event`/`EventBus`/`EventSource.TOOL` envelope architecture
unchanged; the only change to `core.events` is eight additive `EventType` members.
"""

from __future__ import annotations

from enum import StrEnum

from core.events import EventType


class ObservabilityPhase(StrEnum):
    EXECUTION_TRACED = "execution_traced"
    REPLAY_RECORDED = "replay_recorded"
    REPLAY_VALIDATED = "replay_validated"
    REPLAY_MISMATCH = "replay_mismatch"
    AUDIT_RECORDED = "audit_recorded"
    DIAGNOSTICS_GENERATED = "diagnostics_generated"
    METRICS_UPDATED = "metrics_updated"
    FAILURE_ANALYZED = "failure_analyzed"


PHASE_EVENT_TYPE: dict[ObservabilityPhase, EventType] = {
    ObservabilityPhase.EXECUTION_TRACED: EventType.TOOL_EXECUTION_TRACED,
    ObservabilityPhase.REPLAY_RECORDED: EventType.TOOL_REPLAY_RECORDED,
    ObservabilityPhase.REPLAY_VALIDATED: EventType.TOOL_REPLAY_VALIDATED,
    ObservabilityPhase.REPLAY_MISMATCH: EventType.TOOL_REPLAY_MISMATCH,
    ObservabilityPhase.AUDIT_RECORDED: EventType.TOOL_AUDIT_RECORDED,
    ObservabilityPhase.DIAGNOSTICS_GENERATED: EventType.TOOL_DIAGNOSTICS_GENERATED,
    ObservabilityPhase.METRICS_UPDATED: EventType.TOOL_METRICS_UPDATED,
    ObservabilityPhase.FAILURE_ANALYZED: EventType.TOOL_FAILURE_ANALYZED,
}

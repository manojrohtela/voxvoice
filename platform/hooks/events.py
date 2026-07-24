"""Hook Framework events (M6.3B) — phase constants + their `EventType`
mapping. Reuses the existing `Event`/`EventBus`/`EventSource.HOOKS` envelope
architecture unchanged; the only change to `core.events` is five additive
`EventType` members plus one additive `EventSource` member.
"""

from __future__ import annotations

from enum import StrEnum

from core.events import EventType


class HookPhase(StrEnum):
    RUN_COMPLETED = "run_completed"
    REPLAY_RECORDED = "replay_recorded"
    REPLAY_VALIDATED = "replay_validated"
    REPLAY_MISMATCH = "replay_mismatch"
    DIAGNOSTICS_GENERATED = "diagnostics_generated"


PHASE_EVENT_TYPE: dict[HookPhase, EventType] = {
    HookPhase.RUN_COMPLETED: EventType.HOOK_RUN_COMPLETED,
    HookPhase.REPLAY_RECORDED: EventType.HOOK_REPLAY_RECORDED,
    HookPhase.REPLAY_VALIDATED: EventType.HOOK_REPLAY_VALIDATED,
    HookPhase.REPLAY_MISMATCH: EventType.HOOK_REPLAY_MISMATCH,
    HookPhase.DIAGNOSTICS_GENERATED: EventType.HOOK_DIAGNOSTICS_GENERATED,
}

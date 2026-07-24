"""Governance events (M6.1B-2) — phase constants + their `EventType` mapping.

Reuses the existing `Event`/`EventBus`/`EventSource.TOOL` envelope architecture
unchanged; the only change to `core.events` is nine additive `EventType` members
(§ events, no existing member touched — AR-32), the same pattern the M6.1A design
already anticipated for descriptor-lifecycle events ("an additive vocabulary
extension, not a bus change, if ever needed").
"""

from __future__ import annotations

from enum import StrEnum

from core.events import EventType


class GovernancePhase(StrEnum):
    POLICY_EVALUATED = "policy_evaluated"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXPIRED = "approval_expired"
    AUTHORIZATION_COMPLETED = "authorization_completed"
    RATE_LIMIT_EVALUATED = "rate_limit_evaluated"
    SECRET_REQUESTED = "secret_requested"
    GOVERNANCE_COMPLETED = "governance_completed"


PHASE_EVENT_TYPE: dict[GovernancePhase, EventType] = {
    GovernancePhase.POLICY_EVALUATED: EventType.TOOL_POLICY_EVALUATED,
    GovernancePhase.APPROVAL_REQUESTED: EventType.TOOL_APPROVAL_REQUESTED,
    GovernancePhase.APPROVAL_GRANTED: EventType.TOOL_APPROVAL_GRANTED,
    GovernancePhase.APPROVAL_REJECTED: EventType.TOOL_APPROVAL_REJECTED,
    GovernancePhase.APPROVAL_EXPIRED: EventType.TOOL_APPROVAL_EXPIRED,
    GovernancePhase.AUTHORIZATION_COMPLETED: EventType.TOOL_AUTHORIZATION_COMPLETED,
    GovernancePhase.RATE_LIMIT_EVALUATED: EventType.TOOL_RATE_LIMIT_EVALUATED,
    GovernancePhase.SECRET_REQUESTED: EventType.TOOL_SECRET_REQUESTED,
    GovernancePhase.GOVERNANCE_COMPLETED: EventType.TOOL_GOVERNANCE_COMPLETED,
}

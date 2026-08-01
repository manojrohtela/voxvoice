"""Deployment Actuator event types (Epic 8, M8.16) — ADR-0022 §Observability.

Typed `deployment.*` facts on the §A5 envelope — the Actuator is one more event *producer* (one
producer per fact). The durable audit trail subscribes to these; the Actuator itself never writes
to the audit store (it emits through an injected sink, staying decoupled from the control plane).
"""

from __future__ import annotations

from enum import StrEnum


class DeploymentEventType(StrEnum):
    REQUESTED = "deployment.requested"
    VALIDATED = "deployment.validated"
    PENDING_APPROVAL = "deployment.pending_approval"
    APPROVED = "deployment.approved"
    REJECTED = "deployment.rejected"
    QUEUED = "deployment.queued"
    STARTED = "deployment.started"
    VERIFYING = "deployment.verifying"
    COMPLETED = "deployment.completed"
    FAILED = "deployment.failed"
    ROLLING_BACK = "deployment.rolling_back"
    ROLLED_BACK = "deployment.rolled_back"
    CANCELLED = "deployment.cancelled"
    HEALTH_FAILED = "deployment.health_failed"

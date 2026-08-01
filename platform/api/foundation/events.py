"""Foundation domain events (M8.2).

Control-plane *domain* events — organization/workspace/membership/role/settings
lifecycle facts — emitted once by their owning service (Constitution §A5: one typed,
enveloped fact per owning producer; §A11.2: the event stream doubles as the audit
trail).

These deliberately use a foundation-local envelope rather than `core.events.Event`:
the runtime `Event` carries `session_id`/`seq` (bus-assigned, per-conversation) which
do not apply to control-plane domain facts, and reusing it would force meaningless
fields. This keeps the foundation self-contained and touches no frozen Epic-5 file,
while remaining structurally bridgeable onto the audit log later (id/type/ts/org/actor
are all present). `EventEmitter` is the seam; a recording emitter is the test/audit
default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FoundationEventType(StrEnum):
    """The foundation domain-event vocabulary. Dotted, lowercase, stable; additive."""

    ORGANIZATION_CREATED = "organization.created"
    # M8.6 — organization lifecycle (additive).
    ORGANIZATION_UPDATED = "organization.updated"
    ORGANIZATION_DELETED = "organization.deleted"
    ORGANIZATION_STATUS_CHANGED = "organization.status_changed"
    INVITATION_DECLINED = "invitation.declined"
    INVITATION_CANCELLED = "invitation.cancelled"
    INVITATION_RESENT = "invitation.resent"
    INVITATION_EXPIRED = "invitation.expired"
    WORKSPACE_CREATED = "workspace.created"
    # M8.7 — workspace lifecycle (additive).
    WORKSPACE_UPDATED = "workspace.updated"
    WORKSPACE_ARCHIVED = "workspace.archived"
    WORKSPACE_RESTORED = "workspace.restored"
    WORKSPACE_SETTINGS_CHANGED = "workspace.settings_changed"
    WORKSPACE_MEMBER_ADDED = "workspace.member_added"
    WORKSPACE_MEMBER_REMOVED = "workspace.member_removed"
    PROJECT_CREATED = "project.created"
    # M8.8 — project lifecycle (additive).
    PROJECT_UPDATED = "project.updated"
    PROJECT_ARCHIVED = "project.archived"
    PROJECT_RESTORED = "project.restored"
    PROJECT_SETTINGS_CHANGED = "project.settings_changed"
    PROJECT_MEMBER_ADDED = "project.member_added"
    PROJECT_MEMBER_REMOVED = "project.member_removed"
    PROJECT_ENVIRONMENT_SWITCHED = "project.environment_switched"
    # M8.10 — agent builder (additive). Every agent lifecycle fact lands in the durable
    # audit stream (M8.9) so version history + activity feeds reuse one system.
    AGENT_CREATED = "agent.created"
    AGENT_UPDATED = "agent.updated"
    AGENT_CONFIG_CHANGED = "agent.config_changed"
    AGENT_ARCHIVED = "agent.archived"
    AGENT_RESTORED = "agent.restored"
    AGENT_DELETED = "agent.deleted"
    AGENT_DUPLICATED = "agent.duplicated"
    AGENT_PUBLISHED = "agent.published"
    AGENT_UNPUBLISHED = "agent.unpublished"
    AGENT_ROLLED_BACK = "agent.rolled_back"
    AGENT_VERSION_CREATED = "agent.version_created"
    # M8.11 — tool framework (additive). Tool declarations + agent attachments; history reuses
    # the durable audit stream (M8.9).
    TOOL_CREATED = "tool.created"
    TOOL_UPDATED = "tool.updated"
    TOOL_ENABLED = "tool.enabled"
    TOOL_DISABLED = "tool.disabled"
    TOOL_ARCHIVED = "tool.archived"
    TOOL_RESTORED = "tool.restored"
    TOOL_DELETED = "tool.deleted"
    TOOL_ATTACHED = "tool.attached"
    TOOL_DETACHED = "tool.detached"
    TOOL_ATTACHMENT_ENABLED = "tool.attachment_enabled"
    TOOL_ATTACHMENT_DISABLED = "tool.attachment_disabled"
    TOOL_ATTACHMENT_CONFIGURED = "tool.attachment_configured"
    TOOL_REORDERED = "tool.reordered"
    # M8.12 — conversation runtime (additive). Session lifecycle + execution planning; the
    # history reuses the durable audit stream (M8.9).
    SESSION_CREATED = "session.created"
    SESSION_RESUMED = "session.resumed"
    SESSION_CLOSED = "session.closed"
    SESSION_EXPIRED = "session.expired"
    EXECUTION_PLANNED = "execution.planned"
    EXECUTION_FAILED = "execution.failed"
    # M8.13 — conversation playground (additive). Mock-runtime debugging turns; history reuses
    # the durable audit stream (M8.9).
    CONVERSATION_STARTED = "conversation.started"
    TURN_PLANNED = "conversation.turn_planned"
    TURN_COMPLETED = "conversation.turn_completed"
    CONVERSATION_RESET = "conversation.reset"
    CONVERSATION_CLOSED = "conversation.closed"
    # M8.14 — provider adapter binding / real execution (additive).
    EXECUTION_STARTED = "execution.started"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED_REAL = "execution.failed_real"
    PROVIDER_SELECTED = "provider.selected"
    PROVIDER_FAILED = "provider.failed"
    TOOL_EXECUTED = "tool.executed"
    TOOL_FAILED = "tool.failed"
    STREAMING_STARTED = "streaming.started"
    STREAMING_FINISHED = "streaming.finished"
    # M8.15 — knowledge tool (additive). Retrieval reuses the durable audit stream (M8.9).
    KNOWLEDGE_COLLECTION_CREATED = "knowledge.collection_created"
    KNOWLEDGE_COLLECTION_UPDATED = "knowledge.collection_updated"
    KNOWLEDGE_COLLECTION_ARCHIVED = "knowledge.collection_archived"
    KNOWLEDGE_COLLECTION_RESTORED = "knowledge.collection_restored"
    KNOWLEDGE_COLLECTION_DELETED = "knowledge.collection_deleted"
    KNOWLEDGE_DOCUMENT_UPLOADED = "knowledge.document_uploaded"
    KNOWLEDGE_DOCUMENT_VERSIONED = "knowledge.document_versioned"
    KNOWLEDGE_DOCUMENT_ARCHIVED = "knowledge.document_archived"
    KNOWLEDGE_DOCUMENT_RESTORED = "knowledge.document_restored"
    KNOWLEDGE_DOCUMENT_DELETED = "knowledge.document_deleted"
    KNOWLEDGE_CHUNK_CREATED = "knowledge.chunk_created"
    KNOWLEDGE_EMBEDDING_GENERATED = "knowledge.embedding_generated"
    KNOWLEDGE_INDEXED = "knowledge.indexed"
    KNOWLEDGE_RETRIEVED = "knowledge.retrieved"
    # M8.16 — Deployment Actuator (ADR-0022); the Actuator's typed deployment.* facts mirrored
    # into the durable Audit Platform (one producer per fact).
    DEPLOYMENT_REQUESTED = "deployment.requested"
    DEPLOYMENT_VALIDATED = "deployment.validated"
    DEPLOYMENT_PENDING_APPROVAL = "deployment.pending_approval"
    DEPLOYMENT_APPROVED = "deployment.approved"
    DEPLOYMENT_REJECTED = "deployment.rejected"
    DEPLOYMENT_QUEUED = "deployment.queued"
    DEPLOYMENT_STARTED = "deployment.started"
    DEPLOYMENT_VERIFYING = "deployment.verifying"
    DEPLOYMENT_COMPLETED = "deployment.completed"
    DEPLOYMENT_FAILED = "deployment.failed"
    DEPLOYMENT_ROLLING_BACK = "deployment.rolling_back"
    DEPLOYMENT_ROLLED_BACK = "deployment.rolled_back"
    DEPLOYMENT_CANCELLED = "deployment.cancelled"
    DEPLOYMENT_HEALTH_FAILED = "deployment.health_failed"
    # M8.17 — Observability alert-rule configuration facts (the only Observability writes; metrics/
    # logs/traces/health are projections, never new events).
    OBSERVABILITY_ALERT_RULE_CREATED = "observability.alert_rule_created"
    OBSERVABILITY_ALERT_RULE_UPDATED = "observability.alert_rule_updated"
    OBSERVABILITY_ALERT_RULE_DELETED = "observability.alert_rule_deleted"
    ENVIRONMENT_CREATED = "environment.created"
    MEMBER_INVITED = "member.invited"
    MEMBER_ACTIVATED = "member.activated"
    MEMBER_SUSPENDED = "member.suspended"
    ROLE_GRANTED = "role.granted"
    ROLE_REVOKED = "role.revoked"
    SETTINGS_UPDATED = "settings.updated"
    FEATURE_FLAG_UPDATED = "feature_flag.updated"
    PREFERENCES_UPDATED = "preferences.updated"
    ACCESS_GRANTED = "access.granted"
    ACCESS_DENIED = "access.denied"


@dataclass(frozen=True, slots=True)
class FoundationEvent:
    """One immutable control-plane domain fact. `org_id` is always present (isolation
    boundary); `actor` is the user/subject that caused it (audit); `resource` names
    the affected aggregate id; `data` is a free-form, non-secret payload."""

    id: str
    type: FoundationEventType
    ts: float
    org_id: str
    actor: str = ""
    resource: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class EventEmitter(ABC):
    """The emission seam. Services depend on this, never on a concrete sink."""

    @abstractmethod
    def emit(self, event: FoundationEvent) -> None: ...


class NullEventEmitter(EventEmitter):
    """Discards events (for callers that do not observe them)."""

    def emit(self, event: FoundationEvent) -> None:  # noqa: D401 - intentional no-op
        return None


class RecordingEventEmitter(EventEmitter):
    """Keeps every emitted event in order — the default, doubling as an in-memory
    audit trail and the test observation point. Deterministic.

    M8.9 (additive): also implements the `audit.AuditReader` read seam over its own
    in-order list, so the default in-memory Foundation gets a working (non-durable) audit
    reader for free — `.events`/`.of_type` are unchanged, existing callers keep working."""

    def __init__(self) -> None:
        self._events: list[FoundationEvent] = []

    def emit(self, event: FoundationEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> tuple[FoundationEvent, ...]:
        return tuple(self._events)

    def of_type(self, type_: FoundationEventType) -> tuple[FoundationEvent, ...]:
        return tuple(e for e in self._events if e.type is type_)

    # ── audit.AuditReader (M8.9) — the in-memory read side ────────────────────────
    def _records(self):
        # seq is the 1-based append position (stable, monotonic).
        from .audit import AuditRecord

        return [AuditRecord.of(i + 1, e) for i, e in enumerate(self._events)]

    def query(self, q):
        from .audit import paginate

        return paginate([r for r in self._records() if q.matches(r)], q)

    def stream(self, org_id: str):
        return tuple(r for r in self._records() if r.org_id == org_id)

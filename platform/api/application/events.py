"""Application-level events (M8.3).

Orchestration-altitude facts — a *use case* completed or was rejected, or a composite
business operation (org provisioned, member onboarded) finished. These are a **distinct
altitude** from the Foundation's domain events (`organization.created`, `role.granted`,
…): the Foundation records that a domain fact happened; the Application records that a
*use case* ran to a given outcome. The Application never re-emits a Foundation event
(no duplication) — it emits its own vocabulary.

Reuses the Foundation's `Clock`/`IdGenerator` seams for determinism (§A2.4). The emitter
is a seam; a recording emitter is the default (doubles as the application audit trail).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

PREFIX_APP_EVENT = "aevt"


class ApplicationEventType(StrEnum):
    """The application-event vocabulary. Dotted, lowercase, stable; additive.
    Deliberately disjoint from `FoundationEventType`."""

    # cross-cutting audit (every dispatched command)
    USE_CASE_COMPLETED = "use_case.completed"
    USE_CASE_REJECTED = "use_case.rejected"
    # composite business outcomes (staged transactionally by use cases)
    ORGANIZATION_PROVISIONED = "organization.provisioned"
    ORGANIZATION_UPDATED = "organization.updated"
    ORGANIZATION_DELETED = "organization.deleted"
    WORKSPACE_PROVISIONED = "workspace.provisioned"
    # M8.7 — workspace lifecycle (additive).
    WORKSPACE_UPDATED = "workspace.updated"
    WORKSPACE_ARCHIVED = "workspace.archived"
    WORKSPACE_RESTORED = "workspace.restored"
    WORKSPACE_MEMBER_ADDED = "workspace.member_added"
    WORKSPACE_MEMBER_REMOVED = "workspace.member_removed"
    MEMBER_ONBOARDED = "member.onboarded"
    MEMBER_OFFBOARDED = "member.offboarded"
    ROLE_ASSIGNMENT_CHANGED = "role_assignment.changed"
    PROJECT_PROVISIONED = "project.provisioned"
    # M8.8 — project lifecycle (additive).
    PROJECT_UPDATED = "project.updated"
    PROJECT_ARCHIVED = "project.archived"
    PROJECT_RESTORED = "project.restored"
    PROJECT_MEMBER_ADDED = "project.member_added"
    PROJECT_MEMBER_REMOVED = "project.member_removed"
    PROJECT_ENVIRONMENT_SWITCHED = "project.environment_switched"
    # M8.10 — agent builder (additive).
    AGENT_CREATED = "agent.created"
    AGENT_UPDATED = "agent.updated"
    AGENT_ARCHIVED = "agent.archived"
    AGENT_RESTORED = "agent.restored"
    AGENT_DELETED = "agent.deleted"
    AGENT_DUPLICATED = "agent.duplicated"
    AGENT_PUBLISHED = "agent.published"
    AGENT_UNPUBLISHED = "agent.unpublished"
    AGENT_ROLLED_BACK = "agent.rolled_back"
    # M8.11 — tool framework (additive).
    TOOL_CREATED = "tool.created"
    TOOL_UPDATED = "tool.updated"
    TOOL_ENABLED = "tool.enabled"
    TOOL_DISABLED = "tool.disabled"
    TOOL_ARCHIVED = "tool.archived"
    TOOL_RESTORED = "tool.restored"
    TOOL_DELETED = "tool.deleted"
    TOOL_ATTACHED = "tool.attached"
    TOOL_DETACHED = "tool.detached"
    TOOL_ATTACHMENT_TOGGLED = "tool.attachment_toggled"
    TOOL_ATTACHMENT_CONFIGURED = "tool.attachment_configured"
    TOOL_REORDERED = "tool.reordered"
    # M8.12 — conversation runtime (additive).
    SESSION_CREATED = "session.created"
    SESSION_RESUMED = "session.resumed"
    SESSION_CLOSED = "session.closed"
    SESSION_EXPIRED = "session.expired"
    EXECUTION_PLANNED = "execution.planned"
    # M8.13 — conversation playground (additive).
    CONVERSATION_STARTED = "conversation.started"
    TURN_COMPLETED = "conversation.turn_completed"
    CONVERSATION_RESET = "conversation.reset"
    CONVERSATION_CLOSED = "conversation.closed"
    # M8.14 — provider adapter binding / real execution (additive).
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_STREAMED = "execution.streamed"
    # M8.15 — knowledge tool (additive).
    KNOWLEDGE_COLLECTION_CHANGED = "knowledge.collection_changed"
    KNOWLEDGE_DOCUMENT_CHANGED = "knowledge.document_changed"
    KNOWLEDGE_INDEXED = "knowledge.indexed"
    KNOWLEDGE_SEARCHED = "knowledge.searched"
    DEPLOYMENT_CHANGED = "deployment.changed"  # M8.16 — deployment lifecycle orchestration
    OBSERVABILITY_ALERT_CHANGED = "observability.alert_changed"  # M8.17 — alert-rule config
    PREFERENCES_SAVED = "preferences.saved"


@dataclass(frozen=True, slots=True)
class ApplicationEvent:
    """One immutable application-orchestration fact. `use_case` names the operation;
    `outcome` is 'completed'/'rejected'; `org_id`/`actor` give the audit context."""

    id: str
    type: ApplicationEventType
    ts: float
    use_case: str
    outcome: str
    org_id: str = ""
    actor: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class ApplicationEventEmitter(ABC):
    """The emission seam. Use cases and the buses depend on this, never a concrete sink."""

    @abstractmethod
    def emit(self, event: ApplicationEvent) -> None: ...


class NullApplicationEventEmitter(ApplicationEventEmitter):
    def emit(self, event: ApplicationEvent) -> None:  # noqa: D401 - intentional no-op
        return None


class RecordingApplicationEventEmitter(ApplicationEventEmitter):
    """Keeps every emitted event in order — the default application audit trail and
    the test observation point. Deterministic."""

    def __init__(self) -> None:
        self._events: list[ApplicationEvent] = []

    def emit(self, event: ApplicationEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> tuple[ApplicationEvent, ...]:
        return tuple(self._events)

    def of_type(self, type_: ApplicationEventType) -> tuple[ApplicationEvent, ...]:
        return tuple(e for e in self._events if e.type is type_)

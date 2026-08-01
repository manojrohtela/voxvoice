"""Persistence models (M8.4) — SQLModel tables for the Customer Platform Foundation.

These are the **persistence representation** only; they are never seen by the Foundation
or Application (mapping.py translates row ↔ frozen domain aggregate). Same tables run on
SQLite (dev/test) and Postgres/Supabase (prod) unchanged — JSON columns are portable
(the existing `api/models.py` precedent).

Tables are namespaced `cp_*` (customer platform) to avoid any collision with the legacy
Control-Plane tables that share `SQLModel.metadata`. Referential integrity is enforced by
the Foundation domain services (existence checks), so these tables use plain indexed
columns rather than DB-level foreign keys — decoupled table creation, no ordering
constraints; FK constraints can be added in a later migration.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column
from sqlmodel import Field, SQLModel


class UserRow(SQLModel, table=True):
    __tablename__ = "cp_user"

    id: str = Field(primary_key=True)
    email: str = Field(index=True)
    display_name: str = ""
    created_at: float = 0.0
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))


class OrganizationRow(SQLModel, table=True):
    __tablename__ = "cp_organization"

    id: str = Field(primary_key=True)
    name: str
    plan: str = "free"
    region: str = "us"
    created_at: float = 0.0
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))
    # M8.6 — settings + lifecycle status (additive columns; migration 0002).
    status: str = Field(default="active", index=True)
    timezone: str = "UTC"
    language: str = "en"
    logo_url: str | None = None
    updated_at: float = 0.0


class WorkspaceRow(SQLModel, table=True):
    __tablename__ = "cp_workspace"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    name: str
    slug: str = Field(index=True)
    created_at: float = 0.0
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))
    # M8.7 — settings + lifecycle status (additive columns; migration 0003).
    description: str = ""
    icon: str = ""
    color: str = ""
    status: str = Field(default="active", index=True)
    updated_at: float = 0.0


class WorkspaceMembershipRow(SQLModel, table=True):
    __tablename__ = "cp_workspace_membership"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    user_id: str = Field(index=True)
    role: str = "member"
    status: str = "active"
    created_at: float = 0.0


class MembershipRow(SQLModel, table=True):
    __tablename__ = "cp_membership"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    user_id: str = Field(index=True)
    status: str = "invited"
    created_at: float = 0.0
    # M8.6 — invitation metadata (additive columns; migration 0002).
    invited_role: str = "member"
    expires_at: float = 0.0


class RoleBindingRow(SQLModel, table=True):
    __tablename__ = "cp_role_binding"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    user_id: str = Field(index=True)
    role: str = ""
    scope: str = ""
    scope_id: str = ""
    created_at: float = 0.0


class EnvironmentRow(SQLModel, table=True):
    __tablename__ = "cp_environment"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    name: str = ""
    kind: str = "development"
    created_at: float = 0.0


class ProjectRow(SQLModel, table=True):
    __tablename__ = "cp_project"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    name: str = ""
    slug: str = Field(index=True)
    created_at: float = 0.0
    # M8.8 — settings + lifecycle status (additive columns; migration 0004).
    description: str = ""
    icon: str = ""
    status: str = Field(default="active", index=True)
    updated_at: float = 0.0
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))


class ProjectMembershipRow(SQLModel, table=True):
    __tablename__ = "cp_project_membership"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    project_id: str = Field(index=True)
    user_id: str = Field(index=True)
    role: str = "member"
    status: str = "active"
    created_at: float = 0.0


class AuditEventRow(SQLModel, table=True):
    """The durable, append-only audit stream (M8.9). Never updated, never deleted. `seq` is
    an autoincrement primary key giving a stable total order for pagination; `workspace_id`/
    `project_id` are hoisted out of the event payload as indexed columns for efficient
    workspace/project activity queries. Every column except `data` is indexed for filtering."""

    __tablename__ = "cp_audit_event"

    seq: int | None = Field(default=None, primary_key=True)
    id: str = Field(index=True)
    type: str = Field(index=True)
    ts: float = Field(index=True)
    org_id: str = Field(index=True)
    actor: str = Field(default="", index=True)
    resource: str = ""
    workspace_id: str | None = Field(default=None, index=True)
    project_id: str | None = Field(default=None, index=True)
    data: dict = Field(default_factory=dict, sa_column=Column("data", SAJSON))


class AgentRow(SQLModel, table=True):
    """An Agent Builder config object (M8.10). `config` holds the full nested configuration
    (prompt/model/voice/variables) as a portable JSON column — secret variable *values* live
    here but are never emitted by any API (redaction happens at the view boundary)."""

    __tablename__ = "cp_agent"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    project_id: str = Field(index=True)
    name: str = ""
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    description: str = ""
    icon: str = ""
    category: str = Field(default="", index=True)
    tags: list = Field(default_factory=list, sa_column=Column("tags", SAJSON))
    language: str = "en"
    greeting: str = ""
    status: str = Field(default="draft", index=True)
    version: int = 0
    published_version: int = 0
    config: dict = Field(default_factory=dict, sa_column=Column("config", SAJSON))
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))


class AgentVersionRow(SQLModel, table=True):
    """An immutable published agent version (M8.10). Append-only: created on publish, only
    ever status-flipped to superseded, never deleted. `snapshot` is the full agent config +
    descriptive fields at publish time (JSON)."""

    __tablename__ = "cp_agent_version"

    id: str = Field(primary_key=True)
    agent_id: str = Field(index=True)
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    project_id: str = Field(index=True)
    number: int = Field(default=0, index=True)
    created_by: str = ""
    created_at: float = 0.0
    description: str = ""
    status: str = "published"
    rollback_target: int | None = None
    snapshot: dict = Field(default_factory=dict, sa_column=Column("snapshot", SAJSON))


class ToolRow(SQLModel, table=True):
    """A reusable tool declaration (M8.11). `config` holds the full typed configuration
    (schemas/settings/timeout/retry/depends_on) as a portable JSON column — never an SDK
    object. Declaration only; nothing here is executed."""

    __tablename__ = "cp_tool"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    project_id: str = Field(index=True)
    name: str = ""
    category: str = Field(default="custom", index=True)
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    description: str = ""
    icon: str = ""
    status: str = Field(default="draft", index=True)
    visibility: str = "project"
    enabled: bool = True
    version: int = 1
    config: dict = Field(default_factory=dict, sa_column=Column("config", SAJSON))
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))


class AgentToolRow(SQLModel, table=True):
    """The attachment of a tool to an agent (M8.11) — carries per-agent ordering, an enable
    flag and an optional config override."""

    __tablename__ = "cp_agent_tool"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    project_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    tool_id: str = Field(index=True)
    position: int = 0
    enabled: bool = True
    config_override: dict = Field(
        default_factory=dict, sa_column=Column("config_override", SAJSON)
    )
    created_at: float = 0.0


class ConversationSessionRow(SQLModel, table=True):
    """A runtime conversation session (M8.12). `state` holds the explicit conversation state
    (resolved prompt/variables/tool plan/metadata/diagnostics) as a portable JSON column — **no
    message history, no vectors**. Bound to an immutable published agent version."""

    __tablename__ = "cp_conversation_session"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    project_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    agent_version: int = 0
    correlation_id: str = Field(default="", index=True)
    status: str = Field(default="active", index=True)
    created_at: float = 0.0
    updated_at: float = 0.0
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))
    state: dict = Field(default_factory=dict, sa_column=Column("state", SAJSON))


class ConversationMessageRow(SQLModel, table=True):
    """The durable conversation message store (M8.14) — append-only per session, ordered by
    `seq`. Plain message store; **no vectors, no embeddings**. Backs the memory adapter."""

    __tablename__ = "cp_conversation_message"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    session_id: str = Field(index=True)
    seq: int = Field(default=0, index=True)
    role: str = ""
    content: str = ""
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))
    created_at: float = 0.0


class KnowledgeCollectionRow(SQLModel, table=True):
    __tablename__ = "cp_knowledge_collection"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    project_id: str = Field(index=True)
    name: str = ""
    base: str = "default"
    description: str = ""
    status: str = Field(default="active", index=True)
    version: int = 1
    embedding_provider: str = "local"
    embedding_model: str = "local-embed"
    chunk_size: int = 800
    chunk_overlap: int = 100
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))


class KnowledgeDocumentRow(SQLModel, table=True):
    __tablename__ = "cp_knowledge_document"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    project_id: str = Field(index=True)
    collection_id: str = Field(index=True)
    name: str = ""
    mime_type: str = "text/plain"
    size_bytes: int = 0
    checksum: str = Field(default="", index=True)
    content: str = ""
    version: int = 1
    status: str = Field(default="active", index=True)
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))


class KnowledgeChunkRow(SQLModel, table=True):
    """A knowledge chunk (M8.15). `embedding` holds the deterministic vector as a JSON list once
    generated. **No dedicated vector column type** — the vector store adapter searches over these
    persisted vectors (no external index required in this build)."""

    __tablename__ = "cp_knowledge_chunk"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    collection_id: str = Field(index=True)
    document_id: str = Field(index=True)
    idx: int = Field(default=0, index=True)
    text: str = ""
    checksum: str = ""
    embedding: list = Field(default_factory=list, sa_column=Column("embedding", SAJSON))
    embedding_status: str = "pending"
    index_status: str = Field(default="unindexed", index=True)
    created_at: float = 0.0
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))


class DeploymentExecutionRow(SQLModel, table=True):
    """A deployment execution (M8.16, ADR-0022). Durable System-of-Record for deployment execution
    state (one writer — the Actuator). Scalar columns support project/deployment queries; the full
    immutable aggregate (job + transitions + approvals + rollbacks + health + logs + result) lives
    a portable JSON `data` blob (mirrors the message-store/knowledge JSON-column precedent)."""

    __tablename__ = "cp_deployment_execution"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    project_id: str = Field(index=True)
    deployment_id: str = Field(default="", index=True)
    environment: str = ""
    state: str = Field(default="requested", index=True)
    attempts: int = 1
    requested_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    data: dict = Field(default_factory=dict, sa_column=Column("data", SAJSON))


class AlertRuleRow(SQLModel, table=True):
    """An Observability alert rule (M8.17) — the only durable Observability state (config). Metrics/
    logs/traces/health are projections of the audit stream and have no rows of their own."""

    __tablename__ = "cp_alert_rule"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    project_id: str | None = Field(default=None, index=True)
    name: str = ""
    metric_key: str = ""
    comparator: str = "gt"
    threshold: float = 0.0
    window_seconds: float = 3600.0
    severity: str = "warning"
    enabled: bool = True
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


class PreferencesRow(SQLModel, table=True):
    __tablename__ = "cp_user_preferences"

    # Composite identity: one preferences row per (user, org).
    user_id: str = Field(primary_key=True)
    org_id: str = Field(primary_key=True)
    theme: str = "dark"
    locale: str = "en"
    keyboard_shortcuts_enabled: bool = True
    favorites: list = Field(default_factory=list, sa_column=Column(SAJSON))
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))


# The persistence-layer table set (used by migrations + create_all).
CP_TABLES = (
    UserRow,
    OrganizationRow,
    WorkspaceRow,
    WorkspaceMembershipRow,
    MembershipRow,
    RoleBindingRow,
    EnvironmentRow,
    ProjectRow,
    ProjectMembershipRow,
    PreferencesRow,
    AuditEventRow,
    AgentRow,
    AgentVersionRow,
    ToolRow,
    AgentToolRow,
    ConversationSessionRow,
    ConversationMessageRow,
    KnowledgeCollectionRow,
    KnowledgeDocumentRow,
    KnowledgeChunkRow,
    DeploymentExecutionRow,
    AlertRuleRow,
)

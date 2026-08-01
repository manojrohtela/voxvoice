"""HTTP request/response models (M8.5) — Pydantic v2.

The wire contract, distinct from Application commands/queries and Foundation view DTOs (no
domain object is ever exposed directly). Request bodies map into Application commands;
responses are built from Application view DTOs. Foundation enums are reused for role/scope/
kind fields so validation + OpenAPI enums come for free. Pure data + `from_view`
constructors — no logic.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from api.foundation import (
    EnvironmentKind,
    OrgRole,
    ProjectRole,
    Role,
    RoleScope,
    ToolCategory,
    ToolVisibility,
    WsRole,
)

# ── requests ─────────────────────────────────────────────────────────────────────


class RegisterUserBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"email": "dev@acme.io", "display_name": "Dev"}}
    )
    email: str = Field(description="User email address")
    display_name: str = Field(default="", description="Human-readable name")


class TokenBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"user_id": "usr_abc"}})
    user_id: str = Field(description="Existing user id to mint a dev/test token for")
    org_id: str | None = Field(default=None, description="Optional org claim")


class CreateOrganizationBody(BaseModel):
    name: str
    plan: str = "free"
    region: str = "us"


class CreateWorkspaceBody(BaseModel):
    name: str
    slug: str = Field(description="url-safe slug (lowercase, hyphenated)")


class InviteMemberBody(BaseModel):
    user_id: str


class GrantRoleBody(BaseModel):
    role: Role
    scope: RoleScope
    scope_id: str = Field(description="org id (ORGANIZATION) or workspace id (WORKSPACE)")


class CreateProjectBody(BaseModel):
    name: str
    slug: str


class CreateEnvironmentBody(BaseModel):
    name: str
    kind: EnvironmentKind = EnvironmentKind.DEVELOPMENT


class UpdatePreferencesBody(BaseModel):
    theme: str = "dark"
    locale: str = "en"
    keyboard_shortcuts_enabled: bool = True
    favorites: tuple[str, ...] = ()


# ── responses ────────────────────────────────────────────────────────────────────


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class IdentityResponse(BaseModel):
    user_id: str
    org_id: str | None = None
    workspace_id: str | None = None


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str

    @classmethod
    def from_view(cls, v) -> UserResponse:
        return cls(id=v.id, email=v.email, display_name=v.display_name)


class OrganizationResponse(BaseModel):
    id: str
    name: str
    plan: str
    region: str
    status: str = "active"
    timezone: str = "UTC"
    language: str = "en"
    logo_url: str | None = None

    @classmethod
    def from_view(cls, v) -> OrganizationResponse:
        return cls(
            id=v.id, name=v.name, plan=v.plan, region=v.region,
            status=getattr(v, "status", "active"), timezone=getattr(v, "timezone", "UTC"),
            language=getattr(v, "language", "en"), logo_url=getattr(v, "logo_url", None),
        )


class UpdateOrganizationBody(BaseModel):
    name: str | None = None
    timezone: str | None = None
    language: str | None = None
    logo_url: str | None = None
    metadata: dict | None = None


class InviteBody(BaseModel):
    user_id: str
    tier: OrgRole = OrgRole.MEMBER
    ttl_seconds: float = 0.0


class InvitationResponse(BaseModel):
    membership_id: str
    org_id: str
    user_id: str
    status: str
    invited_role: str
    expires_at: float

    @classmethod
    def from_dict(cls, d: dict) -> InvitationResponse:
        return cls(**d)


class ActivityResponse(BaseModel):
    summary: dict
    feed: list[dict]
    # M8.9 — pagination metadata (additive; absent values default so old clients are fine).
    total: int = 0
    limit: int = 0
    offset: int = 0
    has_more: bool = False
    next_offset: int | None = None


class ActivityFeedResponse(BaseModel):
    """A summary-less activity feed page (recent / user activity projections, M8.9)."""

    feed: list[dict]
    total: int = 0
    limit: int = 0
    offset: int = 0
    has_more: bool = False
    next_offset: int | None = None


class WorkspaceResponse(BaseModel):
    id: str
    org_id: str
    name: str
    slug: str
    description: str = ""
    icon: str = ""
    color: str = ""
    status: str = "active"

    @classmethod
    def from_view(cls, v) -> WorkspaceResponse:
        return cls(
            id=v.id, org_id=v.org_id, name=v.name, slug=v.slug,
            description=getattr(v, "description", ""), icon=getattr(v, "icon", ""),
            color=getattr(v, "color", ""), status=getattr(v, "status", "active"),
        )

    @classmethod
    def from_dict(cls, d: dict) -> WorkspaceResponse:
        return cls(
            id=d["id"], org_id=d["org_id"], name=d["name"], slug=d["slug"],
            description=d.get("description", ""), icon=d.get("icon", ""),
            color=d.get("color", ""), status=d.get("status", "active"),
        )


class UpdateWorkspaceBody(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    metadata: dict | None = None


class AddWorkspaceMemberBody(BaseModel):
    user_id: str
    role: WsRole = WsRole.MEMBER


class WorkspaceMemberResponse(BaseModel):
    membership_id: str
    org_id: str
    workspace_id: str
    user_id: str
    role: str
    status: str

    @classmethod
    def from_view(cls, v) -> WorkspaceMemberResponse:
        return cls(
            membership_id=v.membership_id, org_id=v.org_id, workspace_id=v.workspace_id,
            user_id=v.user_id, role=v.role, status=v.status,
        )

    @classmethod
    def from_dict(cls, d: dict) -> WorkspaceMemberResponse:
        return cls(**d)


class WorkspaceActivityResponse(BaseModel):
    summary: dict
    feed: list[dict]
    total: int = 0
    limit: int = 0
    offset: int = 0
    has_more: bool = False
    next_offset: int | None = None


class WorkspaceNavigationResponse(BaseModel):
    org_id: str
    workspaces: list[dict]
    favorites: list[str]
    recent: list[str]
    default_workspace_id: str | None = None


class MemberResponse(BaseModel):
    membership_id: str
    org_id: str
    user_id: str
    status: str
    roles: tuple[str, ...] = ()

    @classmethod
    def from_view(cls, v) -> MemberResponse:
        return cls(
            membership_id=v.membership_id, org_id=v.org_id, user_id=v.user_id,
            status=v.status, roles=tuple(v.roles),
        )


class ProjectResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    slug: str
    org_id: str = ""
    description: str = ""
    icon: str = ""
    status: str = "active"

    @classmethod
    def from_view(cls, v) -> ProjectResponse:
        return cls(
            id=v.id, workspace_id=v.workspace_id, name=v.name, slug=v.slug,
            org_id=getattr(v, "org_id", ""), description=getattr(v, "description", ""),
            icon=getattr(v, "icon", ""), status=getattr(v, "status", "active"),
        )

    @classmethod
    def from_dict(cls, d: dict) -> ProjectResponse:
        return cls(
            id=d["id"], workspace_id=d["workspace_id"], name=d["name"], slug=d["slug"],
            org_id=d.get("org_id", ""), description=d.get("description", ""),
            icon=d.get("icon", ""), status=d.get("status", "active"),
        )


class UpdateProjectBody(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    metadata: dict | None = None


class AddProjectMemberBody(BaseModel):
    user_id: str
    role: ProjectRole = ProjectRole.MEMBER


class ProjectMemberResponse(BaseModel):
    membership_id: str
    org_id: str
    workspace_id: str
    project_id: str
    user_id: str
    role: str
    status: str

    @classmethod
    def from_view(cls, v) -> ProjectMemberResponse:
        return cls(
            membership_id=v.membership_id, org_id=v.org_id, workspace_id=v.workspace_id,
            project_id=v.project_id, user_id=v.user_id, role=v.role, status=v.status,
        )

    @classmethod
    def from_dict(cls, d: dict) -> ProjectMemberResponse:
        return cls(**d)


class ProjectActivityResponse(BaseModel):
    summary: dict
    feed: list[dict]
    total: int = 0
    limit: int = 0
    offset: int = 0
    has_more: bool = False
    next_offset: int | None = None


# ── agent builder (M8.10) ─────────────────────────────────────────────────────────


class CreateAgentBody(BaseModel):
    """Create an agent. `config` is the provider-independent nested config (prompt/model/
    voice/variables) — a free-form object so new providers/fields need no schema change."""

    model_config = ConfigDict(protected_namespaces=())
    name: str
    description: str = ""
    icon: str = ""
    category: str = ""
    tags: tuple[str, ...] = ()
    language: str = "en"
    greeting: str = ""
    config: dict | None = None


class UpdateAgentBody(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    category: str | None = None
    tags: tuple[str, ...] | None = None
    language: str | None = None
    greeting: str | None = None
    config_patch: dict | None = None
    metadata: dict | None = None


class DuplicateAgentBody(BaseModel):
    name: str | None = None


class PublishAgentBody(BaseModel):
    description: str = ""


class RollbackAgentBody(BaseModel):
    target_number: int = Field(description="the version number to roll back to")


class AgentResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    id: str
    org_id: str
    workspace_id: str
    project_id: str
    name: str
    status: str
    description: str = ""
    icon: str = ""
    category: str = ""
    tags: tuple[str, ...] = ()
    language: str = "en"
    greeting: str = ""
    version: int = 0
    published_version: int = 0
    created_by: str = ""
    config: dict = Field(default_factory=dict)

    @classmethod
    def from_view(cls, v) -> AgentResponse:
        return cls(
            id=v.id, org_id=v.org_id, workspace_id=v.workspace_id, project_id=v.project_id,
            name=v.name, status=v.status, description=v.description, icon=v.icon,
            category=v.category, tags=tuple(v.tags), language=v.language,
            greeting=v.greeting, version=v.version, published_version=v.published_version,
            created_by=v.created_by, config=v.config,
        )

    @classmethod
    def from_dict(cls, d: dict) -> AgentResponse:
        return cls(**d)


class AgentSummaryResponse(BaseModel):
    id: str
    name: str
    status: str
    category: str = ""
    icon: str = ""
    tags: tuple[str, ...] = ()
    version: int = 0
    published_version: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> AgentSummaryResponse:
        return cls(**d)


class AgentVersionResponse(BaseModel):
    id: str
    agent_id: str
    number: int
    status: str
    created_by: str
    created_at: float
    description: str = ""
    rollback_target: int | None = None
    snapshot: dict = Field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> AgentVersionResponse:
        return cls(**d)


class AgentVersionDiffResponse(BaseModel):
    agent_id: str
    from_version: int
    to_version: int
    changes: list[dict]


# ── tool framework (M8.11) ────────────────────────────────────────────────────────


class CreateToolBody(BaseModel):
    """Register a reusable tool. `config` is the provider-independent typed configuration
    (schemas/settings/timeout/retry/depends_on) — a free-form object, never an SDK object."""

    name: str
    category: ToolCategory = ToolCategory.CUSTOM
    description: str = ""
    icon: str = ""
    visibility: ToolVisibility = ToolVisibility.PROJECT
    config: dict | None = None


class UpdateToolBody(BaseModel):
    name: str | None = None
    category: ToolCategory | None = None
    description: str | None = None
    icon: str | None = None
    visibility: ToolVisibility | None = None
    config_patch: dict | None = None
    metadata: dict | None = None


class ToolResponse(BaseModel):
    id: str
    org_id: str
    workspace_id: str
    project_id: str
    name: str
    category: str
    status: str
    version: int
    enabled: bool
    visibility: str = "project"
    description: str = ""
    icon: str = ""
    created_by: str = ""
    config: dict = Field(default_factory=dict)

    @classmethod
    def from_view(cls, v) -> ToolResponse:
        return cls(
            id=v.id, org_id=v.org_id, workspace_id=v.workspace_id, project_id=v.project_id,
            name=v.name, category=v.category, status=v.status, version=v.version,
            enabled=v.enabled, visibility=v.visibility, description=v.description,
            icon=v.icon, created_by=v.created_by, config=v.config,
        )

    @classmethod
    def from_dict(cls, d: dict) -> ToolResponse:
        return cls(**d)


class ToolSummaryResponse(BaseModel):
    id: str
    name: str
    category: str
    status: str
    version: int
    enabled: bool
    icon: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> ToolSummaryResponse:
        return cls(**d)


class AgentToolResponse(BaseModel):
    id: str
    agent_id: str
    tool_id: str
    position: int
    enabled: bool
    config_override: dict = Field(default_factory=dict)

    @classmethod
    def from_view(cls, v) -> AgentToolResponse:
        return cls(
            id=v.id, agent_id=v.agent_id, tool_id=v.tool_id, position=v.position,
            enabled=v.enabled, config_override=v.config_override,
        )

    @classmethod
    def from_dict(cls, d: dict) -> AgentToolResponse:
        return cls(**d)


class AttachToolBody(BaseModel):
    tool_id: str
    position: int | None = None


class ConfigureAgentToolBody(BaseModel):
    config_override: dict = Field(default_factory=dict)


class ReorderToolsBody(BaseModel):
    ordered_tool_ids: list[str]


# ── conversation runtime (M8.12) ────────────────────────────────────────────────


class CreateSessionBody(BaseModel):
    """Create a runtime session for a published agent. `runtime_variables` are the highest-
    precedence variable tier; the runtime plans deterministically — no execution."""

    runtime_variables: dict | None = None
    correlation_id: str = ""
    metadata: dict | None = None


class SessionResponse(BaseModel):
    id: str
    org_id: str
    workspace_id: str
    project_id: str
    agent_id: str
    agent_version: int
    status: str
    correlation_id: str
    created_at: float
    updated_at: float
    metadata: dict = Field(default_factory=dict)
    state: dict = Field(default_factory=dict)

    @classmethod
    def from_view(cls, v) -> SessionResponse:
        return cls(
            id=v.id, org_id=v.org_id, workspace_id=v.workspace_id, project_id=v.project_id,
            agent_id=v.agent_id, agent_version=v.agent_version, status=v.status,
            correlation_id=v.correlation_id, created_at=v.created_at, updated_at=v.updated_at,
            metadata=v.metadata, state=v.state,
        )

    @classmethod
    def from_dict(cls, d: dict) -> SessionResponse:
        return cls(**d)


class SessionSummaryResponse(BaseModel):
    id: str
    agent_id: str
    agent_version: int
    status: str
    created_at: float
    updated_at: float

    @classmethod
    def from_dict(cls, d: dict) -> SessionSummaryResponse:
        return cls(**d)


# ── conversation playground (M8.13) ─────────────────────────────────────────────


class StartConversationBody(BaseModel):
    """Start a playground conversation for a published agent (mock runtime only)."""

    runtime_variables: dict | None = None
    metadata: dict | None = None


class ContinueTurnBody(BaseModel):
    user_input: str = ""


class TurnResponse(BaseModel):
    index: int
    user_input: str
    llm_output: dict = Field(default_factory=dict)
    tool_invocations: list[dict] = Field(default_factory=list)
    voice_output: dict | None = None
    token_estimate: int = 0
    timing: dict = Field(default_factory=dict)
    diagnostics: list[dict] = Field(default_factory=list)

    @classmethod
    def from_view(cls, v) -> TurnResponse:
        return cls(
            index=v.index, user_input=v.user_input, llm_output=v.llm_output,
            tool_invocations=list(v.tool_invocations), voice_output=v.voice_output,
            token_estimate=v.token_estimate, timing=v.timing,
            diagnostics=list(v.diagnostics),
        )

    @classmethod
    def from_dict(cls, d: dict) -> TurnResponse:
        return cls(**d)


class ConversationResponse(BaseModel):
    session_id: str
    agent_id: str
    agent_version: int
    status: str
    turns: list[dict] = Field(default_factory=list)
    messages: list[dict] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> ConversationResponse:
        return cls(**d)


# ── provider adapter binding / real execution (M8.14) ───────────────────────────


class ExecuteTurnBody(BaseModel):
    user_input: str = ""


class ExecutionResultResponse(BaseModel):
    assistant_response: str = ""
    messages: list[dict] = Field(default_factory=list)
    tool_results: list[dict] = Field(default_factory=list)
    provider_metadata: dict = Field(default_factory=dict)
    token_usage: dict = Field(default_factory=dict)
    latency_ms: int = 0
    errors: list[dict] = Field(default_factory=list)
    diagnostics: list[dict] = Field(default_factory=list)
    execution_metadata: dict = Field(default_factory=dict)
    stream: list[dict] = Field(default_factory=list)

    @classmethod
    def from_view(cls, v) -> ExecutionResultResponse:
        return cls(
            assistant_response=v.assistant_response, messages=list(v.messages),
            tool_results=list(v.tool_results), provider_metadata=v.provider_metadata,
            token_usage=v.token_usage, latency_ms=v.latency_ms, errors=list(v.errors),
            diagnostics=list(v.diagnostics), execution_metadata=v.execution_metadata,
            stream=list(v.stream),
        )


class MessageResponse(BaseModel):
    id: str
    seq: int
    role: str
    content: str
    metadata: dict = Field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> MessageResponse:
        return cls(**d)


# ── knowledge tool (M8.15) ───────────────────────────────────────────────────────


class CreateKnowledgeCollectionBody(BaseModel):
    name: str
    base: str = "default"
    description: str = ""
    embedding_provider: str = "local"
    embedding_model: str = "local-embed"
    chunk_size: int = 800
    chunk_overlap: int = 100


class UpdateKnowledgeCollectionBody(BaseModel):
    changes: dict = Field(default_factory=dict)


class UploadDocumentBody(BaseModel):
    name: str
    content: str
    mime_type: str = "text/plain"
    metadata: dict | None = None


class ReplaceDocumentBody(BaseModel):
    content: str
    mime_type: str | None = None


class SearchKnowledgeBody(BaseModel):
    query: str
    top_k: int = 5
    threshold: float = 0.0
    filters: dict | None = None


# ── deployment actuator (M8.16) ──────────────────────────────────────────────────


class PlanDeploymentBody(BaseModel):
    """Request a read-only Deployment Plan for a published agent (no execution)."""

    agent_id: str
    environment: str = "development"
    strategy: str = "rolling"
    requires_approval: bool = False


class SubmitDeploymentBody(BaseModel):
    """Submit a deployment for a published agent. Production is approval-gated automatically;
    `auto_approve` runs the whole Local path in one call (dev/test convenience)."""

    agent_id: str
    environment: str = "development"
    strategy: str = "rolling"
    requires_approval: bool = False
    auto_approve: bool = False


class DeploymentActionBody(BaseModel):
    reason: str = ""


# ── observability (M8.17) ────────────────────────────────────────────────────────


class CreateAlertRuleBody(BaseModel):
    name: str
    metric_key: str
    comparator: str = "gt"
    threshold: float = 0.0
    window_seconds: float = 3600.0
    severity: str = "warning"
    project_id: str | None = None


class UpdateAlertRuleBody(BaseModel):
    changes: dict = Field(default_factory=dict)


class KnowledgeCollectionResponse(BaseModel):
    id: str
    org_id: str
    workspace_id: str
    project_id: str
    name: str
    base: str
    status: str
    version: int
    embedding_provider: str
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> KnowledgeCollectionResponse:
        return cls(**d)


class KnowledgeDocumentResponse(BaseModel):
    id: str
    collection_id: str
    name: str
    mime_type: str
    size_bytes: int
    checksum: str
    version: int
    status: str

    @classmethod
    def from_dict(cls, d: dict) -> KnowledgeDocumentResponse:
        return cls(**d)


class KnowledgeChunkResponse(BaseModel):
    id: str
    document_id: str
    index: int
    text: str
    checksum: str
    embedding_status: str
    index_status: str
    dimensions: int
    metadata: dict = Field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> KnowledgeChunkResponse:
        return cls(**d)


class SwitchEnvironmentBody(BaseModel):
    environment: EnvironmentKind


class ProjectEnvironmentResponse(BaseModel):
    project_id: str
    environments: list[str]
    current: str


class EnvironmentResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    kind: str

    @classmethod
    def from_view(cls, v) -> EnvironmentResponse:
        return cls(id=v.id, workspace_id=v.workspace_id, name=v.name, kind=v.kind)


class PreferencesResponse(BaseModel):
    user_id: str
    org_id: str
    theme: str
    locale: str
    keyboard_shortcuts_enabled: bool
    favorites: tuple[str, ...]

    @classmethod
    def from_view(cls, v) -> PreferencesResponse:
        return cls(
            user_id=v.user_id, org_id=v.org_id, theme=v.theme, locale=v.locale,
            keyboard_shortcuts_enabled=v.keyboard_shortcuts_enabled,
            favorites=tuple(v.favorites),
        )


class ErrorBody(BaseModel):
    type: str
    message: str
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: str
    environment: str


class ReadyResponse(BaseModel):
    ready: bool
    checks: dict[str, bool]


class VersionResponse(BaseModel):
    api: str
    title: str


# Reusable OpenAPI error responses.
ERROR_RESPONSES: dict = {
    400: {"model": ErrorResponse, "description": "Bad request / no handler"},
    401: {"model": ErrorResponse, "description": "Unauthenticated"},
    403: {"model": ErrorResponse, "description": "Forbidden (authorization / isolation)"},
    404: {"model": ErrorResponse, "description": "Not found"},
    409: {"model": ErrorResponse, "description": "Conflict"},
    422: {"model": ErrorResponse, "description": "Validation error"},
}

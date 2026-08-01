"""Data-transfer objects (M8.2).

Immutable request/view shapes decoupled from the domain aggregates, so a future
transport layer (the FastAPI Control Plane routers, a later milestone) maps
HTTP↔DTO↔domain without leaking persistence or domain internals across the boundary
(Constitution §A11.4 — versioned interface surfaces). No FastAPI/Pydantic here: these
are plain frozen dataclasses the domain layer stays independent of.

Request DTOs carry *intent* (what to create/change); view DTOs are read projections
returned to the Consumer surface (ADR-0021 — Studio renders projections, owns nothing).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .agent_domain import Agent, AgentVersion
from .domain import (
    Environment,
    EnvironmentKind,
    Membership,
    Organization,
    Project,
    ProjectMembership,
    Role,
    RoleBinding,
    RoleScope,
    User,
    UserPreferences,
    Workspace,
    WorkspaceMembership,
)
from .playground_domain import ConversationTurn
from .runtime_domain import ConversationSession
from .tool_domain import AgentTool, Tool

# ── request DTOs (intent) ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RegisterUserRequest:
    email: str
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class CreateOrganizationRequest:
    name: str
    owner_user_id: str
    plan: str = "free"
    region: str = "us"


@dataclass(frozen=True, slots=True)
class CreateWorkspaceRequest:
    org_id: str
    name: str
    slug: str


@dataclass(frozen=True, slots=True)
class InviteMemberRequest:
    org_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class GrantRoleRequest:
    org_id: str
    user_id: str
    role: Role
    scope: RoleScope
    scope_id: str


@dataclass(frozen=True, slots=True)
class CreateEnvironmentRequest:
    org_id: str
    workspace_id: str
    name: str
    kind: EnvironmentKind = EnvironmentKind.DEVELOPMENT


@dataclass(frozen=True, slots=True)
class CreateProjectRequest:
    org_id: str
    workspace_id: str
    name: str
    slug: str


@dataclass(frozen=True, slots=True)
class UpdatePreferencesRequest:
    user_id: str
    org_id: str
    theme: str = "dark"
    locale: str = "en"
    keyboard_shortcuts_enabled: bool = True
    favorites: tuple[str, ...] = ()


# ── view DTOs (read projections) ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OrganizationView:
    id: str
    name: str
    plan: str
    region: str
    # M8.6 — settings + lifecycle status (additive, defaulted).
    status: str = "active"
    timezone: str = "UTC"
    language: str = "en"
    logo_url: str | None = None

    @classmethod
    def of(cls, org: Organization) -> OrganizationView:
        return cls(
            id=org.id, name=org.name, plan=org.plan, region=org.region,
            status=org.status.value, timezone=org.timezone, language=org.language,
            logo_url=org.logo_url,
        )


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    id: str
    org_id: str
    name: str
    slug: str
    # M8.7 — settings + lifecycle status (additive, defaulted).
    description: str = ""
    icon: str = ""
    color: str = ""
    status: str = "active"

    @classmethod
    def of(cls, ws: Workspace) -> WorkspaceView:
        return cls(
            id=ws.id, org_id=ws.org_id, name=ws.name, slug=ws.slug,
            description=ws.description, icon=ws.icon, color=ws.color,
            status=ws.status.value,
        )


@dataclass(frozen=True, slots=True)
class WorkspaceMemberView:
    """A workspace membership projection (M8.7)."""

    membership_id: str
    org_id: str
    workspace_id: str
    user_id: str
    role: str
    status: str

    @classmethod
    def of(cls, wm: WorkspaceMembership) -> WorkspaceMemberView:
        return cls(
            membership_id=wm.id, org_id=wm.org_id, workspace_id=wm.workspace_id,
            user_id=wm.user_id, role=wm.role, status=wm.status.value,
        )


@dataclass(frozen=True, slots=True)
class MemberView:
    membership_id: str
    org_id: str
    user_id: str
    status: str
    roles: tuple[str, ...] = ()

    @classmethod
    def of(cls, m: Membership, bindings: tuple[RoleBinding, ...] = ()) -> MemberView:
        return cls(
            membership_id=m.id,
            org_id=m.org_id,
            user_id=m.user_id,
            status=m.status.value,
            roles=tuple(b.role.value for b in bindings),
        )


@dataclass(frozen=True, slots=True)
class ProjectView:
    id: str
    workspace_id: str
    name: str
    slug: str
    # M8.8 — settings + lifecycle status (additive, defaulted).
    org_id: str = ""
    description: str = ""
    icon: str = ""
    status: str = "active"

    @classmethod
    def of(cls, p: Project) -> ProjectView:
        return cls(
            id=p.id, workspace_id=p.workspace_id, name=p.name, slug=p.slug,
            org_id=p.org_id, description=p.description, icon=p.icon, status=p.status.value,
        )


@dataclass(frozen=True, slots=True)
class ProjectMemberView:
    """A project membership projection (M8.8)."""

    membership_id: str
    org_id: str
    workspace_id: str
    project_id: str
    user_id: str
    role: str
    status: str

    @classmethod
    def of(cls, pm: ProjectMembership) -> ProjectMemberView:
        return cls(
            membership_id=pm.id, org_id=pm.org_id, workspace_id=pm.workspace_id,
            project_id=pm.project_id, user_id=pm.user_id, role=pm.role,
            status=pm.status.value,
        )


@dataclass(frozen=True, slots=True)
class EnvironmentView:
    id: str
    workspace_id: str
    name: str
    kind: str

    @classmethod
    def of(cls, e: Environment) -> EnvironmentView:
        return cls(id=e.id, workspace_id=e.workspace_id, name=e.name, kind=e.kind.value)


@dataclass(frozen=True, slots=True)
class AgentView:
    """A full agent projection (M8.10). `config` is the nested config dict with **secret
    variable values redacted** (never emitted by any API)."""

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
    config: dict = field(default_factory=dict)

    @classmethod
    def of(cls, a: Agent) -> AgentView:
        return cls(
            id=a.id, org_id=a.org_id, workspace_id=a.workspace_id, project_id=a.project_id,
            name=a.name, status=a.status.value, description=a.description, icon=a.icon,
            category=a.category, tags=a.tags, language=a.language, greeting=a.greeting,
            version=a.version, published_version=a.published_version, created_by=a.created_by,
            config=a.config.as_dict(redact_secrets=True),
        )


@dataclass(frozen=True, slots=True)
class AgentSummaryView:
    """A compact agent list item (no config body)."""

    id: str
    name: str
    status: str
    category: str = ""
    icon: str = ""
    tags: tuple[str, ...] = ()
    version: int = 0
    published_version: int = 0

    @classmethod
    def of(cls, a: Agent) -> AgentSummaryView:
        return cls(
            id=a.id, name=a.name, status=a.status.value, category=a.category, icon=a.icon,
            tags=a.tags, version=a.version, published_version=a.published_version,
        )


@dataclass(frozen=True, slots=True)
class AgentVersionView:
    """An immutable agent version projection (M8.10). The snapshot's config is re-emitted
    with secret values redacted."""

    id: str
    agent_id: str
    number: int
    status: str
    created_by: str
    created_at: float
    description: str = ""
    rollback_target: int | None = None
    snapshot: dict = field(default_factory=dict)

    @classmethod
    def of(cls, v: AgentVersion) -> AgentVersionView:
        return cls(
            id=v.id, agent_id=v.agent_id, number=v.number, status=v.status.value,
            created_by=v.created_by, created_at=v.created_at, description=v.description,
            rollback_target=v.rollback_target, snapshot=_redacted_snapshot(v.snapshot),
        )


def _redacted_snapshot(snapshot: dict) -> dict:
    """Re-emit a version snapshot with secret variable values stripped (defence in depth —
    the snapshot stores them for fidelity; no read boundary exposes them)."""
    from .agent_domain import AgentConfig

    out = dict(snapshot)
    if "config" in out:
        out["config"] = AgentConfig.from_dict(out["config"]).as_dict(redact_secrets=True)
    return out


@dataclass(frozen=True, slots=True)
class ToolView:
    """A full tool projection (M8.11). `config` is the typed configuration dict."""

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
    config: dict = field(default_factory=dict)

    @classmethod
    def of(cls, t: Tool) -> ToolView:
        return cls(
            id=t.id, org_id=t.org_id, workspace_id=t.workspace_id, project_id=t.project_id,
            name=t.name, category=t.category.value, status=t.status.value, version=t.version,
            enabled=t.enabled, visibility=t.visibility.value, description=t.description,
            icon=t.icon, created_by=t.created_by, config=t.config.as_dict(),
        )


@dataclass(frozen=True, slots=True)
class ToolSummaryView:
    """A compact tool catalog/library item (no config body)."""

    id: str
    name: str
    category: str
    status: str
    version: int
    enabled: bool
    icon: str = ""

    @classmethod
    def of(cls, t: Tool) -> ToolSummaryView:
        return cls(
            id=t.id, name=t.name, category=t.category.value, status=t.status.value,
            version=t.version, enabled=t.enabled, icon=t.icon,
        )


@dataclass(frozen=True, slots=True)
class AgentToolView:
    """An agent↔tool attachment projection (M8.11)."""

    id: str
    agent_id: str
    tool_id: str
    position: int
    enabled: bool
    config_override: dict = field(default_factory=dict)

    @classmethod
    def of(cls, a: AgentTool) -> AgentToolView:
        return cls(
            id=a.id, agent_id=a.agent_id, tool_id=a.tool_id, position=a.position,
            enabled=a.enabled, config_override=dict(a.config_override),
        )


@dataclass(frozen=True, slots=True)
class UserView:
    id: str
    email: str
    display_name: str

    @classmethod
    def of(cls, u: User) -> UserView:
        return cls(id=u.id, email=u.email, display_name=u.display_name)


@dataclass(frozen=True, slots=True)
class PreferencesView:
    user_id: str
    org_id: str
    theme: str
    locale: str
    keyboard_shortcuts_enabled: bool
    favorites: tuple[str, ...]

    @classmethod
    def of(cls, p: UserPreferences) -> PreferencesView:
        return cls(
            user_id=p.user_id,
            org_id=p.org_id,
            theme=p.theme,
            locale=p.locale,
            keyboard_shortcuts_enabled=p.keyboard_shortcuts_enabled,
            favorites=p.favorites,
        )


@dataclass(frozen=True, slots=True)
class SessionView:
    """A full conversation-session projection (M8.12). `state` is the resolved runtime state
    with **secret variable values redacted** (never emitted by any API)."""

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
    metadata: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)

    @classmethod
    def of(cls, s: ConversationSession) -> SessionView:
        return cls(
            id=s.id, org_id=s.org_id, workspace_id=s.workspace_id, project_id=s.project_id,
            agent_id=s.agent_id, agent_version=s.agent_version, status=s.status.value,
            correlation_id=s.correlation_id, created_at=s.created_at, updated_at=s.updated_at,
            metadata=dict(s.metadata), state=s.state.as_dict(redact_secrets=True),
        )


@dataclass(frozen=True, slots=True)
class SessionSummaryView:
    """A compact session list item (no resolved state body)."""

    id: str
    agent_id: str
    agent_version: int
    status: str
    created_at: float
    updated_at: float

    @classmethod
    def of(cls, s: ConversationSession) -> SessionSummaryView:
        return cls(
            id=s.id, agent_id=s.agent_id, agent_version=s.agent_version, status=s.status.value,
            created_at=s.created_at, updated_at=s.updated_at,
        )


@dataclass(frozen=True, slots=True)
class TurnView:
    """One playground turn projection (M8.13) — deterministic mock adapter outputs."""

    index: int
    user_input: str
    llm_output: dict = field(default_factory=dict)
    tool_invocations: tuple[dict, ...] = ()
    voice_output: dict | None = None
    token_estimate: int = 0
    timing: dict = field(default_factory=dict)
    diagnostics: tuple[dict, ...] = ()

    @classmethod
    def of(cls, t: ConversationTurn) -> TurnView:
        return cls(
            index=t.index, user_input=t.user_input, llm_output=t.llm_output,
            tool_invocations=tuple(t.tool_invocations), voice_output=t.voice_output,
            token_estimate=t.token_estimate, timing=t.timing,
            diagnostics=tuple(t.diagnostics),
        )


@dataclass(frozen=True, slots=True)
class ConversationView:
    """A playground conversation projection (M8.13): the session + its turns + derived chat
    messages. Secret variable values in the session state are redacted."""

    session_id: str
    agent_id: str
    agent_version: int
    status: str
    turns: tuple[dict, ...] = ()
    messages: tuple[dict, ...] = ()

    @classmethod
    def of(cls, s: ConversationSession) -> ConversationView:
        turns = [ConversationTurn.from_dict(t) for t in s.state.history]
        messages: list[dict] = []
        for t in turns:
            messages.extend(t.messages())
        return cls(
            session_id=s.id, agent_id=s.agent_id, agent_version=s.agent_version,
            status=s.status.value, turns=tuple(t.as_dict() for t in turns),
            messages=tuple(messages),
        )


@dataclass(frozen=True, slots=True)
class MessageView:
    """One stored conversation message projection (M8.14 memory store)."""

    id: str
    seq: int
    role: str
    content: str
    metadata: dict = field(default_factory=dict)

    @classmethod
    def of(cls, m) -> MessageView:
        return cls(id=m.id, seq=m.seq, role=m.role, content=m.content, metadata=m.metadata)


@dataclass(frozen=True, slots=True)
class ExecutionResultView:
    """The result of a real (or mock) provider execution turn (M8.14)."""

    assistant_response: str = ""
    messages: tuple[dict, ...] = ()
    tool_results: tuple[dict, ...] = ()
    provider_metadata: dict = field(default_factory=dict)
    token_usage: dict = field(default_factory=dict)
    latency_ms: int = 0
    errors: tuple[dict, ...] = ()
    diagnostics: tuple[dict, ...] = ()
    execution_metadata: dict = field(default_factory=dict)
    stream: tuple[dict, ...] = ()

    @classmethod
    def of(cls, r) -> ExecutionResultView:
        d = r.as_dict()
        return cls(
            assistant_response=d["assistant_response"], messages=tuple(d["messages"]),
            tool_results=tuple(d["tool_results"]), provider_metadata=d["provider_metadata"],
            token_usage=d["token_usage"], latency_ms=d["latency_ms"], errors=tuple(d["errors"]),
            diagnostics=tuple(d["diagnostics"]), execution_metadata=d["execution_metadata"],
            stream=tuple(d["stream"]),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeCollectionView:
    """A knowledge collection projection (M8.15)."""

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
    def of(cls, c) -> KnowledgeCollectionView:
        return cls(
            id=c.id, org_id=c.org_id, workspace_id=c.workspace_id, project_id=c.project_id,
            name=c.name, base=c.base, status=c.status.value, version=c.version,
            embedding_provider=c.embedding_provider, embedding_model=c.embedding_model,
            chunk_size=c.chunk_size, chunk_overlap=c.chunk_overlap, description=c.description,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentView:
    """A knowledge document projection (M8.15) — content omitted from list views."""

    id: str
    collection_id: str
    name: str
    mime_type: str
    size_bytes: int
    checksum: str
    version: int
    status: str

    @classmethod
    def of(cls, d) -> KnowledgeDocumentView:
        return cls(
            id=d.id, collection_id=d.collection_id, name=d.name, mime_type=d.mime_type,
            size_bytes=d.size_bytes, checksum=d.checksum, version=d.version, status=d.status.value,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeChunkView:
    """A knowledge chunk projection (M8.15) — embedding vector not exposed, only its status."""

    id: str
    document_id: str
    index: int
    text: str
    checksum: str
    embedding_status: str
    index_status: str
    dimensions: int
    metadata: dict = field(default_factory=dict)

    @classmethod
    def of(cls, c) -> KnowledgeChunkView:
        v = c.as_view()
        return cls(
            id=v["id"], document_id=v["document_id"], index=v["index"], text=v["text"],
            checksum=v["checksum"], embedding_status=v["embedding_status"],
            index_status=v["index_status"], dimensions=v["dimensions"], metadata=v["metadata"],
        )

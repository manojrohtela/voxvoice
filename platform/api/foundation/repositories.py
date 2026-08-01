"""Repository interfaces + deterministic in-memory implementations (M8.2).

The persistence seam. Interfaces (ABCs) let a real System-of-Record backing land later
without touching services (dependency inversion; Constitution §D6/§A7 — one System of
Record, behind an interface). The in-memory implementations are first-class, fully
deterministic (insertion-ordered iteration, id-keyed lookup), and are the default the
factory wires — the same "local, in-process, deterministic default" pattern Epic 5 used
for providers.

**Organization isolation is enforced here, structurally (§A11.2):** every org-owned
lookup takes an `org_id` and filters on it; an aggregate whose `org_id` differs is never
returned. A caller cannot read across tenants because the query shape forbids it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .agent_domain import Agent, AgentStatus, AgentVersion
from .domain import (
    Environment,
    Membership,
    Organization,
    Project,
    ProjectMembership,
    RoleBinding,
    User,
    UserPreferences,
    Workspace,
    WorkspaceMembership,
)
from .execution_domain import ConversationMessage
from .knowledge_domain import (
    DocumentStatus,
    KnowledgeChunk,
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeStatus,
)
from .observability_domain import AlertRule
from .runtime_domain import ConversationSession
from .tool_domain import AgentTool, Tool, ToolStatus

# ── interfaces ──────────────────────────────────────────────────────────────────


class UserRepository(ABC):
    @abstractmethod
    def add(self, user: User) -> User: ...
    @abstractmethod
    def get(self, user_id: str) -> User | None: ...
    @abstractmethod
    def by_email(self, email: str) -> User | None: ...


class OrganizationRepository(ABC):
    @abstractmethod
    def add(self, org: Organization) -> Organization: ...
    @abstractmethod
    def get(self, org_id: str) -> Organization | None: ...
    @abstractmethod
    def update(self, org: Organization) -> Organization:
        """Persist an immutable partial-update of an existing organization (M8.6)."""


class WorkspaceRepository(ABC):
    @abstractmethod
    def add(self, workspace: Workspace) -> Workspace: ...
    @abstractmethod
    def get(self, org_id: str, workspace_id: str) -> Workspace | None: ...
    @abstractmethod
    def by_slug(self, org_id: str, slug: str) -> Workspace | None: ...
    @abstractmethod
    def list(self, org_id: str) -> tuple[Workspace, ...]: ...
    @abstractmethod
    def update(self, workspace: Workspace) -> Workspace:
        """Persist an immutable partial-update of an existing workspace (M8.7)."""


class WorkspaceMembershipRepository(ABC):
    """Workspace-level membership (M8.7) — org-and-workspace scoped by construction."""

    @abstractmethod
    def add(self, membership: WorkspaceMembership) -> WorkspaceMembership: ...
    @abstractmethod
    def get(
        self, org_id: str, workspace_id: str, user_id: str
    ) -> WorkspaceMembership | None: ...
    @abstractmethod
    def update(self, membership: WorkspaceMembership) -> WorkspaceMembership: ...
    @abstractmethod
    def list(self, org_id: str, workspace_id: str) -> tuple[WorkspaceMembership, ...]: ...
    @abstractmethod
    def list_for_user(
        self, org_id: str, user_id: str
    ) -> tuple[WorkspaceMembership, ...]: ...


class MembershipRepository(ABC):
    @abstractmethod
    def add(self, membership: Membership) -> Membership: ...
    @abstractmethod
    def get(self, org_id: str, user_id: str) -> Membership | None: ...
    @abstractmethod
    def update(self, membership: Membership) -> Membership: ...
    @abstractmethod
    def list(self, org_id: str) -> tuple[Membership, ...]: ...


class RoleBindingRepository(ABC):
    @abstractmethod
    def add(self, binding: RoleBinding) -> RoleBinding: ...
    @abstractmethod
    def remove(self, org_id: str, binding_id: str) -> None: ...
    @abstractmethod
    def for_user(self, org_id: str, user_id: str) -> tuple[RoleBinding, ...]: ...
    @abstractmethod
    def list(self, org_id: str) -> tuple[RoleBinding, ...]: ...


class EnvironmentRepository(ABC):
    @abstractmethod
    def add(self, environment: Environment) -> Environment: ...
    @abstractmethod
    def list(self, org_id: str, workspace_id: str) -> tuple[Environment, ...]: ...


class ProjectRepository(ABC):
    @abstractmethod
    def add(self, project: Project) -> Project: ...
    @abstractmethod
    def by_slug(self, org_id: str, workspace_id: str, slug: str) -> Project | None: ...
    @abstractmethod
    def list(self, org_id: str, workspace_id: str) -> tuple[Project, ...]: ...
    @abstractmethod
    def get(self, org_id: str, workspace_id: str, project_id: str) -> Project | None:
        """Fetch one project by id, org+workspace scoped (M8.8)."""
    @abstractmethod
    def update(self, project: Project) -> Project:
        """Persist an immutable partial-update of an existing project (M8.8)."""


class ProjectMembershipRepository(ABC):
    """Project-level membership (M8.8) — org/workspace/project scoped by construction."""

    @abstractmethod
    def add(self, membership: ProjectMembership) -> ProjectMembership: ...
    @abstractmethod
    def get(
        self, org_id: str, project_id: str, user_id: str
    ) -> ProjectMembership | None: ...
    @abstractmethod
    def update(self, membership: ProjectMembership) -> ProjectMembership: ...
    @abstractmethod
    def list(self, org_id: str, project_id: str) -> tuple[ProjectMembership, ...]: ...


class AgentRepository(ABC):
    """Agent-builder config objects (M8.10) — org/workspace/project scoped by construction."""

    @abstractmethod
    def add(self, agent: Agent) -> Agent: ...
    @abstractmethod
    def get(self, org_id: str, project_id: str, agent_id: str) -> Agent | None: ...
    @abstractmethod
    def by_name(self, org_id: str, project_id: str, name: str) -> Agent | None: ...
    @abstractmethod
    def list(self, org_id: str, project_id: str) -> tuple[Agent, ...]: ...
    @abstractmethod
    def update(self, agent: Agent) -> Agent:
        """Persist an immutable partial-update of an existing agent (M8.10)."""


class AgentVersionRepository(ABC):
    """The immutable, append-only agent version store (M8.10). Versions are created on
    publish and never updated (except a single supersede-status flip) or deleted."""

    @abstractmethod
    def add(self, version: AgentVersion) -> AgentVersion: ...
    @abstractmethod
    def get(self, org_id: str, agent_id: str, number: int) -> AgentVersion | None: ...
    @abstractmethod
    def list(self, org_id: str, agent_id: str) -> tuple[AgentVersion, ...]: ...
    @abstractmethod
    def update_status(self, version: AgentVersion) -> AgentVersion:
        """Flip a version's status to SUPERSEDED (the only mutation allowed on a version)."""


class ToolRepository(ABC):
    """Reusable tool declarations (M8.11) — org/workspace/project scoped by construction."""

    @abstractmethod
    def add(self, tool: Tool) -> Tool: ...
    @abstractmethod
    def get(self, org_id: str, project_id: str, tool_id: str) -> Tool | None: ...
    @abstractmethod
    def by_name(self, org_id: str, project_id: str, name: str) -> Tool | None: ...
    @abstractmethod
    def list(self, org_id: str, project_id: str) -> tuple[Tool, ...]: ...
    @abstractmethod
    def update(self, tool: Tool) -> Tool:
        """Persist an immutable partial-update of an existing tool (M8.11)."""


class AgentToolRepository(ABC):
    """Agent↔tool attachments (M8.11) — org/workspace/project/agent scoped by construction."""

    @abstractmethod
    def add(self, attachment: AgentTool) -> AgentTool: ...
    @abstractmethod
    def get(self, org_id: str, agent_id: str, tool_id: str) -> AgentTool | None: ...
    @abstractmethod
    def list(self, org_id: str, agent_id: str) -> tuple[AgentTool, ...]: ...
    @abstractmethod
    def update(self, attachment: AgentTool) -> AgentTool: ...
    @abstractmethod
    def remove(self, org_id: str, agent_id: str, tool_id: str) -> None: ...


class ConversationSessionRepository(ABC):
    """Runtime conversation sessions (M8.12) — org/workspace/project/agent scoped by
    construction. Persists session + explicit conversation state (no message history)."""

    @abstractmethod
    def add(self, session: ConversationSession) -> ConversationSession: ...
    @abstractmethod
    def get(self, org_id: str, session_id: str) -> ConversationSession | None: ...
    @abstractmethod
    def list(self, org_id: str, agent_id: str) -> tuple[ConversationSession, ...]: ...
    @abstractmethod
    def update(self, session: ConversationSession) -> ConversationSession:
        """Persist an immutable partial-update of an existing session (M8.12)."""


class ConversationMessageRepository(ABC):
    """The durable message store (M8.14) — append-only per session, ordered by `seq`. No
    vectors, no embeddings; a plain conversation message store."""

    @abstractmethod
    def add(self, message: ConversationMessage) -> ConversationMessage: ...
    @abstractmethod
    def list(self, org_id: str, session_id: str) -> tuple[ConversationMessage, ...]: ...
    @abstractmethod
    def count(self, org_id: str, session_id: str) -> int: ...
    @abstractmethod
    def delete_for_session(self, org_id: str, session_id: str) -> None:
        """Remove a session's messages (used by conversation reset)."""


class KnowledgeCollectionRepository(ABC):
    """Knowledge collections (M8.15) — org/workspace/project scoped by construction."""

    @abstractmethod
    def add(self, collection: KnowledgeCollection) -> KnowledgeCollection: ...
    @abstractmethod
    def get(
        self, org_id: str, project_id: str, collection_id: str
    ) -> KnowledgeCollection | None: ...
    @abstractmethod
    def by_name(
        self, org_id: str, project_id: str, name: str
    ) -> KnowledgeCollection | None: ...
    @abstractmethod
    def list(self, org_id: str, project_id: str) -> tuple[KnowledgeCollection, ...]: ...
    @abstractmethod
    def update(self, collection: KnowledgeCollection) -> KnowledgeCollection: ...


class KnowledgeDocumentRepository(ABC):
    """Knowledge documents (M8.15) — org/collection scoped by construction."""

    @abstractmethod
    def add(self, document: KnowledgeDocument) -> KnowledgeDocument: ...
    @abstractmethod
    def get(
        self, org_id: str, collection_id: str, document_id: str
    ) -> KnowledgeDocument | None: ...
    @abstractmethod
    def by_checksum(
        self, org_id: str, collection_id: str, checksum: str
    ) -> KnowledgeDocument | None: ...
    @abstractmethod
    def list(self, org_id: str, collection_id: str) -> tuple[KnowledgeDocument, ...]: ...
    @abstractmethod
    def update(self, document: KnowledgeDocument) -> KnowledgeDocument: ...


class KnowledgeChunkRepository(ABC):
    """Knowledge chunks (M8.15) — append/replace per document; ordered by index. Carries the
    (deterministic) embedding vector once generated."""

    @abstractmethod
    def add(self, chunk: KnowledgeChunk) -> KnowledgeChunk: ...
    @abstractmethod
    def update(self, chunk: KnowledgeChunk) -> KnowledgeChunk: ...
    @abstractmethod
    def get(self, org_id: str, chunk_id: str) -> KnowledgeChunk | None: ...
    @abstractmethod
    def list_for_document(self, org_id: str, document_id: str) -> tuple[KnowledgeChunk, ...]: ...
    @abstractmethod
    def list_for_collection(
        self, org_id: str, collection_id: str
    ) -> tuple[KnowledgeChunk, ...]: ...
    @abstractmethod
    def delete_for_document(self, org_id: str, document_id: str) -> None: ...


class AlertRuleRepository(ABC):
    """Observability alert rules (M8.17) — the only durable Observability state (config). Org-scoped
    by construction; metrics/logs/traces/health hold no state (they project the audit stream)."""

    @abstractmethod
    def add(self, rule: AlertRule) -> AlertRule: ...
    @abstractmethod
    def get(self, org_id: str, rule_id: str) -> AlertRule | None: ...
    @abstractmethod
    def list(self, org_id: str) -> tuple[AlertRule, ...]: ...
    @abstractmethod
    def update(self, rule: AlertRule) -> AlertRule: ...
    @abstractmethod
    def delete(self, org_id: str, rule_id: str) -> None: ...


class PreferencesRepository(ABC):
    @abstractmethod
    def put(self, preferences: UserPreferences) -> UserPreferences: ...
    @abstractmethod
    def get(self, org_id: str, user_id: str) -> UserPreferences | None: ...


# ── in-memory implementations (deterministic, insertion-ordered) ─────────────────


class InMemoryUserRepository(UserRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, User] = {}
        self._by_email: dict[str, str] = {}

    def add(self, user: User) -> User:
        self._by_id[user.id] = user
        self._by_email[user.email.lower()] = user.id
        return user

    def get(self, user_id: str) -> User | None:
        return self._by_id.get(user_id)

    def by_email(self, email: str) -> User | None:
        uid = self._by_email.get(email.lower())
        return self._by_id.get(uid) if uid else None


class InMemoryOrganizationRepository(OrganizationRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, Organization] = {}

    def add(self, org: Organization) -> Organization:
        self._by_id[org.id] = org
        return org

    def get(self, org_id: str) -> Organization | None:
        return self._by_id.get(org_id)

    def update(self, org: Organization) -> Organization:
        self._by_id[org.id] = org
        return org


class InMemoryWorkspaceRepository(WorkspaceRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, Workspace] = {}

    def add(self, workspace: Workspace) -> Workspace:
        self._by_id[workspace.id] = workspace
        return workspace

    def get(self, org_id: str, workspace_id: str) -> Workspace | None:
        ws = self._by_id.get(workspace_id)
        return ws if ws and ws.org_id == org_id else None

    def by_slug(self, org_id: str, slug: str) -> Workspace | None:
        for ws in self._by_id.values():
            if ws.org_id == org_id and ws.slug == slug:
                return ws
        return None

    def list(self, org_id: str) -> tuple[Workspace, ...]:
        return tuple(ws for ws in self._by_id.values() if ws.org_id == org_id)

    def update(self, workspace: Workspace) -> Workspace:
        self._by_id[workspace.id] = workspace
        return workspace


class InMemoryWorkspaceMembershipRepository(WorkspaceMembershipRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, WorkspaceMembership] = {}

    def add(self, membership: WorkspaceMembership) -> WorkspaceMembership:
        self._by_id[membership.id] = membership
        return membership

    def get(
        self, org_id: str, workspace_id: str, user_id: str
    ) -> WorkspaceMembership | None:
        for wm in self._by_id.values():
            if (
                wm.org_id == org_id
                and wm.workspace_id == workspace_id
                and wm.user_id == user_id
            ):
                return wm
        return None

    def update(self, membership: WorkspaceMembership) -> WorkspaceMembership:
        self._by_id[membership.id] = membership
        return membership

    def list(self, org_id: str, workspace_id: str) -> tuple[WorkspaceMembership, ...]:
        return tuple(
            wm
            for wm in self._by_id.values()
            if wm.org_id == org_id and wm.workspace_id == workspace_id
        )

    def list_for_user(
        self, org_id: str, user_id: str
    ) -> tuple[WorkspaceMembership, ...]:
        return tuple(
            wm
            for wm in self._by_id.values()
            if wm.org_id == org_id and wm.user_id == user_id
        )


class InMemoryMembershipRepository(MembershipRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, Membership] = {}

    def add(self, membership: Membership) -> Membership:
        self._by_id[membership.id] = membership
        return membership

    def get(self, org_id: str, user_id: str) -> Membership | None:
        for m in self._by_id.values():
            if m.org_id == org_id and m.user_id == user_id:
                return m
        return None

    def update(self, membership: Membership) -> Membership:
        self._by_id[membership.id] = membership
        return membership

    def list(self, org_id: str) -> tuple[Membership, ...]:
        return tuple(m for m in self._by_id.values() if m.org_id == org_id)


class InMemoryRoleBindingRepository(RoleBindingRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, RoleBinding] = {}

    def add(self, binding: RoleBinding) -> RoleBinding:
        self._by_id[binding.id] = binding
        return binding

    def remove(self, org_id: str, binding_id: str) -> None:
        existing = self._by_id.get(binding_id)
        if existing and existing.org_id == org_id:
            del self._by_id[binding_id]

    def for_user(self, org_id: str, user_id: str) -> tuple[RoleBinding, ...]:
        return tuple(
            b for b in self._by_id.values() if b.org_id == org_id and b.user_id == user_id
        )

    def list(self, org_id: str) -> tuple[RoleBinding, ...]:
        return tuple(b for b in self._by_id.values() if b.org_id == org_id)


class InMemoryEnvironmentRepository(EnvironmentRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, Environment] = {}

    def add(self, environment: Environment) -> Environment:
        self._by_id[environment.id] = environment
        return environment

    def list(self, org_id: str, workspace_id: str) -> tuple[Environment, ...]:
        return tuple(
            e
            for e in self._by_id.values()
            if e.org_id == org_id and e.workspace_id == workspace_id
        )


class InMemoryProjectRepository(ProjectRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, Project] = {}

    def add(self, project: Project) -> Project:
        self._by_id[project.id] = project
        return project

    def by_slug(self, org_id: str, workspace_id: str, slug: str) -> Project | None:
        for p in self._by_id.values():
            if p.org_id == org_id and p.workspace_id == workspace_id and p.slug == slug:
                return p
        return None

    def list(self, org_id: str, workspace_id: str) -> tuple[Project, ...]:
        return tuple(
            p
            for p in self._by_id.values()
            if p.org_id == org_id and p.workspace_id == workspace_id
        )

    def get(self, org_id: str, workspace_id: str, project_id: str) -> Project | None:
        p = self._by_id.get(project_id)
        return p if p and p.org_id == org_id and p.workspace_id == workspace_id else None

    def update(self, project: Project) -> Project:
        self._by_id[project.id] = project
        return project


class InMemoryProjectMembershipRepository(ProjectMembershipRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, ProjectMembership] = {}

    def add(self, membership: ProjectMembership) -> ProjectMembership:
        self._by_id[membership.id] = membership
        return membership

    def get(
        self, org_id: str, project_id: str, user_id: str
    ) -> ProjectMembership | None:
        for pm in self._by_id.values():
            if pm.org_id == org_id and pm.project_id == project_id and pm.user_id == user_id:
                return pm
        return None

    def update(self, membership: ProjectMembership) -> ProjectMembership:
        self._by_id[membership.id] = membership
        return membership

    def list(self, org_id: str, project_id: str) -> tuple[ProjectMembership, ...]:
        return tuple(
            pm
            for pm in self._by_id.values()
            if pm.org_id == org_id and pm.project_id == project_id
        )


class InMemoryAgentRepository(AgentRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, Agent] = {}

    def add(self, agent: Agent) -> Agent:
        self._by_id[agent.id] = agent
        return agent

    def get(self, org_id: str, project_id: str, agent_id: str) -> Agent | None:
        a = self._by_id.get(agent_id)
        return a if a and a.org_id == org_id and a.project_id == project_id else None

    def by_name(self, org_id: str, project_id: str, name: str) -> Agent | None:
        norm = name.strip().casefold()
        for a in self._by_id.values():
            if (
                a.org_id == org_id
                and a.project_id == project_id
                and a.status is not AgentStatus.DELETED
                and a.name.strip().casefold() == norm
            ):
                return a
        return None

    def list(self, org_id: str, project_id: str) -> tuple[Agent, ...]:
        return tuple(
            a
            for a in self._by_id.values()
            if a.org_id == org_id and a.project_id == project_id
        )

    def update(self, agent: Agent) -> Agent:
        self._by_id[agent.id] = agent
        return agent


class InMemoryAgentVersionRepository(AgentVersionRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, AgentVersion] = {}

    def add(self, version: AgentVersion) -> AgentVersion:
        self._by_id[version.id] = version
        return version

    def get(self, org_id: str, agent_id: str, number: int) -> AgentVersion | None:
        for v in self._by_id.values():
            if v.org_id == org_id and v.agent_id == agent_id and v.number == number:
                return v
        return None

    def list(self, org_id: str, agent_id: str) -> tuple[AgentVersion, ...]:
        return tuple(
            v
            for v in self._by_id.values()
            if v.org_id == org_id and v.agent_id == agent_id
        )

    def update_status(self, version: AgentVersion) -> AgentVersion:
        self._by_id[version.id] = version
        return version


class InMemoryToolRepository(ToolRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, Tool] = {}

    def add(self, tool: Tool) -> Tool:
        self._by_id[tool.id] = tool
        return tool

    def get(self, org_id: str, project_id: str, tool_id: str) -> Tool | None:
        t = self._by_id.get(tool_id)
        return t if t and t.org_id == org_id and t.project_id == project_id else None

    def by_name(self, org_id: str, project_id: str, name: str) -> Tool | None:
        norm = name.strip().casefold()
        for t in self._by_id.values():
            if (
                t.org_id == org_id
                and t.project_id == project_id
                and t.status is not ToolStatus.DELETED
                and t.name.strip().casefold() == norm
            ):
                return t
        return None

    def list(self, org_id: str, project_id: str) -> tuple[Tool, ...]:
        return tuple(
            t
            for t in self._by_id.values()
            if t.org_id == org_id and t.project_id == project_id
        )

    def update(self, tool: Tool) -> Tool:
        self._by_id[tool.id] = tool
        return tool


class InMemoryAgentToolRepository(AgentToolRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, AgentTool] = {}

    def add(self, attachment: AgentTool) -> AgentTool:
        self._by_id[attachment.id] = attachment
        return attachment

    def get(self, org_id: str, agent_id: str, tool_id: str) -> AgentTool | None:
        for a in self._by_id.values():
            if a.org_id == org_id and a.agent_id == agent_id and a.tool_id == tool_id:
                return a
        return None

    def list(self, org_id: str, agent_id: str) -> tuple[AgentTool, ...]:
        return tuple(
            a
            for a in self._by_id.values()
            if a.org_id == org_id and a.agent_id == agent_id
        )

    def update(self, attachment: AgentTool) -> AgentTool:
        self._by_id[attachment.id] = attachment
        return attachment

    def remove(self, org_id: str, agent_id: str, tool_id: str) -> None:
        found = [
            k for k, a in self._by_id.items()
            if a.org_id == org_id and a.agent_id == agent_id and a.tool_id == tool_id
        ]
        for k in found:
            del self._by_id[k]


class InMemoryConversationSessionRepository(ConversationSessionRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, ConversationSession] = {}

    def add(self, session: ConversationSession) -> ConversationSession:
        self._by_id[session.id] = session
        return session

    def get(self, org_id: str, session_id: str) -> ConversationSession | None:
        s = self._by_id.get(session_id)
        return s if s and s.org_id == org_id else None

    def list(self, org_id: str, agent_id: str) -> tuple[ConversationSession, ...]:
        return tuple(
            s
            for s in self._by_id.values()
            if s.org_id == org_id and s.agent_id == agent_id
        )

    def update(self, session: ConversationSession) -> ConversationSession:
        self._by_id[session.id] = session
        return session


class InMemoryConversationMessageRepository(ConversationMessageRepository):
    def __init__(self) -> None:
        self._items: list[ConversationMessage] = []

    def add(self, message: ConversationMessage) -> ConversationMessage:
        self._items.append(message)
        return message

    def list(self, org_id: str, session_id: str) -> tuple[ConversationMessage, ...]:
        return tuple(
            sorted(
                (m for m in self._items if m.org_id == org_id and m.session_id == session_id),
                key=lambda m: m.seq,
            )
        )

    def count(self, org_id: str, session_id: str) -> int:
        return sum(
            1 for m in self._items if m.org_id == org_id and m.session_id == session_id
        )

    def delete_for_session(self, org_id: str, session_id: str) -> None:
        self._items = [
            m for m in self._items
            if not (m.org_id == org_id and m.session_id == session_id)
        ]


class InMemoryKnowledgeCollectionRepository(KnowledgeCollectionRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, KnowledgeCollection] = {}

    def add(self, collection: KnowledgeCollection) -> KnowledgeCollection:
        self._by_id[collection.id] = collection
        return collection

    def get(self, org_id, project_id, collection_id) -> KnowledgeCollection | None:
        c = self._by_id.get(collection_id)
        return c if c and c.org_id == org_id and c.project_id == project_id else None

    def by_name(self, org_id, project_id, name) -> KnowledgeCollection | None:
        norm = name.strip().casefold()
        for c in self._by_id.values():
            if (c.org_id == org_id and c.project_id == project_id
                    and c.status is not KnowledgeStatus.DELETED
                    and c.name.strip().casefold() == norm):
                return c
        return None

    def list(self, org_id, project_id) -> tuple[KnowledgeCollection, ...]:
        return tuple(
            c for c in self._by_id.values()
            if c.org_id == org_id and c.project_id == project_id
        )

    def update(self, collection: KnowledgeCollection) -> KnowledgeCollection:
        self._by_id[collection.id] = collection
        return collection


class InMemoryKnowledgeDocumentRepository(KnowledgeDocumentRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, KnowledgeDocument] = {}

    def add(self, document: KnowledgeDocument) -> KnowledgeDocument:
        self._by_id[document.id] = document
        return document

    def get(self, org_id, collection_id, document_id) -> KnowledgeDocument | None:
        d = self._by_id.get(document_id)
        return d if d and d.org_id == org_id and d.collection_id == collection_id else None

    def by_checksum(self, org_id, collection_id, checksum) -> KnowledgeDocument | None:
        for d in self._by_id.values():
            if (d.org_id == org_id and d.collection_id == collection_id
                    and d.status is not DocumentStatus.DELETED and d.checksum == checksum):
                return d
        return None

    def list(self, org_id, collection_id) -> tuple[KnowledgeDocument, ...]:
        return tuple(
            d for d in self._by_id.values()
            if d.org_id == org_id and d.collection_id == collection_id
        )

    def update(self, document: KnowledgeDocument) -> KnowledgeDocument:
        self._by_id[document.id] = document
        return document


class InMemoryKnowledgeChunkRepository(KnowledgeChunkRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, KnowledgeChunk] = {}

    def add(self, chunk: KnowledgeChunk) -> KnowledgeChunk:
        self._by_id[chunk.id] = chunk
        return chunk

    def update(self, chunk: KnowledgeChunk) -> KnowledgeChunk:
        self._by_id[chunk.id] = chunk
        return chunk

    def get(self, org_id, chunk_id) -> KnowledgeChunk | None:
        c = self._by_id.get(chunk_id)
        return c if c and c.org_id == org_id else None

    def list_for_document(self, org_id, document_id) -> tuple[KnowledgeChunk, ...]:
        return tuple(sorted(
            (c for c in self._by_id.values()
             if c.org_id == org_id and c.document_id == document_id),
            key=lambda c: c.index,
        ))

    def list_for_collection(self, org_id, collection_id) -> tuple[KnowledgeChunk, ...]:
        return tuple(
            c for c in self._by_id.values()
            if c.org_id == org_id and c.collection_id == collection_id
        )

    def delete_for_document(self, org_id, document_id) -> None:
        for k in [k for k, c in self._by_id.items()
                  if c.org_id == org_id and c.document_id == document_id]:
            del self._by_id[k]


class InMemoryAlertRuleRepository(AlertRuleRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, AlertRule] = {}

    def add(self, rule: AlertRule) -> AlertRule:
        self._by_id[rule.id] = rule
        return rule

    def get(self, org_id, rule_id) -> AlertRule | None:
        r = self._by_id.get(rule_id)
        return r if r and r.org_id == org_id else None

    def list(self, org_id) -> tuple[AlertRule, ...]:
        return tuple(r for r in self._by_id.values() if r.org_id == org_id)

    def update(self, rule: AlertRule) -> AlertRule:
        self._by_id[rule.id] = rule
        return rule

    def delete(self, org_id, rule_id) -> None:
        r = self._by_id.get(rule_id)
        if r and r.org_id == org_id:
            del self._by_id[rule_id]


class InMemoryPreferencesRepository(PreferencesRepository):
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], UserPreferences] = {}

    def put(self, preferences: UserPreferences) -> UserPreferences:
        self._by_key[(preferences.org_id, preferences.user_id)] = preferences
        return preferences

    def get(self, org_id: str, user_id: str) -> UserPreferences | None:
        return self._by_key.get((org_id, user_id))

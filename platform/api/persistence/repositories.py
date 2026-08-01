"""SQLModel-backed repository implementations (M8.4).

The production implementations of the Foundation's repository **interfaces** — no
interface changes (they satisfy `api.foundation.repositories.*` exactly). Each repo reads
and writes the shared `Database.session`; it **never commits or rolls back** — the
`SqlUnitOfWork` owns the transaction boundary (the Application layer opens it). Writes made
here become durable when the enclosing Unit of Work commits, and vanish if it rolls back
(real atomicity — closing the M8.3 in-memory limitation).

Every method maps row ↔ frozen domain aggregate at the boundary (no persistence leakage)
and every org-owned read is org-scoped by construction (§A11.2).
"""

from __future__ import annotations

from actuator import (
    DeploymentExecution,
    DeploymentExecutionRepository,
)
from api.foundation import (
    Agent,
    AgentRepository,
    AgentTool,
    AgentToolRepository,
    AgentVersion,
    AgentVersionRepository,
    AlertRule,
    AlertRuleRepository,
    ConversationMessage,
    ConversationMessageRepository,
    ConversationSession,
    ConversationSessionRepository,
    Environment,
    EnvironmentRepository,
    KnowledgeChunk,
    KnowledgeChunkRepository,
    KnowledgeCollection,
    KnowledgeCollectionRepository,
    KnowledgeDocument,
    KnowledgeDocumentRepository,
    Membership,
    MembershipRepository,
    Organization,
    OrganizationRepository,
    PreferencesRepository,
    Project,
    ProjectMembership,
    ProjectMembershipRepository,
    ProjectRepository,
    RoleBinding,
    RoleBindingRepository,
    Tool,
    ToolRepository,
    User,
    UserPreferences,
    UserRepository,
    Workspace,
    WorkspaceMembership,
    WorkspaceMembershipRepository,
    WorkspaceRepository,
)

from . import mapping as m
from .database import Database
from .models import (
    AgentRow,
    AgentToolRow,
    AgentVersionRow,
    AlertRuleRow,
    ConversationMessageRow,
    ConversationSessionRow,
    DeploymentExecutionRow,
    EnvironmentRow,
    KnowledgeChunkRow,
    KnowledgeCollectionRow,
    KnowledgeDocumentRow,
    MembershipRow,
    OrganizationRow,
    PreferencesRow,
    ProjectMembershipRow,
    ProjectRow,
    RoleBindingRow,
    ToolRow,
    UserRow,
    WorkspaceMembershipRow,
    WorkspaceRow,
)
from .queries import all_where, first_where, get_by_pk


class _SqlRepo:
    def __init__(self, database: Database) -> None:
        self._db = database

    @property
    def _s(self):
        return self._db.session


class SqlUserRepository(_SqlRepo, UserRepository):
    def add(self, user: User) -> User:
        self._s.add(m.user_to_row(user))
        self._s.flush()  # assign/insert now; commit deferred to the Unit of Work
        return user

    def get(self, user_id: str) -> User | None:
        row = get_by_pk(self._s, UserRow, user_id)
        return m.user_to_domain(row) if row else None

    def by_email(self, email: str) -> User | None:
        row = first_where(self._s, UserRow, email=email)
        return m.user_to_domain(row) if row else None


class SqlOrganizationRepository(_SqlRepo, OrganizationRepository):
    def add(self, org: Organization) -> Organization:
        self._s.add(m.org_to_row(org))
        self._s.flush()
        return org

    def get(self, org_id: str) -> Organization | None:
        row = get_by_pk(self._s, OrganizationRow, org_id)
        return m.org_to_domain(row) if row else None

    def update(self, org: Organization) -> Organization:
        row = get_by_pk(self._s, OrganizationRow, org.id)
        if row is not None:
            row.name = org.name
            row.meta = dict(org.metadata)
            row.status = org.status.value
            row.timezone = org.timezone
            row.language = org.language
            row.logo_url = org.logo_url
            row.updated_at = org.updated_at
            self._s.add(row)
            self._s.flush()
        return org


class SqlWorkspaceRepository(_SqlRepo, WorkspaceRepository):
    def add(self, workspace: Workspace) -> Workspace:
        self._s.add(m.workspace_to_row(workspace))
        self._s.flush()
        return workspace

    def get(self, org_id: str, workspace_id: str) -> Workspace | None:
        row = first_where(self._s, WorkspaceRow, id=workspace_id, org_id=org_id)
        return m.workspace_to_domain(row) if row else None

    def by_slug(self, org_id: str, slug: str) -> Workspace | None:
        row = first_where(self._s, WorkspaceRow, org_id=org_id, slug=slug)
        return m.workspace_to_domain(row) if row else None

    def list(self, org_id: str) -> tuple[Workspace, ...]:
        rows = all_where(self._s, WorkspaceRow, org_id=org_id)
        return tuple(m.workspace_to_domain(r) for r in rows)

    def update(self, workspace: Workspace) -> Workspace:
        row = get_by_pk(self._s, WorkspaceRow, workspace.id)
        if row is not None:
            row.name = workspace.name
            row.meta = dict(workspace.metadata)
            row.description = workspace.description
            row.icon = workspace.icon
            row.color = workspace.color
            row.status = workspace.status.value
            row.updated_at = workspace.updated_at
            self._s.add(row)
            self._s.flush()
        return workspace


class SqlWorkspaceMembershipRepository(_SqlRepo, WorkspaceMembershipRepository):
    def add(self, membership: WorkspaceMembership) -> WorkspaceMembership:
        self._s.add(m.workspace_membership_to_row(membership))
        self._s.flush()
        return membership

    def get(
        self, org_id: str, workspace_id: str, user_id: str
    ) -> WorkspaceMembership | None:
        row = first_where(
            self._s, WorkspaceMembershipRow,
            org_id=org_id, workspace_id=workspace_id, user_id=user_id,
        )
        return m.workspace_membership_to_domain(row) if row else None

    def update(self, membership: WorkspaceMembership) -> WorkspaceMembership:
        row = get_by_pk(self._s, WorkspaceMembershipRow, membership.id)
        if row is not None:
            row.role = membership.role
            row.status = membership.status.value
            self._s.add(row)
            self._s.flush()
        return membership

    def list(self, org_id: str, workspace_id: str) -> tuple[WorkspaceMembership, ...]:
        rows = all_where(
            self._s, WorkspaceMembershipRow, org_id=org_id, workspace_id=workspace_id
        )
        return tuple(m.workspace_membership_to_domain(r) for r in rows)

    def list_for_user(
        self, org_id: str, user_id: str
    ) -> tuple[WorkspaceMembership, ...]:
        rows = all_where(
            self._s, WorkspaceMembershipRow, org_id=org_id, user_id=user_id
        )
        return tuple(m.workspace_membership_to_domain(r) for r in rows)


class SqlMembershipRepository(_SqlRepo, MembershipRepository):
    def add(self, membership: Membership) -> Membership:
        self._s.add(m.membership_to_row(membership))
        self._s.flush()
        return membership

    def get(self, org_id: str, user_id: str) -> Membership | None:
        row = first_where(self._s, MembershipRow, org_id=org_id, user_id=user_id)
        return m.membership_to_domain(row) if row else None

    def update(self, membership: Membership) -> Membership:
        row = get_by_pk(self._s, MembershipRow, membership.id)
        if row is not None:
            row.status = membership.status.value
            row.invited_role = membership.invited_role  # M8.6 (additive)
            row.expires_at = membership.expires_at
            self._s.add(row)
            self._s.flush()
        return membership

    def list(self, org_id: str) -> tuple[Membership, ...]:
        rows = all_where(self._s, MembershipRow, org_id=org_id)
        return tuple(m.membership_to_domain(r) for r in rows)


class SqlRoleBindingRepository(_SqlRepo, RoleBindingRepository):
    def add(self, binding: RoleBinding) -> RoleBinding:
        self._s.add(m.role_binding_to_row(binding))
        self._s.flush()
        return binding

    def remove(self, org_id: str, binding_id: str) -> None:
        row = get_by_pk(self._s, RoleBindingRow, binding_id)
        if row is not None and row.org_id == org_id:
            self._s.delete(row)
            self._s.flush()

    def for_user(self, org_id: str, user_id: str) -> tuple[RoleBinding, ...]:
        rows = all_where(self._s, RoleBindingRow, org_id=org_id, user_id=user_id)
        return tuple(m.role_binding_to_domain(r) for r in rows)

    def list(self, org_id: str) -> tuple[RoleBinding, ...]:
        rows = all_where(self._s, RoleBindingRow, org_id=org_id)
        return tuple(m.role_binding_to_domain(r) for r in rows)


class SqlEnvironmentRepository(_SqlRepo, EnvironmentRepository):
    def add(self, environment: Environment) -> Environment:
        self._s.add(m.environment_to_row(environment))
        self._s.flush()
        return environment

    def list(self, org_id: str, workspace_id: str) -> tuple[Environment, ...]:
        rows = all_where(self._s, EnvironmentRow, org_id=org_id, workspace_id=workspace_id)
        return tuple(m.environment_to_domain(r) for r in rows)


class SqlProjectRepository(_SqlRepo, ProjectRepository):
    def add(self, project: Project) -> Project:
        self._s.add(m.project_to_row(project))
        self._s.flush()
        return project

    def by_slug(self, org_id: str, workspace_id: str, slug: str) -> Project | None:
        row = first_where(
            self._s, ProjectRow, org_id=org_id, workspace_id=workspace_id, slug=slug
        )
        return m.project_to_domain(row) if row else None

    def list(self, org_id: str, workspace_id: str) -> tuple[Project, ...]:
        rows = all_where(self._s, ProjectRow, org_id=org_id, workspace_id=workspace_id)
        return tuple(m.project_to_domain(r) for r in rows)

    def get(self, org_id: str, workspace_id: str, project_id: str) -> Project | None:
        row = first_where(
            self._s, ProjectRow, id=project_id, org_id=org_id, workspace_id=workspace_id
        )
        return m.project_to_domain(row) if row else None

    def update(self, project: Project) -> Project:
        row = get_by_pk(self._s, ProjectRow, project.id)
        if row is not None:
            row.name = project.name
            row.description = project.description
            row.icon = project.icon
            row.status = project.status.value
            row.updated_at = project.updated_at
            row.meta = dict(project.metadata)
            self._s.add(row)
            self._s.flush()
        return project


class SqlProjectMembershipRepository(_SqlRepo, ProjectMembershipRepository):
    def add(self, membership: ProjectMembership) -> ProjectMembership:
        self._s.add(m.project_membership_to_row(membership))
        self._s.flush()
        return membership

    def get(
        self, org_id: str, project_id: str, user_id: str
    ) -> ProjectMembership | None:
        row = first_where(
            self._s, ProjectMembershipRow,
            org_id=org_id, project_id=project_id, user_id=user_id,
        )
        return m.project_membership_to_domain(row) if row else None

    def update(self, membership: ProjectMembership) -> ProjectMembership:
        row = get_by_pk(self._s, ProjectMembershipRow, membership.id)
        if row is not None:
            row.role = membership.role
            row.status = membership.status.value
            self._s.add(row)
            self._s.flush()
        return membership

    def list(self, org_id: str, project_id: str) -> tuple[ProjectMembership, ...]:
        rows = all_where(
            self._s, ProjectMembershipRow, org_id=org_id, project_id=project_id
        )
        return tuple(m.project_membership_to_domain(r) for r in rows)


class SqlAgentRepository(_SqlRepo, AgentRepository):
    def add(self, agent: Agent) -> Agent:
        self._s.add(m.agent_to_row(agent))
        self._s.flush()
        return agent

    def get(self, org_id: str, project_id: str, agent_id: str) -> Agent | None:
        row = first_where(
            self._s, AgentRow, id=agent_id, org_id=org_id, project_id=project_id
        )
        return m.agent_to_domain(row) if row else None

    def by_name(self, org_id: str, project_id: str, name: str) -> Agent | None:
        norm = name.strip().casefold()
        for row in all_where(self._s, AgentRow, org_id=org_id, project_id=project_id):
            if row.status != "deleted" and row.name.strip().casefold() == norm:
                return m.agent_to_domain(row)
        return None

    def list(self, org_id: str, project_id: str) -> tuple[Agent, ...]:
        rows = all_where(self._s, AgentRow, org_id=org_id, project_id=project_id)
        return tuple(m.agent_to_domain(r) for r in rows)

    def update(self, agent: Agent) -> Agent:
        row = get_by_pk(self._s, AgentRow, agent.id)
        if row is not None:
            row.name = agent.name
            row.description = agent.description
            row.icon = agent.icon
            row.category = agent.category
            row.tags = list(agent.tags)
            row.language = agent.language
            row.greeting = agent.greeting
            row.status = agent.status.value
            row.version = agent.version
            row.published_version = agent.published_version
            row.config = agent.config.as_dict(redact_secrets=False)
            row.meta = dict(agent.metadata)
            row.updated_at = agent.updated_at
            self._s.add(row)
            self._s.flush()
        return agent


class SqlAgentVersionRepository(_SqlRepo, AgentVersionRepository):
    def add(self, version: AgentVersion) -> AgentVersion:
        self._s.add(m.agent_version_to_row(version))
        self._s.flush()
        return version

    def get(self, org_id: str, agent_id: str, number: int) -> AgentVersion | None:
        row = first_where(
            self._s, AgentVersionRow, org_id=org_id, agent_id=agent_id, number=number
        )
        return m.agent_version_to_domain(row) if row else None

    def list(self, org_id: str, agent_id: str) -> tuple[AgentVersion, ...]:
        rows = all_where(self._s, AgentVersionRow, org_id=org_id, agent_id=agent_id)
        return tuple(m.agent_version_to_domain(r) for r in rows)

    def update_status(self, version: AgentVersion) -> AgentVersion:
        row = get_by_pk(self._s, AgentVersionRow, version.id)
        if row is not None:
            row.status = version.status.value  # only the supersede flip
            self._s.add(row)
            self._s.flush()
        return version


class SqlToolRepository(_SqlRepo, ToolRepository):
    def add(self, tool: Tool) -> Tool:
        self._s.add(m.tool_to_row(tool))
        self._s.flush()
        return tool

    def get(self, org_id: str, project_id: str, tool_id: str) -> Tool | None:
        row = first_where(
            self._s, ToolRow, id=tool_id, org_id=org_id, project_id=project_id
        )
        return m.tool_to_domain(row) if row else None

    def by_name(self, org_id: str, project_id: str, name: str) -> Tool | None:
        norm = name.strip().casefold()
        for row in all_where(self._s, ToolRow, org_id=org_id, project_id=project_id):
            if row.status != "deleted" and row.name.strip().casefold() == norm:
                return m.tool_to_domain(row)
        return None

    def list(self, org_id: str, project_id: str) -> tuple[Tool, ...]:
        rows = all_where(self._s, ToolRow, org_id=org_id, project_id=project_id)
        return tuple(m.tool_to_domain(r) for r in rows)

    def update(self, tool: Tool) -> Tool:
        row = get_by_pk(self._s, ToolRow, tool.id)
        if row is not None:
            row.name = tool.name
            row.category = tool.category.value
            row.description = tool.description
            row.icon = tool.icon
            row.status = tool.status.value
            row.visibility = tool.visibility.value
            row.enabled = tool.enabled
            row.version = tool.version
            row.config = tool.config.as_dict()
            row.meta = dict(tool.metadata)
            row.updated_at = tool.updated_at
            self._s.add(row)
            self._s.flush()
        return tool


class SqlAgentToolRepository(_SqlRepo, AgentToolRepository):
    def add(self, attachment: AgentTool) -> AgentTool:
        self._s.add(m.agent_tool_to_row(attachment))
        self._s.flush()
        return attachment

    def get(self, org_id: str, agent_id: str, tool_id: str) -> AgentTool | None:
        row = first_where(
            self._s, AgentToolRow, org_id=org_id, agent_id=agent_id, tool_id=tool_id
        )
        return m.agent_tool_to_domain(row) if row else None

    def list(self, org_id: str, agent_id: str) -> tuple[AgentTool, ...]:
        rows = all_where(self._s, AgentToolRow, org_id=org_id, agent_id=agent_id)
        return tuple(m.agent_tool_to_domain(r) for r in rows)

    def update(self, attachment: AgentTool) -> AgentTool:
        row = get_by_pk(self._s, AgentToolRow, attachment.id)
        if row is not None:
            row.position = attachment.position
            row.enabled = attachment.enabled
            row.config_override = dict(attachment.config_override)
            self._s.add(row)
            self._s.flush()
        return attachment

    def remove(self, org_id: str, agent_id: str, tool_id: str) -> None:
        row = first_where(
            self._s, AgentToolRow, org_id=org_id, agent_id=agent_id, tool_id=tool_id
        )
        if row is not None:
            self._s.delete(row)
            self._s.flush()


class SqlConversationSessionRepository(_SqlRepo, ConversationSessionRepository):
    def add(self, session: ConversationSession) -> ConversationSession:
        self._s.add(m.session_to_row(session))
        self._s.flush()
        return session

    def get(self, org_id: str, session_id: str) -> ConversationSession | None:
        row = first_where(self._s, ConversationSessionRow, id=session_id, org_id=org_id)
        return m.session_to_domain(row) if row else None

    def list(self, org_id: str, agent_id: str) -> tuple[ConversationSession, ...]:
        rows = all_where(self._s, ConversationSessionRow, org_id=org_id, agent_id=agent_id)
        return tuple(m.session_to_domain(r) for r in rows)

    def update(self, session: ConversationSession) -> ConversationSession:
        row = get_by_pk(self._s, ConversationSessionRow, session.id)
        if row is not None:
            row.status = session.status.value
            row.updated_at = session.updated_at
            row.meta = dict(session.metadata)
            row.state = session.state.as_dict(redact_secrets=False)
            self._s.add(row)
            self._s.flush()
        return session


class SqlConversationMessageRepository(_SqlRepo, ConversationMessageRepository):
    def add(self, message: ConversationMessage) -> ConversationMessage:
        self._s.add(m.message_to_row(message))
        self._s.flush()
        return message

    def list(self, org_id: str, session_id: str) -> tuple[ConversationMessage, ...]:
        rows = all_where(self._s, ConversationMessageRow, org_id=org_id, session_id=session_id)
        return tuple(m.message_to_domain(r) for r in sorted(rows, key=lambda r: r.seq))

    def count(self, org_id: str, session_id: str) -> int:
        return len(all_where(self._s, ConversationMessageRow, org_id=org_id, session_id=session_id))

    def delete_for_session(self, org_id: str, session_id: str) -> None:
        for row in all_where(
            self._s, ConversationMessageRow, org_id=org_id, session_id=session_id
        ):
            self._s.delete(row)
        self._s.flush()


class SqlKnowledgeCollectionRepository(_SqlRepo, KnowledgeCollectionRepository):
    def add(self, collection: KnowledgeCollection) -> KnowledgeCollection:
        self._s.add(m.knowledge_collection_to_row(collection))
        self._s.flush()
        return collection

    def get(self, org_id, project_id, collection_id) -> KnowledgeCollection | None:
        row = first_where(self._s, KnowledgeCollectionRow, id=collection_id, org_id=org_id,
                          project_id=project_id)
        return m.knowledge_collection_to_domain(row) if row else None

    def by_name(self, org_id, project_id, name) -> KnowledgeCollection | None:
        norm = name.strip().casefold()
        for row in all_where(self._s, KnowledgeCollectionRow, org_id=org_id, project_id=project_id):
            if row.status != "deleted" and row.name.strip().casefold() == norm:
                return m.knowledge_collection_to_domain(row)
        return None

    def list(self, org_id, project_id) -> tuple[KnowledgeCollection, ...]:
        rows = all_where(self._s, KnowledgeCollectionRow, org_id=org_id, project_id=project_id)
        return tuple(m.knowledge_collection_to_domain(r) for r in rows)

    def update(self, collection: KnowledgeCollection) -> KnowledgeCollection:
        row = get_by_pk(self._s, KnowledgeCollectionRow, collection.id)
        if row is not None:
            row.name = collection.name
            row.description = collection.description
            row.status = collection.status.value
            row.version = collection.version
            row.embedding_provider = collection.embedding_provider
            row.embedding_model = collection.embedding_model
            row.chunk_size = collection.chunk_size
            row.chunk_overlap = collection.chunk_overlap
            row.updated_at = collection.updated_at
            row.meta = dict(collection.metadata)
            self._s.add(row)
            self._s.flush()
        return collection


class SqlKnowledgeDocumentRepository(_SqlRepo, KnowledgeDocumentRepository):
    def add(self, document: KnowledgeDocument) -> KnowledgeDocument:
        self._s.add(m.knowledge_document_to_row(document))
        self._s.flush()
        return document

    def get(self, org_id, collection_id, document_id) -> KnowledgeDocument | None:
        row = first_where(self._s, KnowledgeDocumentRow, id=document_id, org_id=org_id,
                          collection_id=collection_id)
        return m.knowledge_document_to_domain(row) if row else None

    def by_checksum(self, org_id, collection_id, checksum) -> KnowledgeDocument | None:
        for row in all_where(
            self._s, KnowledgeDocumentRow, org_id=org_id, collection_id=collection_id,
            checksum=checksum,
        ):
            if row.status != "deleted":
                return m.knowledge_document_to_domain(row)
        return None

    def list(self, org_id, collection_id) -> tuple[KnowledgeDocument, ...]:
        rows = all_where(self._s, KnowledgeDocumentRow, org_id=org_id, collection_id=collection_id)
        return tuple(m.knowledge_document_to_domain(r) for r in rows)

    def update(self, document: KnowledgeDocument) -> KnowledgeDocument:
        row = get_by_pk(self._s, KnowledgeDocumentRow, document.id)
        if row is not None:
            row.name = document.name
            row.mime_type = document.mime_type
            row.size_bytes = document.size_bytes
            row.checksum = document.checksum
            row.content = document.content
            row.version = document.version
            row.status = document.status.value
            row.updated_at = document.updated_at
            row.meta = dict(document.metadata)
            self._s.add(row)
            self._s.flush()
        return document


class SqlKnowledgeChunkRepository(_SqlRepo, KnowledgeChunkRepository):
    def add(self, chunk: KnowledgeChunk) -> KnowledgeChunk:
        self._s.add(m.knowledge_chunk_to_row(chunk))
        self._s.flush()
        return chunk

    def update(self, chunk: KnowledgeChunk) -> KnowledgeChunk:
        row = get_by_pk(self._s, KnowledgeChunkRow, chunk.id)
        if row is not None:
            row.embedding = list(chunk.embedding)
            row.embedding_status = chunk.embedding_status.value
            row.index_status = chunk.index_status.value
            self._s.add(row)
            self._s.flush()
        return chunk

    def get(self, org_id, chunk_id) -> KnowledgeChunk | None:
        row = first_where(self._s, KnowledgeChunkRow, id=chunk_id, org_id=org_id)
        return m.knowledge_chunk_to_domain(row) if row else None

    def list_for_document(self, org_id, document_id) -> tuple[KnowledgeChunk, ...]:
        rows = all_where(self._s, KnowledgeChunkRow, org_id=org_id, document_id=document_id)
        return tuple(m.knowledge_chunk_to_domain(r) for r in sorted(rows, key=lambda r: r.idx))

    def list_for_collection(self, org_id, collection_id) -> tuple[KnowledgeChunk, ...]:
        rows = all_where(self._s, KnowledgeChunkRow, org_id=org_id, collection_id=collection_id)
        return tuple(m.knowledge_chunk_to_domain(r) for r in rows)

    def delete_for_document(self, org_id, document_id) -> None:
        for row in all_where(self._s, KnowledgeChunkRow, org_id=org_id, document_id=document_id):
            self._s.delete(row)
        self._s.flush()


class SqlDeploymentExecutionRepository(_SqlRepo, DeploymentExecutionRepository):
    """Durable deployment execution store (M8.16, ADR-0022). Upserts the whole immutable aggregate
    on every state change (the Actuator returns a new value per transition). `get` resolves by
    execution id **or** deployment id, both org-scoped."""

    def add(self, execution: DeploymentExecution) -> DeploymentExecution:
        self._s.add(m.deployment_execution_to_row(execution))
        self._s.flush()
        return execution

    def update(self, execution: DeploymentExecution) -> DeploymentExecution:
        existing = get_by_pk(self._s, DeploymentExecutionRow, execution.id)
        if existing is not None:
            self._s.delete(existing)
            self._s.flush()
        self._s.add(m.deployment_execution_to_row(execution))
        self._s.flush()
        return execution

    def get(self, org_id: str, execution_ref: str) -> DeploymentExecution | None:
        row = first_where(self._s, DeploymentExecutionRow, id=execution_ref, org_id=org_id)
        if row is None:
            row = first_where(
                self._s, DeploymentExecutionRow, deployment_id=execution_ref, org_id=org_id
            )
        return m.deployment_execution_to_domain(row) if row else None

    def list_for_project(self, org_id: str, project_id: str) -> tuple[DeploymentExecution, ...]:
        rows = all_where(self._s, DeploymentExecutionRow, org_id=org_id, project_id=project_id)
        return tuple(m.deployment_execution_to_domain(r) for r in rows)


class SqlAlertRuleRepository(_SqlRepo, AlertRuleRepository):
    """Durable Observability alert rules (M8.17). Org-scoped; the only Observability write path."""

    def add(self, rule: AlertRule) -> AlertRule:
        self._s.add(m.alert_rule_to_row(rule))
        self._s.flush()
        return rule

    def get(self, org_id: str, rule_id: str) -> AlertRule | None:
        row = first_where(self._s, AlertRuleRow, id=rule_id, org_id=org_id)
        return m.alert_rule_to_domain(row) if row else None

    def list(self, org_id: str) -> tuple[AlertRule, ...]:
        rows = all_where(self._s, AlertRuleRow, org_id=org_id)
        return tuple(m.alert_rule_to_domain(r) for r in rows)

    def update(self, rule: AlertRule) -> AlertRule:
        existing = get_by_pk(self._s, AlertRuleRow, rule.id)
        if existing is not None:
            self._s.delete(existing)
            self._s.flush()
        self._s.add(m.alert_rule_to_row(rule))
        self._s.flush()
        return rule

    def delete(self, org_id: str, rule_id: str) -> None:
        row = first_where(self._s, AlertRuleRow, id=rule_id, org_id=org_id)
        if row is not None:
            self._s.delete(row)
            self._s.flush()


class SqlPreferencesRepository(_SqlRepo, PreferencesRepository):
    def put(self, preferences: UserPreferences) -> UserPreferences:
        existing = self._s.get(PreferencesRow, (preferences.user_id, preferences.org_id))
        if existing is not None:
            self._s.delete(existing)
            self._s.flush()
        self._s.add(m.preferences_to_row(preferences))
        self._s.flush()
        return preferences

    def get(self, org_id: str, user_id: str) -> UserPreferences | None:
        row = self._s.get(PreferencesRow, (user_id, org_id))
        return m.preferences_to_domain(row) if row else None

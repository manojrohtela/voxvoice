"""FoundationFactory + the `Foundation` facade (M8.2).

The composition root that wires repositories, seams, the validator, the permission
evaluator, and the event emitter into the domain services — the one place construction
happens (mirrors `product.ProductFactory` / Epic 5's runtime factories). `.default()`
returns a fully in-memory, deterministic foundation suitable for tests and local dev; a
production wiring swaps the repository + clock + id + emitter seams without touching a
service.
"""

from __future__ import annotations

from dataclasses import dataclass

from actuator import (
    DeploymentExecutionRepository,
    InMemoryDeploymentExecutionRepository,
)

from .activity import ActivityProjectionService
from .agent_builder import AgentBuilderService
from .audit import AuditReader, NullAuditReader
from .config import FeatureFlagResolver
from .deployment import DeploymentActuatorService
from .events import EventEmitter, RecordingEventEmitter
from .execution import ConversationExecutionService, ExecutionEngine, MockExecutionEngine
from .knowledge import KnowledgeSearchService, KnowledgeService
from .knowledge_adapters import KnowledgeRetriever, MockKnowledgeRetriever
from .observability import ObservabilityService
from .org_lifecycle import InvitationService, OrganizationLifecycleService
from .playground import PlaygroundService
from .project_lifecycle import (
    ProjectEnvironmentService,
    ProjectLifecycleService,
    ProjectMembershipService,
)
from .rbac import PermissionEvaluator, RbacAuthorizer
from .repositories import (
    AgentRepository,
    AgentToolRepository,
    AgentVersionRepository,
    AlertRuleRepository,
    ConversationMessageRepository,
    ConversationSessionRepository,
    EnvironmentRepository,
    InMemoryAgentRepository,
    InMemoryAgentToolRepository,
    InMemoryAgentVersionRepository,
    InMemoryAlertRuleRepository,
    InMemoryConversationMessageRepository,
    InMemoryConversationSessionRepository,
    InMemoryEnvironmentRepository,
    InMemoryKnowledgeChunkRepository,
    InMemoryKnowledgeCollectionRepository,
    InMemoryKnowledgeDocumentRepository,
    InMemoryMembershipRepository,
    InMemoryOrganizationRepository,
    InMemoryPreferencesRepository,
    InMemoryProjectMembershipRepository,
    InMemoryProjectRepository,
    InMemoryRoleBindingRepository,
    InMemoryToolRepository,
    InMemoryUserRepository,
    InMemoryWorkspaceMembershipRepository,
    InMemoryWorkspaceRepository,
    KnowledgeChunkRepository,
    KnowledgeCollectionRepository,
    KnowledgeDocumentRepository,
    MembershipRepository,
    OrganizationRepository,
    PreferencesRepository,
    ProjectMembershipRepository,
    ProjectRepository,
    RoleBindingRepository,
    ToolRepository,
    UserRepository,
    WorkspaceMembershipRepository,
    WorkspaceRepository,
)
from .runtime import ConversationRuntimeService
from .seams import Clock, DefaultIdGenerator, IdGenerator, SystemClock
from .services import (
    AccessService,
    ConfigurationService,
    IdentityService,
    MembershipService,
    ProjectService,
    WorkspaceService,
)
from .tool_framework import AgentToolService, ToolCatalogService
from .validation import FoundationValidator
from .workspace_lifecycle import (
    WorkspaceLifecycleService,
    WorkspaceMembershipService,
    WorkspaceNavigationService,
)


@dataclass(frozen=True, slots=True)
class Foundation:
    """The assembled Customer Platform Foundation — the six domain services plus the
    RBAC authorizer bridged onto the frozen `tenant.access.Authorizer` seam and the
    event emitter (the in-memory audit trail by default)."""

    identity: IdentityService
    membership: MembershipService
    access: AccessService
    workspaces: WorkspaceService
    projects: ProjectService
    configuration: ConfigurationService
    authorizer: RbacAuthorizer
    emitter: EventEmitter
    # M8.6 — Organization Lifecycle + Invitation services (additive).
    org_lifecycle: OrganizationLifecycleService
    invitations: InvitationService
    # M8.7 — Workspace Lifecycle + Membership + Navigation services (additive).
    workspace_lifecycle: WorkspaceLifecycleService
    workspace_members: WorkspaceMembershipService
    workspace_navigation: WorkspaceNavigationService
    # M8.8 — Project Lifecycle + Membership + Environment services (additive).
    project_lifecycle: ProjectLifecycleService
    project_members: ProjectMembershipService
    project_environments: ProjectEnvironmentService
    # M8.9 — durable Audit & Activity Platform (additive; read side).
    audit_reader: AuditReader
    activity: ActivityProjectionService
    # M8.10 — Agent Builder service (additive).
    agent_builder: AgentBuilderService
    # M8.11 — Tool Framework services (additive).
    tools: ToolCatalogService
    agent_tools: AgentToolService
    # M8.12 — Conversation Runtime service (additive).
    conversation_runtime: ConversationRuntimeService
    # M8.13 — Conversation Playground service (additive; mock runtime).
    playground: PlaygroundService
    # M8.14 — real execution binding (additive; provider engine injected at composition).
    conversation_execution: ConversationExecutionService
    # M8.15 — Knowledge Tool services (additive; retriever injected at composition).
    knowledge: KnowledgeService
    knowledge_search: KnowledgeSearchService
    # M8.16 — Deployment Actuator service (additive; ADR-0022). The control-plane seam onto the
    # top-level `actuator/` package (sole owner of deployment execution).
    deployment: DeploymentActuatorService
    # M8.17 — Observability Platform (additive). A passive read-model over the durable audit
    # stream (metrics/logs/traces/health/usage/cost/dashboards) + durable alert-rule config.
    observability: ObservabilityService


class FoundationFactory:
    @staticmethod
    def create(
        *,
        users: UserRepository,
        orgs: OrganizationRepository,
        workspaces: WorkspaceRepository,
        memberships: MembershipRepository,
        role_bindings: RoleBindingRepository,
        environments: EnvironmentRepository,
        projects: ProjectRepository,
        preferences: PreferencesRepository,
        ids: IdGenerator,
        clock: Clock,
        emitter: EventEmitter,
        workspace_memberships: WorkspaceMembershipRepository | None = None,
        project_memberships: ProjectMembershipRepository | None = None,
        audit_reader: AuditReader | None = None,
        agents: AgentRepository | None = None,
        agent_versions: AgentVersionRepository | None = None,
        tools: ToolRepository | None = None,
        agent_tools: AgentToolRepository | None = None,
        sessions: ConversationSessionRepository | None = None,
        messages: ConversationMessageRepository | None = None,
        execution_engine: ExecutionEngine | None = None,
        knowledge_collections: KnowledgeCollectionRepository | None = None,
        knowledge_documents: KnowledgeDocumentRepository | None = None,
        knowledge_chunks: KnowledgeChunkRepository | None = None,
        knowledge_retriever: KnowledgeRetriever | None = None,
        deployment_executions: DeploymentExecutionRepository | None = None,
        alert_rules: AlertRuleRepository | None = None,
    ) -> Foundation:
        # Additive (M8.7/M8.8): default the new repos so existing callers keep working.
        workspace_memberships = (
            workspace_memberships or InMemoryWorkspaceMembershipRepository()
        )
        project_memberships = project_memberships or InMemoryProjectMembershipRepository()
        # Additive (M8.10): default the agent repos so existing callers keep working.
        agents = agents or InMemoryAgentRepository()
        agent_versions = agent_versions or InMemoryAgentVersionRepository()
        # Additive (M8.11): default the tool repos so existing callers keep working.
        tools = tools or InMemoryToolRepository()
        agent_tools = agent_tools or InMemoryAgentToolRepository()
        # Additive (M8.12): default the session repo so existing callers keep working.
        sessions = sessions or InMemoryConversationSessionRepository()
        # Additive (M8.14): default the message store + a mock execution engine (offline) so
        # existing callers keep working; the persistent app injects the real provider engine.
        messages = messages or InMemoryConversationMessageRepository()
        execution_engine = execution_engine or MockExecutionEngine()
        # Additive (M8.15): default the knowledge repos + a deterministic offline retriever.
        knowledge_collections = knowledge_collections or InMemoryKnowledgeCollectionRepository()
        knowledge_documents = knowledge_documents or InMemoryKnowledgeDocumentRepository()
        knowledge_chunks = knowledge_chunks or InMemoryKnowledgeChunkRepository()
        knowledge_retriever = knowledge_retriever or MockKnowledgeRetriever()
        # Additive (M8.16): default the durable deployment-execution repo so existing callers
        # keep working; persistence injects the SQL-backed one.
        deployment_executions = (
            deployment_executions or InMemoryDeploymentExecutionRepository()
        )
        # Additive (M8.17): default the durable alert-rule repo so existing callers keep working.
        alert_rules = alert_rules or InMemoryAlertRuleRepository()
        # Additive (M8.9): the audit reader defaults to the emitter when it also implements
        # the read seam (the in-memory RecordingEventEmitter does), else an empty reader.
        if audit_reader is None:
            audit_reader = (
                emitter
                if hasattr(emitter, "query") and hasattr(emitter, "stream")
                else NullAuditReader()
            )
        evaluator = PermissionEvaluator()
        validator = FoundationValidator()
        base = {
            "ids": ids,
            "clock": clock,
            "emitter": emitter,
            "evaluator": evaluator,
            "role_bindings": role_bindings,
        }
        # M8.12/M8.13: the playground reuses the same runtime instance (built once here).
        conversation_runtime = ConversationRuntimeService(
            projects=projects, agents=agents, agent_versions=agent_versions,
            tools=tools, agent_tools=agent_tools, sessions=sessions, **base,
        )
        return Foundation(
            identity=IdentityService(
                users=users, orgs=orgs, memberships=memberships,
                validator=validator, **base,
            ),
            membership=MembershipService(
                orgs=orgs, users=users, memberships=memberships, **base,
            ),
            access=AccessService(
                orgs=orgs, workspaces=workspaces, memberships=memberships,
                validator=validator, **base,
            ),
            workspaces=WorkspaceService(
                orgs=orgs, workspaces=workspaces, environments=environments,
                validator=validator, **base,
            ),
            projects=ProjectService(
                workspaces=workspaces, projects=projects, validator=validator, **base,
            ),
            configuration=ConfigurationService(
                preferences=preferences, resolver=FeatureFlagResolver(), **base,
            ),
            org_lifecycle=OrganizationLifecycleService(
                orgs=orgs, memberships=memberships, validator=validator, **base,
            ),
            invitations=InvitationService(
                orgs=orgs, users=users, memberships=memberships, **base,
            ),
            workspace_lifecycle=WorkspaceLifecycleService(
                orgs=orgs, workspaces=workspaces, ws_memberships=workspace_memberships,
                validator=validator, **base,
            ),
            workspace_members=WorkspaceMembershipService(
                orgs=orgs, users=users, workspaces=workspaces, memberships=memberships,
                ws_memberships=workspace_memberships, **base,
            ),
            workspace_navigation=WorkspaceNavigationService(
                workspaces=workspaces, preferences=preferences, **base,
            ),
            project_lifecycle=ProjectLifecycleService(
                workspaces=workspaces, projects=projects,
                project_memberships=project_memberships, validator=validator, **base,
            ),
            project_members=ProjectMembershipService(
                orgs=orgs, users=users, workspaces=workspaces, projects=projects,
                memberships=memberships, project_memberships=project_memberships, **base,
            ),
            project_environments=ProjectEnvironmentService(
                projects=projects, preferences=preferences, **base,
            ),
            audit_reader=audit_reader,
            activity=ActivityProjectionService(audit_reader),
            agent_builder=AgentBuilderService(
                projects=projects, agents=agents, agent_versions=agent_versions, **base,
            ),
            tools=ToolCatalogService(projects=projects, tools=tools, **base),
            agent_tools=AgentToolService(
                projects=projects, agents=agents, tools=tools, agent_tools=agent_tools, **base,
            ),
            conversation_runtime=conversation_runtime,
            playground=PlaygroundService(
                runtime=conversation_runtime, sessions=sessions, **base,
            ),
            conversation_execution=ConversationExecutionService(
                sessions=sessions, agent_versions=agent_versions, messages=messages,
                engine=execution_engine, **base,
            ),
            knowledge=KnowledgeService(
                projects=projects, collections=knowledge_collections,
                documents=knowledge_documents, chunks=knowledge_chunks, **base,
            ),
            knowledge_search=KnowledgeSearchService(
                projects=projects, collections=knowledge_collections,
                chunks=knowledge_chunks, retriever=knowledge_retriever, **base,
            ),
            deployment=DeploymentActuatorService(
                projects=projects, agents=agents, agent_versions=agent_versions,
                executions=deployment_executions, **base,
            ),
            observability=ObservabilityService(
                reader=audit_reader, alert_rules=alert_rules, **base,
            ),
            authorizer=RbacAuthorizer(role_bindings.for_user, evaluator),
            emitter=emitter,
        )

    @staticmethod
    def default(*, ids: IdGenerator | None = None, clock: Clock | None = None) -> Foundation:
        """A fully in-memory, deterministic foundation. Pass a `SequentialIdGenerator`
        + `FixedClock` for byte-reproducible tests; omit for production-shaped seams
        (real clock + collision-resistant ids) over in-memory stores."""
        return FoundationFactory.create(
            users=InMemoryUserRepository(),
            orgs=InMemoryOrganizationRepository(),
            workspaces=InMemoryWorkspaceRepository(),
            memberships=InMemoryMembershipRepository(),
            role_bindings=InMemoryRoleBindingRepository(),
            environments=InMemoryEnvironmentRepository(),
            projects=InMemoryProjectRepository(),
            preferences=InMemoryPreferencesRepository(),
            ids=ids or DefaultIdGenerator(),
            clock=clock or SystemClock(),
            emitter=RecordingEventEmitter(),
        )

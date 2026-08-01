"""foundation — the Customer Platform Foundation (Epic 8, M8.2).

The base every future Epic 8 feature builds on: identity (User/Organization/Workspace/
Membership/RoleBinding), platform (Environment/Project/UserPreferences), security (an
RBAC foundation + permission evaluation + organization isolation, bridged onto the
frozen `tenant.access.Authorizer` seam), configuration (settings + deterministic
feature-flag resolution), and the supporting infrastructure (repository + service +
event seams, validation, DTOs, a composition factory).

**Ownership (ADR-0021).** This is a Control-Plane management/authoring surface — it owns
identity/tenancy/authoring/management state only. It never owns runtime/session/
execution/provider state, never actuates anything (ADR-0022), and never imports a frozen
Epic 5/6/7 runtime. Dependency direction: `api.foundation → core, tenant` only (no
`runtime`/`product`/`tool`/… import; no FastAPI in the domain layer) — test-enforced,
acyclic (AR-15).

Determinism (§A2.4): all behaviour (validation, permission evaluation, isolation,
resolution) is deterministic; the only non-deterministic edges (id minting, wall clock)
are injected `IdGenerator`/`Clock` seams. Nothing in Epic 5/6/7 is modified.
"""

from __future__ import annotations

from .activity import ActivityProjectionService
from .agent_builder import (
    MAX_PROMPT_CHARS,
    STANDARD_VARIABLES,
    AgentBuilderService,
    validate_agent,
)
from .agent_domain import (
    Agent,
    AgentConfig,
    AgentStatus,
    AgentVariable,
    AgentVersion,
    AgentVersionStatus,
    CreativityProfile,
    ModelConfig,
    PromptConfig,
    ReasoningMode,
    ResponseLength,
    SafetyProfile,
    VariableType,
    VoiceConfig,
)
from .audit import (
    AuditPage,
    AuditQuery,
    AuditReader,
    AuditRecord,
    NullAuditReader,
    paginate,
    project_of,
    workspace_of,
)
from .config import (
    FeatureFlag,
    FeatureFlagResolver,
    FeatureFlagState,
    OrganizationSettings,
    SettingScope,
    WorkspaceSettings,
)
from .deployment import DeploymentActuatorService
from .diagnostics import (
    Diagnostic,
    FoundationDiagnosticCode,
    FoundationDiagnostics,
    Severity,
)
from .domain import (
    Environment,
    EnvironmentKind,
    Membership,
    MembershipStatus,
    Organization,
    OrganizationStatus,
    OrgRole,
    Project,
    ProjectMembership,
    ProjectMembershipStatus,
    ProjectRole,
    ProjectStatus,
    Role,
    RoleBinding,
    RoleScope,
    User,
    UserPreferences,
    Workspace,
    WorkspaceMembership,
    WorkspaceMembershipStatus,
    WorkspaceStatus,
    WsRole,
)
from .dto import (
    AgentSummaryView,
    AgentToolView,
    AgentVersionView,
    AgentView,
    ConversationView,
    CreateEnvironmentRequest,
    CreateOrganizationRequest,
    CreateProjectRequest,
    CreateWorkspaceRequest,
    EnvironmentView,
    ExecutionResultView,
    GrantRoleRequest,
    InviteMemberRequest,
    KnowledgeChunkView,
    KnowledgeCollectionView,
    KnowledgeDocumentView,
    MemberView,
    MessageView,
    OrganizationView,
    PreferencesView,
    ProjectMemberView,
    ProjectView,
    RegisterUserRequest,
    SessionSummaryView,
    SessionView,
    ToolSummaryView,
    ToolView,
    TurnView,
    UpdatePreferencesRequest,
    UserView,
    WorkspaceMemberView,
    WorkspaceView,
)
from .errors import (
    AuthorizationError,
    ConflictError,
    FoundationError,
    FoundationValidationError,
    NotFoundError,
    OrganizationIsolationError,
)
from .events import (
    EventEmitter,
    FoundationEvent,
    FoundationEventType,
    NullEventEmitter,
    RecordingEventEmitter,
)
from .execution import (
    ConversationExecutionService,
    ExecutionEngine,
    MockExecutionEngine,
)
from .execution_domain import (
    ConversationMessage,
    ExecutionError,
    ExecutionResult,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)
from .factory import Foundation, FoundationFactory
from .knowledge import KnowledgeSearchService, KnowledgeService
from .knowledge_adapters import (
    EMBED_DIM,
    HashEmbedder,
    InMemoryVectorStore,
    KnowledgeEmbedder,
    KnowledgeRetriever,
    MockKnowledgeRetriever,
    VectorStore,
    cosine,
    hash_embed,
    indexed_chunks_as_corpus,
    rank_and_select,
)
from .knowledge_chunking import (
    MAX_DOCUMENT_BYTES,
    SUPPORTED_MIME_TYPES,
    checksum,
    chunk_text,
    is_supported_mime,
)
from .knowledge_domain import (
    ContextPackage,
    DocumentStatus,
    EmbeddingStatus,
    IndexStatus,
    KnowledgeChunk,
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeStatus,
    RetrievedChunk,
)
from .observability import ObservabilityService
from .observability_domain import (
    AlertComparator,
    AlertEvent,
    AlertRule,
    AlertSeverity,
    AlertState,
    HealthAggregation,
    HealthSnapshot,
    HealthStatus,
    LogCategory,
    LogEntry,
    LogLevel,
    LogQuery,
    MetricDefinition,
    MetricKind,
    MetricSnapshot,
    Span,
    Trace,
)
from .org_lifecycle import InvitationService, OrganizationLifecycleService
from .playground import PlaygroundService
from .playground_domain import ConversationTurn
from .project_lifecycle import (
    STANDARD_ENVIRONMENTS,
    ProjectEnvironmentService,
    ProjectLifecycleService,
    ProjectMembershipService,
)
from .rbac import (
    ORG_ROLE_BINDING,
    ROLE_PERMISSIONS,
    WS_ROLE_BINDING,
    Permission,
    PermissionEvaluator,
    RbacAuthorizer,
    permissions_for,
    role_for_org_tier,
    role_for_ws_tier,
)
from .repositories import (
    AgentRepository,
    AgentToolRepository,
    AgentVersionRepository,
    AlertRuleRepository,
    ConversationMessageRepository,
    ConversationSessionRepository,
    EnvironmentRepository,
    InMemoryAlertRuleRepository,
    InMemoryConversationMessageRepository,
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
from .runtime import SYSTEM_DEFAULT_VARIABLES, ConversationRuntimeService
from .runtime_adapters import (
    ADAPTER_KINDS,
    LLMAdapter,
    MemoryAdapter,
    ToolExecutor,
    VoiceAdapter,
)
from .runtime_domain import (
    ConversationSession,
    ConversationState,
    ExecutionPlan,
    ResolvedPrompt,
    ResolvedVariable,
    SessionStatus,
    ToolPlanStep,
    VariableSource,
)
from .runtime_mocks import (
    MockAdapterSet,
    MockLLMAdapter,
    MockMemoryAdapter,
    MockToolExecutor,
    MockVoiceAdapter,
)
from .seams import (
    Clock,
    DefaultIdGenerator,
    FixedClock,
    IdGenerator,
    SequentialIdGenerator,
    SystemClock,
)
from .services import (
    AccessService,
    ConfigurationService,
    IdentityService,
    MembershipService,
    ProjectService,
    WorkspaceService,
)
from .tool_domain import (
    AgentTool,
    RetryPolicy,
    Tool,
    ToolCategory,
    ToolConfig,
    ToolStatus,
    ToolVisibility,
)
from .tool_framework import AgentToolService, ToolCatalogService, validate_tool
from .validation import FoundationValidator, is_valid_email, is_valid_slug
from .workspace_lifecycle import (
    WorkspaceLifecycleService,
    WorkspaceMembershipService,
    WorkspaceNavigationService,
)

__all__ = [
    # audit & activity (M8.9)
    "ActivityProjectionService", "AuditPage", "AuditQuery", "AuditReader", "AuditRecord",
    "NullAuditReader", "paginate", "project_of", "workspace_of",
    # agent builder (M8.10)
    "AgentBuilderService", "validate_agent", "STANDARD_VARIABLES", "MAX_PROMPT_CHARS",
    "Agent", "AgentConfig", "AgentStatus", "AgentVariable", "AgentVersion",
    "AgentVersionStatus", "CreativityProfile", "ModelConfig", "PromptConfig",
    "ReasoningMode", "ResponseLength", "SafetyProfile", "VariableType", "VoiceConfig",
    "AgentRepository", "AgentVersionRepository", "AgentView", "AgentSummaryView",
    "AgentVersionView",
    # tool framework (M8.11)
    "ToolCatalogService", "AgentToolService", "validate_tool", "Tool", "AgentTool",
    "ToolConfig", "RetryPolicy", "ToolCategory", "ToolStatus", "ToolVisibility",
    "ToolRepository", "AgentToolRepository", "ToolView", "ToolSummaryView", "AgentToolView",
    # conversation runtime (M8.12)
    "ConversationRuntimeService", "SYSTEM_DEFAULT_VARIABLES", "ConversationSession",
    "ConversationState", "ExecutionPlan", "ResolvedPrompt", "ResolvedVariable",
    "SessionStatus", "ToolPlanStep", "VariableSource", "ConversationSessionRepository",
    "SessionView", "SessionSummaryView", "LLMAdapter", "VoiceAdapter", "ToolExecutor",
    "MemoryAdapter", "ADAPTER_KINDS",
    # conversation playground (M8.13)
    "PlaygroundService", "ConversationTurn", "TurnView", "ConversationView",
    "MockAdapterSet", "MockLLMAdapter", "MockToolExecutor", "MockVoiceAdapter",
    "MockMemoryAdapter",
    # provider adapter binding / real execution (M8.14)
    "ConversationExecutionService", "ExecutionEngine", "MockExecutionEngine",
    "ExecutionResult", "ExecutionError", "StreamEvent", "StreamEventType", "TokenUsage",
    "ConversationMessage", "ConversationMessageRepository",
    "InMemoryConversationMessageRepository", "ExecutionResultView", "MessageView",
    # knowledge tool (M8.15)
    "KnowledgeService", "KnowledgeSearchService",
    "KnowledgeCollection", "KnowledgeDocument", "KnowledgeChunk", "RetrievedChunk",
    "ContextPackage", "KnowledgeStatus", "DocumentStatus", "EmbeddingStatus", "IndexStatus",
    "KnowledgeCollectionRepository", "KnowledgeDocumentRepository", "KnowledgeChunkRepository",
    "KnowledgeCollectionView", "KnowledgeDocumentView", "KnowledgeChunkView",
    # deployment actuator (M8.16)
    "DeploymentActuatorService",
    # observability (M8.17)
    "ObservabilityService", "AlertRule", "AlertComparator", "AlertSeverity", "AlertState",
    "AlertEvent", "MetricDefinition", "MetricKind", "MetricSnapshot", "LogEntry", "LogLevel",
    "LogCategory", "LogQuery", "Span", "Trace", "HealthSnapshot", "HealthAggregation",
    "HealthStatus", "AlertRuleRepository", "InMemoryAlertRuleRepository",
    "KnowledgeRetriever", "KnowledgeEmbedder", "VectorStore", "MockKnowledgeRetriever",
    "HashEmbedder", "InMemoryVectorStore", "hash_embed", "cosine", "rank_and_select",
    "indexed_chunks_as_corpus", "EMBED_DIM",
    "chunk_text", "checksum", "is_supported_mime", "SUPPORTED_MIME_TYPES", "MAX_DOCUMENT_BYTES",
    # config
    "FeatureFlag", "FeatureFlagResolver", "FeatureFlagState", "OrganizationSettings",
    "SettingScope", "WorkspaceSettings",
    # diagnostics
    "Diagnostic", "FoundationDiagnosticCode", "FoundationDiagnostics", "Severity",
    # domain
    "Environment", "EnvironmentKind", "Membership", "MembershipStatus", "Organization",
    "OrganizationStatus", "OrgRole", "Project", "ProjectMembership",
    "ProjectMembershipStatus", "ProjectRole", "ProjectStatus", "Role", "RoleBinding",
    "RoleScope", "User", "UserPreferences", "Workspace", "WorkspaceMembership",
    "WorkspaceMembershipStatus", "WorkspaceStatus", "WsRole",
    # org lifecycle (M8.6)
    "InvitationService", "OrganizationLifecycleService", "ORG_ROLE_BINDING",
    "role_for_org_tier",
    # workspace lifecycle (M8.7)
    "WorkspaceLifecycleService", "WorkspaceMembershipService", "WorkspaceNavigationService",
    "WorkspaceMembershipRepository", "WS_ROLE_BINDING", "role_for_ws_tier",
    "WorkspaceMemberView",
    # project lifecycle (M8.8)
    "ProjectLifecycleService", "ProjectMembershipService", "ProjectEnvironmentService",
    "ProjectMembershipRepository", "ProjectMemberView", "STANDARD_ENVIRONMENTS",
    # dto
    "CreateEnvironmentRequest", "CreateOrganizationRequest", "CreateProjectRequest",
    "CreateWorkspaceRequest", "EnvironmentView", "GrantRoleRequest", "InviteMemberRequest",
    "MemberView", "OrganizationView", "PreferencesView", "ProjectView",
    "RegisterUserRequest", "UpdatePreferencesRequest", "UserView", "WorkspaceView",
    # errors
    "AuthorizationError", "ConflictError", "FoundationError", "FoundationValidationError",
    "NotFoundError", "OrganizationIsolationError",
    # events
    "EventEmitter", "FoundationEvent", "FoundationEventType", "NullEventEmitter",
    "RecordingEventEmitter",
    # factory
    "Foundation", "FoundationFactory",
    # rbac
    "ROLE_PERMISSIONS", "Permission", "PermissionEvaluator", "RbacAuthorizer",
    "permissions_for",
    # repositories
    "EnvironmentRepository", "MembershipRepository", "OrganizationRepository",
    "PreferencesRepository", "ProjectRepository", "RoleBindingRepository",
    "UserRepository", "WorkspaceRepository",
    # seams
    "Clock", "DefaultIdGenerator", "FixedClock", "IdGenerator", "SequentialIdGenerator",
    "SystemClock",
    # services
    "AccessService", "ConfigurationService", "IdentityService", "MembershipService",
    "ProjectService", "WorkspaceService",
    # validation
    "FoundationValidator", "is_valid_email", "is_valid_slug",
]

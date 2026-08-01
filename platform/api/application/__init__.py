"""application — the Customer Platform Application Layer (Epic 8, M8.3).

The orchestration layer between a future API and the M8.2 Foundation. It owns use cases,
command/query handlers (CQRS), transaction boundaries (Unit of Work), authorization
orchestration, audit orchestration, validation orchestration, DTO mapping, and
application-level events. The **Foundation remains responsible only for domain behaviour**
— the Application never duplicates validation, permission logic, or persistence; it
coordinates them.

**Ownership & boundaries.** `api.application → api.foundation, core, tenant` only — no
FastAPI/HTTP/WebSocket, no database, no frozen Epic 5/6/7 runtime (test-enforced,
acyclic; the API/transport layer does not exist yet). Authorization is orchestrated by
calling the Foundation's `Authorizer` seam (no permission logic leaks upward). Determinism
(§A2.4) via the shared `Clock`/`IdGenerator` seams. The Foundation is byte-for-byte
unchanged by this milestone.
"""

from __future__ import annotations

from .activity import (
    ActivityFilter,
    GetRecentActivity,
    GetUserActivity,
)
from .agent_builder import (
    ArchiveAgent,
    CompareAgentVersions,
    CreateAgent,
    DeleteAgent,
    DuplicateAgent,
    GetAgent,
    GetAgentActivity,
    GetAgentVersion,
    ListAgents,
    ListAgentVersions,
    PublishAgent,
    RestoreAgent,
    RollbackAgent,
    SearchAgents,
    UnpublishAgent,
    UpdateAgent,
)
from .application import Application, ApplicationFactory
from .authorization import AuthorizationOrchestrator, tenant_for
from .commands import (
    ActivateMember,
    CreateEnvironment,
    CreateOrganization,
    CreateProject,
    CreateWorkspace,
    GrantRole,
    InviteMember,
    RegisterUser,
    RemoveMember,
    RevokeRole,
    UpdatePreferences,
)
from .deployment import (
    ApproveDeployment,
    CancelDeployment,
    GetDeploymentHealth,
    GetDeploymentLogs,
    GetDeploymentStatus,
    GetDeploymentTimeline,
    ListDeploymentHistory,
    ListDeploymentTargets,
    PlanDeployment,
    RejectDeployment,
    RetryDeployment,
    RollbackDeployment,
    SubmitDeployment,
)
from .dispatcher import CommandBus, CommandHandler, QueryBus, QueryHandler
from .errors import ApplicationError, NoHandlerError, UnsupportedOperationError
from .events import (
    ApplicationEvent,
    ApplicationEventEmitter,
    ApplicationEventType,
    NullApplicationEventEmitter,
    RecordingApplicationEventEmitter,
)
from .execution import (
    ExecuteTurn,
    GetConversationMessages,
    GetProviderDiagnostics,
)
from .knowledge import (
    ArchiveDocument,
    ArchiveKnowledgeCollection,
    ChunkDocument,
    CreateKnowledgeCollection,
    DeleteDocument,
    DeleteKnowledgeCollection,
    GenerateEmbeddings,
    GetKnowledgeCollection,
    IndexKnowledgeCollection,
    ListChunks,
    ListDocuments,
    ListKnowledgeCollections,
    ReplaceDocument,
    RestoreDocument,
    RestoreKnowledgeCollection,
    SearchKnowledge,
    UpdateKnowledgeCollection,
    UploadDocument,
    VectorStatus,
)
from .observability import (
    CreateAlertRule,
    DeleteAlertRule,
    EvaluateAlerts,
    GetAlertHistory,
    GetCost,
    GetDashboard,
    GetHealth,
    GetHealthHistory,
    GetLogs,
    GetMetricDefinitions,
    GetMetricReport,
    GetMetricSeries,
    GetPerformance,
    GetTrace,
    GetTraces,
    GetUsage,
    ListAlertRules,
    UpdateAlertRule,
)
from .org_lifecycle import (
    AcceptInvitation,
    CancelInvitation,
    DeclineInvitation,
    DeleteOrganization,
    GetOrganizationActivity,
    InviteToOrganization,
    ListPendingInvitations,
    ResendInvitation,
    UpdateOrganization,
)
from .playground import (
    CloseConversation,
    ContinueConversation,
    GetAdapterOutputs,
    GetConversation,
    ListConversationTurns,
    ResetConversation,
    StartConversation,
)
from .project_lifecycle import (
    AddProjectMember,
    ArchiveProject,
    GetProjectActivity,
    GetProjectEnvironment,
    ListProjectMembers,
    RemoveProjectMember,
    RestoreProject,
    SearchProjects,
    SwitchProjectEnvironment,
    UpdateProject,
)
from .queries import (
    GetOrganization,
    GetPreferences,
    GetUser,
    GetWorkspace,
    ListEnvironments,
    ListMembers,
    ListProjects,
    ListWorkspaces,
)
from .runtime import (
    CloseSession,
    CreateSession,
    ExpireSession,
    GetExecutionPlan,
    GetRuntimeState,
    GetSession,
    ListSessions,
    ResumeSession,
)
from .tool_framework import (
    ArchiveTool,
    AttachTool,
    ConfigureAgentTool,
    CreateTool,
    DeleteTool,
    DetachTool,
    DisableAgentTool,
    DisableTool,
    EnableAgentTool,
    EnableTool,
    GetTool,
    GetToolActivity,
    ListAgentTools,
    ListTools,
    ReorderAgentTools,
    RestoreTool,
    SearchTools,
    UpdateTool,
)
from .unit_of_work import (
    InMemoryUnitOfWork,
    InMemoryUnitOfWorkFactory,
    UnitOfWork,
    UnitOfWorkFactory,
)
from .use_cases import CommandUseCases, QueryUseCases
from .workspace_lifecycle import (
    AddWorkspaceMember,
    ArchiveWorkspace,
    FavoriteWorkspace,
    GetWorkspaceActivity,
    GetWorkspaceNavigation,
    ListWorkspaceMembers,
    RecordRecentWorkspace,
    RemoveWorkspaceMember,
    RestoreWorkspace,
    SearchWorkspaces,
    SetDefaultWorkspace,
    UpdateWorkspace,
)

__all__ = [
    # facade / factory
    "Application", "ApplicationFactory",
    # authorization
    "AuthorizationOrchestrator", "tenant_for",
    # commands
    "ActivateMember", "CreateEnvironment", "CreateOrganization", "CreateProject",
    "CreateWorkspace", "GrantRole", "InviteMember", "RegisterUser", "RemoveMember",
    "RevokeRole", "UpdatePreferences",
    # queries
    "GetOrganization", "GetPreferences", "GetUser", "GetWorkspace", "ListEnvironments",
    "ListMembers", "ListProjects", "ListWorkspaces",
    # dispatch
    "CommandBus", "CommandHandler", "QueryBus", "QueryHandler",
    # org lifecycle (M8.6)
    "UpdateOrganization", "DeleteOrganization", "InviteToOrganization", "AcceptInvitation",
    "DeclineInvitation", "CancelInvitation", "ResendInvitation", "GetOrganizationActivity",
    "ListPendingInvitations",
    # workspace lifecycle (M8.7)
    "UpdateWorkspace", "ArchiveWorkspace", "RestoreWorkspace", "AddWorkspaceMember",
    "RemoveWorkspaceMember", "SetDefaultWorkspace", "FavoriteWorkspace",
    "RecordRecentWorkspace", "ListWorkspaceMembers", "GetWorkspaceActivity",
    "GetWorkspaceNavigation", "SearchWorkspaces",
    # project lifecycle (M8.8)
    "UpdateProject", "ArchiveProject", "RestoreProject", "AddProjectMember",
    "RemoveProjectMember", "SwitchProjectEnvironment", "ListProjectMembers",
    "GetProjectActivity", "GetProjectEnvironment", "SearchProjects",
    # audit & activity (M8.9)
    "ActivityFilter", "GetRecentActivity", "GetUserActivity",
    # agent builder (M8.10)
    "CreateAgent", "UpdateAgent", "ArchiveAgent", "RestoreAgent", "DeleteAgent",
    "DuplicateAgent", "PublishAgent", "UnpublishAgent", "RollbackAgent", "GetAgent",
    "ListAgents", "SearchAgents", "ListAgentVersions", "GetAgentVersion",
    "CompareAgentVersions", "GetAgentActivity",
    # tool framework (M8.11)
    "CreateTool", "UpdateTool", "EnableTool", "DisableTool", "ArchiveTool", "RestoreTool",
    "DeleteTool", "AttachTool", "DetachTool", "EnableAgentTool", "DisableAgentTool",
    "ConfigureAgentTool", "ReorderAgentTools", "GetTool", "ListTools", "SearchTools",
    "ListAgentTools", "GetToolActivity",
    # conversation runtime (M8.12)
    "CreateSession", "ResumeSession", "CloseSession", "ExpireSession", "GetSession",
    "ListSessions", "GetRuntimeState", "GetExecutionPlan",
    # conversation playground (M8.13)
    "StartConversation", "ContinueConversation", "ResetConversation", "CloseConversation",
    "GetConversation", "ListConversationTurns", "GetAdapterOutputs",
    # provider adapter binding / real execution (M8.14)
    "ExecuteTurn", "GetConversationMessages", "GetProviderDiagnostics",
    # knowledge tool (M8.15)
    "CreateKnowledgeCollection", "UpdateKnowledgeCollection", "ArchiveKnowledgeCollection",
    "RestoreKnowledgeCollection", "DeleteKnowledgeCollection", "UploadDocument",
    "ReplaceDocument", "ArchiveDocument", "RestoreDocument", "DeleteDocument", "ChunkDocument",
    "GenerateEmbeddings", "IndexKnowledgeCollection", "SearchKnowledge",
    "ListKnowledgeCollections", "GetKnowledgeCollection", "ListDocuments", "ListChunks",
    # deployment actuator (M8.16)
    "SubmitDeployment", "ApproveDeployment", "RejectDeployment", "CancelDeployment",
    "RetryDeployment", "RollbackDeployment", "PlanDeployment", "GetDeploymentStatus",
    "ListDeploymentHistory", "GetDeploymentTimeline", "GetDeploymentLogs",
    "GetDeploymentHealth", "ListDeploymentTargets",
    # observability (M8.17)
    "CreateAlertRule", "UpdateAlertRule", "DeleteAlertRule", "GetMetricReport",
    "GetMetricDefinitions", "GetMetricSeries", "GetLogs", "GetTraces", "GetTrace", "GetHealth",
    "GetHealthHistory", "GetUsage", "GetCost", "GetPerformance", "GetDashboard", "ListAlertRules",
    "EvaluateAlerts", "GetAlertHistory",
    "VectorStatus",
    # errors
    "ApplicationError", "NoHandlerError", "UnsupportedOperationError",
    # events
    "ApplicationEvent", "ApplicationEventEmitter", "ApplicationEventType",
    "NullApplicationEventEmitter", "RecordingApplicationEventEmitter",
    # unit of work
    "InMemoryUnitOfWork", "InMemoryUnitOfWorkFactory", "UnitOfWork", "UnitOfWorkFactory",
    # use cases
    "CommandUseCases", "QueryUseCases",
]
